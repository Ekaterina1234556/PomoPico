/*
 * M5StickC Plus 2 - Smart Task Timer (v5)
 * Fixed: Alarm rings continuously until button A pressed
 */

#include <M5Unified.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// === UUID ===
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHAR_UUID_TIME      "beb5483e-36e1-4688-b7f5-ea07361b26a8"
#define CHAR_UUID_ALARM     "beb5483e-36e1-4688-b7f5-ea07361b26a9"
#define CHAR_UUID_TASK      "beb5483e-36e1-4688-b7f5-ea07361b26ac"
#define CHAR_UUID_STATUS    "beb5483e-36e1-4688-b7f5-ea07361b26aa"

#define MAX_ALARMS 5
#define MAX_TASKS 10

// === Типы анимаций ===
#define ANIM_NONE       0
#define ANIM_RUNNING    1
#define ANIM_WORKING    2
#define ANIM_READING    3
#define ANIM_EXERCISE   4
#define ANIM_WALKING    5

struct Alarm {
    uint8_t hour;
    uint8_t minute;
    bool enabled;
    bool triggered;
};

struct Task {
    char name[21];
    char description[31];
    uint8_t hour;
    uint8_t minute;
    uint8_t duration;
    uint8_t animation;
    bool active;
    bool completed;
};

// === Состояния ===
enum DeviceState {
    STATE_HOME,
    STATE_TASK_LIST,
    STATE_TASK_RUN,
    STATE_ALARM_RINGING,  // === НОВОЕ: Будильник звонит ===
    STATE_SCREEN_OFF
};

DeviceState currentState = STATE_HOME;
bool displayEnabled = true;

// === Глобальные переменные ===
RTC_DATA_ATTR unsigned long rtc_currentTimeSec = 43200;
RTC_DATA_ATTR Alarm rtc_alarms[MAX_ALARMS];
RTC_DATA_ATTR uint8_t rtc_alarmCount = 0;
RTC_DATA_ATTR Task rtc_tasks[MAX_TASKS];
RTC_DATA_ATTR uint8_t rtc_taskCount = 0;
RTC_DATA_ATTR bool rtc_initialized = false;

int currentTaskIndex = -1;
unsigned long taskRemainingSec = 0;
bool isTaskPaused = false;
unsigned long lastTimerTick = 0;
int listSelectionIndex = 0;

unsigned long lastAnimFrame = 0;
int animFrame = 0;
unsigned long lastActivityTime = 0;

// === Для будильника ===
bool alarmRinging = false;
unsigned long alarmLastBeep = 0;
unsigned long alarmLastBlink = 0;
bool alarmDisplayOn = true;

bool deviceConnected = false;
BLEServer *pServer = nullptr;
BLECharacteristic *pCharTime = nullptr;
BLECharacteristic *pCharAlarm = nullptr;
BLECharacteristic *pCharTask = nullptr;
BLECharacteristic *pCharStatus = nullptr;
BLEAdvertising *pAdvertising = nullptr;

uint8_t taskReceiveIndex = 0;
bool waitingForTasks = false;

// === Кнопки ===
unsigned long btnA_pressTime = 0;
unsigned long btnB_pressTime = 0;
bool btnA_pressed = false;
bool btnB_pressed = false;

// === Прототипы ===
void showHomeScreen();
void showTaskListScreen();
void showTaskRunScreen_Timer();
void showTaskRunScreen_Desc();
void showTaskRunScreen_Anim();
void startTask(int index);
void stopTask();
void resumeTask();
void completeTask();
void checkAlarms();
void initDefaults();
void goToHome();
void updateButtons();
void toggleDisplay();
void wakeDisplay();
void startAlarmRing();
void stopAlarmRing();
void handleAlarmRinging();

// === Отрисовка анимаций ===
void drawCharacter(int x, int y, int frame, uint8_t animType);
void drawRunningChar(int x, int y, int frame);
void drawWorkingChar(int x, int y, int frame);
void drawReadingChar(int x, int y, int frame);
void drawExerciseChar(int x, int y, int frame);
void drawWalkingChar(int x, int y, int frame);

