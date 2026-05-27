#include <Arduino.h>
#include <Wire.h>

#define SDA_PIN 21
#define SCL_PIN 22

#define IMU_ADDR 0x23
#define USB_BAUD 115200

const unsigned long SAMPLE_INTERVAL_MS = 10;  // 100 Hz
unsigned long lastSampleMs = 0;
unsigned long lastErrorMs = 0;
uint8_t lastFailedRegister = 0;
uint8_t lastRequestedLength = 0;
uint8_t lastReceivedLength = 0;
uint8_t lastTransmissionError = 0;

void scanI2CBus() {
  Serial.println("I2C scan start...");
  byte found = 0;

  for (byte address = 1; address < 127; address++) {
    Wire.beginTransmission(address);
    byte error = Wire.endTransmission();

    if (error == 0) {
      Serial.print("I2C device found at 0x");
      if (address < 16) {
        Serial.print("0");
      }
      Serial.println(address, HEX);
      found++;
    }
  }

  if (found == 0) {
    Serial.println("No I2C devices found.");
  }
  Serial.println("I2C scan done.");
}

int16_t toInt16LE(uint8_t low, uint8_t high) {
  return (int16_t)((high << 8) | low);
}

bool readRegisters(uint8_t startReg, uint8_t *buffer, uint8_t len) {
  lastFailedRegister = startReg;
  lastRequestedLength = len;
  lastReceivedLength = 0;
  lastTransmissionError = 0;

  Wire.beginTransmission(IMU_ADDR);
  Wire.write(startReg);

  lastTransmissionError = Wire.endTransmission(true);
  if (lastTransmissionError != 0) {
    return false;
  }

  uint8_t received = Wire.requestFrom(IMU_ADDR, len);
  lastReceivedLength = received;
  if (received != len) {
    return false;
  }

  for (uint8_t i = 0; i < len; i++) {
    buffer[i] = Wire.read();
  }

  return true;
}

void dumpRegisterRange(uint8_t firstReg, uint8_t lastReg) {
  Serial.print("Register dump 0x");
  if (firstReg < 16) {
    Serial.print("0");
  }
  Serial.print(firstReg, HEX);
  Serial.print("-0x");
  if (lastReg < 16) {
    Serial.print("0");
  }
  Serial.println(lastReg, HEX);

  for (uint8_t reg = firstReg; reg <= lastReg; reg++) {
    uint8_t value = 0;
    Serial.print("0x");
    if (reg < 16) {
      Serial.print("0");
    }
    Serial.print(reg, HEX);
    Serial.print(": ");
    if (readRegisters(reg, &value, 1)) {
      Serial.println(value, HEX);
    } else {
      Serial.print("read failed, txErr=");
      Serial.print(lastTransmissionError);
      Serial.print(", got=");
      Serial.print(lastReceivedLength);
      Serial.print("/");
      Serial.println(lastRequestedLength);
    }
  }
}

bool readSixAxis(float &ax, float &ay, float &az,
                 float &gx, float &gy, float &gz) {
  uint8_t axData[2];
  uint8_t ayData[2];
  uint8_t azData[2];
  uint8_t gxData[2];
  uint8_t gyData[2];
  uint8_t gzData[2];

  // Read each 16-bit axis separately. This is more compatible than one
  // 12-byte burst read on some I2C IMU modules.
  if (!readRegisters(0x04, axData, 2) ||
      !readRegisters(0x06, ayData, 2) ||
      !readRegisters(0x08, azData, 2) ||
      !readRegisters(0x0A, gxData, 2) ||
      !readRegisters(0x0C, gyData, 2) ||
      !readRegisters(0x0E, gzData, 2)) {
    return false;
  }

  int16_t raw_ax = toInt16LE(axData[0], axData[1]);
  int16_t raw_ay = toInt16LE(ayData[0], ayData[1]);
  int16_t raw_az = toInt16LE(azData[0], azData[1]);
  int16_t raw_gx = toInt16LE(gxData[0], gxData[1]);
  int16_t raw_gy = toInt16LE(gyData[0], gyData[1]);
  int16_t raw_gz = toInt16LE(gzData[0], gzData[1]);

  const float accRatio = 16.0f / 32767.0f;  // g
  const float gyroRatio = (2000.0f / 32767.0f) * (PI / 180.0f);  // rad/s

  ax = raw_ax * accRatio;
  ay = raw_ay * accRatio;
  az = raw_az * accRatio;
  gx = raw_gx * gyroRatio;
  gy = raw_gy * gyroRatio;
  gz = raw_gz * gyroRatio;

  return true;
}

bool checkIMU() {
  uint8_t version[3];
  if (!readRegisters(0x01, version, 3)) {
    return false;
  }

  Serial.print("IMU version: ");
  Serial.print(version[0]);
  Serial.print(".");
  Serial.print(version[1]);
  Serial.print(".");
  Serial.println(version[2]);
  return true;
}

void setup() {
  Serial.begin(USB_BAUD);
  delay(2000);

  Serial.println("BOOT: ESP32 DevKit V1 + YB-MRA02 I2C 100Hz");

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(50000);
  scanI2CBus();

  if (!checkIMU()) {
    Serial.println("ERROR: IMU not found at I2C address 0x23.");
    Serial.println("Check SDA/SCL/GND/3V3 wiring.");
  } else {
    Serial.println("IMU connected.");
  }
  dumpRegisterRange(0x00, 0x1F);

  Serial.println("time_ms,ax_g,ay_g,az_g,gx_rad_s,gy_rad_s,gz_rad_s");
}

void loop() {
  unsigned long now = millis();

  if (now - lastSampleMs >= SAMPLE_INTERVAL_MS) {
    lastSampleMs = now;

    float ax, ay, az;
    float gx, gy, gz;

    if (readSixAxis(ax, ay, az, gx, gy, gz)) {
      Serial.print(now);
      Serial.print(",");
      Serial.print(ax, 6);
      Serial.print(",");
      Serial.print(ay, 6);
      Serial.print(",");
      Serial.print(az, 6);
      Serial.print(",");
      Serial.print(gx, 6);
      Serial.print(",");
      Serial.print(gy, 6);
      Serial.print(",");
      Serial.println(gz, 6);
    } else {
      if (now - lastErrorMs >= 1000) {
        lastErrorMs = now;
        Serial.print("ERROR: I2C read failed at register 0x");
        if (lastFailedRegister < 16) {
          Serial.print("0");
        }
        Serial.print(lastFailedRegister, HEX);
        Serial.print(", txErr=");
        Serial.print(lastTransmissionError);
        Serial.print(", got=");
        Serial.print(lastReceivedLength);
        Serial.print("/");
        Serial.println(lastRequestedLength);
      }
    }
  }
}

