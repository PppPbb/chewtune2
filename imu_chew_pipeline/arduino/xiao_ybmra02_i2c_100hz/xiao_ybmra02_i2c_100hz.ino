#include <Arduino.h>
#include <Wire.h>

#define SDA_PIN 5
#define SCL_PIN 6

#define IMU_ADDR 0x23
#define USB_BAUD 115200

const unsigned long SAMPLE_INTERVAL_MS = 10;  // 100 Hz
unsigned long lastSampleMs = 0;

int16_t toInt16LE(uint8_t low, uint8_t high) {
  return (int16_t)((high << 8) | low);
}

bool readRegisters(uint8_t startReg, uint8_t *buffer, uint8_t len) {
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(startReg);

  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  uint8_t received = Wire.requestFrom(IMU_ADDR, len);
  if (received != len) {
    return false;
  }

  for (uint8_t i = 0; i < len; i++) {
    buffer[i] = Wire.read();
  }

  return true;
}

bool readSixAxis(float &ax, float &ay, float &az,
                 float &gx, float &gy, float &gz) {
  uint8_t data[12];

  // 0x04-0x09: ACCEL_X/Y/Z, 0x0A-0x0F: GYRO_X/Y/Z
  if (!readRegisters(0x04, data, 12)) {
    return false;
  }

  int16_t raw_ax = toInt16LE(data[0], data[1]);
  int16_t raw_ay = toInt16LE(data[2], data[3]);
  int16_t raw_az = toInt16LE(data[4], data[5]);
  int16_t raw_gx = toInt16LE(data[6], data[7]);
  int16_t raw_gy = toInt16LE(data[8], data[9]);
  int16_t raw_gz = toInt16LE(data[10], data[11]);

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

  Serial.println("BOOT: XIAO ESP32S3 + YB-MRA02 I2C 100Hz");

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);

  if (!checkIMU()) {
    Serial.println("ERROR: IMU not found at I2C address 0x23.");
    Serial.println("Check SDA/SCL/GND/3V3 wiring.");
  } else {
    Serial.println("IMU connected.");
  }

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
      Serial.println("ERROR: I2C read failed");
    }
  }
}