// === Callbacks ===
class ServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) override { 
        deviceConnected = true; 
        if(pAdvertising) pAdvertising->stop(); 
        wakeDisplay();
        lastActivityTime = millis();
        if(currentState==STATE_HOME) showHomeScreen(); 
    }
    void onDisconnect(BLEServer* pServer) override { 
        deviceConnected = false; 
        if(pAdvertising) pAdvertising->start(); 
        if(currentState==STATE_HOME) showHomeScreen(); 
    }
};

class TimeCallback: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) override {
        std::string v = std::string((char*)pCharacteristic->getData(), pCharacteristic->getLength());
        if(v.length()>=6) {
            uint8_t h = (uint8_t)v[3];
            uint8_t m = (uint8_t)v[4];
            uint8_t s = (uint8_t)v[5];
            rtc_currentTimeSec = h * 3600 + m * 60 + s;
            wakeDisplay();
            lastActivityTime = millis();
            if(currentState==STATE_HOME) showHomeScreen();
        }
    }
};

class AlarmCallback: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) override {
        std::string v = std::string((char*)pCharacteristic->getData(), pCharacteristic->getLength());
        if(v.length()>=3) {
            uint8_t h=v[0], m=v[1], en=v[2];
            bool found=false;
            for(int i=0;i<rtc_alarmCount;i++) 
                if(rtc_alarms[i].hour==h && rtc_alarms[i].minute==m) { 
                    rtc_alarms[i].enabled=(en==1); found=true; break; 
                }
            if(!found && en && rtc_alarmCount<MAX_ALARMS) { 
                rtc_alarms[rtc_alarmCount].hour=h; 
                rtc_alarms[rtc_alarmCount].minute=m; 
                rtc_alarms[rtc_alarmCount].enabled=true; 
                rtc_alarmCount++; 
            }
            wakeDisplay();
            lastActivityTime = millis();
            if(currentState==STATE_HOME) showHomeScreen();
        }
    }
};

class TaskCallback: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) override {
        std::string v = std::string((char*)pCharacteristic->getData(), pCharacteristic->getLength());
        if(v.length()==1) {
            rtc_taskCount = v[0]; 
            if(rtc_taskCount>MAX_TASKS) rtc_taskCount=MAX_TASKS;
            taskReceiveIndex=0; 
            waitingForTasks=(rtc_taskCount>0);
            for(int i=0;i<MAX_TASKS;i++) { 
                rtc_tasks[i].active=false; 
                rtc_tasks[i].completed=false; 
            }
            wakeDisplay();
            lastActivityTime = millis();
            return;
        }
        if(v.length()>=54 && waitingForTasks && taskReceiveIndex<rtc_taskCount) {
            Task &t = rtc_tasks[taskReceiveIndex];
            t.hour=v[0]; t.minute=v[1]; t.duration=v[2]; t.animation=v[3];
            for(int i=0;i<20;i++) t.name[i]=v[4+i]; t.name[20]='\0';
            for(int i=0;i<30;i++) t.description[i]=v[24+i]; t.description[30]='\0';
            t.active=true; t.completed=false;
            taskReceiveIndex++;
            if(taskReceiveIndex>=rtc_taskCount) waitingForTasks=false;
            wakeDisplay();
            lastActivityTime = millis();
        }
    }
};

