# ChewTune

ChewTune 是一个基于双侧 MPU6050 IMU 的实时咀嚼检测与音乐干预原型。系统判断是否正在咀嚼、识别左右咀嚼侧、估算并校准 CPM（每分钟咀嚼次数），再根据速度、稳定性、偏侧和 PPB（两口之间的停顿）控制分层音乐、立体声声像和提示音。

仓库还包含一个独立的音乐生成工作流，用于根据音乐偏好和干预目标生成四层音乐的 prompt、API payload 与播放策略。该工作流目前没有连接具体音乐生成服务。

## 目录结构

```text
0804_Detection_Music/
├─ README.md
├─ run_detection.ps1
├─ open_music_workflow.ps1
├─ detection_music_runtime/
│  ├─ requirements.txt
│  ├─ arduino/
│  ├─ python/
│  ├─ models/
│  └─ music/
├─ music_generation_workflow/
│  ├─ frontend/
│  ├─ backend/
│  └─ *.json / *.md
└─ docs/
   └─ DATA_AND_LABEL_SPEC.md
```

## 快速开始

### 安装依赖

建议使用 Python 3.9 或更高版本：

```powershell
cd detection_music_runtime
python -m pip install -r requirements.txt
```

主要依赖包括 NumPy、Pandas、Matplotlib、PySerial、scikit-learn、Joblib 和 Pygame。

### 启动检测与音乐干预

从项目根目录运行：

```powershell
.\run_detection.ps1 -Port COM4
```

无 GUI 模式：

```powershell
.\run_detection.ps1 -Port COM4 -NoGui
```

直接运行 Python 入口：

```powershell
cd detection_music_runtime
python python\main.py --port COM4
```

程序调用链：

```text
run_detection.ps1
  -> detection_music_runtime/python/main.py
      -> detection_music_runtime/python/detector.py
```

### 不连接传感器测试音乐

```powershell
cd detection_music_runtime
python python\main.py --test-music-state normal --test-pan 0
python python\main.py --test-music-state fast --test-pan 0
python python\main.py --test-ppb-cue ding
```

### 打开音乐生成工作流

```powershell
.\open_music_workflow.ps1
```

该脚本打开 `music_generation_workflow/frontend/index.html`，不会启动检测程序或连接传感器。

## 硬件与固件

系统使用 XIAO ESP32S3 和两个 MPU6050。两个传感器共享 I²C 总线：

```text
两个 MPU6050 VCC -> XIAO 3V3
两个 MPU6050 GND -> XIAO GND
两个 MPU6050 SDA -> XIAO D4 / GPIO5 / SDA
两个 MPU6050 SCL -> XIAO D5 / GPIO6 / SCL

左侧 MPU6050 AD0 -> GND，地址 0x68
右侧 MPU6050 AD0 -> 3V3，地址 0x69
```

两个 AD0 不能同时悬空或接地，否则都会使用 `0x68`，无法共用同一 I²C 总线。

固件位置：

```text
detection_music_runtime/arduino/xiao_dual_mpu6050_i2c_100hz/xiao_dual_mpu6050_i2c_100hz.ino
```

固件以约 100 Hz 输出串口 CSV：

```text
time_ms,l_ax,l_ay,l_az,l_gx,l_gy,l_gz,r_ax,r_ay,r_az,r_gx,r_gy,r_gz
```

## Python 模块

| 文件 | 职责 |
|---|---|
| `python/main.py` | 最终检测、GUI、音乐分层、声像与 PPB 干预入口 |
| `python/detector.py` | 串口解析、特征、咀嚼状态、左右侧和 CPM 检测 |
| `python/collect_chewing_state_data.py` | 采集咀嚼/非咀嚼训练数据 |
| `python/collect_rate_calibration.py` | 采集人工咀嚼事件与 CPM 校准数据 |
| `python/train_chewing_state_rf.py` | 训练可选的咀嚼状态随机森林 |
| `python/train_side_classifier.py` | 训练当前左右侧分类器 |
| `python/train_rate_calibrator.py` | 训练 CPM 校准器 |
| `python/evaluate_chewing_state_rf.py` | 评估咀嚼状态随机森林 |
| `python/evaluate_side_classifiers.py` | 实时比较左右侧识别方法 |

