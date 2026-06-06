# ChewTune WeChat BLE Probe

Minimal WeChat Mini Program used to verify that a phone can receive BLE Notify messages from the XIAO ESP32S3.

## Import

1. Open WeChat Developer Tools.
2. Import this `wechat_ble_probe` directory.
3. Use a real Mini Program AppID, or keep `touristappid` for local testing if supported by the installed Developer Tools.
4. Compile and preview on a real phone. BLE cannot be fully validated in the desktop simulator.

## Test

1. Upload the BLE-enabled dual MPU6050 firmware.
2. Make sure the phone Bluetooth permission is enabled.
3. Open the Mini Program on the phone and tap `搜索并连接`.
4. Confirm that heartbeat notifications such as `H,12345` appear once per second.
5. Close Arduino Serial Monitor and run:

```powershell
cd C:\Users\17740\Desktop\chewtune2\S2_spatial_chewing_system
python python\s2_spatial_intervention.py --port COM9
```

6. Confirm that UI summaries such as `U,1,108,L,72,26,W` appear and update the metric panel.

## BLE UUIDs

```text
Device:  ChewTune-S2
Service: 7b100001-7c6a-4d91-a461-9c987d97b100
Notify:  7b100002-7c6a-4d91-a461-9c987d97b100
```

If no device is found on Android, verify that Bluetooth and the permissions requested by WeChat are enabled. If the Mini Program connects but receives no data, confirm that Notify subscription succeeded and test the firmware using nRF Connect.
