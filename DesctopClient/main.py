import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import asyncio
from bleak import BleakScanner, BleakClient
import threading
from datetime import datetime
import platform

# === UUID ===
SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHAR_UUID_TIME = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
CHAR_UUID_ALARM = "beb5483e-36e1-4688-b7f5-ea07361b26a9"
CHAR_UUID_TASK = "beb5483e-36e1-4688-b7f5-ea07361b26ac"
CHAR_UUID_STATUS = "beb5483e-36e1-4688-b7f5-ea07361b26aa"

# === Карта анимаций ===
ANIM_MAP = {
    "Бег": 1,
    "Работа": 2,
    "Чтение": 3,
    "Упражнения": 4,
    "Ходьба": 5
}

ANIM_NAMES_REVERSE = {v: k for k, v in ANIM_MAP.items()}

class Task:
    def __init__(self, name, description, hour, minute, duration, animation):
        self.name = name[:20]
        self.description = description[:30]
        self.hour = hour
        self.minute = minute
        self.duration = duration
        self.animation = animation
        self.completed = False
    
    def to_bytes(self):
        data = bytearray(54)
        data[0] = self.hour
        data[1] = self.minute
        data[2] = self.duration
        data[3] = self.animation
        
        name_bytes = self.name.encode('utf-8')[:20]
        data[4:4+len(name_bytes)] = name_bytes
        
        desc_bytes = self.description.encode('utf-8')[:30]
        data[24:24+len(desc_bytes)] = desc_bytes
        
        return bytes(data)

class AlarmClockApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PomoPico")
        self.root.geometry("900x700")
        
        self.client = None
        self.is_connected = False
        self.device_address = None
        self.devices = []
        self.tasks = []
        self.alarms = []
        
        self.loop = asyncio.new_event_loop()
        
        # === Инициализация виджетов ===
        self.device_listbox = None
        self.alarm_listbox = None
        self.task_listbox = None
        
        # === Хранилища данных (не удаляются) ===
        self.saved_devices = []
        self.saved_alarms = []
        self.saved_tasks = []
        
        self.create_main_layout()
        self.thread = threading.Thread(target=self.run_asyncio, daemon=True)
        self.thread.start()
        
        self.root.after(1000, self.scan_devices)

    def create_main_layout(self):
        # === ВЕРХНЯЯ ПАНЕЛЬ: СТАТУС ===
        status_frame = ttk.Frame(self.root, padding="15")
        status_frame.pack(fill=tk.X)
        
        self.connection_label = ttk.Label(
            status_frame, 
            text="НЕ ПОДКЛЮЧЕНО", 
            font=("Arial", 24, "bold"),
            foreground="red"
        )
        self.connection_label.pack()
        
        sys_info = f"{platform.system()} | Python {platform.python_version()}"
        ttk.Label(status_frame, text=sys_info, foreground="gray").pack(pady=5)

        # === БОКОВОЕ МЕНЮ ===
        menu_frame = ttk.LabelFrame(self.root, text="Меню", padding="10")
        menu_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        
        ttk.Button(menu_frame, text="Настройки", command=self.show_settings, width=20).pack(pady=5)
        ttk.Button(menu_frame, text="Будильники", command=self.show_alarms, width=20).pack(pady=5)
        ttk.Button(menu_frame, text="Задачи", command=self.show_tasks, width=20).pack(pady=5)
        
        ttk.Separator(menu_frame, orient='horizontal').pack(fill=tk.X, pady=10)
        
        ttk.Button(menu_frame, text="Сканировать", command=self.scan_devices, width=20).pack(pady=5)
        ttk.Button(menu_frame, text="Подключиться", command=self.connect_device, width=20).pack(pady=5)
        ttk.Button(menu_frame, text="Отключиться", command=self.disconnect_device, width=20).pack(pady=5)

        # === ОСНОВНАЯ ОБЛАСТЬ ===
        self.content_frame = ttk.Frame(self.root, padding="10")
        self.content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        # === ЛОГ ===
        log_frame = ttk.LabelFrame(self.root, text="Лог событий", padding="10")
        log_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=10, pady=10)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, width=35, height=30, state='disabled')
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.show_settings()

    def log(self, msg):
        self.log_text.config(state='normal')
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def update_connection_status(self):
        if self.is_connected:
            self.connection_label.config(text="ПОДКЛЮЧЕНО", foreground="green")
        else:
            self.connection_label.config(text="НЕ ПОДКЛЮЧЕНО", foreground="red")

    def clear_content(self):
        for widget in self.content_frame.winfo_children():
            widget.destroy()
        self.device_listbox = None
        self.alarm_listbox = None
        self.task_listbox = None

    # === ВОСПОЛНЕНИЕ СПИСКОВ ===
    def refresh_device_listbox(self):
        if self.device_listbox:
            self.device_listbox.delete(0, tk.END)
            for dev in self.saved_devices:
                txt = f"{dev['name']} ({dev['address']}) [{dev['rssi']} dBm]"
                self.device_listbox.insert(tk.END, txt)

    def refresh_alarm_listbox(self):
        if self.alarm_listbox:
            self.alarm_listbox.delete(0, tk.END)
            for alarm in self.saved_alarms:
                self.alarm_listbox.insert(tk.END, alarm)

    def refresh_task_listbox(self):
        if self.task_listbox:
            self.task_listbox.delete(0, tk.END)
            for task in self.saved_tasks:
                self.task_listbox.insert(tk.END, task)

    # ==================== СТРАНИЦА: НАСТРОЙКИ ====================
    def show_settings(self):
        self.clear_content()
        
        ttk.Label(self.content_frame, text="Настройки подключения", font=("Arial", 16, "bold")).pack(pady=10)
        
        dev_frame = ttk.LabelFrame(self.content_frame, text="Найденные устройства", padding="10")
        dev_frame.pack(fill=tk.X, pady=10)
        
        self.device_listbox = tk.Listbox(dev_frame, height=8)
        self.device_listbox.pack(fill=tk.X, pady=5)
        self.device_listbox.bind('<<ListboxSelect>>', self.on_device_select)
        
        # === ВОСПОЛНЯЕМ СПИСОК ===
        self.refresh_device_listbox()
        
        time_frame = ttk.LabelFrame(self.content_frame, text="Установка времени", padding="10")
        time_frame.pack(fill=tk.X, pady=10)
        
        tf = ttk.Frame(time_frame)
        tf.pack()
        
        ttk.Label(tf, text="ЧЧ:").grid(row=0, column=0, padx=5)
        self.time_h = ttk.Spinbox(tf, from_=0, to=23, width=5)
        self.time_h.grid(row=0, column=1, padx=5)
        self.time_h.set(datetime.now().hour)
        
        ttk.Label(tf, text="ММ:").grid(row=0, column=2, padx=5)
        self.time_m = ttk.Spinbox(tf, from_=0, to=59, width=5)
        self.time_m.grid(row=0, column=3, padx=5)
        self.time_m.set(datetime.now().minute)
        
        ttk.Label(tf, text="СС:").grid(row=0, column=4, padx=5)
        self.time_s = ttk.Spinbox(tf, from_=0, to=59, width=5)
        self.time_s.grid(row=0, column=5, padx=5)
        self.time_s.set(datetime.now().second)
        
        ttk.Button(time_frame, text="Отправить время", command=self.set_time).pack(pady=10)
        
        info_frame = ttk.LabelFrame(self.content_frame, text="Информация", padding="10")
        info_frame.pack(fill=tk.X, pady=10)
        
        ttk.Label(info_frame, text="1. Нажмите 'Сканировать' для поиска устройства").pack(anchor=tk.W)
        ttk.Label(info_frame, text="2. Выберите устройство из списка").pack(anchor=tk.W)
        ttk.Label(info_frame, text="3. Нажмите 'Подключиться'").pack(anchor=tk.W)
        ttk.Label(info_frame, text="4. Время синхронизируется автоматически при подключении").pack(anchor=tk.W)

    # ==================== СТРАНИЦА: БУДИЛЬНИКИ ====================
    def show_alarms(self):
        self.clear_content()
        
        ttk.Label(self.content_frame, text="Управление будильниками", font=("Arial", 16, "bold")).pack(pady=10)
        
        add_frame = ttk.LabelFrame(self.content_frame, text="Добавить будильник", padding="10")
        add_frame.pack(fill=tk.X, pady=10)
        
        af = ttk.Frame(add_frame)
        af.pack()
        
        ttk.Label(af, text="Время:").grid(row=0, column=0, padx=5)
        self.alarm_h = ttk.Spinbox(af, from_=0, to=23, width=5)
        self.alarm_h.grid(row=0, column=1, padx=5)
        self.alarm_h.set(datetime.now().hour)
        
        self.alarm_m = ttk.Spinbox(af, from_=0, to=59, width=5)
        self.alarm_m.grid(row=0, column=2, padx=5)
        self.alarm_m.set((datetime.now().minute + 1) % 60)
        
        self.alarm_en_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(af, text="Включён", variable=self.alarm_en_var).grid(row=0, column=3, padx=10)
        
        ttk.Button(af, text="Добавить", command=self.add_alarm).grid(row=0, column=4, padx=10)
        
        list_frame = ttk.LabelFrame(self.content_frame, text="Активные будильники", padding="10")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        self.alarm_listbox = tk.Listbox(list_frame, height=10)
        self.alarm_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.alarm_listbox.yview)
        sb.pack(side=tk.RIGHT, fill="y")
        self.alarm_listbox.config(yscrollcommand=sb.set)
        
        # === ВОСПОЛНЯЕМ СПИСОК ===
        self.refresh_alarm_listbox()
        
        bf = ttk.Frame(self.content_frame)
        bf.pack(fill=tk.X, pady=5)
        
        ttk.Button(bf, text="Очистить все", command=self.clear_alarms).pack(side=tk.LEFT, padx=5)

    # ==================== СТРАНИЦА: ЗАДАЧИ ====================
    def show_tasks(self):
        self.clear_content()
        
        ttk.Label(self.content_frame, text="Управление задачами", font=("Arial", 16, "bold")).pack(pady=10)
        
        add_frame = ttk.LabelFrame(self.content_frame, text="Добавить задачу", padding="10")
        add_frame.pack(fill=tk.X, pady=10)
        
        ttk.Label(add_frame, text="Название:").grid(row=0, column=0, sticky=tk.E, padx=5, pady=2)
        self.task_name = ttk.Entry(add_frame, width=35)
        self.task_name.grid(row=0, column=1, padx=5, pady=2)
        self.task_name.insert(0, "Новая задача")
        
        ttk.Label(add_frame, text="Описание:").grid(row=1, column=0, sticky=tk.E, padx=5, pady=2)
        self.task_desc = ttk.Entry(add_frame, width=35)
        self.task_desc.grid(row=1, column=1, padx=5, pady=2)
        self.task_desc.insert(0, "Описание задачи")
        
        ttk.Label(add_frame, text="Время:").grid(row=2, column=0, sticky=tk.E, padx=5, pady=2)
        tf = ttk.Frame(add_frame)
        tf.grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)
        
        self.task_h = ttk.Spinbox(tf, from_=0, to=23, width=4)
        self.task_h.pack(side=tk.LEFT)
        self.task_h.set(datetime.now().hour)
        ttk.Label(tf, text=":").pack(side=tk.LEFT)
        self.task_m = ttk.Spinbox(tf, from_=0, to=59, width=4)
        self.task_m.pack(side=tk.LEFT)
        self.task_m.set((datetime.now().minute + 15) % 60)
        
        ttk.Label(add_frame, text="Длительность:").grid(row=3, column=0, sticky=tk.E, padx=5, pady=2)
        self.task_dur = ttk.Combobox(add_frame, values=["15 мин", "30 мин", "45 мин"], state="readonly", width=10)
        self.task_dur.grid(row=3, column=1, sticky=tk.W, padx=5, pady=2)
        self.task_dur.set("15 мин")
        
        ttk.Label(add_frame, text="Анимация:").grid(row=4, column=0, sticky=tk.E, padx=5, pady=2)
        self.task_anim = ttk.Combobox(add_frame, values=[
            "Бег", "Работа", "Чтение", "Упражнения", "Ходьба"
        ], state="readonly", width=15)
        self.task_anim.grid(row=4, column=1, sticky=tk.W, padx=5, pady=2)
        self.task_anim.set("Бег")
        
        ttk.Button(add_frame, text="Добавить в список", command=self.add_task).grid(row=5, column=0, columnspan=2, pady=10)
        
        list_frame = ttk.LabelFrame(self.content_frame, text="Список задач", padding="10")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        self.task_listbox = tk.Listbox(list_frame, height=8)
        self.task_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.task_listbox.yview)
        sb.pack(side=tk.RIGHT, fill="y")
        self.task_listbox.config(yscrollcommand=sb.set)
        
        # === ВОСПОЛНЯЕМ СПИСОК ===
        self.refresh_task_listbox()
        
        bf = ttk.Frame(self.content_frame)
        bf.pack(fill=tk.X, pady=5)
        
        ttk.Button(bf, text="Удалить", command=self.remove_task).pack(side=tk.LEFT, padx=5)
        ttk.Button(bf, text="Очистить все", command=self.clear_tasks).pack(side=tk.LEFT, padx=5)
        ttk.Button(bf, text="ОТПРАВИТЬ ВСЕ НА УСТРОЙСТВО", command=self.send_all_tasks).pack(side=tk.RIGHT, padx=5)

    # ==================== ФУНКЦИИ ПОДКЛЮЧЕНИЯ ===
    def scan_devices(self):
        self.log("Сканирование устройств...")
        
        if not self.device_listbox:
            self.log("Перейдите на страницу 'Настройки' для сканирования")
            self.show_settings()
            return
        
        try:
            self.device_listbox.delete(0, tk.END)
        except:
            self.log("Listbox недоступен, обновляем страницу")
            self.show_settings()
            return
        
        self.devices = []
        
        async def do_scan():
            try:
                found = await BleakScanner.discover(timeout=10.0, return_adv=True)
                for address, (device, adv) in found.items():
                    name = device.name if device.name else "Unknown"
                    rssi = adv.rssi if hasattr(adv, 'rssi') else 0
                    self.devices.append({"address": address, "name": name, "rssi": rssi})
                
                self.log(f"Найдено устройств: {len(self.devices)}")
                
                # === СОХРАНЯЕМ В ПАМЯТЬ ===
                self.saved_devices = self.devices.copy()
                
                try:
                    if self.device_listbox:
                        self.device_listbox.delete(0, tk.END)
                        for dev in self.devices:
                            txt = f"{dev['name']} ({dev['address']}) [{dev['rssi']} dBm]"
                            self.device_listbox.insert(tk.END, txt)
                except Exception as e:
                    self.log(f"Ошибка обновления списка: {e}")
                
                if len(self.devices) == 0:
                    self.log("Устройства не найдены. Проверьте Bluetooth.")
                    
            except Exception as e:
                self.log(f"Ошибка сканирования: {e}")
        
        asyncio.run_coroutine_threadsafe(do_scan(), self.loop)

    def on_device_select(self, event):
        sel = self.device_listbox.curselection()
        if sel and self.devices:
            idx = sel[0]
            self.device_address = self.devices[idx]['address']
            self.log(f"Выбрано: {self.devices[idx]['name']}")

    def connect_device(self):
        if not self.device_address:
            messagebox.showwarning("Ошибка", "Сначала выберите устройство из списка!")
            return
        
        self.log(f"Подключение к {self.device_address}...")
        
        async def do_connect():
            try:
                self.client = BleakClient(self.device_address)
                await self.client.connect()
                self.is_connected = True
                
                now = datetime.now()
                time_data = bytes([0, 0, 0, now.hour, now.minute, now.second])
                await self.client.write_gatt_char(CHAR_UUID_TIME, time_data)
                self.log(f"Авто-синхронизация: {now.hour:02d}:{now.minute:02d}:{now.second:02d}")
                
                self.root.after(0, self.update_connection_status)
                self.log("Подключение успешно!")
                
            except Exception as e:
                self.log(f"Ошибка подключения: {e}")
                self.root.after(0, self.update_connection_status)
        
        asyncio.run_coroutine_threadsafe(do_connect(), self.loop)

    def disconnect_device(self):
        async def do_disconnect():
            if self.client and self.is_connected:
                await self.client.disconnect()
                self.is_connected = False
                self.client = None
                self.root.after(0, self.update_connection_status)
                self.log("Отключено")
        
        asyncio.run_coroutine_threadsafe(do_disconnect(), self.loop)

    def set_time(self):
        if not self.is_connected:
            messagebox.showwarning("Ошибка", "Сначала подключитесь к устройству!")
            return
        
        h = int(self.time_h.get())
        m = int(self.time_m.get())
        s = int(self.time_s.get())
        data = bytes([0, 0, 0, h, m, s])
        
        async def send():
            try:
                await self.client.write_gatt_char(CHAR_UUID_TIME, data)
                self.log(f"Время установлено: {h:02d}:{m:02d}:{s:02d}")
                messagebox.showinfo("Успех", "Время отправлено на устройство!")
            except Exception as e:
                self.log(f"Ошибка: {e}")
        
        asyncio.run_coroutine_threadsafe(send(), self.loop)

    def add_alarm(self):
        if not self.is_connected:
            messagebox.showwarning("Ошибка", "Сначала подключитесь!")
            return
        
        h = int(self.alarm_h.get())
        m = int(self.alarm_m.get())
        en = 1 if self.alarm_en_var.get() else 0
        data = bytes([h, m, en])
        
        async def send():
            try:
                await self.client.write_gatt_char(CHAR_UUID_ALARM, data)
                status = "ВКЛ" if en else "ВЫКЛ"
                alarm_text = f"{h:02d}:{m:02d} [{status}]"
                self.saved_alarms.append(alarm_text)  # === СОХРАНЯЕМ ===
                if self.alarm_listbox:
                    self.alarm_listbox.insert(tk.END, alarm_text)
                self.log(f"Будильник добавлен: {h:02d}:{m:02d}")
            except Exception as e:
                self.log(f"Ошибка: {e}")
        
        asyncio.run_coroutine_threadsafe(send(), self.loop)

    def clear_alarms(self):
        self.saved_alarms = []
        if self.alarm_listbox:
            self.alarm_listbox.delete(0, tk.END)
        self.log("Будильники очищены")

    def add_task(self):
        name = self.task_name.get().strip() or "Задача"
        desc = self.task_desc.get().strip() or "-"
        h = int(self.task_h.get())
        m = int(self.task_m.get())
        dur_map = {"15 мин": 1, "30 мин": 2, "45 мин": 3}
        dur = dur_map.get(self.task_dur.get(), 1)
        animation = ANIM_MAP.get(self.task_anim.get(), 1)
        
        t = Task(name, desc, h, m, dur, animation)
        self.tasks.append(t)
        
        anim_name = self.task_anim.get()
        task_text = f"{h:02d}:{m:02d} - {name} ({self.task_dur.get()}, {anim_name})"
        self.saved_tasks.append(task_text)  # === СОХРАНЯЕМ ===
        
        if self.task_listbox:
            self.task_listbox.insert(tk.END, task_text)
        self.log(f"Задача добавлена: {name} | {anim_name}")

    def remove_task(self):
        sel = self.task_listbox.curselection() if self.task_listbox else None
        if sel:
            self.saved_tasks.pop(sel[0])
            self.tasks.pop(sel[0])
            self.task_listbox.delete(sel[0])
            self.log("Задача удалена")

    def clear_tasks(self):
        self.saved_tasks = []
        self.tasks = []
        if self.task_listbox:
            self.task_listbox.delete(0, tk.END)
        self.log("Все задачи удалены")

    def send_all_tasks(self):
        if not self.is_connected:
            messagebox.showwarning("Ошибка", "Сначала подключитесь!")
            return
        if not self.tasks:
            messagebox.showinfo("Инфо", "Список задач пуст!")
            return
        
        async def send():
            try:
                count = len(self.tasks)
                self.log(f"Отправка {count} задач...")
                
                await self.client.write_gatt_char(CHAR_UUID_TASK, bytes([count]))
                await asyncio.sleep(0.1)
                
                for i, task in enumerate(self.tasks):
                    await self.client.write_gatt_char(CHAR_UUID_TASK, task.to_bytes())
                    anim_name = ANIM_NAMES_REVERSE.get(task.animation, "Unknown")
                    self.log(f"   {i+1}/{count}: {task.name} ({anim_name})")
                    await asyncio.sleep(0.05)
                
                self.log("ВСЕ ЗАДАЧИ ОТПРАВЛЕНЫ!")
                messagebox.showinfo("Успех", f"Отправлено {count} задач!")
                
            except Exception as e:
                self.log(f"Ошибка: {e}")
                messagebox.showerror("Ошибка", str(e))
        
        asyncio.run_coroutine_threadsafe(send(), self.loop)

    def run_asyncio(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

if __name__ == "__main__":
    root = tk.Tk()
    app = AlarmClockApp(root)
    root.mainloop()
