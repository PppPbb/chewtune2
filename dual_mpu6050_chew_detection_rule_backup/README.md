# Dual MPU6050 Chewing Side Detection

This version uses two MPU6050 sensors, one on the left side and one on the right side.

## Wiring For XIAO ESP32S3

Both sensors share the same I2C bus:

```text
Both MPU6050 VCC -> XIAO 3V3
Both MPU6050 GND -> XIAO GND
Both MPU6050 SDA -> XIAO D4 / GPIO5 / SDA
Both MPU6050 SCL -> XIAO D5 / GPIO6 / SCL
```

Important address wiring:

```text
Left  MPU6050 AD0 -> GND   address 0x68
Right MPU6050 AD0 -> 3V3   address 0x69
```

If both AD0 pins are left floating or tied to GND, both sensors will use `0x68` and cannot work together on the same I2C bus.

## Arduino

Open and upload:

```text
arduino/xiao_dual_mpu6050_i2c_100hz/xiao_dual_mpu6050_i2c_100hz.ino
```

Serial output:

```text
time_ms,l_ax,l_ay,l_az,l_gx,l_gy,l_gz,r_ax,r_ay,r_az,r_gx,r_gy,r_gz
```

## Python

Install dependencies:

```powershell
cd C:\Users\YUN\Desktop\chewtune\chewtune2\dual_mpu6050_chew_detection
pip install -r requirements.txt
```

Run:

```powershell
python python\realtime_dual_mpu6050_detection.py --port COM4
```

The plot shows left/right accelerometer magnitude, left/right gyroscope magnitude, chewing event, and CPM.

Save a recording:

```powershell
python python\realtime_dual_mpu6050_detection.py --port COM4 --activity-label left_chewing --save-csv data\left_test_01.csv
```

## How Side Detection Works

The first version is rule-based:

```text
1. Read both MPU6050 sensors at 100 Hz.
2. In each 2 second window, score the left and right sensors.
3. A side score is based on waveform strength, peak count, CPM range, and rhythm regularity.
4. If the stronger side clearly exceeds the other side, output left_chewing or right_chewing.
5. If both sides are too similar, output both_or_unknown.
```

Useful tuning options:

```powershell
python python\realtime_dual_mpu6050_detection.py --port COM4 --side-ratio 1.05
python python\realtime_dual_mpu6050_detection.py --port COM4 --min-channel-std 0.01
python python\realtime_dual_mpu6050_detection.py --port COM4 --counter-min-peak-distance 0.4
```
