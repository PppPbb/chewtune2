# ChewTune 音乐筛选清单

当前版本只生成四个基础 stem：

```text
background.wav
melody.wav
drum.wav
bass.wav
```

不再生成状态变体音频。所有干预状态通过运行时播放参数完成，例如 volume、pan、filter、crossfade、ducking、cue trigger。

## A. 技术对齐检查

| 检查项 | 通过标准 | 失败处理 |
|---|---|---|
| 四层时长一致 | 时长误差小于 20 ms | 重新裁剪或退回生成 |
| 采样率一致 | 全部 44.1 kHz 或全部 48 kHz | 统一重采样 |
| 起点一致 | 第一拍下落点对齐 | 重新对齐 stem 起点 |
| 终点一致 | 四层 loop end 完全一致 | 重新裁剪 |
| BPM 匹配 | 检测 BPM 与目标 BPM 误差小于 2 | time-stretch 或退回生成 |
| 峰值不过载 | peak < -1 dBFS | 降低增益 |
| 响度合适 | integrated loudness 在 -22 到 -16 LUFS | 调整整体响度 |
| 循环平滑 | 首尾 100 ms 无点击、断裂 | 加 crossfade 或退回 |
| 静音异常 | 单层不应全程静音 | 退回生成 |

## B. 听感筛选

| 检查项 | 判断问题 |
|---|---|
| Comfort | 是否适合用餐，不紧张、不催促 |
| Non-intrusive | 是否低打扰，不抢注意力 |
| Stability | 长循环是否稳定、耐听 |
| Clarity | Clear 倾向是否能听出轮廓，但不尖锐 |
| Smoothness | Smooth 倾向是否连贯、柔和 |
| Sweetness identity | 是否保持高音调、明亮、和谐、愉悦、不刺耳 |

## C. Stem 独立性筛选

| Stem / 组合 | 判断问题 |
|---|---|
| background 单独播放 | 是否是单音持续层，带轻微不和谐但不刺耳、不像警报 |
| melody 单独播放 | 是否没有明显 drum / bass / pad 铺底 |
| drum 单独播放 | 是否只是低打扰轻打击乐，不含旋律或和声 |
| bass 单独播放 | 是否没有高频旋律，主要承担根音与和声支撑 |
| background + melody | background 淡入后是否产生轻微低奖励感，但不破坏旋律可接受度 |
| background + drum | background 是否不形成倒计时、警报或强节拍压力 |
| background + bass | 持续单音是否不造成低频浑浊或强烈跑调感 |
| melody + bass | bass 是否支撑旋律，而不是跑调 |
| 四层全开 | cpm_too_fast 状态下是否可接受、非刺耳、可通过淡出恢复正常 |

## D. Playback Controllability 筛选

因为系统不再依赖预生成变体，所以必须验证四层能被播放参数有效控制。

| 控制动作 | 通过标准 |
|---|---|
| 降低 drum volume | 节奏存在感明显降低，音乐不塌陷 |
| 提高 bass volume | 稳定 / 奖励感增强，但不轰头 |
| 降低 melody volume + low-pass | 能形成过快时的 muffled 感，但不刺耳 |
| 淡入 background volume | cpm_too_fast 状态下形成轻微不和谐的低奖励感，淡出后不留残余干扰 |
| pan 调整 | 左右偏侧提示自然，不造成眩晕或声像撕裂 |
| crossfade 2.5-4s | 状态变化连续、细微、可逆 |
| drum low-pass | 能降低打扰感，不产生闷响或失真 |
| cue_trigger | 能从独立 cue library 触发 soft_pop / soft_ding，不依赖音乐 stem 变体 |

## E. Retry / Fallback 检查

| 检查项 | 通过标准 |
|---|---|
| 单 stem retry | 单层失败时只重试失败 stem，不破坏已通过 stem |
| master_spec 保持 | retry 时保留 BPM、key、chord、loop、downbeat |
| fallback 到已审核库 | 连续失败后能选择最近通过审核的 library item |
| safe silence with cue | 最后兜底状态静音四层音乐，只保留低音量独立 cue |
| cue fallback | cue 缺失时跳过 cue_trigger，不阻塞四层播放 |

## 人工评分表

每项 1-5 分，低于 3 分需要返工。

| 项目 | 分数 |
|---|---:|
| 四层合成自然度 |  |
| 用餐舒适度 |  |
| 节拍可跟随度 |  |
| Stem 独立性 |  |
| 播放参数可控性 |  |
| Retry / fallback 可恢复性 |  |
| 循环耐听度 |  |

## 入库判断

```text
技术对齐检查全部通过
+ 人工平均分 >= 4.0
+ Playback controllability >= 4.0
+ 无刺耳、惊吓、过度催促问题
= 可以入库
```