## 检测与干预逻辑

### 默认检测链路

```text
双侧 IMU 数据
  -> 咀嚼/静止阈值门控
  -> 左右侧分类
  -> 咀嚼事件与原始 CPM
  -> CPM 校准
  -> 稳定性、速度、偏侧与 PPB 干预
```

默认使用 `models/chewing_state_threshold.json` 判断是否咀嚼。三个运动指标都超过阈值后，窗口才进入左右侧判断和 CPM 计算：

```text
total_gyro_std_min: 0.15
max_axis_gyro_std_min: 0.115
total_acc_std_min: 0.02
```

使用随机森林替代阈值门控：

```powershell
python python\detector.py --port COM4 --enable-chewing-state-model
```

### 左右声像

```text
初始声像为 0.00（中央）
left_chewing  -> 目标声像每次估计咀嚼向左移动 1/30
right_chewing -> 目标声像每次估计咀嚼向右移动 1/30
当前声像平滑跟随目标声像
```

声像范围为 `-1.00`（左）到 `+1.00`（右）。这里使用立体声左右电平差，不是完整 HRTF 渲染。

### 音乐状态

```text
pause  -> 静音
normal -> drum + melody
stable -> bass + drum + melody
fast   -> background + drum
```

- 快速咀嚼阈值默认为 `120 CPM`：最近 4 个有效间隔中至少 2 个瞬时 CPM 超过阈值时进入 `fast`。
- 连续 5 秒没有检测到咀嚼后进入 `pause`。
- 稳定性使用最近最多 5 个有效间隔：`stability = 1 - (std / mean) / 0.22`，默认 `stability >= 0.50` 为稳定。

### PPB 提示

PPB 为上一口结束到下一口开始的间隔，默认目标为 4 秒：

```text
等待停顿       -> 每秒播放一次 pop
达到 4 秒目标  -> 播放 ding
达到目标前下一口开始 -> 播放 error
```

参数调整：

```powershell
python python\main.py --port COM4 --ppb-threshold-seconds 4
python python\main.py --port COM4 --disable-ppb-cues
```

## 模型

`detection_music_runtime/models/` 只保留当前版本使用的四个文件：

| 文件 | 用途 | 默认启用 | 生成方式 |
|---|---|---:|---|
| `chewing_state_threshold.json` | 咀嚼/静止阈值门控 | 是 | 手工配置 |
| `dual_side_cnn.pkl` | 左右侧咀嚼分类 | 是 | `python/train_side_classifier.py` |
| `cpm_calibrator.pkl` | CPM 校准 | 是 | `python/train_rate_calibrator.py` |
| `chewing_state_rf.pkl` | 咀嚼/非咀嚼随机森林 | 否 | `python/train_chewing_state_rf.py` |

当前文件的 SHA-256：

```text
chewing_state_rf.pkl
5BCDB21EEDA7FB0AAB0C2FB7F921FD9BB7BF2EB74A3266563A541713452C81FF

chewing_state_threshold.json
846C369FD5CB77CDAA514841712174E9606B12270CBE526D7292C6EA77EE1754

cpm_calibrator.pkl
AECA7167918A7285F9E8CDE08960A4C377D251799C627D51A6F5C73FF1FA3522

dual_side_cnn.pkl
B38AFED0712057C748AB616E5F24EF7A89520E6A4444BE26F6AF7E1C19D904A0
```

模型重新训练后应同步更新哈希。原始训练数据不保存在仓库中。

### 训练左右侧分类器

采集固定位置、每段约 20–30 秒的左右侧数据后运行：

```powershell
python python\train_side_classifier.py
```

默认后端使用随机一维卷积核提取时序特征，再使用轻量分类器，不依赖 TensorFlow。可选 TensorFlow Conv1D：

```powershell
python python\train_side_classifier.py --backend tensorflow --model-out models\dual_side_cnn.keras
```

### 校准 CPM

采集时每次真实咀嚼按一次空格：

```powershell
python python\collect_rate_calibration.py --port COM4 --raw-out data\rate_calibration\rate_calibration_01.csv --events-out data\rate_calibration\rate_calibration_01_events.csv
python python\train_rate_calibrator.py
```

