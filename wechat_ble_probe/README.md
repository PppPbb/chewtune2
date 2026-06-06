# ChewTune 微信小程序 BLE Probe

这是一个无需后端的初步验证页，用于确认微信小程序能够连接 `ChewTune-S2` 或
`ChewTune-S3`，订阅 BLE Notify，并实时显示电脑端算法生成的 UI 摘要。

## 使用步骤

1. 使用 Arduino IDE 将带 BLE GATT Server 的固件烧录到 XIAO ESP32S3。
2. 启动 S3 电脑端程序：

   ```powershell
   cd C:\Users\17740\Desktop\chewtune2\S3_random_forest_system
   python python\s3_rf_spatial_intervention.py --port COM9
   ```

3. 在微信开发者工具中导入本目录 `wechat_ble_probe`。
4. 使用真机调试，在手机系统设置中开启蓝牙，并授予微信蓝牙/定位权限。
5. 点击“扫描并连接”，看到 Notify 数量持续增加即表示链路正常。

微信开发者工具模拟器不能完整验证 BLE，请以真机结果为准。若只收到 heartbeat，
但 UI 数据不更新，请确认 S3 Python 程序正在运行且未传入 `--disable-ble-ui`。

## 协议

- Service: `7b100001-7c6a-4d91-a461-9c987d97b100`
- Notify: `7b100002-7c6a-4d91-a461-9c987d97b100`
- 心跳：`H,<milliseconds>`
- UI 摘要：`U,<chewing>,<cpm>,<side>,<stability>,<ppb_tenths>,<ppb_state>`
- PPB 状态：`I` 空闲、`D` 确认停顿中、`W` 倒计时、`R` 达标、`T` 过短
