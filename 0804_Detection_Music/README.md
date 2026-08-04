# ChewTune 最终检测与音乐工作流整合包

本目录将当前项目中可运行的双侧 IMU 检测/音乐干预程序，与音乐库生成工作流整理到一起。

## 目录

```text
ChewTune_Final_Detection_Music/
  detection_music_runtime/     双侧 IMU 检测、模型、实时分层音乐播放
  music_generation_workflow/   音乐偏好、prompt、API payload 和前端预览工作流
  docs/                        数据与标签规范
  run_detection.ps1           启动实时检测与音乐干预
  open_music_workflow.ps1      打开音乐生成工作流前端
```

## 1. 实时检测与音乐干预

检测运行时来自当前项目的 `S3_random_forest_system`，包含：

- 咀嚼/非咀嚼 Random Forest 状态检测。
- 双侧 IMU 左右偏侧检测。
- CPM 校准。
- 咀嚼稳定性、PPB 和偏侧音乐干预。
- `background`、`melody`、`drum`、`bass` 四层循环播放。
- `pop`、`ding`、`error` PPB 提示音。

安装 Python 依赖：

```powershell
cd detection_music_runtime
python -m pip install -r requirements.txt
```

连接双侧 MPU6050 后，从整合包根目录运行：

```powershell
.\run_detection.ps1 -Port COM4
```

也可以直接运行：

```powershell
cd detection_music_runtime
python python\s3_rf_spatial_intervention.py --port COM4
```

无 GUI：

```powershell
.\run_detection.ps1 -Port COM4 -NoGui
```

只测试音乐，不连接传感器：

```powershell
cd detection_music_runtime
python python\s3_rf_spatial_intervention.py --test-music-state normal --test-pan 0
python python\s3_rf_spatial_intervention.py --test-music-state fast --test-pan 0
python python\s3_rf_spatial_intervention.py --test-ppb-cue ding
```

Arduino 固件位于：

```text
detection_music_runtime/arduino/xiao_dual_mpu6050_i2c_100hz/
```

## 2. 音乐生成工作流

打开前端：

```powershell
.\open_music_workflow.ps1
```

也可以直接打开：

```text
music_generation_workflow/frontend/index.html
```

工作流能够根据音乐偏好和干预目标生成：

- 四层音乐的 master specification。
- `background`、`melody`、`drum`、`bass` 的生成提示词。
- 音乐生成 API payload。
- 播放参数调度策略。

注意：当前工作流没有绑定具体音乐生成服务。`backend/prompt_engine.js` 负责生成请求结构，但真正的 `musicApi.generate(...)` 仍需根据选定的音乐生成 API 实现。当前实时程序播放的是 `detection_music_runtime/music/` 内已经生成好的音频。

## 3. 关键入口

| 功能 | 文件 |
|---|---|
| 最终实时检测与音乐干预 | `detection_music_runtime/python/s3_rf_spatial_intervention.py` |
| 实际整合实现 | `detection_music_runtime/python/s2_spatial_intervention.py` |
| 双侧 IMU 检测与特征 | `detection_music_runtime/python/realtime_dual_mpu6050_detection.py` |
| 咀嚼状态 RF 训练 | `detection_music_runtime/python/train_chewing_state_rf.py` |
| 偏侧模型训练 | `detection_music_runtime/python/train_dual_side_cnn.py` |
| CPM 校准训练 | `detection_music_runtime/python/train_rate_calibrator.py` |
| 音乐 prompt/payload 引擎 | `music_generation_workflow/backend/prompt_engine.js` |
| 音乐工作流前端 | `music_generation_workflow/frontend/index.html` |

## 4. 模型与数据说明

`detection_music_runtime/models/` 包含当前模型文件。原始采集数据没有复制到本整合包，避免把实验数据和受试者数据混入运行代码。需要重新训练时，请按照数据规范建立独立数据目录。

当前模型仍属于原型阶段。随机重叠窗口指标不能代表跨受试者泛化能力，正式结果应按受试者或至少按采集会话隔离评估。