禁用校准器进行对比：

```powershell
python python\detector.py --port COM4 --disable-cpm-calibrator
```

### 实时方法比较

```powershell
python python\evaluate_side_classifiers.py --port COM4
```

窗口打开时使用 `G` 标记左侧真值、`H` 标记右侧真值。结果写入 `data/method_eval_results.csv`。

## 音频素材

音频位于 `detection_music_runtime/music/`。循环层支持 `.wav`、`.mp3` 或 `.ogg`：

```text
background
melody
drum
bass
```

提示音使用：

```text
pop
ding
error
```

循环层持续播放并通过音量切换和立体声声像参与干预；提示音在独立通道单次播放，不参与循环。

## 音乐生成工作流

工作流根据两个音乐偏好轴生成四层可调度音乐：

- `smooth_clear`：从柔和、连贯、圆润到清晰、颗粒明确、轮廓清楚。
- `foreground_activity`：从背景化、低存在感到更前景、更有旋律和节奏活动。

两个轴进一步映射为：

```text
smoothness
clarity
backgroundness
foreground_activity
```

`backgroundness` 是偏好权重，不等于 `background.wav` 的运行职责。当前 `background` 层是在咀嚼过快时淡入的持续干预音。

最小生成链路：

```text
music_preference + intervention_goal
  -> 权重计算
  -> master specification
  -> 四层 API request
  -> background / melody / drum / bass
  -> retry / fallback
  -> 后处理与筛选
  -> 入库
  -> 运行时参数调制
```

### 工作流文件

| 文件 | 用途 |
|---|---|
| `workflow_spec.md` | 完整节点、输入输出、公式、筛选和入库方式 |
| `style_model.json` | 二维偏好模型、权重公式和参数映射 |
| `prompt_library.json` | 提示词库和参数映射 |
| `metadata_template.json` | 音乐入库 metadata 模板 |
| `screening_checklist.md` | 自动与人工筛选清单 |
| `master_parameter_options.md/.json` | 母版参数候选池和采样规则 |
| `playback_policy.json` | 运行时播放调度策略 |
| `cue_library.json` | 独立提示音定义 |
| `backend/api_payload_schema.json` | 生成 payload 的 JSON Schema |
| `backend/prompt_engine.js` | Prompt 与 payload 生成逻辑 |

### 后端 API 接入

`prompt_engine.js` 输出一个包含以下内容的 `ChewTune Stem Generation Payload`：

```text
library_id
music_preference
intervention_goal
master_spec
stem_requests[]
postprocess_plan
retry_policy
fallback_policy
playback_policy
```

每个 `stem_request` 包含：

- `system_prompt`：固定该层职责和限制。
- `user_prompt`：本次 master spec、BPM、调式、和弦及层级描述。
- `generation_params`：供 API 或后处理使用的结构化参数。
- `output`：文件名、格式、采样率、声道和循环要求。

实际接入时需要实现：

```js
musicApi.generate({
  system,
  prompt,
  params,
  output
})
```

字段映射原则：

```text
system_prompt     -> system / instructions / negative constraints
user_prompt       -> prompt / input / generation prompt
generation_params -> metadata / tags / model controls
output            -> 保存文件名与格式
```

单个 stem 失败时先按 `retry_policy` 仅重试失败层；仍失败时按 `fallback_policy` 使用最近通过审核的库条目。`cue_trigger` 引用独立的 `cue_library.json`，不属于四个音乐 stem。

## 数据与评估

数据和标签规范位于 `docs/DATA_AND_LABEL_SPEC.md`。原始传感器数据、人工真值和模型输出应分开保存，同一采集会话不能同时进入训练集与测试集。

当前系统仍属于研究原型。随机重叠窗口的指标不能代表跨受试者泛化能力，正式评估应按受试者或至少按采集会话隔离。

## 常用调试命令

```powershell
# 仅运行检测器
python python\detector.py --port COM4

# 禁用左右侧模型，使用规则方法
python python\detector.py --port COM4 --disable-model

# 调整规则参数
python python\detector.py --port COM4 --side-ratio 1.05
python python\detector.py --port COM4 --min-channel-std 0.01
python python\detector.py --port COM4 --counter-min-peak-distance 0.4
```
