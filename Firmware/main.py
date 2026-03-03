"""
Принимает сообщения по Bluetooth и выводит их в консоль Thonny
UUID: 6E400001-B5A3-F393-E0A9-E50E24DCCA9E (Nordic UART Service)
"""

import bluetooth
import time
from machine import Pin

# ========== Константы BLE ==========
_IRQ_CENTRAL_CONNECT = const(1) if 'const' in dir() else 1
_IRQ_CENTRAL_DISCONNECT = const(2) if 'const' in dir() else 2
_IRQ_GATTS_WRITE = const(3) if 'const' in dir() else 3

_FLAG_READ = const(0x0002) if 'const' in dir() else 0x0002
_FLAG_WRITE_NO_RESPONSE = const(0x0004) if 'const' in dir() else 0x0004
_FLAG_WRITE = const(0x0008) if 'const' in dir() else 0x0008
_FLAG_NOTIFY = const(0x0010) if 'const' in dir() else 0x0010

# UUID сервиса и характеристик (Nordic UART)
_UART_SERVICE_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
_UART_TX_UUID = bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")  # TX (отправляем уведомления)
_UART_RX_UUID = bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E")  # RX (принимаем записи)

# ========== Helper: генерация пакета ==========
def advertising_payload(name=None, services=None):
    """Создаёт payload для BLE"""
    payload = bytearray()
    
    # Флаги: LE General Discoverable Mode, BR/EDR not supported
    payload += bytes([0x02, 0x01, 0x06])
    
    # Имя устройства
    if name:
        name_bytes = name.encode('utf-8')
        payload += bytes([len(name_bytes) + 1, 0x09]) + name_bytes
    
    # UUID сервиса (128-bit)
    if services:
        for uuid in services:
            uuid_bytes = bytes(uuid)
            if len(uuid_bytes) == 16:
                payload += bytes([0x11, 0x07]) + uuid_bytes
    
    return payload

# ========== Класс BLE периферии ==========
class BLEUART:
    def __init__(self, name="PicoW-UART"):
        self._ble = bluetooth.BLE()
        self._ble.active(True)
        self._ble.irq(self._irq_handler)
        
        # Регистрация сервиса
        ((self._tx_handle, self._rx_handle),) = self._ble.gatts_register_services((
            (_UART_SERVICE_UUID, (
                (_UART_TX_UUID, _FLAG_READ | _FLAG_NOTIFY),
                (_UART_RX_UUID, _FLAG_WRITE | _FLAG_WRITE_NO_RESPONSE),
            )),
        ))
        
        self._connections = set()
        self._rx_callback = None
        self._name = name
        
        # Запуск
        self._advertise()
        print(f"BLE готов: {name}")
    
    def _irq_handler(self, event, data):
        """Обработчик BLE событий"""
        if event == _IRQ_CENTRAL_CONNECT:
            conn_handle, _, _ = data
            print(f"Подключено: {conn_handle}")
            self._connections.add(conn_handle)
            
        elif event == _IRQ_CENTRAL_DISCONNECT:
            conn_handle, _, _ = data
            print(f"Отключено: {conn_handle}")
            self._connections.discard(conn_handle)
            self._advertise()  
            
        elif event == _IRQ_GATTS_WRITE:
            conn_handle, value_handle = data
            if value_handle == self._rx_handle and self._rx_callback:
                data = self._ble.gatts_read(value_handle)
                self._rx_callback(data)
    
    def _advertise(self, interval_us=500000):
        """Запустить BLE"""
        payload = advertising_payload(name=self._name, services=[_UART_SERVICE_UUID])
        self._ble.gap_advertise(interval_us, adv_data=payload)
        print("Поиск...")
    
    def send(self, data):
        """Отправить данные подключенному клиенту"""
        if isinstance(data, str):
            data = data.encode('utf-8')
        for conn_handle in self._connections:
            self._ble.gatts_notify(conn_handle, self._tx_handle, data)
    
    def on_rx(self, callback):
        """Установить callback для приёма данных"""
        self._rx_callback = callback
    
    def is_connected(self):
        """Проверить наличие подключений"""
        return len(self._connections) > 0

# ========== Основной код ==========
def on_message_received(data):
    """Callback: вызывается при получении сообщения"""
    try:
        # Декодируем и печатаем в консоль Thonny
        message = data.decode('utf-8').strip()
        print(f"📥 ПОЛУЧЕНО: {message}")
        
        # Отправляем подтверждение обратно
        uart.send(f"Pico: {message}\r\n")
        
        # Обработка команды "toggle" для светодиода
        if message.lower() == 'toggle':
            led.value(not led.value())
            print(f"LED: {'ON' if led.value() else 'OFF'}")
            
    except Exception as e:
        print(f"Ошибка обработки: {e}")

# Инициализация
print("Pico W BLE UART — запуск...")
led = Pin("LED", Pin.OUT)
led.value(0)

# Создаём BLE UART
uart = BLEUART(name="PicoW-UART")
uart.on_rx(on_message_received)

# Главный цикл
print("Ожидание подключения...")
while True:
    # Продлеваем если нет подключений
    if not uart.is_connected():
        uart._advertise()
    
    # Небольшая пауза для экономии ресурсов
    time.sleep_ms(100)