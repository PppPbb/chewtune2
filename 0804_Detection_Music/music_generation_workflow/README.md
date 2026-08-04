# ChewTune 音乐库生成工作流方案

本文件夹用于描述 ChewTune 如何从用户音乐偏好生成可分层、可调度、可入库的音乐素材。

核心设定：

- 所有音乐都必须具备 ChewTune 的“甜味特征”：高音调、明亮、和谐、愉悦、不刺耳。
- 偏好不再决定“甜不甜”，而是在甜味音乐内部调节两个轴：
  - `smooth_clear`：从 Smooth（柔和、连贯、圆润）到 Clear（清晰、颗粒明确、轮廓清楚）。
  - `foreground_activity`：从 Background / Low Presence（背景化、低存在感）到 Active / Foreground（更前景、更有旋律/节奏活动）。
- 两个轴拆成四个生成指标：
  - `smoothness`
  - `clarity`
  - `backgroundness`
  - `foreground_activity`
- 注意：`backgroundness` 是偏好权重名，不等于 background.wav 的音乐职责。当前 `background.wav` 被定义为用户咀嚼过快时淡入的持续单音干预层，带轻微、不刺耳的不和谐感。

## 文件说明

| 文件 | 用途 |
|---|---|
| `workflow_spec.md` | 完整工作流：节点、输入输出、公式、筛选、入库方式 |
| `style_model.json` | 二维偏好模型、权重公式、参数映射表 |
| `prompt_library.json` | 提示词库，以及参数到提示词的映射关系 |
| `metadata_template.json` | 每套音乐入库时的 metadata 模板 |
| `screening_checklist.md` | 音乐筛选的人工与自动检查清单 |
| `master_parameter_options.md` | 音乐母版规格候选池、加权采样规则与四层使用方式 |
| `master_parameter_options.json` | 母版规格候选池与采样规则的结构化版本 |
| `playback_policy.json` | 运行时播放参数调度策略 |
| `cue_library.json` | 独立提示音库，供 `cue_trigger` 调用 |
| `backend/` | 可接音乐生成 API 的 payload 生成逻辑与 schema |

## 最小实现链路

```text
music_preference + intervention_goal
→ 权重计算
→ Master Specification Generation
→ 四层 API Request
→ 生成 background / melody / drum / bass 四个基础 stem
→ retry / fallback
→ 后处理与筛选
→ 入库
→ 运行时 playback parameter modulation
```
