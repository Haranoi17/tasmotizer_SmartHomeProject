#!/usr/bin/env python
import re
import sys
from time import sleep

import serial

import tasmotizer_esptool as esptool
import json

from datetime import datetime

from PyQt5.QtCore import QUrl, Qt, QThread, QObject, pyqtSignal, pyqtSlot, QSettings, QTimer, QSize, QIODevice
from PyQt5.QtGui import QPixmap, QCloseEvent
from PyQt5.QtNetwork import QNetworkRequest, QNetworkAccessManager, QNetworkReply
from PyQt5.QtSerialPort import QSerialPortInfo, QSerialPort
from PyQt5.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton, QComboBox, QWidget, QCheckBox, QRadioButton, \
    QButtonGroup, QFileDialog, QProgressBar, QLabel, QMessageBox, QPlainTextEdit, QTextEdit, QDialogButtonBox, QGroupBox, QFormLayout, QStatusBar

import banner

from gui import HLayout, VLayout, GroupBoxH, GroupBoxV, SpinBox, dark_palette
from utils import MODULES, NoBinFile, NetworkError

from copy import deepcopy

__version__ = '1.2.1'

BINS_URL = 'http://ota.tasmota.com'

class ESPWorker(QObject):
    error = pyqtSignal(Exception)
    waiting = pyqtSignal()
    done = pyqtSignal()

    def __init__(self, port, actions, **params):
        super().__init__()
        self.command = [
                      '--chip', 'esp8266',
                      '--port', port,
                      '--baud', '115200'
            ]

        self._actions = actions
        self._params = params
        self._continue = False

    @pyqtSlot()
    def run(self):
        esptool.sw.setContinueFlag(True)

        try:
            if 'backup' in self._actions:
                command_backup = ['read_flash', '0x00000', self._params['backup_size'],
                                  'backup_{}.bin'.format(datetime.now().strftime('%Y%m%d_%H%M%S'))]
                esptool.main(self.command + command_backup)

                auto_reset = self._params['auto_reset']
                if not auto_reset:
                    self.wait_for_user()

            if esptool.sw.continueFlag() and 'write' in self._actions:
                file_path = self._params['file_path']
                command_write = ['write_flash', '--flash_mode', 'dout', '0x00000', file_path]

                if 'erase' in self._actions:
                    command_write.append('--erase-all')
                esptool.main(self.command + command_write)

        except (esptool.FatalError, serial.SerialException) as e:
            self.error.emit(e)
        self.done.emit()

    def wait_for_user(self):
        self._continue = False
        self.waiting.emit()
        while not self._continue:
            sleep(.1)

    def continue_ok(self):
        self._continue = True

    def abort(self):
        esptool.sw.setContinueFlag(False)


class SendConfigDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(640)
        self.setWindowTitle('Send configuration to device')
        self.settings = QSettings('tasmotizer.cfg', QSettings.IniFormat)

        self.commands = None
        self.module_mode = 0

        self.createUI()
        self.loadSettings()

    def createUI(self):
        vl = VLayout()
        self.setLayout(vl)

        # Wifi groupbox
        self.gbWifi = QGroupBox('WiFi')
        self.gbWifi.setCheckable(True)
        self.gbWifi.setChecked(False)
        flWifi = QFormLayout()
        self.leAP = QLineEdit()
        self.leAPPwd = QLineEdit()
        self.leAPPwd.setEchoMode(QLineEdit.Password)
        flWifi.addRow('SSID', self.leAP)
        flWifi.addRow('Password', self.leAPPwd)
        self.gbWifi.setLayout(flWifi)

        # Recovery Wifi groupbox
        self.gbRecWifi = QGroupBox('Recovery WiFi')
        self.gbRecWifi.setCheckable(True)
        self.gbRecWifi.setChecked(False)
        flRecWifi = QFormLayout()
        lbRecAP = QLabel('Recovery')
        lbRecAP.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        lbRecAPPwd = QLabel('a1b2c3d4')
        lbRecAPPwd.setAlignment(Qt.AlignVCenter | Qt.AlignRight)

        flRecWifi.addRow('SSID', lbRecAP)
        flRecWifi.addRow('Password', lbRecAPPwd)
        self.gbRecWifi.setLayout(flRecWifi)

        vl_wifis = VLayout(0)
        vl_wifis.addWidgets([self.gbWifi, self.gbRecWifi])

        # MQTT groupbox
        self.gbMQTT = QGroupBox('MQTT')
        self.gbMQTT.setCheckable(True)
        self.gbMQTT.setChecked(False)
        flMQTT = QFormLayout()
        self.leBroker = QLineEdit()
        self.sbPort = SpinBox()
        self.sbPort.setValue(1883)
        self.leTopic = QLineEdit()
        self.leTopic.setText('tasmota')
        self.leFullTopic = QLineEdit()
        self.leFullTopic.setText('%prefix%/%topic%/')
        self.leFriendlyName = QLineEdit()
        self.leMQTTUser = QLineEdit()
        self.leMQTTPass = QLineEdit()
        self.leMQTTPass.setEchoMode(QLineEdit.Password)

        flMQTT.addRow('Host', self.leBroker)
        flMQTT.addRow('Port', self.sbPort)
        flMQTT.addRow('Topic', self.leTopic)
        flMQTT.addRow('FullTopic', self.leFullTopic)
        flMQTT.addRow('FriendlyName', self.leFriendlyName)
        flMQTT.addRow('User [optional]', self.leMQTTUser)
        flMQTT.addRow('Password [optional]', self.leMQTTPass)
        self.gbMQTT.setLayout(flMQTT)

        # Module/template groupbox
        self.gbModule = GroupBoxV('Module/template')
        self.gbModule.setCheckable(True)
        self.gbModule.setChecked(False)

        hl_m_rb = HLayout()
        self.rbModule = QRadioButton('Module')
        self.rbModule.setChecked(True)
        self.rbTemplate = QRadioButton('Template')
        hl_m_rb.addWidgets([self.rbModule, self.rbTemplate])

        self.rbgModule = QButtonGroup(self.gbModule)
        self.rbgModule.addButton(self.rbModule, 0)
        self.rbgModule.addButton(self.rbTemplate, 1)

        self.cbModule = QComboBox()
        for mod_id, mod_name in MODULES.items():
            self.cbModule.addItem(mod_name, mod_id)

        self.leTemplate = QLineEdit()
        self.leTemplate.setPlaceholderText('Paste template string here')
        self.leTemplate.setVisible(False)

        self.gbModule.addLayout(hl_m_rb)
        self.gbModule.addWidgets([self.cbModule, self.leTemplate])
        self.rbgModule.buttonClicked[int].connect(self.setModuleMode)

        # layout all widgets
        hl_wifis_mqtt = HLayout(0)
        hl_wifis_mqtt.addLayout(vl_wifis)
        hl_wifis_mqtt.addWidget(self.gbMQTT)

        vl.addLayout(hl_wifis_mqtt)
        vl.addWidget(self.gbModule)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        vl.addWidget(btns)

    def loadSettings(self):
        self.gbWifi.setChecked(self.settings.value('gbWifi', False, bool))
        self.leAP.setText(self.settings.value('AP'))

        self.gbRecWifi.setChecked(self.settings.value('gbRecWifi', False, bool))

        self.gbMQTT.setChecked(self.settings.value('gbMQTT', False, bool))
        self.leBroker.setText(self.settings.value('Broker'))
        self.sbPort.setValue(self.settings.value('Port', 1883, int))
        self.leTopic.setText(self.settings.value('Topic', 'tasmota'))
        self.leFullTopic.setText(self.settings.value('FullTopic', '%prefix%/%topic%/'))
        self.leFriendlyName.setText(self.settings.value('FriendlyName'))
        self.leMQTTUser.setText(self.settings.value('MQTTUser'))

        self.gbModule.setChecked(self.settings.value('gbModule', False, bool))

        module_mode = self.settings.value('ModuleMode', 0, int)
        for b in self.rbgModule.buttons():
            if self.rbgModule.id(b) == module_mode:
                b.setChecked(True)
                self.setModuleMode(module_mode)
        self.cbModule.setCurrentText(self.settings.value('Module', 'Generic'))
        self.leTemplate.setText(self.settings.value('Template'))

    def setModuleMode(self, radio):
        self.module_mode = radio
        self.cbModule.setVisible(not radio)
        self.leTemplate.setVisible(radio)

    def accept(self):
        ok = True

        if self.gbWifi.isChecked() and (len(self.leAP.text()) == 0 or len(self.leAPPwd.text()) == 0):
            ok = False
            QMessageBox.warning(self, 'WiFi details incomplete', 'Input WiFi AP and Password')

        if self.gbMQTT.isChecked() and not self.leBroker.text():
            ok = False
            QMessageBox.warning(self, 'MQTT details incomplete', 'Input broker hostname')

        if self.module_mode == 1 and len(self.leTemplate.text()) == 0:
            ok = False
            QMessageBox.warning(self, 'Template string missing', 'Input template string')

        if ok:
            backlog = []

            if self.gbWifi.isChecked():
                backlog.extend(['ssid1 {}'.format(self.leAP.text()), 'password1 {}'.format(self.leAPPwd.text())])

            if self.gbRecWifi.isChecked():
                backlog.extend(['ssid2 Recovery', 'password2 a1b2c3d4'])

            if self.gbMQTT.isChecked():
                backlog.extend(['mqtthost {}'.format(self.leBroker.text()), 'mqttport {}'.format(self.sbPort.value())])

                topic = self.leTopic.text()
                if topic and topic != 'tasmota':
                    backlog.append('topic {}'.format(topic))

                fulltopic = self.leFullTopic.text()
                if fulltopic and fulltopic != '%prefix%/%topic%/':
                    backlog.append('fulltopic {}'.format(fulltopic))

                fname = self.leFriendlyName.text()
                if fname:
                    backlog.append('friendlyname {}'.format(fname))

                mqttuser = self.leMQTTUser.text()
                if mqttuser:
                    backlog.append('mqttuser {}'.format(mqttuser))

                    mqttpassword = self.leMQTTPass.text()
                    if mqttpassword:
                        backlog.append('mqttpassword {}'.format(mqttpassword))

            if self.gbModule.isChecked():
                if self.module_mode == 0:
                    backlog.append('module {}'.format(self.cbModule.currentData()))

                elif self.module_mode == 1:
                    backlog.extend(['template {}'.format(self.leTemplate.text()), 'module 0'])

            self.commands = 'backlog {}\n'.format(';'.join(backlog))

            self.done(QDialog.Accepted)


