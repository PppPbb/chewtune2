# 音乐母版规格候选池

母版规格不生成 master.wav，而是为四层 stem 提供共享约束。它是一个受约束的参数生成器。

系统先保证所有候选都符合 ChewTune 的甜味边界：

```text
高音调 / 明亮 / 和谐 / 愉悦 / 不刺耳 / 适合用餐循环
```

然后根据用户偏好做两件事：

```text
1. 用偏好权重决定不同维度 prompt 词的数量比例
2. 用偏好权重影响部分母版规格的采样概率
```

最终输出给四层音乐生成节点的是一份明确 master specification brief。候选池只存在于系统内部，不能把候选值并列写进 prompt。

## 1. 偏好权重 → Prompt 词数量比例

四个权重：

```text
clarity
smoothness
foreground_activity
backgroundness
```

决定 prompt 里各类描述词出现多少。示例公式：

```text
total_style_words = 12
word_count_i = max(1, round(weight_i / sum(weights) * total_style_words))
```

例如：

```json
{
  "clarity": 0.76,
  "smoothness": 0.39,
  "foreground_activity": 0.47,
  "backgroundness": 0.68
}
```

可能映射为：

```text
clarity 4 个词
smoothness 2 个词
foreground_activity 3 个词
backgroundness 3 个词
```

这比只选 high / mid / low 更细，因为偏好会影响 prompt 中描述的“占比”。

## 2. Master Specification Generation

母版规格通过“安全候选池 + 加权采样”生成。

```text
偏好输入
→ 权重计算
→ 候选池加权
→ 随机采样并记录 generation_seed
→ 输出一套明确母版规格
```

同一条入库音乐必须保存 `generation_seed`，这样可以复现当时选出的 key、mode、chord progression、loop length 等参数。

## 3. 当前候选池

### Key / Tonic

调性可以随机，但必须从适合甜味音乐的 key 中选。`clear_bias` 表示该 key 在 Smooth ←→ Clear 轴上更偏 Clear 端的采样倾向，不是第三个独立维度。

| tonic | key | `clear_bias` | 说明 |
|---|---|---:|---|
| `C` | `C major` | `0.72` | 中性、干净，略偏 Clear |
| `D` | `D major` | `0.86` | 更偏 Clear |
| `F` | `F major` | `0.55` | 更接近 Smooth / 平衡 |
| `G` | `G major` | `0.76` | 偏 Clear，开放轻快 |
| `A` | `A major` | `0.90` | 强 Clear 倾向 |

### Mode / Scale

调式受偏好影响，但必须保持愉悦、明亮、和谐：

| mode | 更适合 | scale 生成 |
|---|---|---|
| `major` | 平衡、稳定 | `{tonic} major pentatonic plus leading tone` |
| `major pentatonic` | Smooth、Background | `{tonic} major pentatonic` |
| `lydian-inflected major` | Clear | `{tonic} lydian color over a consonant major base` |

注意：最终 prompt 只会写一个已经选中的 mode，例如 `Mode: major pentatonic.`，不会写候选。

### Chord Progression

和弦走向可以随机，但都必须符合甜味音乐的协和、愉悦、可循环边界：

| 罗马级数 | 倾向 |
|---|---|
| `Imaj7 | V6 | vi7 | IVmaj7` | 平衡甜味 |
| `I6 | V6 | vi7 | IVmaj7` | 更清晰、更轻 |
| `Imaj7 | iii7 | IVmaj7 | V6` | 更偏 Clear |
| `I6 | IVmaj7 | V6 | Imaj7` | 更背景、更简单 |
| `Imaj7 | vi7 | ii7 | V6` | 更主动、更有走向 |

系统会在选定 key 后把罗马级数转成真实和弦。例如 key 为 `D major` 时：

```text
Imaj7 | V6 | vi7 | IVmaj7
→ Dmaj7 | A6 | Bm7 | Gmaj7
```

### Rhythm / Loop

节奏可以随机，但必须适合咀嚼训练：

| 参数 | 候选 |
|---|---|
| `target_bpm` | 来自 `target_cpm` |
| `meter` | 当前固定 `4/4` |
| `loop_bars` | `8` / `16` / `32` |
| `harmonic_rhythm` | `8 bars per chord` / `4 bars per chord` / `2 bars per chord` |
| `drum_pattern` | `soft sparse pulse with clear downbeat` |
| `downbeat_strength` | 由 clarity 加权 |
| `percussion_timbre` | soft clear taps / rounded muted soft taps |
| `syncopation_level` | 由 foreground_activity 加权 |
| `transient_sharpness` | 由 clarity 加权 |
| `downbeat` | `bar 1 beat 1, no pickup` |
| `stem_start` | `0:00.000` |

Background / Low Presence 越高，和声 stem 越倾向慢和声节奏；同时 background.wav 作为 `cpm_too_fast` 的持续单音干预层，可以在过快状态下获得更高可感知度。Foreground 越高，可以允许更快一点的和声或节奏存在感。

## 4. 偏好影响哪些母版规格

| 偏好维度 | 主要影响 |
|---|---|
| `clarity` | 清晰词数量、mode 采样、key 的 `clear_bias`、和弦 `clear_bias`、transient sharpness |
| `smoothness` | 柔和词数量、pentatonic 倾向、连贯音符、持续单音稳定度 |
| `foreground_activity` | 前景词数量、旋律密度、鼓点密度、bass 活动、较主动和弦走向 |
| `backgroundness` | 背景词数量、慢 harmonic rhythm、过快干预单音的可感知度与轻微张力 |

## 5. 四层生成时的使用方式

母版规格只用于统一参数，不生成独立音频：

```text
生成一份明确 master specification brief
→ background prompt 嵌入持续单音/轻微不和谐干预 brief
→ melody prompt 嵌入旋律/音色相关 brief
→ drum prompt 只嵌入节奏/对齐相关 brief
→ bass prompt 只嵌入和声/节奏相关 brief
```

## 6. 硬性规则

```text
候选池可以很大；
随机采样必须记录 seed；
最终 prompt 中每个参数只能有一个明确值；
四层必须共享同一份已采样母版规格；
母版规格不对应任何独立音频文件；
不能把候选值并列写进 prompt。
```
