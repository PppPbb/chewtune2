# ChewTune 音乐库生成工作流

## 0. 核心架构

ChewTune 音乐库不是播放列表，而是一套可被咀嚼状态实时调度的四层音乐素材系统。

当前架构分为四层：

```text
展示层 frontend/
生成逻辑层 backend/prompt_engine.js
配置与规范层 style_model / prompt_library / master_parameter_options / metadata / screening
运行调度层 playback_policy / real-time chewing state / layer-level parameter modulation
```

系统只生成四个基础 stem：

```text
background.wav
melody.wav
drum.wav
bass.wav
```

不生成 `melody_clear`、`drum_reduced` 等状态变体。所有干预状态通过运行时播放参数实现。

## 1. 节点 A：用户输入

输入分为两部分：

```json
{
  "music_preference": {
    "smooth_clear": 0.72,
    "foreground_activity": 0.38
  },
  "intervention_goal": {
    "target_cpm": 86,
    "training_goal": ["slow_down", "stable_rhythm"]
  }
}
```

`music_preference` 决定音乐质感：

```text
X 轴：Smooth ←→ Clear
Y 轴：Background / Low Presence ←→ Active / Foreground
```

`intervention_goal` 决定后续实时播放策略。用户偏好 foreground 不代表系统会持续增强节奏；如果检测到 CPM 过快，运行时仍会降低 drum 音量、降低 melody presence，并淡入 background 持续单音干预层。

## 2. 节点 B：权重计算

二维偏好转为四个内部权重：

| 内部变量 | 展示名称 |
|---|---|
| `clarity` | Clear |
| `smoothness` | Smooth |
| `foreground_activity` | Foreground Activity |
| `backgroundness` | Background Presence |

公式：

```text
x = music_preference.smooth_clear
y = music_preference.foreground_activity
m = 0.15

clarity = m + (1 - m) * x
smoothness = m + (1 - m) * (1 - x)
foreground_activity = m + (1 - m) * y
backgroundness = m + (1 - m) * (1 - y)
```

这四个权重不会直接生成音乐，而是影响：

```text
1. prompt 词汇比例
2. Master Specification 参数采样概率
3. melody / drum / bass / background 的连续控制参数
```

## 3. 节点 C：母版规格生成

推荐名称：

```text
Master Specification Generation
```

它不是 `master.wav`，也不是一首完整音乐，而是所有 stem 共享的一套约束。

输出示例：

```json
{
  "generation_seed": 421608,
  "master_logic": "constrained weighted sampling",
  "target_bpm": 86,
  "key": "D major",
  "mode": "major",
  "scale": "D major pentatonic plus C# for gentle leading tone",
  "chord_progression": "D6 | A6 | Bm7 | Gmaj7",
  "harmonic_rhythm": "4 bars per chord; one full progression per 16-bar loop",
  "meter": "4/4",
  "loop_bars": 16,
  "drum_pattern": "soft sparse pulse with clear downbeat",
  "drum_density": 0.31,
  "downbeat_strength": 0.62,
  "percussion_timbre": "soft clear taps, non-sharp",
  "syncopation_level": 0.17,
  "transient_sharpness": 0.49
}
```

母版规格由安全候选池加权采样生成：

```text
key / mode / chord / drum pattern / loop / seed
```

其中 drum 相关约束用于保证 drum layer 是低打扰轻打击乐层，而不是随意生成的强节拍。

## 4. 节点 D：Prompt / API Request 生成

每个 stem 输出独立 API request：

```json
{
  "stem": "drum",
  "system_prompt": "固定 Drum 层职责...",
  "user_prompt": "本次采样出的 drum layer brief...",
  "generation_params": {},
  "output": {
    "filename": "drum.wav",
    "format": "wav",
    "sample_rate": 48000,
    "channels": 2,
    "loopable": true
  }
}
```

字段分工：

| 字段 | 作用 |
|---|---|
| `system_prompt` | 固定层职责，限制这一层只能生成什么 |
| `user_prompt` | 本次 master spec + layer brief |
| `generation_params` | 结构化参数，方便 API 和后处理读取 |
| `output` | 输出文件名与格式 |

四层职责：

| 层 | 职责 | 排除内容 |
|---|---|---|
| `background` | 咀嚼过快时淡入的持续单音干预层，带轻微、不刺耳的不和谐感 | 主旋律、和弦铺底、pad 氛围、明显鼓点、低频主导 |
| `melody` | 旋律轮廓、清晰度、连贯度 | drum、bass、pad 铺底 |
| `drum` | 轻打击乐、downbeat、节奏存在感 | 主旋律、和声、bass、强鼓点 |
| `bass` | 根音、和声支撑、稳定感 | melody、drum、high-register timbre |

Drum 层定义：

```text
A controllable percussion layer for adjusting rhythmic presence,
not an aggressive beat layer.
```

## 5. 节点 E：四层音乐生成

后端入口：

