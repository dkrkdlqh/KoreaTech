import threading
import sys
import json
import traceback
import time

from queue import Queue

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



ORDER_STATE_BREW_BEFORE         :int = -1
ORDER_STATE_BREW_START          :int = 0
ORDER_STATE_BREW_COMPLETE       :int = 1
ORDER_STATE_PICKUP_ENABLE       :int = 2




class MultiProcessController():
    '''
    해당 클래스는 '[한기대] 공정교육용 로봇 시스템 - 통합 데모'룰 수행하는 기능이 구현되어있다.
    특이사항 : 로봇별 커피머신과 픽업대가 지정되어 있다. (UR5 + 1번드롱기 + 픽업대B / Indy7R + 2번드롱기 + 픽업대C)
    '''



    def __init__(self):
        
        # config 변수 선언 ------------------------
        self.__storeId                  :int = 6
        self.__printerId                :int = 6
        self.__trayNum                  :int = 2
        

        # 일반 변수 선언 --------------------------
        self.__orderId                  :int        = -1
        self.__menuIdList               :list[int]  = [-1, -1]
        self.__menuStateList            :list[int]  = [ORDER_STATE_BREW_BEFORE, ORDER_STATE_BREW_BEFORE] 
        self.__curMenuIndex             :int        = 0

        # 센서 상태 : 감지(0), 미감지(1)
        self.__hasCupOnMiddleATray      :int        = 1
        self.__hasCupOnMiddleBTray      :int        = 1
        self.__hasCupOnDeloghi01Tray    :int        = 1
        self.__hasCupOnDeloghi02Tray    :int        = 1
        self.__hasCupOnPickupATray      :int        = 1
        self.__hasCupOnPickupBTray      :int        = 1
        self.__hasCupOnPickupCTray      :int        = 1
        
        self.__delonghi01Status         :int        = DelonghiState.NOT_READY
        self.__delonghi02Status         :int        = DelonghiState.NOT_READY

        self.__delonghi01MenuIndex      :int        = -1
        self.__delonghi02MenuIndex      :int        = -1




        # 통신 변수 선언 --------------------------------------------------------
        self.__plcComm              :MelsecPLCVar = MelsecPLCVar(self.commVarEventCallback)
        self.__plcComm.connect("192.168.3.60", 9988)
        
        self.__delonghi01Comm       :BLEVar = BLEVar(self.commVarEventCallback)
        self.__delonghi01Comm.connect("00:a0:50:31:89:32", "00035b03-58e6-07dd-021a-08123a000300", "00035b03-58e6-07dd-021a-08123a000301", "00002902-0000-1000-8000-00805f9b34fb")
        
        self.__delonghi02Comm       :BLEVar = BLEVar(self.commVarEventCallback)
        self.__delonghi02Comm.connect("00:a0:50:63:19:9f", "00035b03-58e6-07dd-021a-08123a000300", "00035b03-58e6-07dd-021a-08123a000301", "00002902-0000-1000-8000-00805f9b34fb")


        # 로봇 통신 변수 선언
        # 로봇은 정면을 기준으로 좌측부터 indy7 -> UR5 -> indy7 순서로 배치됨
        self.__indy7LComm           :ModbusTCPVar = ModbusTCPVar(self.commVarEventCallback)
        self.__indy7LComm.connect("192.168.3.100", 502)

        self.__ur5Comm              :ModbusTCPVar = ModbusTCPVar(self.commVarEventCallback)
        self.__ur5Comm.connect("192.168.3.101", 502)

        self.__indy7RComm           :ModbusTCPVar = ModbusTCPVar(self.commVarEventCallback)
        self.__indy7RComm.connect("192.168.3.102", 502)

        self.__indy7LGripperComm    :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__indy7LGripperComm.connect("192.168.3.160", 5000)
        
        self.__ur5GripperComm       :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__ur5GripperComm.connect("192.168.3.170", 5000)

        self.__indy7RGripperComm    :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__indy7RGripperComm.connect("192.168.3.180", 5000)

        self.__delonghi01Container  :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__delonghi01Container.connect("192.168.3.121", 60000)

        self.__delonghi02Container  :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__delonghi02Container.connect("192.168.3.122", 60000)

        self.__cupDispenser         :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__cupDispenser.connect("192.168.3.110", 60000)

        self.__crcComm              :MqttVar = MqttVar(self.commVarEventCallback)
        self.__crcComm.connect("b85b26e22ac34763bd9cc18d7f655038.s2.eu.hivemq.cloud", 8883, "admin", "201103crcBroker", ["crc/jts", "print/mbrush"])
        self.__crcComm.setSubscribeFilter(MqttFilterData(CRCKey.KEY_STORE_ID, self.__storeId))

        self.__initGripper(self.__indy7LGripperComm)
        self.__initGripper(self.__ur5GripperComm)
        self.__initGripper(self.__indy7RGripperComm)

        self.__tpmSysFuncManager    :TPMSysFuncManager = TPMSysFuncManager()

        # CRC 서버 통신 처리 쓰레드
        self.__tpmSysFuncManager.runCRCCommunication(self.__crcComm, self.__storeId, self.__printerId, self.__trayNum)
        
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
            if self.__orderId == -1:
                self.__orderId = self.__tpmSysFuncManager.getCRCOrderId()    
            
            # 2. 주문 메뉴 수신 ========================================================================================================
            elif self.__menuIdList == [-1, -1]:
            
                self.__menuIdList = self.__tpmSysFuncManager.getCRCOrderMenuList()
                # 첫번째 메뉴부터 제조 시작    
                self.__curMenuIndex = 0
            
            # 3. 모든 주문 메뉴 제조 완료 -> 주문 완료 처리 =============================================================================
            elif self.__menuStateList == [ORDER_STATE_PICKUP_ENABLE, ORDER_STATE_PICKUP_ENABLE]:

                self.__tpmSysFuncManager.publishCRCOrderComplete(self.__crcComm, self.__storeId, self.__orderId)
                self.__orderId              = -1
                self.__menuIdList           = [-1, -1]
                self.__menuStateList        = [ORDER_STATE_BREW_BEFORE, ORDER_STATE_BREW_BEFORE]
                self.__delonghi01MenuIndex  = -1
                self.__delonghi02MenuIndex  = -1
            
            
            # 4. 타겟 메뉴가 '핫 아메리카노'인 경우 =====================================================================================
            elif self.__menuIdList[self.__curMenuIndex] == 1000: 
                
                if self.__menuStateList[self.__curMenuIndex] == ORDER_STATE_BREW_BEFORE:

                    # 중간 거치대에 컵이 없으면 
                    if self.__hasCupOnMiddleBTray == 0:

                        # 1번 드롱기에 컵이 없고, 사용 가능한 상태 -> 1번 드롱기에 curMenuIndex번 'hot 아메리카노' 메뉴 제조 명령
                        if self.__hasCupOnDeloghi01Tray == 0 and self.__delonghi01Status == DelonghiState.READY:

                            self.__deliveryHotCup()
                            self.__startBrewHotAmericanoOnDelonghi01()
                            self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_BREW_START
                            self.__delonghi01MenuIndex = self.__curMenuIndex

                        # 2번 드롱기에 컵이 없고, 사용 가능한 상태 -> 2번 드롱기에 curMenuIndex번 'hot 아메리카노' 메뉴 제조 명령
                        elif self.__hasCupOnDeloghi02Tray == 0 and self.__delonghi02Status == DelonghiState.READY:

                            self.__deliveryHotCup()
                            self.__startBrewHotAmericanoOnDelonghi02()
                            self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_BREW_START
                            self.__delonghi02MenuIndex = self.__curMenuIndex
            
                elif self.__menuStateList[self.__curMenuIndex] == ORDER_STATE_BREW_START:

                    # 타겟 인덱스의 메뉴를 1번 드롱기에서 담당하고
                    if self.__curMenuIndex == self.__delonghi01MenuIndex:    

                        # 대기 상태 & 1번 드롱기가 제조를 완료 -> 타겟 메뉴를 '제조 완료' 상태로 변경   
                        if self.__delonghi01Status == DelonghiState.READY:
                            self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_BREW_COMPLETE

                    # 타겟 인덱스의 메뉴를 2번 드롱기에서 담당하고
                    elif self.__curMenuIndex == self.__delonghi02MenuIndex:

                        # 대기 상태 & 2번 드롱기가 제조를 완료 -> 타겟 메뉴를 '제조 완료' 상태로 변경    
                        if self.__delonghi02Status == DelonghiState.READY:
                            self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_BREW_COMPLETE

                elif self.__menuStateList[self.__curMenuIndex] == ORDER_STATE_BREW_COMPLETE:

                    # 타겟 인덱스의 메뉴를 1번 드롱기에서 담당하고, 픽업대B에 컵이 없다면 -> 음료컵을 픽업대B로 P&P   
                    if self.__curMenuIndex == self.__delonghi01MenuIndex and self.__hasCupOnPickupBTray == 0:

                        self.__bringCupDelonghi01ToTrayB()
                        self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_PICKUP_ENABLE

                    # 타겟 인덱스의 메뉴를 2번 드롱기에서 담당하고, 픽업대C에 컵이 없다면 -> 음료컵을 픽업대C로 P&P
                    elif self.__curMenuIndex == self.__delonghi02MenuIndex and self.__hasCupOnPickupCTray == 0:

                        self.__bringCupDelonghi02ToTrayC()
                        self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_PICKUP_ENABLE

                # 다음 순번 메뉴로 이동
                self.__moveNextIndex()
                

            # 5. 타겟 메뉴가 '아이스 아메리카노'인 경우 =====================================================================================
            elif self.__menuIdList[self.__curMenuIndex] == 1001:
                
                if self.__menuStateList[self.__curMenuIndex] == ORDER_STATE_BREW_BEFORE:

                    # 중간 거치대에 컵이 없으면 
                    if self.__hasCupOnMiddleBTray == 0:

                        # 1번 드롱기에 컵이 없고, 사용 가능한 상태 -> 1번 드롱기에 curMenuIndex번 'ice 아메리카노' 메뉴 제조 명령
                        if self.__hasCupOnDeloghi01Tray == 0 and self.__delonghi01Status == DelonghiState.READY:

                            self.__deliveryIceCup()
                            self.__startBrewIceAmericanoOnDelonghi01
                            self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_BREW_START
                            self.__delonghi01MenuIndex = self.__curMenuIndex

                        # 2번 드롱기에 컵이 없고, 사용 가능한 상태 -> 2번 드롱기에 curMenuIndex번 'ice 아메리카노' 메뉴 제조 명령
                        elif self.__hasCupOnDeloghi02Tray == 0 and self.__delonghi02Status == DelonghiState.READY:

                            self.__deliveryIceCup()
                            self.__startBrewIceAmericanoOnDelonghi02()
                            self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_BREW_START
                            self.__delonghi02MenuIndex = self.__curMenuIndex
            
                elif self.__menuStateList[self.__curMenuIndex] == ORDER_STATE_BREW_START:

                    # 타겟 인덱스의 메뉴를 1번 드롱기에서 담당하고
                    if self.__curMenuIndex == self.__delonghi01MenuIndex:    

                        # 대기 상태 & 1번 드롱기가 제조를 완료 -> 타겟 메뉴를 '제조 완료' 상태로 변경  
                        if self.__delonghi01Status == DelonghiState.READY:
                            self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_BREW_COMPLETE

                    # 타겟 인덱스의 메뉴를 2번 드롱기에서 담당하고
                    elif self.__curMenuIndex == self.__delonghi02MenuIndex:

                        # 대기 상태 & 2번 드롱기가 제조를 완료 -> 타겟 메뉴를 '제조 완료' 상태로 변경  
                        if self.__delonghi02Status == DelonghiState.READY:
                            self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_BREW_COMPLETE

                elif self.__menuStateList[self.__curMenuIndex] == ORDER_STATE_BREW_COMPLETE:

                    # 타겟 인덱스의 메뉴를 1번 드롱기에서 담당하고, 픽업대B에 컵이 없다면 -> 음료컵을 픽업대B로 P&P    
                    if self.__curMenuIndex == self.__delonghi01MenuIndex and self.__hasCupOnPickupBTray == 0:

                        self.__bringCupDelonghi01ToTrayB()
                        self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_PICKUP_ENABLE

                    # 타겟 인덱스의 메뉴를 2번 드롱기에서 담당하고, 픽업대C에 컵이 없다면 -> 음료컵을 픽업대C로 P&P    
                    elif self.__curMenuIndex == self.__delonghi02MenuIndex and self.__hasCupOnPickupCTray == 0:

                        self.__bringCupDelonghi02ToTrayC()
                        self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_PICKUP_ENABLE

                # 다음 순번 메뉴로 이동
                self.__moveNextIndex()

            # 6. 해당 인덱스에 타겟 메뉴가 없다면 -> 제조할 음료가 없으므로, 바로 완료 상태로 처리 ============================================
            elif self.__menuIdList[self.__curMenuIndex] == -1:  

                self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_PICKUP_ENABLE
                # 다음 순번 메뉴로 이동
                self.__moveNextIndex()
            



    
    def __delonghiAndSensorStatusCheckingThreadHandler(self):
        '''
        ### 드롱기 실시간 상태 체크 쓰레드
        '''

        while True:
            
            if MainData.isRunningTPMProgram == False:
                break

            # 주기적으로 드롱기 상태 체크    
            self.__delonghi01Status         = self.__tpmSysFuncManager.getDelonghiStateCode(self.__delonghi01Comm)
            self.__delonghi02Status         = self.__tpmSysFuncManager.getDelonghiStateCode(self.__delonghi02Comm)
            
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
            

            # 2번 드롱기 찌꺼기 통 가득!
            if self.__delonghi02Status == DelonghiState.ERR_FULL_GROUNDS:
                
                self.__delonghi02Container.write("OPEN")
                time.sleep(15)
                self.__delonghi02Container.write("CLOSE")

            # 2번 드롱기 찌꺼기 통 열림!
            elif self.__delonghi02Status == DelonghiState.ERR_OPENED_GROUNDS_CONTAINER:
                
                self.__delonghi02Container.write("CLOSE")

            # 2번 드롱기 휴면 상태! 
            elif self.__delonghi02Status == DelonghiState.ERR_POWERED_OFF:

                self.__tpmSysFuncManager.wakeupDeloghi(self.__delonghi02Comm)


            time.sleep(1)



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



    def commVarEventCallback(self, eventId:int, data):
        '''
        통신 변수 이벤트 처리 전용 콜백 함수
        '''

        targetVar :str = ""

        if data == self.__plcComm:
            targetVar = "PLC"
        elif data == self.__delonghi01Comm:
            targetVar = "1번 드롱기"
        elif data == self.__delonghi02Comm:
            targetVar = "2번 드롱기"
        elif data == self.__indy7LComm:
            targetVar = "Indy7L"
        elif data == self.__ur5Comm:
            targetVar = "UR5"
        elif data == self.__indy7RComm:
            targetVar = "Indy7R"
        
        if eventId == Event.COMM_VAR_DISCONNECTED:

            CDRLog(f"{targetVar} 통신 끊어짐")
            self.__terminateSystem()

        elif eventId == Event.COMM_VAR_FAILED_TO_CONNECT:
            
            CDRLog(f"{targetVar} 통신 연결 실패")
            self.__terminateSystem()



    def __terminateSystem(self):
        sys.exit()



    def __deliveryHotCup(self):
        '''
        ### indy7L이 핫 컵을 중간 거치대로 전달 
        '''
        
        # Indy7L 그리퍼 닫기
        self.__holdGripper(self.__indy7LGripperComm) 

        # Indy7L이 컵디스펜서의 핫 음료컵을 받을 수 있는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 1)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)
        
        # 컵 디스펜서에서 핫 음료컵 배출
        self.__reqDispensingHotCup()
        time.sleep(3)

        # Indy7L이 중간 거치대에 컵을 내려놓는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 21)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)

        # Indy7L 그리퍼 열기 -> 컵은 중간 거치대에 place 
        self.__releaseGripper(self.__indy7LGripperComm) 
        time.sleep(2)

        # Indy7L이 중간 거치대에서 홈 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 23)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)




    def __deliveryIceCup(self):
        '''
        ### indy7L이 아이스 컵을 중간 거치대로 전달     
        '''      
        # Indy7L 그리퍼 닫기
        self.__holdGripper(self.__indy7LGripperComm) 

        # Indy7L이 컵디스펜서의 아이스 음료컵을 받을 수 있는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 2)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)
        
        # 컵 디스펜서에서 아이스 음료컵 배출
        self.__reqDispensingIceCup()
        time.sleep(3)

        # Indy7L이 거치대A에에 컵을 내려놓는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 11)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)

        # Indy7L 그리퍼 열기
        self.__releaseGripper(self.__indy7LGripperComm) 

        # Indy7L이 거치대A에 아이스 컵 잡는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 13)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)

        # Indy7L 그리퍼 닫기
        self.__holdGripper(self.__indy7LGripperComm) 

        # Indy7L이 제빙기에 얼음 받는 위치로 이동하고 레버 밀기기
        self.__sendIndyCmd(self.__indy7LComm, 14)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)

        # 제빙기에서 얼음 배출. 대기하면서 컵에 얼음 받기
        time.sleep(8)

        # Indy7L이 중간 거치대에 컵을 내려놓는 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 22)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)

        # Indy7L 그리퍼 열기 -> 컵은 중간 거치대에 place 
        self.__releaseGripper(self.__indy7LGripperComm) 
        time.sleep(2)

        # Indy7L이 중간 거치대에서 홈 위치로 이동
        self.__sendIndyCmd(self.__indy7LComm, 23)
        self.__waitUntilIndyMotionComplete(self.__indy7LComm)




    def __startBrewHotAmericanoOnDelonghi01(self):
        '''
        ### UR5가 빈 컵을 중간 거치대에서 1번 드롱기로 P&P -> 1번 드롱기에서 '핫 아메리카노' 음료 제조 시작 & UR5는 홈위치로 이동
        ''' 

        # UR5 그리퍼 열기
        self.__releaseGripper(self.__ur5GripperComm) 

        # UR5가 홈위치에서 중간 거치대의 컵 잡는 위치로 이동
        self.__sendURCmd(self.__ur5Comm, 1)
        self.__waitUntilURMotionComplete(self.__ur5Comm)

        # UR5 그리퍼 닫기
        self.__holdGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # UR5가 1번 드롱기에 컵을 내려놓는 위치로 이동
        self.__sendURCmd(self.__ur5Comm, 2)
        self.__waitUntilURMotionComplete(self.__ur5Comm)

        # UR5 그리퍼 열기 -> 컵은 1번 드롱기에 place
        self.__releaseGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # 1번 드롱기에서 아메리카노 제조 명령 전달
        self.__tpmSysFuncManager.brewDelonghiAmericano(self.__delonghi01Comm)

        # 커피 제조 시작과 동시에 UR5는 홈 위치로 이동
        self.__sendURCmd(self.__ur5Comm, 3)
        self.__waitUntilURMotionComplete(self.__ur5Comm)



    def __startBrewIceAmericanoOnDelonghi01(self):
        '''
        ### UR5가 빈 컵을 중간 거치대에서 1번 드롱기로 P&P -> 1번 드롱기에서 '아이스 아메리카노' 음료 제조 시작 & UR5는 홈위치로 이동  
        '''
        # UR5 그리퍼 열기
        self.__releaseGripper(self.__ur5GripperComm) 

        # UR5가 홈위치에서 중간 거치대의 컵 잡는 위치로 이동
        self.__sendURCmd(self.__ur5Comm, 1)
        self.__waitUntilURMotionComplete(self.__ur5Comm)

        # UR5 그리퍼 닫기
        self.__holdGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # UR5가 1번 드롱기에 컵을 내려놓는 위치로 이동
        self.__sendURCmd(self.__ur5Comm, 2)
        self.__waitUntilURMotionComplete(self.__ur5Comm)

        # UR5 그리퍼 열기 -> 컵은 1번 드롱기에 place
        self.__releaseGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # 1번 드롱기에서 에스프레소 제조 명령 전달
        self.__tpmSysFuncManager.brewDelonghiEspresso(self.__delonghi01Comm)

        # 커피 제조 시작과 동시에 UR5는 홈 위치로 이동
        self.__sendURCmd(self.__ur5Comm, 3)
        self.__waitUntilURMotionComplete(self.__ur5Comm)



    def __startBrewHotAmericanoOnDelonghi02(self):
        '''
        ### Indy7R이 빈 컵을 중간 거치대에서 2번 드롱기로 P&P -> 2번 드롱기에서 '핫 아메리카노' 음료 제조 시작 & Indy7R은 홈위치로 이동   
        '''
        # Indy7 그리퍼 열기
        self.__releaseGripper(self.__indy7RGripperComm) 

        # Indy7이이 홈위치에서 중간 거치대의 컵 잡는 위치로 이동
        self.__sendIndyCmd(self.__indy7RComm, 1)
        self.__waitUntilIndyMotionComplete(self.__indy7RComm)

        # Indy7 그리퍼 닫기
        self.__holdGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # Indy7이 1번 드롱기에 컵을 내려놓는 위치로 이동
        self.__sendIndyCmd(self.__indy7RComm, 2)
        self.__waitUntilIndyMotionComplete(self.__indy7RComm)

        # Indy7 그리퍼 열기 -> 컵은 1번 드롱기에 place
        self.__releaseGripper(self.__indy7RGripperComm) 
        time.sleep(2)

        # 1번 드롱기에서 아메리카노 제조 명령 전달
        self.__tpmSysFuncManager.brewDelonghiAmericano(self.__delonghi01Comm)

        # 커피 제조 시작과 동시에 Indy7은 홈 위치로 이동
        self.__sendIndyCmd(self.__indy7RComm, 3)
        self.__waitUntilIndyMotionComplete(self.__indy7RComm)



    def __startBrewIceAmericanoOnDelonghi02(self):
        '''
        ### Indy7R이 빈 컵을 중간 거치대에서 2번 드롱기로 P&P -> 2번 드롱기에서 '아이스 아메리카노' 음료 제조 시작 & Indy7R은 홈위치로 이동 
        '''
        # Indy7 그리퍼 열기
        self.__releaseGripper(self.__indy7RGripperComm) 

        # Indy7이이 홈위치에서 중간 거치대의 컵 잡는 위치로 이동
        self.__sendIndyCmd(self.__indy7RComm, 1)
        self.__waitUntilIndyMotionComplete(self.__indy7RComm)

        # Indy7 그리퍼 닫기
        self.__holdGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # Indy7이 1번 드롱기에 컵을 내려놓는 위치로 이동
        self.__sendIndyCmd(self.__indy7RComm, 2)
        self.__waitUntilIndyMotionComplete(self.__indy7RComm)

        # Indy7 그리퍼 열기 -> 컵은 1번 드롱기에 place
        self.__releaseGripper(self.__indy7RGripperComm) 
        time.sleep(2)

        # 1번 드롱기에서 에스프레소 제조 명령 전달
        self.__tpmSysFuncManager.brewDelonghiEspresso(self.__delonghi01Comm)

        # 커피 제조 시작과 동시에 Indy7은 홈 위치로 이동
        self.__sendIndyCmd(self.__indy7RComm, 3)
        self.__waitUntilIndyMotionComplete(self.__indy7RComm)



    def __bringCupDelonghi01ToTrayB(self):
        '''
        ### UR5가 음료컵을 1번 드롱기에서 픽업대B로 P&P -> UR5는 홈위치로 이동
        '''
        # UR5 그리퍼 열기
        self.__releaseGripper(self.__ur5GripperComm) 

        # UR5가 홈위치에서 중간 거치대의 컵 잡는 위치로 이동
        self.__sendURCmd(self.__ur5Comm, 4)
        self.__waitUntilURMotionComplete(self.__ur5Comm)

        # UR5 그리퍼 닫기
        self.__holdGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # UR5가 픽업대B에 컵을 내려놓는 위치로 이동
        self.__sendURCmd(self.__ur5Comm, 5)
        self.__waitUntilURMotionComplete(self.__ur5Comm)

        # UR5 그리퍼 열기 -> 컵은 픽업대B에 place
        self.__releaseGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # UR5는 홈 위치로 이동
        self.__sendURCmd(self.__ur5Comm, 6)
        self.__waitUntilURMotionComplete(self.__ur5Comm)



    def __bringCupDelonghi02ToTrayC(self):
        '''
        ### Indy7R이 음료컵을 2번 드롱기에서 픽업대C로 P&P -> Indy7R은 홈위치로 이동
        '''
        # Indy7R 그리퍼 열기
        self.__releaseGripper(self.__indy7RGripperComm) 

        # Indy7R이 홈위치에서 중간 거치대의 컵 잡는 위치로 이동
        self.__sendIndyCmd(self.__indy7RComm, 4)
        self.__waitUntilIndyMotionComplete(self.__indy7RComm)

        # Indy7R 그리퍼 닫기
        self.__holdGripper(self.__indy7RGripperComm) 
        time.sleep(2)

        # Indy7R이 픽업대C에 컵을 내려놓는 위치로 이동
        self.__sendIndyCmd(self.__indy7RComm, 5)
        self.__waitUntilIndyMotionComplete(self.__indy7RComm)

        # Indy7R 그리퍼 열기 -> 컵은 픽업대C에 place
        self.__releaseGripper(self.__indy7RGripperComm) 
        time.sleep(2)

        # Indy7R은 홈 위치로 이동
        self.__sendIndyCmd(self.__indy7RComm, 6)
        self.__waitUntilIndyMotionComplete(self.__indy7RComm)



    def __moveNextIndex(self):
        '''
        타겟 메뉴 인덱스를 다음 순번으로 변경
        '''

        self.__curMenuIndex += 1

        if self.__curMenuIndex >= self.__trayNum:
            self.__curMenuIndex = 0


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



    def __sendURCmd(self, urComm:ModbusTCPVar, cmd:int):

        writeModbusResult   :bool           = False
                
        while MainData.isRunningTPMProgram == True:
            
            writeModbusResult = urComm.write(ModbusFuncCode.WRITE_MULTI_REGISTERS, 128, cmd)

            if writeModbusResult == False:
                time.sleep(0.1)
                CDRLog.print("UR modbus 변수 write 실패")
            else:
                break



    def __waitUntilURMotionComplete(self, urComm:ModbusTCPVar):

        time.sleep(0.1)

        readModbusDataValue     :list           = None

        while MainData.isRunningTPMProgram == True:
            
            readModbusDataValue = urComm.read(ModbusFuncCode.READ_HOLDING_REGISTERS, 128, 1)   
            
            if readModbusDataValue == None:

                CDRLog.print("UR modbus 변수 read 실패")

            else:
                # 0번 주소의 값이 '0' -> UR이 대기중인 상태임을 의미  
                if readModbusDataValue[0] == 0:
                    break

            time.sleep(0.1)



    def __reqDispensingHotCup(self) :
        '''
        컵 자판기 종이컵 출력 명령
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
        컵 자판기 플라스틱컵 출력 명령
        '''
        writeTcpIpResult    :bool           = False

        while MainData.isRunningTPMProgram == True:

            writeTcpIpResult = self.__cupDispenser.write('02034101010349', 1)

            if writeTcpIpResult == False:
                time.sleep(0.1)
                CDRLog.print("컵디스펜서 Ice 컵 배출 명령 전송 실패")
            else:
                break 
        