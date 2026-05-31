# XIAO + GY-521 MPU6050 Chewing Detection

这个文件夹是给 GY-521 / MPU6050 新接线准备的 Arduino + Python 实时检测版本。

## 接线

```text
GY-521 VCC  -> XIAO 3V3
GY-521 GND  -> XIAO GND
GY-521 SDA  -> XIAO D4 / GPIO5 / SDA
GY-521 SCL  -> XIAO D5 / GPIO6 / SCL
```

GY-521 默认 I2C 地址通常是 `0x68`。如果模块 AD0 被拉高，地址会变成 `0x69`，这份 Arduino 代码默认用 `0x68`。

## Arduino

打开这个草图并烧录：

```text
arduino/xiao_mpu6050_i2c_100hz/xiao_mpu6050_i2c_100hz.ino
```

串口波特率：`115200`

输出 CSV 格式：

```text
time_ms,ax_g,ay_g,az_g,gx_rad_s,gy_rad_s,gz_rad_s
```

MPU6050 配置：

- 采样率：100 Hz
- 加速度量程：+/-4 g
- 陀螺仪量程：+/-500 deg/s，Python 端接收的是 rad/s
- DLPF：约 42-44 Hz

## Python 实时检测

先安装依赖：

```powershell
cd C:\Users\YUN\Desktop\chewtune\chewtune2\mpu6050_chew_detection
pip install -r requirements.txt
```

运行检测：

```powershell
python python\realtime_mpu6050_detection.py --port COM4 --model ..\imu_chew_pipeline\models\random_forest.pkl
```

运行后会弹出实时波形窗口：加速度、陀螺仪、咀嚼事件和 CPM 会同步显示。默认显示最近 10 秒数据，可以调整：

```powershell
python python\realtime_mpu6050_detection.py --port COM4 --plot-window-seconds 20
```

默认检测方式是 `--detector signal`，也就是根据波形节律判断，不依赖旧模型。规则大致是：忽略开头 5 秒启动晃动后，2 秒窗口里 `gy` 通道波动足够大、有足够运动幅度、至少 2 个峰值、CPM 在 30-160 之间，并且峰间节律不要太乱。任一核心条件不满足时，会强制低于咀嚼判定阈值，避免静止时一直显示 chewing。

根据 `mpu6050_chew_test_01.csv` 的实际数据，默认计数通道改成了 `gy`，最小峰间距改成了 `0.55s`，比 `gyro_mag + 0.25s` 更不容易把一次咀嚼拆成多个峰。

如果想对比旧模型：

```powershell
python python\realtime_mpu6050_detection.py --port COM4 --detector hybrid
```

如果还是太严格，可以先放宽峰值和幅度：

```powershell
python python\realtime_mpu6050_detection.py --port COM4 --signal-min-peaks 1 --signal-min-channel-std 0.03 --signal-min-gyro-std 0.008 --signal-min-acc-std 0.008
```

可选：保存串口数据和检测结果：

```powershell
python python\realtime_mpu6050_detection.py --port COM4 --save-csv data\mpu6050_run_01.csv
```

## 注意

这份 Python 代码会复用原来 `imu_chew_pipeline` 里的特征提取、计数逻辑和模型。MPU6050 与原传感器噪声、坐标方向、安装位置可能不同；如果检测不稳定，建议用 MPU6050 重新采集几组 `chewing / talking / still / head_turn` 数据后重新训练模型。
