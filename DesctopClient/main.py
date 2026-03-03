# Ищет устройства Bluetooth 
# и отправляет на них сообщения
# НЕ работает с кирилицей

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import asyncio
from bleak import BleakClient, BleakScanner
import threading

SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"

class BluetoothApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Bluetooth Sender")
        self.root.geometry("500x600")
        
        self.client = None
        self.is_connected = False
        self.device_address = None
        self.loop = asyncio.new_event_loop()
        
        self.create_widgets()
        self.thread = threading.Thread(target=self.run_asyncio, daemon=True)
        self.thread.start()
    
    def create_widgets(self):  
        # Кнопка сканирования      
        btn_frame = ttk.Frame(self.root, padding="10")
        btn_frame.pack(fill=tk.X)
        
        self.scan_btn = ttk.Button(btn_frame, text="Сканировать", command=self.scan_devices)
        self.scan_btn.pack(side=tk.LEFT, padx=5)
        
        self.connect_btn = ttk.Button(btn_frame, text="Подключиться", command=self.connect_device, state=tk.DISABLED)
        self.connect_btn.pack(side=tk.LEFT, padx=5)
        
        self.disconnect_btn = ttk.Button(btn_frame, text="Отключиться", command=self.disconnect_device, state=tk.DISABLED)
        self.disconnect_btn.pack(side=tk.LEFT, padx=5)
        
        # Статус
        self.status_label = ttk.Label(self.root, text="Статус: Не подключено", foreground="gray")
        self.status_label.pack(pady=5)
        
        # Список устройств
        ttk.Label(self.root, text="Устройства:").pack(anchor=tk.W, padx=10)
        self.device_listbox = tk.Listbox(self.root, height=8)
        self.device_listbox.pack(fill=tk.X, padx=10, pady=5)
        self.device_listbox.bind('<<ListboxSelect>>', self.on_device_select)
        
        # Поле ввода
        ttk.Label(self.root, text="Сообщение:").pack(anchor=tk.W, padx=10)
        self.message_entry = ttk.Entry(self.root)
        self.message_entry.pack(fill=tk.X, padx=10, pady=5)
        self.message_entry.bind('<Return>', lambda e: self.send_message())
        
        # Кнопка отправки
        self.send_btn = ttk.Button(self.root, text="Отправить", command=self.send_message, state=tk.DISABLED)
        self.send_btn.pack(padx=10, pady=5)
        
        # Лог
        ttk.Label(self.root, text="Лог:").pack(anchor=tk.W, padx=10)
        self.log_text = scrolledtext.ScrolledText(self.root, height=15, state='disabled')
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    
    def run_asyncio(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
    
    def log(self, message):
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, f"{message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')
    
    def scan_devices(self):
        self.log("Поиск устройств...")
        self.scan_btn.config(state=tk.DISABLED)
        
        async def scan():
            devices = await BleakScanner.discover(timeout=5.0)
            self.device_listbox.delete(0, tk.END)
            self.devices = devices
            
            for i, device in enumerate(devices):
                name = device.name if device.name else "Неизвестно"
                self.device_listbox.insert(tk.END, f"{i}. {name} ({device.address})")
            
            self.log(f"Найдено {len(devices)} устройств")
            self.scan_btn.config(state=tk.NORMAL)
            if devices:
                self.connect_btn.config(state=tk.NORMAL)
        
        asyncio.run_coroutine_threadsafe(scan(), self.loop)
    
    def on_device_select(self, event):
        selection = self.device_listbox.curselection()
        if selection and hasattr(self, 'devices'):
            index = selection[0]
            self.device_address = self.devices[index].address
            self.log(f"Выбрано: {self.devices[index].name}")
    
    def connect_device(self):
        if not self.device_address:
            messagebox.showwarning("Внимание", "Выберите устройство!")
            return
        
        self.log(f"Подключение к {self.device_address}...")
        
        async def connect():
            try:
                self.client = BleakClient(self.device_address)
                await self.client.connect()
                self.is_connected = True
                
                self.root.after(0, lambda: self.status_label.config(text="Статус: Подключено", foreground="green"))
                self.root.after(0, lambda: self.send_btn.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.disconnect_btn.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.connect_btn.config(state=tk.DISABLED))
                self.log("Подключено!")
            except Exception as e:
                self.log(f"Ошибка: {e}")
                self.root.after(0, lambda: self.status_label.config(text="Статус: Ошибка", foreground="red"))
        
        asyncio.run_coroutine_threadsafe(connect(), self.loop)
    
    def disconnect_device(self):
        async def disconnect():
            if self.client and self.is_connected:
                await self.client.disconnect()
                self.is_connected = False
                
                self.root.after(0, lambda: self.status_label.config(text="Статус: Отключено", foreground="gray"))
                self.root.after(0, lambda: self.send_btn.config(state=tk.DISABLED))
                self.root.after(0, lambda: self.disconnect_btn.config(state=tk.DISABLED))
                self.root.after(0, lambda: self.connect_btn.config(state=tk.NORMAL))
                self.log("Отключено")
        
        asyncio.run_coroutine_threadsafe(disconnect(), self.loop)
    
    def send_message(self):
        if not self.is_connected:
            messagebox.showwarning("Внимание", "Сначала подключитесь!")
            return
        
        message = self.message_entry.get().strip()
        if not message:
            return
        
        async def send():
            try:
                await self.client.write_gatt_char(CHAR_UUID, message.encode('utf-8'))
                self.root.after(0, lambda: self.log(f"Отправлено: {message}"))
                self.root.after(0, lambda: self.message_entry.delete(0, tk.END))
            except Exception as e:
                self.root.after(0, lambda: self.log(f"Ошибка отправки: {e}"))
        
        asyncio.run_coroutine_threadsafe(send(), self.loop)

if __name__ == "__main__":
    root = tk.Tk()
    app = BluetoothApp(root)
    root.mainloop()
