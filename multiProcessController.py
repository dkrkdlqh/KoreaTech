import threading
import sys
import json
import traceback
import time
import datetime

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
        CDRLog.print("[0%] var init Start.")
        # config 변수 선언 ------------------------
        self.__trayNum                  :int = 2
        
        
        self.__indyCmdAddr              :int        = 0
        self.__indyFeedbackAddr         :int        = 1
        self.__indyStartFeedback        :int        = 100
        self.__indtFinFeedback          :int        = 0
        
        
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

        self.__tpmSysFuncManager    :TPMSysFuncManager = TPMSysFuncManager()  #mini
        self.__tpmSysFuncManager.initSysFuncVar() 
        MainData.isRunningTPMProgram = True
        
        self.__tpmSysFuncManager.__storeId                   = 6 #7
        self.__tpmSysFuncManager.__printerId                 = 6 #7
        
        
        CDRLog.print("[30%] Comm init Start.")
        # 통신 변수 선언 --------------------------------------------------------
        self.__plcComm              :MelsecPLCVar = MelsecPLCVar(self.commVarEventCallback)
        self.__plcComm.connect("192.168.3.60", 9988)
        
        self.__delonghi01Comm       :BLEVar = BLEVar(self.commVarEventCallback)
        self.__delonghi01Comm.connect("00:a0:50:31:89:32", "00035b03-58e6-07dd-021a-08123a000300", "00035b03-58e6-07dd-021a-08123a000301", "00002902-0000-1000-8000-00805f9b34fb")
        #("00:A0:50:3D:86:d7", "00035b03-58e6-07dd-021a-08123a000300", "00035b03-58e6-07dd-021a-08123a000301", "00002902-0000-1000-8000-00805f9b34fb")
     
        self.__delonghi02Comm       :BLEVar = BLEVar(self.commVarEventCallback)
        self.__delonghi02Comm.connect("00:a0:50:63:19:9f", "00035b03-58e6-07dd-021a-08123a000300", "00035b03-58e6-07dd-021a-08123a000301", "00002902-0000-1000-8000-00805f9b34fb")
        #("00:A0:50:99:0A:0E", "00035b03-58e6-07dd-021a-08123a000300", "00035b03-58e6-07dd-021a-08123a000301", "00002902-0000-1000-8000-00805f9b34fb")
     
        self.__delonghiContainer  :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__delonghiContainer.connect("192.168.3.123", 60000)
        
        self.__crcComm              :MqttVar = MqttVar(self.commVarEventCallback)
        self.__crcComm.connect("b85b26e22ac34763bd9cc18d7f655038.s2.eu.hivemq.cloud", 8883, "admin", "201103crcBroker", ["crc/jts", "print/mbrush"])
        self.__crcComm.setSubscribeFilter(MqttFilterData(CRCKey.KEY_STORE_ID, self.__tpmSysFuncManager.__storeId))

        self.__cupDispenser         :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__cupDispenser.connect("192.168.3.111", 60000) #("192.168.3.110", 5000)

        # 로봇 통신 변수 선언
        # 로봇은 정면을 기준으로 좌측부터 indy7 -> UR5 -> indy7 순서로 배치됨
        self.__indy7LComm           :ModbusTCPVar = ModbusTCPVar(self.commVarEventCallback)
        self.__indy7LComm.connect("192.168.3.100", 502)#("192.168.3.101", 502) 

        self.__ur5Comm              :ModbusTCPVar = ModbusTCPVar(self.commVarEventCallback)
        self.__ur5Comm.connect("192.168.3.102", 502)

        self.__indy7RComm           :ModbusTCPVar = ModbusTCPVar(self.commVarEventCallback)
        self.__indy7RComm.connect("192.168.3.103", 502)

        self.__indy7LGripperComm    :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__indy7LGripperComm.connect("192.168.3.160", 5000)
        
        self.__ur5GripperComm       :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__ur5GripperComm.connect("192.168.3.170", 5000)

        self.__indy7RGripperComm    :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__indy7RGripperComm.connect("192.168.3.180", 5000)

        self.__tpmSysFuncManager.initDHGripperVar(self.__indy7LGripperComm)
        self.__tpmSysFuncManager.initDHGripperVar(self.__ur5GripperComm)
        self.__tpmSysFuncManager.initDHGripperVar(self.__indy7RGripperComm)

        self.__order_UI             :TcpIPVar = TcpIPVar(self.commVarEventCallback)
        self.__order_UI.connect("127.0.0.1", 6666)        
          
        CDRLog.print("[70%] Comm init Complete.")
        while True:
            if ( 
                self.__plcComm.isConnected()
                and self.__delonghi01Comm.isConnected()
                and self.__delonghi02Comm.isConnected()
                and self.__crcComm.isConnected()
            ):
                break   

        # CRC 서버 통신 처리 쓰레드
        self.__tpmSysFuncManager.runCRCCommunication(self.__crcComm, self.__tpmSysFuncManager.__storeId, self.__tpmSysFuncManager.__printerId, self.__trayNum)
        CDRLog.print("[100%] Comm connect Complete. Thread Start ")
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
            #CDRLog.print(f"{self.__orderId}  {self.__menuIdList} {self.__menuStateList} {self.__curMenuIndex} {self.__delonghi01MenuIndex} {self.__delonghi02MenuIndex}")
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

                self.__tpmSysFuncManager.publishCRCOrderComplete(self.__crcComm, self.__tpmSysFuncManager.__storeId, self.__orderId)
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
                    #CDRLog.print(f"{self.__curMenuIndex} {self.__delonghi01MenuIndex}  // {self.__delonghi02MenuIndex}")
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
                        CDRLog.print(f"Americano Make Complete. orderId : {self.__orderId} Menu : {self.__menuIdList[self.__curMenuIndex]} ")
                        self.UI_reset_thread(slot='b',ordernum=self.__tpmSysFuncManager.getCRCOrderNumber())
                        # 다음 순번 메뉴로 이동
                        self.__moveNextIndex()

                    # 타겟 인덱스의 메뉴를 2번 드롱기에서 담당하고, 픽업대C에 컵이 없다면 -> 음료컵을 픽업대C로 P&P
                    elif self.__curMenuIndex == self.__delonghi02MenuIndex and self.__hasCupOnPickupCTray == 0:

                        self.__bringCupDelonghi02ToTrayC()
                        self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_PICKUP_ENABLE
                        CDRLog.print(f"Americano Make Complete. orderId : {self.__orderId} Menu : {self.__menuIdList[self.__curMenuIndex]} ")
                        self.UI_reset_thread(slot='c',ordernum=self.__tpmSysFuncManager.getCRCOrderNumber())
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
                        CDRLog.print(f"Americano Make Complete. orderId : {self.__orderId} Menu : {self.__menuIdList[self.__curMenuIndex]} ")
                        self.UI_reset_thread(slot='b',ordernum=self.__tpmSysFuncManager.getCRCOrderNumber())
                        # 다음 순번 메뉴로 이동
                        self.__moveNextIndex()

                    # 타겟 인덱스의 메뉴를 2번 드롱기에서 담당하고, 픽업대C에 컵이 없다면 -> 음료컵을 픽업대C로 P&P    
                    elif self.__curMenuIndex == self.__delonghi02MenuIndex and self.__hasCupOnPickupCTray == 0:

                        self.__bringCupDelonghi02ToTrayC()
                        self.__menuStateList[self.__curMenuIndex] = ORDER_STATE_PICKUP_ENABLE
                        CDRLog.print(f"Americano Make Complete. orderId : {self.__orderId} Menu : {self.__menuIdList[self.__curMenuIndex]} ")
                        self.UI_reset_thread(slot='c',ordernum=self.__tpmSysFuncManager.getCRCOrderNumber())

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
                
                self.__delonghiContainer.write("OPEN_1")
                time.sleep(15)
                self.__delonghiContainer.write("CLOSE_1")
                
            # 1번 드롱기 찌꺼기 통 열림!
            elif self.__delonghi01Status == DelonghiState.ERR_OPENED_GROUNDS_CONTAINER:
                
                self.__delonghiContainer.write("CLOSE_1")

            # 1번 드롱기 휴면 상태!    
            elif self.__delonghi01Status == DelonghiState.ERR_POWERED_OFF:

                self.__tpmSysFuncManager.wakeupDeloghi(self.__delonghi01Comm)
            

            # 2번 드롱기 찌꺼기 통 가득!
            if self.__delonghi02Status == DelonghiState.ERR_FULL_GROUNDS:
                
                self.__delonghiContainer.write("OPEN_2")
                time.sleep(15)
                self.__delonghiContainer.write("CLOSE_2")

            # 2번 드롱기 찌꺼기 통 열림!
            elif self.__delonghi02Status == DelonghiState.ERR_OPENED_GROUNDS_CONTAINER:
                
                self.__delonghiContainer.write("CLOSE_2")

            # 2번 드롱기 휴면 상태! 
            elif self.__delonghi02Status == DelonghiState.ERR_POWERED_OFF:

                self.__tpmSysFuncManager.wakeupDeloghi(self.__delonghi02Comm)


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
        elif data == self.__delonghi02Comm:
            targetVar = "2번 드롱기"
        elif data == self.__indy7LComm:
            targetVar = "Indy7L"
        elif data == self.__indy7LGripperComm:
            targetVar = "Indy7L_그리퍼"
        elif data == self.__ur5Comm:
            targetVar = "UR5"
        elif data == self.__ur5GripperComm:
            targetVar = "UR5그리퍼"
        elif data == self.__indy7RComm:
            targetVar = "Indy7R"
        elif data == self.__indy7RGripperComm:
            targetVar = "Indy7R_그리퍼"
        
        if eventId == Event.COMM_VAR_DISCONNECTED:

            CDRLog(f"{targetVar} 통신 끊어짐")
            self.__terminateSystem()

        elif eventId == Event.COMM_VAR_FAILED_TO_CONNECT:
            
            CDRLog(f"{targetVar} 통신 연결 실패")
            self.__terminateSystem()


    def __deliveryHotCup(self):
        '''
        ### indy7L이 핫 컵을 중간 거치대로 전달 
        '''
        
        # Indy7L 그리퍼 닫기
        self.__tpmSysFuncManager.holdDHGripper(self.__indy7LGripperComm) 

        # Indy7L이 컵디스펜서의 핫 음료컵을 받을 수 있는 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 1, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)
        
        # 컵 디스펜서에서 핫 음료컵 배출
        self.__reqDispensingHotCup()
        time.sleep(3)

        # Indy7L이 중간 거치대에 컵을 내려놓는 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 21, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)

        # Indy7L 그리퍼 열기 -> 컵은 중간 거치대에 place 
        self.__tpmSysFuncManager.releaseDHGripper(self.__indy7LGripperComm) 
        time.sleep(2)

        # Indy7L이 중간 거치대에서 홈 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 23, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)




    def __deliveryIceCup(self):
        '''
        ### indy7L이 아이스 컵을 중간 거치대로 전달     
        '''      
        # Indy7L 그리퍼 닫기
        self.__tpmSysFuncManager.holdDHGripper(self.__indy7LGripperComm) 

        # Indy7L이 컵디스펜서의 아이스 음료컵을 받을 수 있는 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 2, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)
        
        # 컵 디스펜서에서 아이스 음료컵 배출
        self.__reqDispensingIceCup()
        time.sleep(3)

        # Indy7L이 거치대A에에 컵을 내려놓는 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 11, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)

        # Indy7L 그리퍼 열기
        self.__tpmSysFuncManager.releaseDHGripper(self.__indy7LGripperComm) 

        # Indy7L이 거치대A에 아이스 컵 잡는 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 13, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)

        # Indy7L 그리퍼 닫기
        self.__tpmSysFuncManager.holdDHGripper(self.__indy7LGripperComm) 

        # Indy7L이 제빙기에 얼음 받는 위치로 이동하고 레버 밀기기
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 14, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)

        # 제빙기에서 얼음 배출. 대기하면서 컵에 얼음 받기
        time.sleep(8)

        # Indy7L이 중간 거치대에 컵을 내려놓는 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 22, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)

        # Indy7L 그리퍼 열기 -> 컵은 중간 거치대에 place 
        self.__tpmSysFuncManager.releaseDHGripper(self.__indy7LGripperComm) 
        time.sleep(2)

        # Indy7L이 중간 거치대에서 홈 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 23, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)




    def __startBrewHotAmericanoOnDelonghi01(self):
        '''
        ### UR5가 빈 컵을 중간 거치대에서 1번 드롱기로 P&P -> 1번 드롱기에서 '핫 아메리카노' 음료 제조 시작 & UR5는 홈위치로 이동
        ''' 

        # UR5 그리퍼 열기
        self.__tpmSysFuncManager.releaseDHGripper(self.__ur5GripperComm) 

        # UR5가 홈위치에서 중간 거치대의 컵 잡는 위치로 이동
        self.__tpmSysFuncManager.sendURCmd(self.__ur5Comm, 1)

        # UR5 그리퍼 닫기
        self.__tpmSysFuncManager.holdDHGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # UR5가 1번 드롱기에 컵을 내려놓는 위치로 이동
        self.__tpmSysFuncManager.sendURCmd(self.__ur5Comm, 2)

        # UR5 그리퍼 열기 -> 컵은 1번 드롱기에 place
        self.__tpmSysFuncManager.releaseDHGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # 1번 드롱기에서 아메리카노 제조 명령 전달
        self.__tpmSysFuncManager.brewDelonghiAmericano(self.__delonghi01Comm)

        # 커피 제조 시작과 동시에 UR5는 홈 위치로 이동
        self.__tpmSysFuncManager.sendURCmd(self.__ur5Comm, 3)



    def __startBrewIceAmericanoOnDelonghi01(self):
        '''
        ### UR5가 빈 컵을 중간 거치대에서 1번 드롱기로 P&P -> 1번 드롱기에서 '아이스 아메리카노' 음료 제조 시작 & UR5는 홈위치로 이동  
        '''
        # UR5 그리퍼 열기
        self.__tpmSysFuncManager.releaseDHGripper(self.__ur5GripperComm) 

        # UR5가 홈위치에서 중간 거치대의 컵 잡는 위치로 이동
        self.__tpmSysFuncManager.sendURCmd(self.__ur5Comm, 1)

        # UR5 그리퍼 닫기
        self.__tpmSysFuncManager.holdDHGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # UR5가 1번 드롱기에 컵을 내려놓는 위치로 이동
        self.__tpmSysFuncManager.sendURCmd(self.__ur5Comm, 2)

        # UR5 그리퍼 열기 -> 컵은 1번 드롱기에 place
        self.__tpmSysFuncManager.releaseDHGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # 1번 드롱기에서 에스프레소 제조 명령 전달
        self.__tpmSysFuncManager.brewDelonghiEspresso(self.__delonghi01Comm)

        # 커피 제조 시작과 동시에 UR5는 홈 위치로 이동
        self.__tpmSysFuncManager.sendURCmd(self.__ur5Comm, 3)



    def __startBrewHotAmericanoOnDelonghi02(self):
        '''
        ### Indy7R이 빈 컵을 중간 거치대에서 2번 드롱기로 P&P -> 2번 드롱기에서 '핫 아메리카노' 음료 제조 시작 & Indy7R은 홈위치로 이동   
        '''
        # Indy7 그리퍼 열기
        self.__tpmSysFuncManager.releaseDHGripper(self.__indy7RGripperComm) 

        # Indy7이이 홈위치에서 중간 거치대의 컵 잡는 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 1, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)

        # Indy7 그리퍼 닫기
        self.__tpmSysFuncManager.holdDHGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # Indy7이 1번 드롱기에 컵을 내려놓는 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 2, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)

        # Indy7 그리퍼 열기 -> 컵은 1번 드롱기에 place
        self.__tpmSysFuncManager.releaseDHGripper(self.__indy7RGripperComm) 
        time.sleep(2)

        # 1번 드롱기에서 아메리카노 제조 명령 전달
        self.__tpmSysFuncManager.brewDelonghiAmericano(self.__delonghi01Comm)

        # 커피 제조 시작과 동시에 Indy7은 홈 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 3, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)



    def __startBrewIceAmericanoOnDelonghi02(self):
        '''
        ### Indy7R이 빈 컵을 중간 거치대에서 2번 드롱기로 P&P -> 2번 드롱기에서 '아이스 아메리카노' 음료 제조 시작 & Indy7R은 홈위치로 이동 
        '''
        # Indy7 그리퍼 열기
        self.__tpmSysFuncManager.releaseDHGripper(self.__indy7RGripperComm) 

        # Indy7이이 홈위치에서 중간 거치대의 컵 잡는 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 1, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)

        # Indy7 그리퍼 닫기
        self.__tpmSysFuncManager.holdDHGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # Indy7이 1번 드롱기에 컵을 내려놓는 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 2, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)

        # Indy7 그리퍼 열기 -> 컵은 1번 드롱기에 place
        self.__tpmSysFuncManager.releaseDHGripper(self.__indy7RGripperComm) 
        time.sleep(2)

        # 1번 드롱기에서 에스프레소 제조 명령 전달
        self.__tpmSysFuncManager.brewDelonghiEspresso(self.__delonghi01Comm)

        # 커피 제조 시작과 동시에 Indy7은 홈 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 3, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)



    def __bringCupDelonghi01ToTrayB(self):
        '''
        ### UR5가 음료컵을 1번 드롱기에서 픽업대B로 P&P -> UR5는 홈위치로 이동
        '''
        # UR5 그리퍼 열기
        self.__tpmSysFuncManager.releaseDHGripper(self.__ur5GripperComm) 

        # UR5가 홈위치에서 중간 거치대의 컵 잡는 위치로 이동
        self.__tpmSysFuncManager.sendURCmd(self.__ur5Comm, 4)

        # UR5 그리퍼 닫기
        self.__tpmSysFuncManager.holdDHGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # UR5가 픽업대B에 컵을 내려놓는 위치로 이동
        self.__tpmSysFuncManager.sendURCmd(self.__ur5Comm, 5)

        # UR5 그리퍼 열기 -> 컵은 픽업대B에 place
        self.__tpmSysFuncManager.releaseDHGripper(self.__ur5GripperComm) 
        time.sleep(2)

        # UR5는 홈 위치로 이동
        self.__tpmSysFuncManager.sendURCmd(self.__ur5Comm, 6)



    def __bringCupDelonghi02ToTrayC(self):
        '''
        ### Indy7R이 음료컵을 2번 드롱기에서 픽업대C로 P&P -> Indy7R은 홈위치로 이동
        '''
        # Indy7R 그리퍼 열기
        self.__tpmSysFuncManager.releaseDHGripper(self.__indy7RGripperComm) 

        # Indy7R이 홈위치에서 중간 거치대의 컵 잡는 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 4, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)

        # Indy7R 그리퍼 닫기
        self.__tpmSysFuncManager.holdDHGripper(self.__indy7RGripperComm) 
        time.sleep(2)

        # Indy7R이 픽업대C에 컵을 내려놓는 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 5, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)

        # Indy7R 그리퍼 열기 -> 컵은 픽업대C에 place
        self.__tpmSysFuncManager.releaseDHGripper(self.__indy7RGripperComm) 
        time.sleep(2)

        # Indy7R은 홈 위치로 이동
        self.__tpmSysFuncManager.sendIndyModbusCmd(self.__indy7LComm, self.__indyCmdAddr, 6, self.__indyFeedbackAddr, self.__indyStartFeedback, self.__indtFinFeedback)



    def __moveNextIndex(self):
        '''
        타겟 메뉴 인덱스를 다음 순번으로 변경
        '''

        self.__curMenuIndex += 1

        if self.__curMenuIndex >= self.__trayNum:
            self.__curMenuIndex = 0

    ##################################################################################################################################################################    
    def UI_reset_thread(self, slot : str, ordernum : int) :
        th_ = threading.Thread(target=self.UI_reset,args=(slot,ordernum,))
        th_.start()

    def UI_reset(self, slot : str, ordernum : int) :
        msg = '$'+slot+str(ordernum)+'%'#'$b'+str(order_num)+'%'

        self.__order_UI.write(msg)
        
        std_time = datetime.datetime.now()
        while True :
            cur_time = datetime.datetime.now()
            time_itv = cur_time - std_time
            if time_itv.total_seconds() > 30 :
                if slot == 'a' :
                    if self.__hasCupOnPickupATray == 0 :
                        msg = '$'+slot+'0%'
                        self.__order_UI.write(msg)
                        break
                    else :
                        print('There is item remaining in Slot A')
                    
                elif slot == 'b' :
                    if self.__hasCupOnPickupBTray == 0 :
                        msg = '$'+slot+'0%'
                        self.__order_UI.write(msg)
                        break
                    else :
                        print('There is item remaining in Slot B')
                    
                elif slot == 'c' :
                    if self.__hasCupOnPickupCTray == 0 :
                        msg = '$'+slot+'0%'
                        self.__order_UI.write(msg)
                        break
                    else :
                        print('There is item remaining in Slot C')
                
                time.sleep(1)


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