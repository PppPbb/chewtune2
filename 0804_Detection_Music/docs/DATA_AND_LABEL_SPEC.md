# ChewTune 数据与标签规范（v1.0）

## 1. 适用范围

本规范用于双侧 IMU、PPG、骨传导及同步视频数据的采集、人工标注、模型训练和离线评估。

核心原则：

1. 原始传感器数据、人工真值和模型输出分开保存。
2. 咀嚼与干扰活动采用多标签，彼此不互斥。
3. 同一原始会话不能同时进入训练集和测试集。
4. 窗口参数属于实验配置，不写死在原始数据中。当前 2 秒窗口、1.5 秒重叠仅作为基线。
5. 无法可靠判断的标签使用 `unknown` 或降低标注质量，不能强制归类。

## 2. 术语定义

| 术语 | 定义 |
|---|---|
| 咀嚼状态 | 某段时间内是否存在一个或多个咀嚼动作 |
| 咀嚼事件（chew event） | 一次完整的下颌咀嚼周期，用一个代表性时间戳标记 |
| 咀嚼次数 | 一段时间内咀嚼事件的数量 |
| 咀嚼口数（bite count） | 食物入口形成的进食单元数量，一口通常包含多次咀嚼 |
| ICI | 相邻两个有效咀嚼事件的时间差，单位秒 |
| CPM | 每分钟咀嚼次数，根据一段连续咀嚼事件估计 |
| PPB | 当前口最后一次咀嚼到下一口第一次咀嚼之间的时间差，单位秒 |
| 咀嚼段 | 一组连续咀嚼事件，通常对应一口食物 |
| 偏侧 | 当前咀嚼主要发生在左侧、右侧、双侧，或无法确定 |
| 干扰 | 走路、说话、摇头等可能改变传感器信号的活动，不等同于非咀嚼 |

PPB 和口数必须基于人工标注的 `bite_id` 验证，不能仅依靠固定时间阈值定义真值。

## 3. 推荐目录结构

```text
data/
  raw/
    SUB001/
      SES_20260810_001/
        imu.csv
        ppg.csv
        bone_audio.wav
        sync_video.mp4
        session_meta.json
  annotations/
    SUB001/
      SES_20260810_001/
        intervals.csv
        chew_events.csv
        bite_events.csv
  processed/
    dataset_version/
      windows.csv
      features.csv
  splits/
    split_v1.json
  predictions/
    model_version/
      SES_20260810_001_predictions.csv
```

- `raw/`：只保存原始采样，不允许滤波或覆盖。
- `annotations/`：人工真值，可以修订并记录标注版本。
- `processed/`：由脚本生成，可以删除后重建。
- `splits/`：固定训练、验证和测试会话。
- `predictions/`：模型输出，不得放入人工标签文件。

## 4. 标识符与文件命名

### 4.1 标识符

| 字段 | 格式 | 示例 |
|---|---|---|
| `subject_id` | `SUB` + 三位匿名编号 | `SUB001` |
| `session_id` | 日期 + 当日序号 | `SES_20260810_001` |
| `wearing_id` | 同一受试者重新佩戴次数 | `W01` |
| `device_id` | 设备固定编号 | `IMU_L_01` |
| `annotation_version` | 语义化版本或递增版本 | `1.0` |

禁止把姓名、学号、手机号等个人信息写入数据文件。

### 4.2 文件命名

传感器类型通过标准文件名区分，不在文件名中重复堆叠标签。行为标签存入标注文件和元数据。

```text
imu.csv
ppg.csv
bone_audio.wav
sync_video.mp4
session_meta.json
intervals.csv
chew_events.csv
bite_events.csv
```

## 5. 时间同步规范

所有文件至少包含相对于会话起点的单调时间：

- `elapsed_s`：从会话统一起点开始的秒数，推荐用于跨设备对齐。
- `time_ms`：设备时间或主机时间，保留用于排查采样和丢包。
- `utc_timestamp`：可选，只用于记录绝对时间。

要求：

