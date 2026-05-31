#include <Arduino.h>
#include <Wire.h>

// XIAO ESP32S3 I2C pins:
// GY-521 VCC -> XIAO 3V3
// GY-521 GND -> XIAO GND
// GY-521 SDA -> XIAO D4 / GPIO5 / SDA
// GY-521 SCL -> XIAO D5 / GPIO6 / SCL
#define SDA_PIN 5
#define SCL_PIN 6

#define MPU6050_ADDR 0x68
#define USB_BAUD 115200

#define REG_SMPLRT_DIV 0x19
#define REG_CONFIG 0x1A
#define REG_GYRO_CONFIG 0x1B
#define REG_ACCEL_CONFIG 0x1C
#define REG_ACCEL_XOUT_H 0x3B
#define REG_PWR_MGMT_1 0x6B
#define REG_WHO_AM_I 0x75

const unsigned long SAMPLE_INTERVAL_US = 10000;  // 100 Hz
unsigned long lastSampleUs = 0;

int16_t toInt16BE(uint8_t high, uint8_t low) {
  return (int16_t)((high << 8) | low);
}

bool writeRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool readRegisters(uint8_t startReg, uint8_t *buffer, uint8_t len) {
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(startReg);

  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  uint8_t received = Wire.requestFrom(MPU6050_ADDR, len);
  if (received != len) {
    return false;
  }

  for (uint8_t i = 0; i < len; i++) {
    buffer[i] = Wire.read();
  }

  return true;
}

bool readRegister(uint8_t reg, uint8_t &value) {
  return readRegisters(reg, &value, 1);
}

bool setupMPU6050() {
  uint8_t whoAmI = 0;
  if (!readRegister(REG_WHO_AM_I, whoAmI)) {
    return false;
  }

  Serial.print("MPU6050 WHO_AM_I: 0x");
  Serial.println(whoAmI, HEX);

  if (whoAmI != 0x68 && whoAmI != 0x69) {
    return false;
  }

  // Wake up the MPU6050, use X gyro PLL as clock source.
  if (!writeRegister(REG_PWR_MGMT_1, 0x01)) return false;
  delay(100);

  // DLPF on, gyro output rate becomes 1 kHz. 1 kHz / (9 + 1) = 100 Hz.
  if (!writeRegister(REG_SMPLRT_DIV, 0x09)) return false;

  // DLPF config 3: accel about 44 Hz, gyro about 42 Hz. Good for jaw motion.
  if (!writeRegister(REG_CONFIG, 0x03)) return false;

  // Gyro full scale +/-500 deg/s: sensitivity 65.5 LSB/(deg/s).
  if (!writeRegister(REG_GYRO_CONFIG, 0x08)) return false;

  // Accel full scale +/-4 g: sensitivity 8192 LSB/g.
  if (!writeRegister(REG_ACCEL_CONFIG, 0x08)) return false;

  return true;
}

bool readSixAxis(float &ax, float &ay, float &az,
                 float &gx, float &gy, float &gz) {
  uint8_t data[14];

  if (!readRegisters(REG_ACCEL_XOUT_H, data, 14)) {
    return false;
  }

  int16_t raw_ax = toInt16BE(data[0], data[1]);
  int16_t raw_ay = toInt16BE(data[2], data[3]);
  int16_t raw_az = toInt16BE(data[4], data[5]);
  int16_t raw_gx = toInt16BE(data[8], data[9]);
  int16_t raw_gy = toInt16BE(data[10], data[11]);
  int16_t raw_gz = toInt16BE(data[12], data[13]);

  ax = raw_ax / 8192.0f;
  ay = raw_ay / 8192.0f;
  az = raw_az / 8192.0f;

  const float gyroScaleRad = (PI / 180.0f) / 65.5f;
  gx = raw_gx * gyroScaleRad;
  gy = raw_gy * gyroScaleRad;
  gz = raw_gz * gyroScaleRad;

  return true;
}

void setup() {
  Serial.begin(USB_BAUD);
  delay(2000);

  Serial.println("BOOT: XIAO ESP32S3 + GY-521 MPU6050 I2C 100Hz");

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);

  if (!setupMPU6050()) {
    Serial.println("ERROR: MPU6050 not found or setup failed at I2C address 0x68.");
    Serial.println("Check VCC=3V3, GND, SDA=D4/GPIO5, SCL=D5/GPIO6.");
  } else {
    Serial.println("MPU6050 connected.");
  }

  Serial.println("time_ms,ax_g,ay_g,az_g,gx_rad_s,gy_rad_s,gz_rad_s");
}

void loop() {
  unsigned long nowUs = micros();

  if (nowUs - lastSampleUs >= SAMPLE_INTERVAL_US) {
    lastSampleUs = nowUs;

    float ax, ay, az;
    float gx, gy, gz;

    if (readSixAxis(ax, ay, az, gx, gy, gz)) {
      Serial.print(millis());
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