class PinConfigDialog(QDialog):

    components_done = pyqtSignal()

    def __init__(self, port):
        super().__init__()
        self.setMinimumWidth(400)
        self.setWindowTitle('Device pin configuration')
        self.settings = QSettings('tasmotizer.cfg', QSettings.IniFormat)

        self.read_data = []
        self.commands = None
        self.module_mode = 0

        # UART setup
        self.port = QSerialPort(port)
        self.port.setBaudRate(115200)
        try:
            self.port.open(QIODevice.ReadWrite)
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Port access error:\n{e}')
        
        self.createUI()

    def read_init_config(self):
        self.components = None
        self.gpios = None
        self.jsons = []

        pins_ret_match = re.compile(r"\d+:\d+:\d+.\d+ RSL: RESULT = \{.*\}")
        self.brackets = {'{' : 0, '}' : -1}

        try:
            self.port.readyRead.connect(self.read_json)
            self.components_done.connect(self.read_gpios)
            self.port.write(bytes('GPIOs\n', 'utf8'))
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Port access error:\n{e}')

    def read_gpios(self):
        if self.components is not None:
            self.components_done.disconnect(self.read_gpios)
        try:
            self.port.write(bytes('GPIO\n', 'utf8'))
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Port access error:\n{e}')


    def read_json(self):
        ret_match = re.compile(r"\d+:\d+:\d+.\d+ RSL: RESULT = \{.*\}")
        json_match = re.compile(r"\{.*\}")
        bracket_open = re.compile(rb'\{')
        bracket_close = re.compile(rb'\}')
        data = self.port.readAll()
        self.brackets['{'] += len(bracket_open.findall(data))
        if self.brackets['{'] > 0:
            self.brackets['}'] = 0
        self.brackets['}'] += len(bracket_close.findall(data))
        self.data += data
        if self.brackets['{'] - self.brackets['}'] == 0:
            to_parse = str(self.data, 'utf-8').replace('\r', '').replace('\n', '')
            print(ret_match.findall(to_parse))
            ret = json_match.findall(to_parse)
            if len(ret) > 0:
                self.jsons.append(json.loads(ret[0]))
            self.components_done.emit()
            # print(self.jsons)
            # self.jsons = [json.loads(str(d)) for d in json_match.findall(self.data)]
            self.data = b''

    def getComponents(self):
        # QUerry the device over UART to get available components (digital and analog)

#         ret = """00:00:38.306 RSL: RESULT = {"0":"None","5728":"Option
# A","32":"Button","64":"Button_n"
# ,"96":"Button_i","128":"Button_i
# n","160":"Switch","192":"Switch_
# n","3264":"Rotary A","3296":"Rot
# ary B","6272":"Rotary A_n","6304
# ":"Rotary B_n","224":"Relay","25
# 6":"Relay_i","8672":"Relay_b","8
# 704":"Relay_bi","288":"Led","320
# ":"Led_i","352":"Counter","384":
# "Counter_n","416":"PWM","448":"P
# WM_i","480":"Buzzer","512":"Buzz
# er_i","544":"LedLink","576":"Led
# Link_i","3840":"Output Hi","3872
# ":"Output Lo","7584":"Heartbeat"
# ,"7616":"Heartbeat_i","8096":"Re
# set","608":"I2C SCL","640":"I2C 
# SDA","832":"SSPI MISO","864":"SS
# PI MOSI","896":"SSPI SCLK","928"
# :"SSPI CS","960":"SSPI DC","3200
# ":"Serial Tx","3232":"Serial Rx"
# ,"1184":"DHT11","1216":"AM2301",
# "1248":"SI7021","8128":"MS01","1
# 280":"DHT11_o","1312":"DS18x20",
# "1344":"DS18x20_o","1376":"WS281
# 2","3136":"ALux IrRcv","3168":"A
# Lux IrSel","3008":"MY92x1 DI","3
# 040":"MY92x1 DCKI","2912":"SM167
# 16 CLK","2944":"SM16716 DAT","29
# 76":"SM16716 PWR","4032":"SM2135
#  Clk","4064":"SM2135 Dat","8448"
# :"SM2335 Clk","8480":"SM2335 Dat
# ","8384":"BP5758D Clk","8416":"B
# P5758D Dat","2272":"Tuya Tx","23
# 04":"Tuya Rx","4128":"EXS Enable
# ","4640":"MOODL Tx","5568":"SHD 
# Boot 0","5600":"SHD Reset","1056
# ":"IRsend","1088":"IRrecv","2592
# ":"HLWBL SEL","2624":"HLWBL SEL_
# i","2656":"HLWBL CF1","2688":"HL
# W8012 CF","2720":"BL0937 CF","34
# 56":"ADE7953 IRQ","8832":"ADE795
# 3 RST","3072":"CSE7766 Tx","3104
# ":"CSE7766 Rx","2752":"MCP39F5 T
# x","2784":"MCP39F5 Rx","2816":"M
# CP39F5 Rst","1472":"PZEM0XX Tx",
# "1504":"PZEM004 Rx","1536":"PZEM
# 016 Rx","1568":"PZEM017 Rx","748
# 8":"BL0939 Rx","5056":"BL0940 Rx
# ","7520":"BL0942 Rx","7072":"ZC 
# Pulse","1792":"SerBr Tx","1824":
# "SerBr Rx","4096":"DeepSleep"}"""

        self.uart_send(b"GPIOs\n")