1. `elapsed_s=0` 对应同一个同步事件。
2. 每次采集开始时产生可被各模态观察到的同步标记，例如按键、提示音或轻敲。
3. 元数据记录每个模态相对主时间轴的偏移量。
4. 不允许仅依赖文件创建时间进行同步。

## 6. 原始传感器文件

### 6.1 双侧 IMU：`imu.csv`

必需字段：

```text
elapsed_s,time_ms,
l_ax,l_ay,l_az,l_gx,l_gy,l_gz,
r_ax,r_ay,r_az,r_gx,r_gy,r_gz,
sync_marker
```

字段说明：

| 字段 | 单位 | 说明 |
|---|---|---|
| `elapsed_s` | s | 统一会话时间 |
| `time_ms` | ms | 原设备或主机时间 |
| `l_ax..l_az` | g | 左侧三轴加速度 |
| `l_gx..l_gz` | deg/s | 左侧三轴角速度 |
| `r_ax..r_az` | g | 右侧三轴加速度 |
| `r_gx..r_gz` | deg/s | 右侧三轴角速度 |
| `sync_marker` | 0/1 | 同步事件标记 |

若设备输出为 m/s² 或 rad/s，必须在元数据中明确，不能混用。

### 6.2 PPG：`ppg.csv`

推荐字段：

```text
elapsed_s,time_ms,ppg_ir,ppg_red,ppg_green,sync_marker
```

不存在的通道可以省略，但元数据必须记录有效通道、采样率、增益和佩戴位置。

### 6.3 骨传导

- 原始音频优先保存为无损 `WAV`。
- 元数据记录采样率、位深、通道数、设备增益和佩戴位置。
- 如果设备只输出预处理特征，保存为 `bone_features.csv`，并明确每列的计算方法。

## 7. 会话元数据：`session_meta.json`

推荐结构：

```json
{
  "schema_version": "1.0",
  "session_id": "SES_20260810_001",
  "subject_id": "SUB001",
  "wearing_id": "W01",
  "created_at": "2026-08-10T10:30:00+08:00",
  "protocol_id": "PILOT_V1",
  "annotation_version": "1.0",
  "food": {
    "food_id": "gum_01",
    "category": "gum",
    "texture": "chewy",
    "description": "sugar-free gum"
  },
  "sensors": {
    "imu": {
      "enabled": true,
      "sample_rate_hz": 100,
      "left_device_id": "IMU_L_01",
      "right_device_id": "IMU_R_01",
      "acc_unit": "g",
      "gyro_unit": "deg/s",
      "placement": "left_and_right_preauricular"
    },
    "ppg": {
      "enabled": false,
      "sample_rate_hz": null,
      "placement": null
    },
    "bone_audio": {
      "enabled": false,
      "sample_rate_hz": null,
      "placement": null
    }
  },
  "time_offsets_s": {
    "imu": 0.0,
    "ppg": null,
    "bone_audio": null,
    "video": null
  },
  "planned_activities": ["chewing", "walking"],
  "notes": ""
}
```

食物类别建议值：

| `category` | 说明 |
|---|---|
| `soft` | 软质食物 |
| `semi_liquid` | 需要少量咀嚼的半流食 |
| `liquid` | 纯液体，通常作为喝水/吞咽负样本 |
| `hard` | 硬质食物 |
| `nuts` | 坚果 |
| `gum` | 口香糖 |
| `other` | 其他，必须填写描述 |

## 8. 区间标签：`intervals.csv`

区间标签描述一段持续行为。推荐字段：

```text
interval_id,start_s,end_s,
chewing_state,chewing_side,
walking,talking,head_shaking,head_nodding,
drinking,swallowing,coughing,device_touch,
food_in_mouth,label_quality,annotator_id,notes
```

### 8.1 咀嚼状态 `chewing_state`

允许值：

- `chewing`：区间内存在连续咀嚼。
- `non_chewing`：可以确认没有咀嚼。
- `unknown`：遮挡、同步错误或动作不明确。

不要继续使用 `still` 或 `speaking` 直接代替 `non_chewing`。静止和说话属于活动标签。

