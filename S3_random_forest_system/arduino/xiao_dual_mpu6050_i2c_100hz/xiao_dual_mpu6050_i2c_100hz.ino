#include <Arduino.h>
#include <Wire.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// XIAO ESP32S3 + two GY-521 / MPU6050 modules on one I2C bus.
//
// Both MPU6050 VCC -> XIAO 3V3
// Both MPU6050 GND -> XIAO GND
// Both MPU6050 SDA -> XIAO D4 / GPIO5 / SDA
// Both MPU6050 SCL -> XIAO D5 / GPIO6 / SCL
//
// IMPORTANT:
// Left  MPU6050 AD0 -> GND, address 0x68
// Right MPU6050 AD0 -> 3V3, address 0x69

#define SDA_PIN 5
#define SCL_PIN 6

#define LEFT_MPU_ADDR 0x68
#define RIGHT_MPU_ADDR 0x69
#define USB_BAUD 115200
#define BLE_DEVICE_NAME "ChewTune-S3"
#define BLE_SERVICE_UUID "7b100001-7c6a-4d91-a461-9c987d97b100"
#define BLE_UI_DATA_UUID "7b100002-7c6a-4d91-a461-9c987d97b100"

#define REG_SMPLRT_DIV 0x19
#define REG_CONFIG 0x1A
#define REG_GYRO_CONFIG 0x1B
#define REG_ACCEL_CONFIG 0x1C
#define REG_ACCEL_XOUT_H 0x3B
#define REG_PWR_MGMT_1 0x6B
#define REG_WHO_AM_I 0x75

const unsigned long SAMPLE_INTERVAL_US = 10000;  // 100 Hz
unsigned long lastSampleUs = 0;
unsigned long lastHeartbeatMs = 0;
unsigned long lastNotifyMs = 0;

BLEServer *bleServer = nullptr;
BLECharacteristic *uiDataCharacteristic = nullptr;
bool phoneConnected = false;
char computerMessage[96];
size_t computerMessageLength = 0;
const size_t NOTIFY_QUEUE_SIZE = 8;
const unsigned long NOTIFY_INTERVAL_MS = 35;
char notifyQueue[NOTIFY_QUEUE_SIZE][96];
size_t notifyQueueHead = 0;
size_t notifyQueueTail = 0;
size_t notifyQueueCount = 0;

struct ImuSample {
  float ax;
  float ay;
  float az;
  float gx;
  float gy;
  float gz;
};

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *server) override {
    phoneConnected = true;
    Serial.println("BLE: Phone connected");
  }

  void onDisconnect(BLEServer *server) override {
    phoneConnected = false;
    notifyQueueHead = 0;
    notifyQueueTail = 0;
    notifyQueueCount = 0;
    Serial.println("BLE: Phone disconnected");
    delay(100);
    server->startAdvertising();
  }
};

class UiDataCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *characteristic) override {
    String value = characteristic->getValue();
    if (value.startsWith("C,") && value.length() < 48) {
      Serial.println(value);
    }
  }
};

void enqueuePhoneMessage(const char *message) {
  if (!phoneConnected || message == nullptr) return;
  if (notifyQueueCount >= NOTIFY_QUEUE_SIZE) {
    notifyQueueHead = (notifyQueueHead + 1) % NOTIFY_QUEUE_SIZE;
    notifyQueueCount--;
  }
  strncpy(notifyQueue[notifyQueueTail], message, sizeof(notifyQueue[notifyQueueTail]) - 1);
  notifyQueue[notifyQueueTail][sizeof(notifyQueue[notifyQueueTail]) - 1] = '\0';
  notifyQueueTail = (notifyQueueTail + 1) % NOTIFY_QUEUE_SIZE;
  notifyQueueCount++;
}

void flushPhoneNotifications() {
  if (
    !phoneConnected ||
    uiDataCharacteristic == nullptr ||
    notifyQueueCount == 0 ||
    millis() - lastNotifyMs < NOTIFY_INTERVAL_MS
  ) {
    return;
  }
  lastNotifyMs = millis();
  const char *message = notifyQueue[notifyQueueHead];
  uiDataCharacteristic->setValue((uint8_t *)message, strlen(message));
  uiDataCharacteristic->notify();
  notifyQueueHead = (notifyQueueHead + 1) % NOTIFY_QUEUE_SIZE;
  notifyQueueCount--;
}

void setupBLE() {
  BLEDevice::init(BLE_DEVICE_NAME);
  bleServer = BLEDevice::createServer();
  bleServer->setCallbacks(new ServerCallbacks());

  BLEService *service = bleServer->createService(BLE_SERVICE_UUID);
  uiDataCharacteristic = service->createCharacteristic(
    BLE_UI_DATA_UUID,
    BLECharacteristic::PROPERTY_READ |
      BLECharacteristic::PROPERTY_NOTIFY |
      BLECharacteristic::PROPERTY_WRITE |
      BLECharacteristic::PROPERTY_WRITE_NR
  );
  uiDataCharacteristic->setCallbacks(new UiDataCallbacks());
  uiDataCharacteristic->addDescriptor(new BLE2902());
  uiDataCharacteristic->setValue("READY");
  service->start();

  BLEAdvertising *advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(BLE_SERVICE_UUID);
  advertising->setScanResponse(true);
  advertising->start();
  Serial.println("BLE: Advertising as ChewTune-S3");
}

void readComputerMessages() {
  while (Serial.available() > 0) {
    char value = (char)Serial.read();
    if (value == '\n') {
      computerMessage[computerMessageLength] = '\0';
      if (
        computerMessageLength >= 2 &&
        (computerMessage[0] == 'U' || computerMessage[0] == 'M') &&
        computerMessage[1] == ','
      ) {
        enqueuePhoneMessage(computerMessage);
      }
      computerMessageLength = 0;
    } else if (value != '\r') {
      if (computerMessageLength < sizeof(computerMessage) - 1) {
        computerMessage[computerMessageLength++] = value;
      } else {
        computerMessageLength = 0;
      }
    }
  }
}