#         ret = ret.replace('\n', '')
#         ret = re.findall(json_match, ret)
#         if len(ret)<1:
#             return None
#         return json.loads(ret[0])

    def getGPIOS(self):
        # Querry the device over UART to get actual pin config
        # result_prefix = "[0-9:.]+\s*RSL:\s*RESULT\s*=\s*"
        # # self.uart_send("GPIO")
        # ret = ""
        # re.sub(result_prefix, '', ret)
        return json.loads("""{

    "GPI O0":{
        "0":"None"
    },
    "GPIO1":{
        "0":"None"
    },
    "GPIO2":{
        "416":"PWM1"
    },
    "GP IO3":{
        "0":"None"
    },
    "GPIO4":{
        "0":"None"
    },
    "GPIO5":{
        "0":"None"
    },
    "GPI O9":{
        "0":"None"
    },
    "GPIO10":{
        "0":" None"
    },
    "GPIO12":{
        "0":"None"
    },
    "GP IO13":{
        "0":"None"
    },
    "GPIO14":{
        "0":"None"
    },
    "GPIO15":{
        "0":"None"
    },
    " GPIO16":{
        "0":"None"
    },
    "GPIO17":{
        " 0":"None"
    }

}""")

    def createUI(self):
        vl = VLayout()
        self.setLayout(vl)

        self.pinBtnGroup = QButtonGroup()
        # self.cbModule = QComboBox()

        # vGPIOLayout = VLayout()
        # gpio_pins = self.getGPIOS()
        # components = self.getComponents()
        # self.prev_setup = gpio_pins
        # self.comboBoxesForGPIOS = {}

        # for pin_id, pin_component in gpio_pins.items():
        #     newComboBox = QComboBox()
        #     for value, name in components.items():
        #         newComboBox.addItem(f"{name} ({value})", value)
        #         if list(pin_component.keys())[0].strip() == value.strip():
        #             newComboBox.setCurrentText(f"{name} ({value.strip()})")

        #     self.comboBoxesForGPIOS[pin_id] = newComboBox
            
        #     labelComboLayout = HLayout()
        #     labelComboLayout.addWidgets([QLabel(pin_id), newComboBox])
        #     vGPIOLayout.addLayout(labelComboLayout)

        # # layout all widgets
        # hl_wifis_mqtt = HLayout(0)

        # vl.addLayout(hl_wifis_mqtt)
        # vl.addLayout(vGPIOLayout)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        vl.addWidget(btns)


    def setModuleMode(self, radio):
        self.module_mode = radio
        self.cbModule.setVisible(not radio)

    def uart_send(self, msg:bytes):
        try:
            self.port.setBaudRate(115200)
            self.port.open(QIODevice.OpenModeFlag.ReadWrite)

            self.port.write(msg)
            self.port.waitForBytesWritten()
           
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Port access error:\n{e}')
        finally:
            self.port.close()

    def accept(self):
        pass
        # for pin, option in self.comboBoxesForGPIOS.items():
        #     if list(self.prev_setup[pin].keys())[0] == option.currentData():
        #         continue
        #     cmd = f"{pin.replace(' ', '')} {option.currentData()}\n"
        #     self.uart_send(bytes(cmd, 'ascii'))

        self.done(QDialog.Accepted)

    def closeEvent(self, a0: QCloseEvent) -> None:
        self.port.close()
        return super().closeEvent(a0)

        