```js
const payload = buildChewTuneGenerationJob(input);

for (const request of payload.stem_requests) {
  await musicApi.generate({
    system: request.system_prompt,
    prompt: request.user_prompt,
    params: request.generation_params,
    output: request.output
  });
}
```

四个 stem 必须共享：

```text
BPM
key reference
mode reference
meter
loop bars
downbeat
start time
end time
```

`chord progression` 是共享音乐骨架的一部分，但不是每层都要实际生成和弦。melody / bass 使用它保持和声一致；background 只把它作为轻微张力参考；drum 忽略和声内容。

## 6. 节点 F：后处理

不生成音频变体。后处理只做基础质量控制：

```json
{
  "postprocess_plan": {
    "alignment_check": true,
    "loudness_normalization": true,
    "stem_independence_check": true,
    "playback_controllability_check": true
  }
}
```

## 7. 节点 G：Retry / Fallback

生成失败或筛选失败时，系统按层重试，而不是直接丢弃整套音乐。

```json
{
  "retry_policy": {
    "max_generation_attempts_per_stem": 3,
    "retry_triggers": [
      "api_error",
      "empty_or_silent_audio",
      "duration_mismatch_over_20ms",
      "bpm_mismatch_over_2",
      "downbeat_misaligned",
      "stem_role_contamination",
      "loop_boundary_click",
      "playback_controllability_failed"
    ]
  }
}
```

重试原则：

```text
优先只重试失败 stem
保留同一份 master_spec
保留 BPM / key / chord / loop / downbeat
第二次强化 system_prompt 排除项
第三次降低失败层的 density / activity
```

Fallback 顺序：

```text
retry_failed_stem_only
→ regenerate_all_stems_with_same_master_spec
→ use_nearest_approved_library_item
→ use_default_safe_silence_with_cue
```

如果进入 `default_safe_silence_with_cue`，系统静音四层音乐，只保留低音量独立 cue。因为 background 现在是轻微不和谐的过快干预层，不再适合作为安全兜底。

## 8. 节点 H：运行调度层

运行时通过 `playback_policy.json` 控制四层播放参数：

```text
volume
pan
filter
low-pass / high-pass
reverb send
crossfade
ducking
cue trigger
```

`cue_trigger` 来自独立 cue library，而不是四层 stem 或预生成状态变体：

```text
cue_library.json
  soft_pop  → cue/pop.wav
  soft_ding → cue/ding.wav
```

cue library 用于短促、温和、不惊吓的提示音。例如 `pause_too_long` 状态触发 `soft_ding`。

示例状态：

| 状态 | 调度策略 |
|---|---|
| `normal` | background 静音，melody / drum / bass 平衡播放 |
| `cpm_too_fast` | 淡入轻微不和谐的 background 持续单音，降低 melody，drum 降音量并 low-pass |
| `stable_target` | bass 提高，形成稳定 / 奖励感 |
| `pause_too_long` | drum 静音，melody low-pass，通过 cue library 触发 `soft_ding` |
| `left_side_bias` / `right_side_bias` | 轻微 pan 调整，辅助偏侧提示 |

## 9. 节点 I：筛选

筛选分为四类：

```text
A. 技术对齐检查
B. 听感筛选
C. Stem 独立性筛选
D. Playback controllability 筛选
```

因为系统依赖播放参数而不是预生成变体，所以必须验证：

```text
降低 drum 是否能明显降低节奏存在感
提高 bass 是否有稳定 / 奖励感
melody 降音量 + low-pass 是否能形成 muffled 效果
background 淡入是否能在 cpm_too_fast 状态产生轻微但不刺耳的低奖励感
pan 调整是否自然
crossfade 是否连续、细微、可逆
```

完整清单见 `screening_checklist.md`。

## 10. 节点 J：入库

目录结构：

```text
music_library/
  user_u001/
    smoothclear_0.72_foreground_0.38_cpm_086_v001/
      audio/
        background.wav
        melody.wav
        drum.wav
        bass.wav
      prompts.json
      metadata.json
      screening_report.json
      playback_policy.json
      cue_library.json
```

metadata 记录：

```json
{
  "music_preference": {},
  "intervention_goal": {},
  "style_weights": {},
  "master_spec": {},
  "audio_files": {},
  "runtime_control": {
    "strategy": "layer-level playback parameter modulation",
    "controls": ["volume", "pan", "filter", "crossfade", "cue_trigger"],
    "cue_library": "cue_library.json"
  }
}
```

## 11. 最小可行版本

MVP 只需要：

```text
1 个 music_preference
1 个 intervention_goal
1 份 master_spec
4 个基础 stem
1 份 prompts.json
1 份 metadata.json
1 份 screening_report.json
1 份 playback_policy.json
1 份 cue_library.json
1 套 retry / fallback policy
```

主链路：

```text
music_preference + intervention_goal
→ 权重计算
→ Master Specification Generation
→ 四层 API Request
→ 四个基础 stem
→ retry / fallback
→ 后处理与筛选
→ 入库
→ 运行时 playback parameter modulation
```