void sendBleHeartbeat() {
  if (!phoneConnected || millis() - lastHeartbeatMs < 1000) return;
  lastHeartbeatMs = millis();
  char heartbeat[20];
  snprintf(heartbeat, sizeof(heartbeat), "H,%lu", lastHeartbeatMs);
  enqueuePhoneMessage(heartbeat);
}

int16_t toInt16BE(uint8_t high, uint8_t low) {
  return (int16_t)((high << 8) | low);
}

bool writeRegister(uint8_t addr, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool readRegisters(uint8_t addr, uint8_t startReg, uint8_t *buffer, uint8_t len) {
  Wire.beginTransmission(addr);
  Wire.write(startReg);

  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  uint8_t received = Wire.requestFrom(addr, len);
  if (received != len) {
    return false;
  }

  for (uint8_t i = 0; i < len; i++) {
    buffer[i] = Wire.read();
  }

  return true;
}

bool readRegister(uint8_t addr, uint8_t reg, uint8_t &value) {
  return readRegisters(addr, reg, &value, 1);
}

bool setupMPU6050(uint8_t addr, const char *name) {
  uint8_t whoAmI = 0;
  if (!readRegister(addr, REG_WHO_AM_I, whoAmI)) {
    Serial.print("ERROR: ");
    Serial.print(name);
    Serial.print(" MPU6050 not found at 0x");
    Serial.println(addr, HEX);
    return false;
  }

  Serial.print(name);
  Serial.print(" MPU6050 WHO_AM_I: 0x");
  Serial.println(whoAmI, HEX);

  if (whoAmI != 0x68 && whoAmI != 0x69) {
    return false;
  }

  if (!writeRegister(addr, REG_PWR_MGMT_1, 0x01)) return false;
  delay(100);
  if (!writeRegister(addr, REG_SMPLRT_DIV, 0x09)) return false;
  if (!writeRegister(addr, REG_CONFIG, 0x03)) return false;
  if (!writeRegister(addr, REG_GYRO_CONFIG, 0x08)) return false;   // +/-500 deg/s
  if (!writeRegister(addr, REG_ACCEL_CONFIG, 0x08)) return false;  // +/-4 g

  return true;
}

bool readSixAxis(uint8_t addr, ImuSample &sample) {
  uint8_t data[14];

  if (!readRegisters(addr, REG_ACCEL_XOUT_H, data, 14)) {
    return false;
  }

  int16_t rawAx = toInt16BE(data[0], data[1]);
  int16_t rawAy = toInt16BE(data[2], data[3]);
  int16_t rawAz = toInt16BE(data[4], data[5]);
  int16_t rawGx = toInt16BE(data[8], data[9]);
  int16_t rawGy = toInt16BE(data[10], data[11]);
  int16_t rawGz = toInt16BE(data[12], data[13]);

  sample.ax = rawAx / 8192.0f;
  sample.ay = rawAy / 8192.0f;
  sample.az = rawAz / 8192.0f;

  const float gyroScaleRad = (PI / 180.0f) / 65.5f;
  sample.gx = rawGx * gyroScaleRad;
  sample.gy = rawGy * gyroScaleRad;
  sample.gz = rawGz * gyroScaleRad;

  return true;
}

void printSample(const ImuSample &sample) {
  Serial.print(sample.ax, 6);
  Serial.print(",");
  Serial.print(sample.ay, 6);
  Serial.print(",");
  Serial.print(sample.az, 6);
  Serial.print(",");
  Serial.print(sample.gx, 6);
  Serial.print(",");
  Serial.print(sample.gy, 6);
  Serial.print(",");
  Serial.print(sample.gz, 6);
}

void setup() {
  Serial.begin(USB_BAUD);
  delay(2000);

  Serial.println("BOOT: XIAO ESP32S3 + dual GY-521 MPU6050 I2C 100Hz");

  setupBLE();

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);

  bool leftOk = setupMPU6050(LEFT_MPU_ADDR, "LEFT");
  bool rightOk = setupMPU6050(RIGHT_MPU_ADDR, "RIGHT");

  if (!leftOk || !rightOk) {
    Serial.println("ERROR: Dual MPU6050 setup incomplete.");
    Serial.println("Check AD0: LEFT->GND address 0x68, RIGHT->3V3 address 0x69.");
  } else {
    Serial.println("Both MPU6050 sensors connected.");
  }

  Serial.println(
    "time_ms,"
    "l_ax,l_ay,l_az,l_gx,l_gy,l_gz,"
    "r_ax,r_ay,r_az,r_gx,r_gy,r_gz"
  );
}

void loop() {
  readComputerMessages();
  flushPhoneNotifications();
  sendBleHeartbeat();

  unsigned long nowUs = micros();

  if (nowUs - lastSampleUs >= SAMPLE_INTERVAL_US) {
    lastSampleUs = nowUs;

    ImuSample left;
    ImuSample right;
    bool leftOk = readSixAxis(LEFT_MPU_ADDR, left);
    bool rightOk = readSixAxis(RIGHT_MPU_ADDR, right);

    if (leftOk && rightOk) {
      Serial.print(millis());
      Serial.print(",");
      printSample(left);
      Serial.print(",");
      printSample(right);
      Serial.println();
    } else {
      Serial.print("ERROR: I2C read failed left=");
      Serial.print(leftOk ? "ok" : "fail");
      Serial.print(" right=");
      Serial.println(rightOk ? "ok" : "fail");
    }
  }
}
