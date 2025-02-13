import threading
import sys
import time

from variable.bleVar import BLEVar
from variable.melsecPLCVar import MelsecPLCVar
from variable.modbusTCPVar import ModbusTCPVar
from variable.mqttVar import MqttVar
from variable.tcpipVar import TcpIPVar 

from cdrutils.log import CDRLog

from const.config import Config
from const.event import Event
from const.crcJsonKeyword import CRCJsonKeyword as CRCKey
from const.delonghiState import DelonghiState
from const.modbusFuncCode import ModbusFuncCode

from data.mainData import MainData
from data.mqttFilterData import MqttFilterData

from manager.tpmSysFuncManager import TPMSysFuncManager




class SingleProcessController():
    '''
    해당 클래스는 '[한기대] 공정교육용 로봇 시스템 - 단독 공정 데모'룰 수행하는 기능이 구현되어있다.
    특이사항 : 한번에 최대 1잔만 제조가 가능한 공정이므로, 로봇이 직접 컵을 들고 추출된 커피를 받는다. 
    '''



    def __init__(self):
		# config 변수 선언 ------------------------
        self.__trayNum                  :int        = 2
		# 일반 변수 선언 --------------------------
        self.__menuId                   :int        = -1

        # 센서 상태 : 감지(0), 미감지(1)
        self.__hasCupOnMiddleATray      :int        = 1
        self.__hasCupOnMiddleBTray      :int        = 1
        self.__hasCupOnDeloghi01Tray    :int        = 1
        self.__hasCupOnDeloghi02Tray    :int        = 1
        self.__hasCupOnPickupATray      :int        = 1
        self.__hasCupOnPickupBTray      :int        = 1
        self.__hasCupOnPickupCTray      :int        = 1
        
        self.__delonghi01Status         :int        = DelonghiState.NOT_READY

        self.__tpmSysFuncManager    :TPMSysFuncManager = TPMSysFuncManager()  #mini
        self.__tpmSysFuncManager.initSysFuncVar() 
        MainData.isRunningTPMProgram = True
        
        self.__tpmSysFuncManager.__storeId                   = 6
        self.__tpmSysFuncManager.__printerId                 = 6
        
        

        # 통신 변수 선언 --------------------------------------------------------
        self.__plcComm              :MelsecPLCVar = MelsecPLCVar(self.commVarEventCallback)
        self.__plcComm.connect("192.168.3.60", 9988)
        
        self.__delonghi01Comm       :BLEVar = BLEVar(self.commVarEventCallback)
        self.__delonghi01Comm.connect("00:a0:50:31:89:32", "00035b03-58e6-07dd-021a-08123a000300", "00035b03-58e6-07dd-021a-08123a000301", "00002902-0000-1000-8000-00805f9b34fb")
        

        self.__delonghi01Container  :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__delonghi01Container.connect("192.168.3.121", 60000)
  
        self.__crcComm              :MqttVar = MqttVar(self.commVarEventCallback)
        self.__crcComm.connect("b85b26e22ac34763bd9cc18d7f655038.s2.eu.hivemq.cloud", 8883, "admin", "201103crcBroker", ["crc/jts", "print/mbrush"])
        self.__crcComm.setSubscribeFilter(MqttFilterData(CRCKey.KEY_STORE_ID, self.__tpmSysFuncManager.__storeId))
 
        # 로봇 통신 변수 선언
        # 로봇은 정면을 기준으로 좌측부터 indy7 -> UR5 -> indy7 순서로 배치됨
        self.__indy7LComm           :ModbusTCPVar = ModbusTCPVar(self.commVarEventCallback)
        self.__indy7LComm.connect("192.168.3.100", 502)#("192.168.3.101", 502)

        ############ TEST 시 주석처리
        self.__cupDispenser         :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__cupDispenser.connect("192.168.3.111", 60000)#("192.168.3.110", 5000)
        
        ############ TEST 시 주석처리
        self.__indy7LGripperComm    :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__indy7LGripperComm.connect("192.168.3.160", 5000)
        self.__initGripper(self.__indy7LGripperComm)

        
        while True:
            if ( 
                self.__plcComm.isConnected()
                and self.__delonghi01Comm.isConnected()
                and self.__crcComm.isConnected()
            ):
                break   
        CDRLog.print("[100%] Comm Complete. Thread Start ")
        # CRC 서버 통신 처리 쓰레드
        self.__tpmSysFuncManager.runCRCCommunication(self.__crcComm, self.__tpmSysFuncManager.__storeId, self.__tpmSysFuncManager.__printerId, self.__trayNum)
        
        # 커피 제조 쓰레드
        threading.Thread(target = self.__coffeeMakingThreadHandler).start()   

        # 드롱기 실시간 상태 체크 쓰레드
        threading.Thread(target = self.__delonghiAndSensorStatusCheckingThreadHandler).start()   
        
        # 키보드 명령 key값 입력 처리 쓰레드
        threading.Thread(target = self.__keyInputThreadHandler).start()    



    def __coffeeMakingThreadHandler(self):
        '''
        ### 커피 제조 쓰레드 \n
        - K하이테크 데모용 TPM 스크립트의 program 트리 구성을 기반으로 작성하였다.
        '''
        
        while True:

            if MainData.isRunningTPMProgram == False:
                break
            
            # 1. orderId 수신 ==========================================================================================================
            if self.__tpmSysFuncManager.__orderId == -1:
                self.__tpmSysFuncManager.__orderId = self.__tpmSysFuncManager.getCRCOrderId()    
            
            # 2. 주문 메뉴 수신 ========================================================================================================
            elif self.__menuId == -1:
            
                self.__menuId = self.__tpmSysFuncManager.getCRCOrderMenu()
                
                # 3. orderId는 존재하나, 제조할 주문 메뉴 정보가 더 이상 존재하지 않는다면, -> 주문 완료 처리
                if self.__menuId == -1:

                    self.__tpmSysFuncManager.publishCRCOrderComplete(self.__crcComm, self.__tpmSysFuncManager.__storeId, self.__tpmSysFuncManager.__orderId)
                    self.__tpmSysFuncManager.__orderId              = -1
                    self.__menuId               = -1
                    
            
            # 4. 타겟 메뉴가 '핫 아메리카노'인 경우 =====================================================================================
            elif self.__menuId == 1000: 
                
                self.__makeHotAmericano()
                

            # 5. 타겟 메뉴가 '아이스 아메리카노'인 경우 =====================================================================================
            elif self.__menuId == 1001:
                
                self.__makeIceAmericano()



    
    def __delonghiAndSensorStatusCheckingThreadHandler(self):
        '''
        ### 드롱기 실시간 상태 체크 쓰레드
        '''

        while True:
            
            if MainData.isRunningTPMProgram == False:
                break

            # 주기적으로 드롱기 상태 체크    
            self.__delonghi01Status         = self.__tpmSysFuncManager.getDelonghiStateCode(self.__delonghi01Comm)
            
            #주기적으로 트레이 센서 감지 상태 체크
            sensorStateList:list[int]       = self.__plcComm.read("M000", 7) 
            self.__hasCupOnMiddleATray      = sensorStateList[0]
            self.__hasCupOnMiddleBTray      = sensorStateList[1]
            self.__hasCupOnDeloghi01Tray    = sensorStateList[2]
            self.__hasCupOnDeloghi02Tray    = sensorStateList[3]
            self.__hasCupOnPickupATray      = sensorStateList[4]
            self.__hasCupOnPickupBTray      = sensorStateList[5]
            self.__hasCupOnPickupCTray      = sensorStateList[6]


            # 1번 드롱기 찌꺼기 통 가득!
            if self.__delonghi01Status == DelonghiState.ERR_FULL_GROUNDS:
                
                self.__delonghi01Container.write("OPEN")
                time.sleep(15)
                self.__delonghi01Container.write("CLOSE")
                
            # 1번 드롱기 찌꺼기 통 열림!
            elif self.__delonghi01Status == DelonghiState.ERR_OPENED_GROUNDS_CONTAINER:
                
                self.__delonghi01Container.write("CLOSE")

            # 1번 드롱기 휴면 상태!    
            elif self.__delonghi01Status == DelonghiState.ERR_POWERED_OFF:

                self.__tpmSysFuncManager.wakeupDeloghi(self.__delonghi01Comm)


            time.sleep(1)






    def commVarEventCallback(self, eventId:int, data):
        '''
        통신 변수 이벤트 처리 전용 콜백 함수
        '''

        targetVar :str = ""
        if data == self.__plcComm:
            targetVar = "PLC"
        elif data == self.__delonghi01Comm:
            targetVar = "1번 드롱기"
        elif data == self.__indy7LComm:
            targetVar = "Indy7L"
        elif data == self.__indy7LGripperComm:
            targetVar = "Indy7L_그리퍼"
        
        if eventId == Event.COMM_VAR_DISCONNECTED:

            CDRLog.print(f"{targetVar} 통신 끊어짐")
            self.__terminateSystem()

        elif eventId == Event.COMM_VAR_FAILED_TO_CONNECT:
            
            CDRLog.print(f"{targetVar} 통신 연결 실패")
            self.__terminateSystem()



    def __makeHotAmericano(self):
        '''
        ### indy7L이 핫 음료를 제조 
        '''
        
        while True:

            if MainData.isRunningTPMProgram == False:
                return

            # 1번드롱기에 컵이 없고, 1번드롱기가 제조 가능한 상태이면 break
            if self.__hasCupOnDeloghi01Tray == 0 and self.__delonghi01Status == DelonghiState.READY:
                break

            time.sleep(1)

        # Indy7L 그리퍼 닫기
        self.__holdGripper(self.__indy7LGripperComm) 

        # Indy7L이 컵디스펜서의 핫 음료컵을 받을 수 있는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 1)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)
        
        # 컵 디스펜서에서 핫 음료컵 배출
        self.__reqDispensingHotCup()
        time.sleep(3)

        # Indy7L이 거치대A에 컵을 내려놓는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 11)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)

        # Indy7L 그리퍼 열기 -> 컵은 거치대A에 place 
        self.__releaseGripper(self.__indy7LGripperComm) 
        time.sleep(2)

        # Indy7L이 거치대A의 컵 잡는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 12)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)

        # Indy7L 그리퍼 닫기
        self.__holdGripper(self.__indy7LGripperComm) 

        # Indy7L이 1번 드롱기 컵 내려놓는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 16)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)

        # 1번 드롱기에서 아메리카노 제조 명령 전달 (Indy7이 컵 잡은 상태에서 제조)
        self.__tpmSysFuncManager.brewDelonghiAmericano(self.__delonghi01Comm)

        time.sleep(2)

        while True:

            if MainData.isRunningTPMProgram == False:
                return

            # 픽업대A에 컵이 없고, 1번드롱기 제조가 완료되면 break
            if self.__hasCupOnPickupATray == 0 and self.__delonghi01Status == DelonghiState.READY:
                break

            time.sleep(1)

        # Indy7L이 픽업대A의 컵 내려놓는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 17)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)

        # Indy7L 그리퍼 열기
        self.__releaseGripper(self.__indy7LGripperComm) 

        # Indy7L이 홈위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 18)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)





    def __makeIceAmericano(self):
        '''
        ### indy7L이 아이스 음료를 제조     
        '''      
        while True:

            if MainData.isRunningTPMProgram == False:
                return

            # 1번드롱기에 컵이 없고, 1번드롱기가 제조 가능한 상태이면 break
            if self.__hasCupOnDeloghi01Tray == 0 and self.__delonghi01Status == DelonghiState.READY:
                break

            time.sleep(1)

        # Indy7L 그리퍼 닫기
        self.__holdGripper(self.__indy7LGripperComm) 

        # Indy7L이 컵디스펜서의 핫 음료컵을 받을 수 있는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 1)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)
        
        # 컵 디스펜서에서 핫 음료컵 배출
        self.__reqDispensingHotCup()
        time.sleep(3)

        # Indy7L이 거치대A에 컵을 내려놓는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 11)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)

        # Indy7L 그리퍼 열기 -> 컵은 거치대A에 place 
        self.__releaseGripper(self.__indy7LGripperComm) 
        time.sleep(2)

        # Indy7L이 거치대A의 컵 잡는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 12)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)

        # Indy7L 그리퍼 닫기
        self.__holdGripper(self.__indy7LGripperComm) 

        # Indy7L이 1번 드롱기 컵 내려놓는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 16)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)

        # 1번 드롱기에서 아메리카노 제조 명령 전달 (Indy7이 컵 잡은 상태에서 제조)
        self.__tpmSysFuncManager.brewDelonghiAmericano(self.__delonghi01Comm)

        time.sleep(2)

        while True:

            if MainData.isRunningTPMProgram == False:
                return

            # 픽업대A에 컵이 없고, 1번드롱기 제조가 완료되면 break
            if self.__hasCupOnPickupATray == 0 and self.__delonghi01Status == DelonghiState.READY:
                break

            time.sleep(1)

        # Indy7L이 픽업대A의 컵 내려놓는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 17)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)

        # Indy7L 그리퍼 열기
        self.__releaseGripper(self.__indy7LGripperComm) 

        # Indy7L이 홈위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 18)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)



    ##################################################################################################################################################################

    def __initGripper(self, gripperComm:TcpIPVar):
        '''
        ### 그리퍼 상태 초기화 명령
        '''

        writeTcpIpResult    :bool           = False

        while MainData.isRunningTPMProgram == True:

            writeTcpIpResult = gripperComm.write('01060100000149f6', 1)

            if writeTcpIpResult == False:
                time.sleep(0.1)
                CDRLog.print("그리퍼 초기화 명령 전송 실패")
            else:
                break


    def __holdGripper(self, gripperComm:TcpIPVar):
        '''
        ### 그리퍼 상태 초기화 명령
        '''

        writeTcpIpResult    :bool           = False

        while MainData.isRunningTPMProgram == True:

            writeTcpIpResult = gripperComm.write('0106010300007836', 1)

            if writeTcpIpResult == False:
                time.sleep(0.1)
                CDRLog.print("그리퍼 hold 명령 전송 실패")
            else:
                break

        
    def __releaseGripper(self, gripperComm:TcpIPVar):
        '''
        ### 그리퍼 상태 초기화 명령
        '''

        writeTcpIpResult    :bool           = False

        while MainData.isRunningTPMProgram == True:

            writeTcpIpResult = gripperComm.write('0106010303E87888', 1)

            if writeTcpIpResult == False:
                time.sleep(0.1)
                CDRLog.print("그리퍼 Release 명령 전송 실패")
            else:
                break


    def __sendIndyCmd(self, indyComm:ModbusTCPVar, cmd:int):
        '''
        ### Indy 로봇의 모드버스 통신 write 명령 
        '''

        writeModbusResult   :bool           = False
                
        while MainData.isRunningTPMProgram == True:
            
            writeModbusResult = indyComm.write(ModbusFuncCode.WRITE_MULTI_REGISTERS, 0, cmd)

            if writeModbusResult == False:
                time.sleep(0.1)
                CDRLog.print("Indy modbus 변수 write 실패")
            else:
                break



    def __waitUntilIndyMotionComplete(self, indyComm:ModbusTCPVar):

        time.sleep(0.1)

        readModbusDataValue     :list           = None

        while MainData.isRunningTPMProgram == True:
            
            readModbusDataValue = indyComm.read(ModbusFuncCode.READ_HOLDING_REGISTERS, 0, 1)   
            
            if readModbusDataValue == None:

                CDRLog.print("Indy modbus 변수 read 실패")

            else:
                # 0번 주소의 값이 '0' -> Indy7이 대기중인 상태임을 의미  
                if readModbusDataValue[0] == 0:
                    break

            time.sleep(0.1)



    def __reqDispensingHotCup(self) :
        '''
        컵 자판기 종이컵 배출 명령
        '''
        writeTcpIpResult    :bool           = False

        while MainData.isRunningTPMProgram == True:

            writeTcpIpResult = self.__cupDispenser.write('0203410201034A', 1)

            if writeTcpIpResult == False:
                time.sleep(0.1)
                CDRLog.print("컵디스펜서 Hot 컵 배출 명령 전송 실패")
            else:
                break    

        

    def __reqDispensingIceCup(self) :
        '''
        컵 자판기 플라스틱컵 배출 명령
        '''
        writeTcpIpResult    :bool           = False

        while MainData.isRunningTPMProgram == True:

            writeTcpIpResult = self.__cupDispenser.write('02034101010349', 1)

            if writeTcpIpResult == False:
                time.sleep(0.1)
                CDRLog.print("컵디스펜서 Ice 컵 배출 명령 전송 실패")
            else:
                break 

    def __keyInputThreadHandler(self):
        '''
        ### 프로그램 종료 키 입력 처리 쓰레드
        '''
        while True:
            
            key = input() 

            if key == Config.KEY_QUIT:
                
                CDRLog.print("+++++++++++++++++++++++++++++++++++++++++++++++++++")    
                CDRLog.print("TMM will be terminated. Goodbye and see you again!!")    
                CDRLog.print("+++++++++++++++++++++++++++++++++++++++++++++++++++")   
                MainData.isRunningTPMProgram    = False
                
                self.__terminateSystem()
                break


        CDRLog.print("============ __keyInputThread terminated...")


    def __terminateSystem(self):
        MainData.isRunningTPMProgram    = False
        sys.exit()
        