### 8.2 咀嚼偏侧 `chewing_side`

允许值：

- `left`
- `right`
- `bilateral`
- `unknown`
- `not_applicable`：`non_chewing` 区间

偏侧仅表示实际咀嚼侧，不表示哪一侧传感器振幅更大。

### 8.3 干扰活动标签

以下字段均使用 `0/1/unknown`：

- `walking`
- `talking`
- `head_shaking`
- `head_nodding`
- `drinking`
- `swallowing`
- `coughing`
- `device_touch`

示例：走路时咀嚼口香糖应标为：

```text
chewing_state=chewing
walking=1
chewing_side=left/right/bilateral/unknown
```

绝不能因为 `walking=1` 就把 `chewing_state` 改成 `non_chewing`。

### 8.4 标注质量 `label_quality`

- `high`：视频清晰，事件和行为可可靠判断。
- `medium`：行为可判断，但精确边界存在小误差。
- `low`：只能粗略判断，不用于最终事件级评估。
- `exclude`：同步错误、传感器脱落或无法判断。

## 9. 咀嚼事件：`chew_events.csv`

每行表示一次人工确认的咀嚼动作：

```text
event_id,event_time_s,bite_id,chewing_side,label_quality,annotator_id,notes
```

规则：

1. `event_time_s` 统一标在下颌闭合峰或协议指定的代表点。
2. 同一数据集必须始终使用同一种事件时间定义。
3. 左右传感器都出现响应时仍只标一个咀嚼事件。
4. 无法分辨单次事件但能确认正在咀嚼时，只保留区间标签，不伪造事件。
5. `bite_id` 连接事件与对应的一口食物，例如 `B001`。

## 10. 进食口事件：`bite_events.csv`

推荐字段：

```text
bite_id,intake_time_s,chewing_start_s,chewing_end_s,
next_bite_start_s,ground_truth_ppb_s,chew_count,
label_quality,annotator_id,notes
```

定义：

- `intake_time_s`：食物进入口腔的时间；无法判断时留空。
- `chewing_start_s`：该口第一次有效咀嚼事件。
- `chewing_end_s`：该口最后一次有效咀嚼事件。
- `ground_truth_ppb_s = next_bite_start_s - chewing_end_s`。
- `chew_count`：属于该 `bite_id` 的有效咀嚼事件数。

最后一口没有下一口时，`next_bite_start_s` 和 `ground_truth_ppb_s` 留空，不填 0。

口香糖通常只有一次入口，后续长时间连续咀嚼不应按停顿自动计为多口。

## 11. 派生指标

派生指标由脚本计算，不直接作为人工原始标签：

### 11.1 ICI

```text
ICI_i = event_time_(i+1) - event_time_i
```

跨 `bite_id` 的事件不纳入同一口内 ICI。

### 11.2 CPM

推荐同时保存：

- `cpm_window`：指定时间窗口内的事件速率。
- `cpm_interval`：由有效 ICI 的中位数或均值换算。
- `cpm_smoothed`：用于实时展示的平滑结果。

每个 CPM 结果必须记录计算方法、观察时长和最少事件数。

### 11.3 PPB

PPB 只在相邻两口都具有可靠边界时计算。口香糖、连续含食或边界未知的会话可以不计算 PPB。

### 11.4 偏侧统计

推荐输出：

- 左侧有效咀嚼事件数及比例。
- 右侧有效咀嚼事件数及比例。
- 双侧事件数及比例。
- `unknown` 数量及比例。

计算左右比例时不能静默丢弃大量 `unknown`，必须同时报告覆盖率。

## 12. 窗口级训练表

窗口表属于 `processed/`，由原始数据和人工标签生成。建议至少包含：

```text
window_id,subject_id,session_id,wearing_id,
start_s,end_s,window_seconds,step_seconds,
chewing_state,chewing_fraction,chewing_side,
walking,talking,head_shaking,head_nodding,
chew_event_count,label_quality
```

窗口标签规则必须写入实验配置，例如：

- `chewing_fraction >= 0.7` 才标记为 `chewing`。
- 正负标签混合且比例不足时标为 `unknown` 或排除。
- 偏侧只在咀嚼事件数达到最低要求时生成。