class CommandDialog(QWidget):
    def __init__(self, port):
        super().__init__()
        self.setMinimumWidth(640)
        self.setWindowTitle('Serial Terminal')

        self.commands = None
        self.module_mode = 0

        self.createUI()

        self.port = QSerialPort(port)
        self.port.setBaudRate(115200)
        self.port.open(QIODevice.OpenModeFlag.ReadWrite)
        self.port.readyRead.connect(self.readFromPort)

    def sendCommand(self):
        if self.commandLine.toPlainText():
            try:
                formatedTxt = self.commandLine.toPlainText().split()
                formatedTxt = " ".join(formatedTxt) + "\n"
                bytes_sent = self.port.write(bytes(formatedTxt, 'utf-8'))
            except Exception as e:
                QMessageBox.critical(self, 'Error', f'Port access error:\n{e}')
            # else:
                # QMessageBox.information(self, 'Done', 'Command sent ({} bytes).'.format(bytes_sent))
            # finally:
                # if self.port.isOpen():
                    # ret = str(self.port.readAll(), 'utf-8')
                    # self.cmdDlg.consoleResponseField.setText(ret)
        else:
            QMessageBox.information(self, 'Done', 'Nothing to send')

    def readFromPort(self):
        data = self.port.readAll()
        if len(data) > 0:
            self.consoleResponseField.append( data.data().decode('utf-8') )

    def createUI(self):
        vl = VLayout()
        self.setLayout(vl)

        # Console Output
        self.consoleResponseField = QTextEdit()
        self.consoleResponseField.setPlaceholderText("Command output will be shown here")
        self.consoleResponseField.setReadOnly(True)
        consoleResponseFieldLayout = HLayout()
        consoleResponseFieldLayout.addWidget(self.consoleResponseField)

        self.commandLine = QTextEdit()
        self.commandLine.setPlaceholderText("You can write a multi-line command here")
        commandLineLayout = HLayout()
        commandLineLayout.addWidget(self.commandLine)
        
        self.pbSendCommand = QPushButton("Send Command")
        self.pbSendCommand.setStyleSheet('background-color: #aaaa00;')
        self.pbSendCommand.setFixedSize(QSize(200,50))
        sendCommnadButtonLayout = HLayout()
        sendCommnadButtonLayout.addWidget(self.pbSendCommand)
        
        # layout all widgets
        vl.addLayout(consoleResponseFieldLayout)
        vl.addLayout(commandLineLayout)
        vl.addLayout(sendCommnadButtonLayout)

        # Bind buttons
        self.pbSendCommand.clicked.connect(self.sendCommand)
    
    def setModuleMode(self, radio):
        self.module_mode = radio
        self.cbModule.setVisible(not radio)
        self.leTemplate.setVisible(radio)

    def accept(self):
        backlog = []

        cmd = self.commandLine.toPlainText()
        if cmd:
             backlog.extend(['cmd', cmd])

        self.commands = 'backlog {}\n'.format(';'.join(backlog))
        self.done(QDialog.Accepted)

    def closeEvent(self, a0: QCloseEvent) -> None:
        self.port.close()
        return super().closeEvent(a0)