void setup() {
    Serial.begin(115200);
    auto cfg = M5.config();
    M5.begin(cfg);
    M5.Display.setRotation(1);
    M5.Display.setTextSize(2);
    M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);

    if(!rtc_initialized) { 
        rtc_currentTimeSec=43200; 
        initDefaults(); 
        rtc_initialized=true; 
    }

    BLEDevice::init("M5Clock_Plus2");
    pServer = BLEDevice::createServer();
    pServer->setCallbacks(new ServerCallbacks());
    BLEService *pService = pServer->createService(SERVICE_UUID);

    pCharTime = pService->createCharacteristic(CHAR_UUID_TIME, 
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
    pCharTime->setCallbacks(new TimeCallback()); 
    pCharTime->addDescriptor(new BLE2902());

    pCharAlarm = pService->createCharacteristic(CHAR_UUID_ALARM, 
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
    pCharAlarm->setCallbacks(new AlarmCallback()); 
    pCharAlarm->addDescriptor(new BLE2902());

    pCharTask = pService->createCharacteristic(CHAR_UUID_TASK, 
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
    pCharTask->setCallbacks(new TaskCallback()); 
    pCharTask->addDescriptor(new BLE2902());

    pCharStatus = pService->createCharacteristic(CHAR_UUID_STATUS, 
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
    pCharStatus->addDescriptor(new BLE2902());

    pService->start();
    pAdvertising = BLEDevice::getAdvertising();
    pAdvertising->addServiceUUID(SERVICE_UUID);
    pAdvertising->setScanResponse(true);
    pAdvertising->setMinPreferred(0x00); 
    pAdvertising->setMaxPreferred(0x00);
    BLEDevice::startAdvertising();

    lastActivityTime = millis();
    showHomeScreen();
}

// === ОБНОВЛЕНИЕ КНОПОК ===
void updateButtons() {
    if (M5.BtnA.isPressed() && !btnA_pressed) {
        btnA_pressed = true;
        btnA_pressTime = millis();
    }
    if (!M5.BtnA.isPressed() && btnA_pressed) {
        btnA_pressed = false;
    }
    
    if (M5.BtnB.isPressed() && !btnB_pressed) {
        btnB_pressed = true;
        btnB_pressTime = millis();
    }
    if (!M5.BtnB.isPressed() && btnB_pressed) {
        btnB_pressed = false;
    }
}

bool btnA_shortPress() {
    if (!btnA_pressed && btnA_pressTime > 0) {
        unsigned long duration = millis() - btnA_pressTime;
        if (duration < 800 && duration > 50) {
            btnA_pressTime = 0;
            return true;
        }
        btnA_pressTime = 0;
    }
    return false;
}

bool btnB_shortPress() {
    if (!btnB_pressed && btnB_pressTime > 0) {
        unsigned long duration = millis() - btnB_pressTime;
        if (duration < 800 && duration > 50) {
            btnB_pressTime = 0;
            return true;
        }
        btnB_pressTime = 0;
    }
    return false;
}

bool btnB_longPress() {
    if (btnB_pressed && (millis() - btnB_pressTime) >= 2000) {
        btnB_pressTime = 0;
        return true;
    }
    return false;
}

bool bothButtonsPressed() {
    return M5.BtnA.isPressed() && M5.BtnB.isPressed();
}

void toggleDisplay() {
    displayEnabled = !displayEnabled;
    if (displayEnabled) {
        wakeDisplay();
    } else {
        M5.Display.sleep();
        M5.Display.fillScreen(TFT_BLACK);
    }
}

void wakeDisplay() {
    if (!displayEnabled) {
        displayEnabled = true;
    }
    M5.Display.wakeup();
    M5.Display.setRotation(1);
    lastActivityTime = millis();
}

// === БУДИЛЬНИК: Начать звонить ===
void startAlarmRing() {
    alarmRinging = true;
    alarmLastBeep = millis();
    alarmLastBlink = millis();
    alarmDisplayOn = true;
    currentState = STATE_ALARM_RINGING;
    M5.Speaker.tone(2000, 300);  // Первый сигнал
    Serial.println("[ALARM] Started ringing!");
}

// === БУДИЛЬНИК: Остановить ===
void stopAlarmRing() {
    alarmRinging = false;
    M5.Speaker.end();
    currentState = STATE_HOME;
    showHomeScreen();
    Serial.println("[ALARM] Stopped by user");
}

// === БУДИЛЬНИК: Обработка звонка ===
void handleAlarmRinging() {
    // Звук каждые 500мс
    if (millis() - alarmLastBeep >= 500) {
        M5.Speaker.tone(2000, 200);
        alarmLastBeep = millis();
    }
    
    // Мигание экрана каждые 500мс
    if (millis() - alarmLastBlink >= 500) {
        alarmDisplayOn = !alarmDisplayOn;
        if (alarmDisplayOn) {
            M5.Display.fillScreen(TFT_RED);
            M5.Display.setTextColor(TFT_WHITE);
        } else {
            M5.Display.fillScreen(TFT_BLACK);
            M5.Display.setTextColor(TFT_RED);
        }
        M5.Display.setTextSize(3);
        M5.Display.setCursor(20, 40);
        M5.Display.print("ALARM!");
        
        // Показываем время будильника
        M5.Display.setTextSize(2);
        M5.Display.setCursor(40, 80);
        // Найти активный будильник
        for(int i=0; i<rtc_alarmCount; i++) {
            if(rtc_alarms[i].triggered) {
                M5.Display.printf("%02d:%02d", rtc_alarms[i].hour, rtc_alarms[i].minute);
                break;
            }
        }
        
        alarmLastBlink = millis();
    }
    
    // Кнопка A = остановить будильник
    if (btnA_shortPress()) {
        stopAlarmRing();
        delay(200);
        return;
    }
    
    // Кнопка B тоже может остановить (для удобства)
    if (btnB_shortPress()) {
        stopAlarmRing();
        delay(200);
        return;
    }
    
    delay(50);
}

// === ГЛАВНЫЙ ЦИКЛ ===
void loop() {
    M5.update();
    updateButtons();

    // === ПРОВЕРКА: БУДИЛЬНИК ЗВОНИТ ===
    if (alarmRinging) {
        handleAlarmRinging();
        return;  // === ВАЖНО: Ничего больше не обрабатываем пока будильник звонит ===
    }

    // === ВКЛ/ВЫКЛ ЭКРАНА (A+B одновременно) ===
    if (bothButtonsPressed()) {
        toggleDisplay();
        delay(200);
        return;
    }

    // Если экран выключен - не обрабатываем остальное
    if (!displayEnabled) {
        delay(100);
        return;
    }

    // === ВОЗВРАТ ДОМОЙ (Удержание B 2 сек) ===
    if (currentState != STATE_HOME && btnB_longPress()) {
        goToHome();
        delay(200);
        return;
    }

    // === ВЫПОЛНЕНИЕ ЗАДАЧИ ===
    if (currentState == STATE_TASK_RUN) {
        handleTaskRunner();
        return;
    }

    // === СПИСОК ЗАДАЧ ===
    if (currentState == STATE_TASK_LIST) {
        if (rtc_taskCount == 0) { 
            goToHome(); 
            return; 
        }
        
        // Кнопка A = ЗАПУСТИТЬ задачу
        if (btnA_shortPress()) {
            startTask(listSelectionIndex);
            lastActivityTime = millis();
            delay(150);
            return;
        }
        
        // Кнопка B = Следующая задача
        if (btnB_shortPress()) {
            listSelectionIndex++;
            if (listSelectionIndex >= rtc_taskCount) listSelectionIndex = 0;
            showTaskListScreen();
            lastActivityTime = millis();
            delay(150);
            return;
        }
        
        delay(50);
        return;
    }

    // === ГЛАВНАЯ (ЧАСЫ) ===
    static unsigned long lastUpdate = 0;
    if (millis() - lastUpdate >= 1000) {
        rtc_currentTimeSec++;
        if (rtc_currentTimeSec >= 24*3600) { 
            rtc_currentTimeSec=0;
            // Сброс триггеров будильников в полночь
            for(int i=0; i<rtc_alarmCount; i++) {
                rtc_alarms[i].triggered = false;
            }
        }
        lastUpdate = millis();
        checkAlarms();
        showHomeScreen();
        
        if(deviceConnected && pCharStatus) {
            char s[30]; 
            snprintf(s,30,"%02d:%02d:%02d",
                (rtc_currentTimeSec/3600)%24,
                (rtc_currentTimeSec/60)%60,
                rtc_currentTimeSec%60);
            pCharStatus->setValue(s); 
            pCharStatus->notify();
        }
    }

    // Кнопка B: Вход в список задач
    if (btnB_shortPress() && rtc_taskCount > 0) {
        currentState = STATE_TASK_LIST;
        listSelectionIndex = 0;
        showTaskListScreen();
        lastActivityTime = millis();
        delay(150);
        return;
    }
    
    // Кнопка A на главном ничего не делает
    
    delay(50);
}

// === ПРОВЕРКА БУДИЛЬНИКОВ ===
void checkAlarms() {
    int h = (rtc_currentTimeSec / 3600) % 24;
    int m = (rtc_currentTimeSec / 60) % 60;
    
    for(int i=0; i<rtc_alarmCount; i++) {
        if(rtc_alarms[i].enabled && !rtc_alarms[i].triggered && 
           rtc_alarms[i].hour==h && rtc_alarms[i].minute==m) {
            rtc_alarms[i].triggered = true;
            startAlarmRing();  // === ЗАПУСКАЕМ НЕПРЕРЫВНЫЙ ЗВОНОК ===
            return;
        }
    }
}

// === ЛОГИКА ТАЙМЕРА ===
void handleTaskRunner() {
    if (!isTaskPaused) {
        if (millis() - lastTimerTick >= 1000) {
            if (taskRemainingSec > 0) {
                taskRemainingSec--;
                lastTimerTick = millis();
            } else {
                completeTask();
                return;
            }
        }
    }

    static int viewMode = 0;
    
    if (btnB_shortPress()) {
        viewMode = (viewMode + 1) % 3;
        if(viewMode==2) { lastAnimFrame=millis(); animFrame=0; }
        if(viewMode==0) showTaskRunScreen_Timer();
        else if(viewMode==1) showTaskRunScreen_Desc();
        else showTaskRunScreen_Anim();
        lastActivityTime = millis();
        delay(150);
        return;
    }

    if (btnA_shortPress()) {
        if (isTaskPaused) { resumeTask(); } 
        else { stopTask(); }
        lastActivityTime = millis();
        delay(150);
        return;
    }

    if (viewMode == 0 && !isTaskPaused) {
        showTaskRunScreen_Timer();
    }
    
    if (viewMode == 2) {
        if (millis() - lastAnimFrame >= 250) {
            animFrame++;
            showTaskRunScreen_Anim();
            lastAnimFrame = millis();
        }
    }
    
    delay(50);
}

void startTask(int index) {
    if (index < 0 || index >= rtc_taskCount) return;
    currentTaskIndex = index;
    
    uint8_t dur = rtc_tasks[index].duration;
    if (dur == 1) taskRemainingSec = 15 * 60;
    else if (dur == 2) taskRemainingSec = 30 * 60;
    else if (dur == 3) taskRemainingSec = 45 * 60;
    else taskRemainingSec = 15 * 60;
    
    isTaskPaused = false;
    currentState = STATE_TASK_RUN;
    
    M5.Speaker.tone(1500, 100);
    showTaskRunScreen_Timer();
}

void stopTask() {
    isTaskPaused = true;
    taskRemainingSec += 300;
    M5.Speaker.tone(400, 200);
    showTaskRunScreen_Timer();
}

void resumeTask() {
    isTaskPaused = false;
    lastTimerTick = millis();
    M5.Speaker.tone(1000, 100);
    showTaskRunScreen_Timer();
}

void completeTask() {
    M5.Speaker.tone(2000, 500);
    M5.Speaker.tone(2500, 500);
    
    for (int i = currentTaskIndex; i < rtc_taskCount - 1; i++) {
        rtc_tasks[i] = rtc_tasks[i+1];
    }
    rtc_taskCount--;
    if (listSelectionIndex >= rtc_taskCount) listSelectionIndex = max(0, (int)rtc_taskCount-1);
    
    currentState = STATE_HOME;
    M5.Display.fillScreen(TFT_WHITE);
    M5.Display.setTextColor(TFT_BLACK);
    M5.Display.setCursor(20, 40);
    M5.Display.setTextSize(3);
    M5.Display.print("DONE!");
    delay(1500);
    goToHome();
}

void goToHome() {
    currentState = STATE_HOME;
    currentTaskIndex = -1;
    isTaskPaused = false;
    wakeDisplay();
    showHomeScreen();
}

// === ОТРИСОВКА АНИМАЦИЙ ===
void drawCharacter(int x, int y, int frame, uint8_t animType) {
    switch(animType) {
        case ANIM_RUNNING: drawRunningChar(x, y, frame); break;
        case ANIM_WORKING: drawWorkingChar(x, y, frame); break;
        case ANIM_READING: drawReadingChar(x, y, frame); break;
        case ANIM_EXERCISE: drawExerciseChar(x, y, frame); break;
        case ANIM_WALKING: drawWalkingChar(x, y, frame); break;
        default: drawRunningChar(x, y, frame);
    }
}

void drawRunningChar(int x, int y, int frame) {
    M5.Display.setTextColor(TFT_WHITE);
    M5.Display.fillCircle(x+8, y+4, 3, TFT_WHITE);
    M5.Display.drawLine(x+8, y+7, x+8, y+14, TFT_WHITE);
    if (frame % 2 == 0) {
        M5.Display.drawLine(x+8, y+14, x+4, y+20, TFT_WHITE);
        M5.Display.drawLine(x+8, y+14, x+12, y+18, TFT_WHITE);
        M5.Display.drawLine(x+8, y+9, x+4, y+12, TFT_WHITE);
        M5.Display.drawLine(x+8, y+9, x+12, y+11, TFT_WHITE);
    } else {
        M5.Display.drawLine(x+8, y+14, x+12, y+20, TFT_WHITE);
        M5.Display.drawLine(x+8, y+14, x+4, y+18, TFT_WHITE);
        M5.Display.drawLine(x+8, y+9, x+12, y+12, TFT_WHITE);
        M5.Display.drawLine(x+8, y+9, x+4, y+11, TFT_WHITE);
    }
}

void drawWorkingChar(int x, int y, int frame) {
    M5.Display.setTextColor(TFT_WHITE);
    M5.Display.fillCircle(x+8, y+4, 3, TFT_WHITE);
    M5.Display.drawLine(x+8, y+7, x+8, y+14, TFT_WHITE);
    M5.Display.drawLine(x+8, y+14, x+6, y+18, TFT_WHITE);
    M5.Display.drawLine(x+8, y+14, x+10, y+18, TFT_WHITE);
    M5.Display.drawLine(x+8, y+9, x+12, y+11, TFT_WHITE);
    M5.Display.drawLine(x+8, y+9, x+14, y+11, TFT_WHITE);
    M5.Display.drawRect(x+16, y+6, 6, 8, TFT_WHITE);
    if (frame % 4 < 2) {
        M5.Display.fillRect(x+17, y+7, 4, 6, TFT_WHITE);
    }
}

void drawReadingChar(int x, int y, int frame) {
    M5.Display.setTextColor(TFT_WHITE);
    M5.Display.fillCircle(x+6, y+5, 3, TFT_WHITE);
    M5.Display.drawLine(x+8, y+8, x+8, y+15, TFT_WHITE);
    M5.Display.drawLine(x+8, y+15, x+5, y+20, TFT_WHITE);
    M5.Display.drawLine(x+8, y+15, x+11, y+20, TFT_WHITE);
    M5.Display.fillRect(x+4, y+10, 8, 5, TFT_WHITE);
    if (frame % 4 < 2) {
        M5.Display.drawLine(x+5, y+11, x+5, y+14, TFT_BLACK);
    } else {
        M5.Display.drawLine(x+8, y+11, x+8, y+14, TFT_BLACK);
    }
}

void drawExerciseChar(int x, int y, int frame) {
    M5.Display.setTextColor(TFT_WHITE);
    M5.Display.fillCircle(x+8, y+4, 3, TFT_WHITE);
    M5.Display.drawLine(x+8, y+7, x+8, y+14, TFT_WHITE);
    if (frame % 3 == 0) {
        M5.Display.drawLine(x+8, y+14, x+5, y+19, TFT_WHITE);
        M5.Display.drawLine(x+8, y+14, x+11, y+19, TFT_WHITE);
    } else if (frame % 3 == 1) {
        M5.Display.drawLine(x+8, y+14, x+4, y+20, TFT_WHITE);
        M5.Display.drawLine(x+8, y+14, x+12, y+20, TFT_WHITE);
    } else {
        M5.Display.drawLine(x+8, y+14, x+6, y+21, TFT_WHITE);
        M5.Display.drawLine(x+8, y+14, x+10, y+21, TFT_WHITE);
    }
    M5.Display.drawLine(x+8, y+9, x+5, y+6, TFT_WHITE);
    M5.Display.drawLine(x+8, y+9, x+11, y+6, TFT_WHITE);
}

void drawWalkingChar(int x, int y, int frame) {
    M5.Display.setTextColor(TFT_WHITE);
    M5.Display.fillCircle(x+8, y+4, 3, TFT_WHITE);
    M5.Display.drawLine(x+8, y+7, x+8, y+14, TFT_WHITE);
    if (frame % 4 < 2) {
        M5.Display.drawLine(x+8, y+14, x+5, y+19, TFT_WHITE);
        M5.Display.drawLine(x+8, y+14, x+10, y+19, TFT_WHITE);
    } else {
        M5.Display.drawLine(x+8, y+14, x+10, y+19, TFT_WHITE);
        M5.Display.drawLine(x+8, y+14, x+5, y+19, TFT_WHITE);
    }
    M5.Display.drawLine(x+8, y+9, x+5, y+12, TFT_WHITE);
    M5.Display.drawLine(x+8, y+9, x+11, y+12, TFT_WHITE);
}

// === ЭКРАНЫ ===
void showHomeScreen() {
    if (!displayEnabled) return;
    
    int h = (rtc_currentTimeSec / 3600) % 24;
    int m = (rtc_currentTimeSec / 60) % 60;
    int s = rtc_currentTimeSec % 60;
    
    M5.Display.fillScreen(TFT_BLACK);
    M5.Display.setTextColor(TFT_WHITE);
    
    M5.Display.setTextSize(4);
    char ts[10]; snprintf(ts,10,"%02d:%02d",h,m);
    int w = M5.Display.textWidth(ts);
    M5.Display.setCursor((M5.Display.width()-w)/2, 40);
    M5.Display.print(ts);
    
    M5.Display.setTextSize(2);
    char ss[4]; snprintf(ss,4,":%02d",s);
    M5.Display.print(ss);
    
    M5.Display.setTextSize(1);
    M5.Display.setCursor(5, M5.Display.height()-20);
    M5.Display.printf("Tasks:%d | Alarms:%d", rtc_taskCount, rtc_alarmCount);
    M5.Display.setCursor(M5.Display.width()-60, M5.Display.height()-20);
    M5.Display.print(deviceConnected?"BLE:ON":"BLE:OFF");
    
    M5.Display.setCursor(5, M5.Display.height()-5);
    M5.Display.print("A+B=Display | B=Tasks");
}

void showTaskListScreen() {
    if (!displayEnabled) return;
    
    M5.Display.fillScreen(TFT_BLACK);
    M5.Display.setTextColor(TFT_WHITE);
    M5.Display.setTextSize(2);
    M5.Display.setCursor(5, 5);
    M5.Display.print("SELECT TASK");
    M5.Display.drawLine(0, 25, M5.Display.width(), 25, TFT_WHITE);
    
    M5.Display.setTextSize(1);
    int y = 35;
    for(int i=0; i<rtc_taskCount; i++) {
        if (i == listSelectionIndex) {
            M5.Display.fillRect(0, y-2, M5.Display.width(), 14, TFT_WHITE);
            M5.Display.setTextColor(TFT_BLACK);
        } else {
            M5.Display.setTextColor(TFT_WHITE);
        }
        M5.Display.setCursor(5, y);
        M5.Display.printf("%d. %02d:%02d %s", i+1, rtc_tasks[i].hour, rtc_tasks[i].minute, rtc_tasks[i].name);
        y += 16;
    }
    
    M5.Display.setTextColor(TFT_WHITE);
    M5.Display.setCursor(5, M5.Display.height()-15);
    M5.Display.print("A=Start | B=Next | Hold B:Home");
}

void showTaskRunScreen_Timer() {
    if (!displayEnabled) return;
    
    M5.Display.fillScreen(TFT_BLACK);
    M5.Display.setTextColor(TFT_WHITE);
    
    M5.Display.setTextSize(1);
    M5.Display.setCursor(5, 5);
    if (currentTaskIndex >=0 && currentTaskIndex < rtc_taskCount)
        M5.Display.print(rtc_tasks[currentTaskIndex].name);
    else M5.Display.print("Task");
    
    if (isTaskPaused) {
        M5.Display.setCursor(M5.Display.width()/2 - 30, 25);
        M5.Display.setTextSize(2);
        M5.Display.print("PAUSED");
        M5.Display.setTextSize(1);
        M5.Display.setCursor(M5.Display.width()/2 - 40, 45);
        M5.Display.print("(+5 min)");
    }
    
    int mm = taskRemainingSec / 60;
    int ss = taskRemainingSec % 60;
    M5.Display.setTextSize(4);
    char ts[10]; snprintf(ts, 10, "%02d:%02d", mm, ss);
    int w = M5.Display.textWidth(ts);
    M5.Display.setCursor((M5.Display.width()-w)/2, 80);
    M5.Display.print(ts);
    
    M5.Display.setTextSize(1);
    M5.Display.setCursor(5, M5.Display.height()-20);
    M5.Display.print(isTaskPaused ? "A=Resume" : "A=Pause");
    M5.Display.setCursor(M5.Display.width()-80, M5.Display.height()-20);
    M5.Display.print("B=View");
}

void showTaskRunScreen_Desc() {
    if (!displayEnabled) return;
    if (currentTaskIndex < 0 || currentTaskIndex >= rtc_taskCount) return;
    Task &t = rtc_tasks[currentTaskIndex];
    
    M5.Display.fillScreen(TFT_BLACK);
    M5.Display.setTextColor(TFT_WHITE);
    
    M5.Display.setTextSize(1);
    M5.Display.setCursor(5, 5);
    M5.Display.printf("Time: %02d:%02d", taskRemainingSec/60, taskRemainingSec%60);
    if(isTaskPaused) M5.Display.print(" (PAUSED)");
    
    M5.Display.drawLine(0, 20, M5.Display.width(), 20, TFT_WHITE);
    
    M5.Display.setTextSize(2);
    M5.Display.setCursor(5, 30);
    String name = String(t.name);
    if(M5.Display.textWidth(name) > M5.Display.width()-10) {
        while(M5.Display.textWidth(name+"...") > M5.Display.width()-10 && name.length()>0) name.remove(name.length()-1);
        name += "...";
    }
    M5.Display.print(name);
    
    M5.Display.setTextSize(1);
    M5.Display.setCursor(5, 60);
    M5.Display.print("Desc:");
    
    int y = 75;
    String desc = String(t.description);
    int cpl = (M5.Display.width()-10) / 6;
    while(desc.length()>0 && y < M5.Display.height()-30) {
        String line;
        if(desc.length()<=cpl) { line=desc; desc=""; }
        else {
            int sp = desc.lastIndexOf(' ', cpl);
            if(sp==-1) sp=cpl;
            line=desc.substring(0,sp);
            desc=desc.substring(sp+1);
        }
        M5.Display.setCursor(5,y);
        M5.Display.print(line);
        y+=15;
    }
    
    M5.Display.setCursor(5, M5.Display.height()-10);
    M5.Display.print("B=Next | A=Pause/Start");
}

void showTaskRunScreen_Anim() {
    if (!displayEnabled) return;
    if (currentTaskIndex < 0 || currentTaskIndex >= rtc_taskCount) return;
    
    M5.Display.fillScreen(TFT_BLACK);
    M5.Display.setTextColor(TFT_WHITE);
    
    M5.Display.setTextSize(1);
    M5.Display.setCursor(5, 5);
    M5.Display.print(rtc_tasks[currentTaskIndex].name);
    
    int mm = taskRemainingSec / 60;
    int ss = taskRemainingSec % 60;
    M5.Display.setTextSize(2);
    char ts[10]; snprintf(ts, 10, "%02d:%02d", mm, ss);
    M5.Display.setCursor(M5.Display.width()-60, 5);
    M5.Display.print(ts);
    
    Task &t = rtc_tasks[currentTaskIndex];
    drawCharacter(100, 50, animFrame, t.animation);
    
    M5.Display.setTextSize(1);
    M5.Display.setCursor(5, 100);
    const char* animNames[] = {"None", "Running", "Working", "Reading", "Exercise", "Walking"};
    M5.Display.print("Anim: ");
    M5.Display.print(animNames[t.animation]);
    
    M5.Display.setCursor(5, M5.Display.height()-10);
    M5.Display.print("B=Desc | A=Pause/Start");
}

void initDefaults() {
    rtc_alarmCount=0; 
    rtc_taskCount=0; 
    taskReceiveIndex=0; 
    waitingForTasks=false;
    alarmRinging = false;
    for(int i=0;i<MAX_TASKS;i++) rtc_tasks[i].active=false;
}