窗口长度和步长由实验决定。当前基线为 2 秒窗口、0.5 秒步长，但不得写成数据集的永久固定规则。

## 13. 模型输出规范

模型输出单独保存到 `predictions/`：

```text
timestamp_s,
pred_chewing,p_chewing,
pred_side,p_left,p_right,p_bilateral,
pred_chew_event,
estimated_cpm,estimated_ici_s,estimated_ppb_s,
estimated_bite_count,signal_quality,model_version
```

`pred_*` 和 `estimated_*` 字段禁止写入人工真值文件。

## 14. 数据划分规范

1. 首选按 `subject_id` 划分训练、验证和测试集。
2. 数据较少时至少按 `session_id` 分组。
3. 同一受试者、会话或原始录制的重叠窗口不得跨集合。
4. 同一次连续录制拆出的多个文件视为同一分组。
5. 窗口切分必须在数据集合划分之后执行。
6. 最终测试受试者不得用于模型选择、滤波调参、窗口调参或阈值调参。
7. `split_v1.json` 固定记录每个会话所属集合，保证所有模型公平比较。

## 15. 采集场景编码

建议每次会话只设置一个主要任务，但允许同时存在多个真实标签。

| 场景 | 咀嚼状态 | 干扰标签 |
|---|---|---|
| 静坐不动 | `non_chewing` | 全部为 0 |
| 说话 | `non_chewing` | `talking=1` |
| 走路 | `non_chewing` | `walking=1` |
| 摇头 | `non_chewing` | `head_shaking=1` |
| 左侧咀嚼 | `chewing` | 按实际活动填写 |
| 走路并咀嚼 | `chewing` | `walking=1` |
| 摇头并咀嚼 | `chewing` | `head_shaking=1` |
| 喝水 | 通常 `non_chewing` | `drinking=1`、必要时 `swallowing=1` |
| 口香糖 | `chewing` | `food.category=gum` |

## 16. 数据质量检查

每次采集结束自动检查：

- 实际采样率及其波动。
- 时间戳是否递增、是否重复。
- 丢包率和最长连续丢包。
- 是否存在饱和、常数通道或异常尖峰。
- 左右传感器是否接反。
- 各模态是否覆盖完整会话。
- 同步偏移是否超过允许范围。
- 区间标签是否重叠或出现空洞。
- 事件时间是否落在对应咀嚼区间内。
- `bite_id` 是否存在且顺序合理。

存在严重同步错误、设备脱落或主通道失效的会话标记为 `exclude`，不得悄悄修补后加入测试集。

## 17. 旧数据兼容映射

现有数据可按以下方式迁移：

| 旧字段/值 | 新字段/值 |
|---|---|
| `label=chewing` | `chewing_state=chewing` |
| `label=still` | `chewing_state=non_chewing`，活动为静止 |
| `label=speaking` | `chewing_state=non_chewing, talking=1` |
| `activity_label=left_chewing` | `chewing_state=chewing, chewing_side=left` |
| `activity_label=right_chewing` | `chewing_state=chewing, chewing_side=right` |
| `state` | 视为旧模型预测，不作为人工真值 |
| `chewing_side` | 若由实时算法产生，移入 `predictions/` |
| `cpm` | 若由实时算法产生，移入 `predictions/` |

旧文件中只有整段活动标签时，可以用于窗口级状态训练，但不能用于事件级 CPM、PPB 或口数的正式评估。

## 18. 最低采集记录要求

每个正式会话结束前必须确认：

- [ ] `subject_id`、`session_id`、`wearing_id` 已填写。
- [ ] 食物类别与描述已填写。
- [ ] 传感器编号、采样率、单位和位置已记录。
- [ ] 咀嚼与干扰使用独立标签。
- [ ] 同步标记成功。
- [ ] 视频或人工标注来源可用。
- [ ] 原始数据未被滤波或覆盖。
- [ ] 采集异常已写入 `notes`。
- [ ] 数据质量检查已运行。