class ProcessDialog(QDialog):
    def __init__(self, port, **kwargs):
        super().__init__()

        self.setWindowTitle('Tasmotizing...')
        self.setFixedWidth(400)

        self.exception = None

        esptool.sw.progress.connect(self.update_progress)

        self.nam = QNetworkAccessManager()
        self.nrBinFile = QNetworkRequest()
        self.bin_data = b''

        self.setLayout(VLayout(5, 5))
        self.actions_layout = QFormLayout()
        self.actions_layout.setSpacing(5)

        self.layout().addLayout(self.actions_layout)

        self._actions = []
        self._action_widgets = {}

        self.port = port

        self.auto_reset = kwargs.get('auto_reset', False)

        self.file_path = kwargs.get('file_path')
        if self.file_path and self.file_path.startswith('http'):
            self._actions.append('download')

        self.backup = kwargs.get('backup')
        if self.backup:
            self._actions.append('backup')
            self.backup_size = kwargs.get('backup_size')

        self.erase = kwargs.get('erase')
        if self.erase:
            self._actions.append('erase')

        if self.file_path:
            self._actions.append('write')

        self.create_ui()
        self.start_process()

    def create_ui(self):
        for action in self._actions:
            pb = QProgressBar()
            pb.setFixedHeight(35)
            self._action_widgets[action] = pb
            self.actions_layout.addRow(action.capitalize(), pb)

        self.btns = QDialogButtonBox(QDialogButtonBox.Abort)
        self.btns.rejected.connect(self.abort)
        self.layout().addWidget(self.btns)

        self.sb = QStatusBar()
        self.layout().addWidget(self.sb)

    def appendBinFile(self):
        self.bin_data += self.bin_reply.readAll()

    def saveBinFile(self):
        if self.bin_reply.error() == QNetworkReply.NoError:
            self.file_path = self.file_path.split('/')[-1]
            with open(self.file_path, 'wb') as f:
                f.write(self.bin_data)
            self.run_esp()
        else:
            raise NetworkError

    def updateBinProgress(self, recv, total):
        self._action_widgets['download'].setValue(recv//total*100)

    def download_bin(self):
        self.nrBinFile.setUrl(QUrl(self.file_path))
        self.bin_reply = self.nam.get(self.nrBinFile)
        self.bin_reply.readyRead.connect(self.appendBinFile)
        self.bin_reply.downloadProgress.connect(self.updateBinProgress)
        self.bin_reply.finished.connect(self.saveBinFile)

    def show_connection_state(self, state):
        self.sb.showMessage(state, 0)

    def run_esp(self):
        params = {
            'file_path': self.file_path,
            'auto_reset': self.auto_reset,
            'erase': self.erase
        }

        if self.backup:
            backup_size = f'0x{2 ** self.backup_size}00000'
            params['backup_size'] = backup_size

        self.esp_thread = QThread()
        self.esp = ESPWorker(
            self.port,
            self._actions,
            **params
        )
        esptool.sw.connection_state.connect(self.show_connection_state)
        self.esp.waiting.connect(self.wait_for_user)
        self.esp.done.connect(self.accept)
        self.esp.error.connect(self.error)
        self.esp.moveToThread(self.esp_thread)
        self.esp_thread.started.connect(self.esp.run)
        self.esp_thread.start()

    def start_process(self):
        if 'download' in self._actions:
            self.download_bin()
            self._actions = self._actions[1:]
        else:
            self.run_esp()

    def update_progress(self, action, value):
        self._action_widgets[action].setValue(value)

    @pyqtSlot()
    def wait_for_user(self):
        dlg = QMessageBox.information(self,
                                      'User action required',
                                      'Please power cycle the device, wait a moment and press OK',
                                      QMessageBox.Ok | QMessageBox.Cancel)
        if dlg == QMessageBox.Ok:
            self.esp.continue_ok()
        elif dlg == QMessageBox.Cancel:
            self.esp.abort()
            self.esp.continue_ok()
            self.abort()

    def stop_thread(self):
        self.esp_thread.wait(2000)
        self.esp_thread.exit()

    def accept(self):
        self.stop_thread()
        self.done(QDialog.Accepted)

    def abort(self):
        self.sb.showMessage('Aborting...', 0)
        QApplication.processEvents()
        self.esp.abort()
        self.stop_thread()
        self.reject()

    def error(self, e):
        self.exception = e
        self.abort()

    def closeEvent(self, e):
        self.stop_thread()


class DeviceIP(QDialog):
    def __init__(self, port: QSerialPort):
        super(DeviceIP, self).__init__()

        self.setWindowTitle('Device IP address')
        self.setLayout(VLayout(10))

        self.ip = QLineEdit()
        self.ip.setAlignment(Qt.AlignCenter)
        self.ip.setReadOnly(True)
        self.ip.setText('xx.xx.xx.xx')
        font = self.ip.font()
        font.setPointSize(24)
        self.ip.setFont(font)

        btn = QDialogButtonBox(QDialogButtonBox.Close)
        btn.rejected.connect(self.reject)

        self.layout().addWidgets([self.ip, btn])

        self.data = b''

        self.port = port

        self.re_ip = re.compile(r'(?:\()((?:[0-9]{1,3}\.){3}[0-9]{1,3})(?:\))')

        try:
            self.port.open(QIODevice.ReadWrite)
            self.port.readyRead.connect(self.read)
            self.port.write(bytes('IPAddress1\n', 'utf8'))
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Port access error:\n{e}')

    def read(self):
        try:
            self.data += self.port.readAll()
            match = self.re_ip.search(bytes(self.data).decode('utf8'))
            if match:
                self.ip.setText(match[1])
        except:
            pass


class Tasmotizer(QDialog):

    def __init__(self):
        super().__init__()
        self.settings = QSettings('tasmotizer.cfg', QSettings.IniFormat)

        self.port = ''

        self.nam = QNetworkAccessManager()
        self.nrRelease = QNetworkRequest(QUrl(f'{BINS_URL}/tasmota/release/release.php'))
        self.nrDevelopment = QNetworkRequest(QUrl(f'{BINS_URL}/tasmota/development.php'))

        self.esp_thread = None

        self.setWindowTitle(f'Tasmotizer {__version__}')
        self.setMinimumWidth(480)

        self.mode = 0  # BIN file
        self.file_path = ''

        self.release_data = b''
        self.development_data = b''

        self.create_ui()

        self.refreshPorts()
        self.getFeeds()

    def create_ui(self):
        vl = VLayout(5)
        self.setLayout(vl)

        # Banner
        banner = QLabel()
        banner.setPixmap(QPixmap(':/banner.png'))
        vl.addWidget(banner)

        # Port groupbox
        gbPort = GroupBoxH('Select port', 3)
        self.cbxPort = QComboBox()
        pbRefreshPorts = QPushButton('Refresh')
        gbPort.addWidget(self.cbxPort)
        gbPort.addWidget(pbRefreshPorts)
        gbPort.layout().setStretch(0, 4)
        gbPort.layout().setStretch(1, 1)

        # Firmware groupbox
        gbFW = GroupBoxV('Select image', 3)

        hl_rb = HLayout(0)
        rbFile = QRadioButton('BIN file')
        self.rbRelease = QRadioButton('Release')
        self.rbRelease.setEnabled(False)
        self.rbDev = QRadioButton('Development')
        self.rbDev.setEnabled(False)

        self.rbgFW = QButtonGroup(gbFW)
        self.rbgFW.addButton(rbFile, 0)
        self.rbgFW.addButton(self.rbRelease, 1)
        self.rbgFW.addButton(self.rbDev, 2)

        hl_rb.addWidgets([rbFile, self.rbRelease, self.rbDev])
        gbFW.addLayout(hl_rb)

        self.wFile = QWidget()
        hl_file = HLayout(0)
        self.file = QLineEdit()
        self.file.setReadOnly(True)
        self.file.setPlaceholderText('Click "Open" to select the image')
        pbFile = QPushButton('Open')
        hl_file.addWidgets([self.file, pbFile])
        self.wFile.setLayout(hl_file)

        self.cbHackboxBin = QComboBox()
        self.cbHackboxBin.setVisible(False)
        self.cbHackboxBin.setEnabled(False)

        self.cbSelfReset = QCheckBox('Self-resetting device (NodeMCU, Wemos)')
        self.cbSelfReset.setToolTip('Check if your device has self-resetting capabilities supported by esptool')

        gbBackup = GroupBoxV('Backup')
        self.cbBackup = QCheckBox('Save original firmware')
        self.cbBackup.setToolTip('Firmware backup is ESPECIALLY recommended when you flash a Sonoff, Tuya, Shelly etc. for the first time.\nWithout a backup you will not be able to restore the original functionality.')

        self.cbxBackupSize = QComboBox()
        self.cbxBackupSize.addItems([f'{2 ** s}MB' for s in range(5)])
        self.cbxBackupSize.setEnabled(False)

        hl_backup_size = HLayout(0)
        hl_backup_size.addWidgets([QLabel('Flash size:'), self.cbxBackupSize])
        hl_backup_size.setStretch(0, 3)
        hl_backup_size.setStretch(1, 1)

        gbBackup.addWidget(self.cbBackup)
        gbBackup.addLayout(hl_backup_size)

        self.cbErase = QCheckBox('Erase before flashing')
        self.cbErase.setToolTip('Erasing previous firmware ensures all flash regions are clean for Tasmota, which prevents many unexpected issues.\nIf unsure, leave enabled.')
        self.cbErase.setChecked(True)

        gbFW.addWidgets([self.wFile, self.cbHackboxBin, self.cbSelfReset, self.cbErase])

        # Buttons
        self.pbTasmotize = QPushButton('Tasmotize!')
        self.pbTasmotize.setFixedHeight(50)
        self.pbTasmotize.setStyleSheet('background-color: #223579;')

        self.pbConfig = QPushButton('Send config')
        self.pbConfig.setStyleSheet('background-color: #571054;')
        self.pbConfig.setFixedHeight(50)

        self.pbGetIP = QPushButton('Get IP')
        self.pbGetIP.setFixedSize(QSize(75, 50))
        self.pbGetIP.setStyleSheet('background-color: #2a8a26;')

        self.pbQuit = QPushButton('Quit')
        self.pbQuit.setStyleSheet('background-color: #c91017;')
        self.pbQuit.setFixedSize(QSize(200, 50))


        hl_btns = HLayout([50, 3, 50, 3])
        hl_btns.addWidgets([self.pbTasmotize, self.pbConfig, self.pbGetIP])
        
        vl.addWidgets([gbPort, gbBackup, gbFW])
        vl.addLayout(hl_btns)
        
        self.pbSendCommand = QPushButton("Serial terminal")
        self.pbSendCommand.setStyleSheet('background-color: #aaaa00;')
        self.pbSendCommand.setFixedSize(QSize(200,50))

        self.pbPinConfig = QPushButton("Pin config")
        self.pbPinConfig.setStyleSheet('background-color: #2a8a26;')
        self.pbPinConfig.setFixedSize(QSize(200,50))

        sendCommnadButtonLayout = HLayout([50, 3, 50, 3])
        sendCommnadButtonLayout.addWidgets([self.pbSendCommand, self.pbPinConfig])
        
        vl.addLayout(sendCommnadButtonLayout)

        quitLayout = HLayout()
        quitLayout.addWidgets([self.pbQuit])
        vl.addLayout(quitLayout)

        self.pbSendCommand.clicked.connect(self.sendCommandDialog)
        self.pbPinConfig.clicked.connect(self.openPinConfig)

        pbRefreshPorts.clicked.connect(self.refreshPorts)
        self.rbgFW.buttonClicked[int].connect(self.setBinMode)
        rbFile.setChecked(True)
        pbFile.clicked.connect(self.openBinFile)

        self.cbBackup.toggled.connect(self.cbxBackupSize.setEnabled)

        self.pbTasmotize.clicked.connect(self.start_process)
        self.pbConfig.clicked.connect(self.send_config)
        self.pbGetIP.clicked.connect(self.get_ip)
        self.pbQuit.clicked.connect(self.reject)
    
    def sendCommandDialog(self):
        self.cmdDlg = CommandDialog(port=self.cbxPort.currentData())
        self.cmdDlg.show()
        # if self.cmdDlg.exec_() == QDialog.Accepted:
            # self.sendCommand()

    def openPinConfig(self):
        self.cmdDlg = PinConfigDialog(port=self.cbxPort.currentData())
        self.cmdDlg.show()
        # if self.cmdDlg.exec_() == QDialog.Accepted:
            # self.sendCommand()

    def refreshPorts(self):
        self.cbxPort.clear()
        ports = reversed(sorted(port.portName() for port in QSerialPortInfo.availablePorts()))
        for p in ports:
            port = QSerialPortInfo(p)
            self.cbxPort.addItem(port.portName(), port.systemLocation())

    def setBinMode(self, radio):
        self.mode = radio
        self.wFile.setVisible(self.mode == 0)
        self.cbHackboxBin.setVisible(self.mode > 0)

        if self.mode == 1:
            self.processReleaseInfo()
        elif self.mode == 2:
            self.processDevelopmentInfo()

    def getFeeds(self):
        self.release_reply = self.nam.get(self.nrRelease)
        self.release_reply.readyRead.connect(self.appendReleaseInfo)
        self.release_reply.finished.connect(lambda: self.rbRelease.setEnabled(True))

        self.development_reply = self.nam.get(self.nrDevelopment)
        self.development_reply.readyRead.connect(self.appendDevelopmentInfo)
        self.development_reply.finished.connect(lambda: self.rbDev.setEnabled(True))

    def appendReleaseInfo(self):
        self.release_data += self.release_reply.readAll()

    def appendDevelopmentInfo(self):
        self.development_data += self.development_reply.readAll()

    def processReleaseInfo(self):
        self.fill_bin_combo(self.release_data, self.rbRelease)

    def processDevelopmentInfo(self):
        self.fill_bin_combo(self.development_data, self.rbDev)

    def fill_bin_combo(self, data, rb):
        try:
            reply = json.loads(str(data, 'utf8'))
            version, bins = list(reply.items())[0]
            version = version.replace('-', ' ').title()

            rb.setText(version)
            if len(bins) > 0:
                self.cbHackboxBin.clear()
                for img in bins:
                    img['filesize'] //= 1024
                    self.cbHackboxBin.addItem('{binary} [{filesize}kB]'.format(**img), '{otaurl}'.format(**img))
                self.cbHackboxBin.setEnabled(True)
        except json.JSONDecodeError as e:
            self.setBinMode(0)
            self.rbgFW.button(0).setChecked(True)
            QMessageBox.critical(self, 'Error', f'Cannot load bin data:\n{e.msg}')

    def openBinFile(self):
        previous_file = self.settings.value('bin_file')
        file, ok = QFileDialog.getOpenFileName(self, 'Select Tasmota image', previous_file, filter='BIN files (*.bin)')
        if ok:
            self.file.setText(file)

    def get_ip(self):
        self.port = QSerialPort(self.cbxPort.currentData())
        self.port.setBaudRate(115200)

        DeviceIP(self.port).exec_()

        if self.port.isOpen():
            self.port.close()

    def send_config(self):
        dlg = SendConfigDialog()
        if dlg.exec_() == QDialog.Accepted:
            if dlg.commands:
                try:
                    self.port = QSerialPort(self.cbxPort.currentData())
                    self.port.setBaudRate(115200)
                    self.port.open(QIODevice.ReadWrite)
                    bytes_sent = self.port.write(bytes(dlg.commands, 'utf8'))
                except Exception as e:
                    QMessageBox.critical(self, 'Error', f'Port access error:\n{e}')
                else:
                    self.settings.setValue('gbWifi', dlg.gbWifi.isChecked())
                    self.settings.setValue('AP', dlg.leAP.text())

                    self.settings.setValue('gbRecWifi', dlg.gbRecWifi.isChecked())

                    self.settings.setValue('gbMQTT', dlg.gbMQTT.isChecked())
                    self.settings.setValue('Broker', dlg.leBroker.text())
                    self.settings.setValue('Port', dlg.sbPort.value())
                    self.settings.setValue('Topic', dlg.leTopic.text())
                    self.settings.setValue('FullTopic', dlg.leFullTopic.text())
                    self.settings.setValue('FriendlyName', dlg.leFriendlyName.text())
                    self.settings.setValue('MQTTUser', dlg.leMQTTUser.text())

                    self.settings.setValue('gbModule', dlg.gbModule.isChecked())
                    self.settings.setValue('ModuleMode', dlg.rbgModule.checkedId())
                    self.settings.setValue('Module', dlg.cbModule.currentText())
                    self.settings.setValue('Template', dlg.leTemplate.text())
                    self.settings.sync()

                    QMessageBox.information(self, 'Done', 'Configuration sent ({} bytes)\nDevice will restart.'.format(bytes_sent))
                finally:
                    if self.port.isOpen():
                        self.port.close()
            else:
                QMessageBox.information(self, 'Done', 'Nothing to send')

    def start_process(self):
        try:
            if self.mode == 0:
                if len(self.file.text()) > 0:
                    self.file_path = self.file.text()
                    self.settings.setValue('bin_file', self.file_path)
                else:
                    raise NoBinFile

            elif self.mode in (1, 2):
                self.file_path = self.cbHackboxBin.currentData()

            process_dlg = ProcessDialog(
                self.cbxPort.currentData(),
                file_path=self.file_path,
                backup=self.cbBackup.isChecked(),
                backup_size=self.cbxBackupSize.currentIndex(),
                erase=self.cbErase.isChecked(),
                auto_reset=self.cbSelfReset.isChecked()
            )
            result = process_dlg.exec_()
            if result == QDialog.Accepted:
                message = 'Process successful!'
                if not self.cbSelfReset.isChecked():
                    message += ' Power cycle the device.'

                QMessageBox.information(self, 'Done', message)
            elif result == QDialog.Rejected:
                if process_dlg.exception:
                    QMessageBox.critical(self, 'Error', str(process_dlg.exception))
                else:
                    QMessageBox.critical(self, 'Process aborted', 'The process has been aborted by the user.')
            
        except NoBinFile:
            QMessageBox.critical(self, 'Image path missing', 'Select a binary to write, or select a different mode.')
        except NetworkError as e:
            QMessageBox.critical(self, 'Network error', e.message)


def main():
    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_DisableWindowContextHelpButton)
    app.setQuitOnLastWindowClosed(True)
    app.setStyle('Fusion')

    app.setPalette(dark_palette)
    app.setStyleSheet('QToolTip { color: #ffffff; background-color: #2a82da; border: 1px solid white; }')
    app.setStyle('Fusion')

    mw = Tasmotizer()
    mw.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
