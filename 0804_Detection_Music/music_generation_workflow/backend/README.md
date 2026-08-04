# ChewTune 后端 Prompt 接入说明

这个文件夹定义音乐生成 API 前的最后一层后端结构。目标是把用户偏好转换成可直接发送给音乐生成服务的请求。

## 输出结构

后端最终输出一个 `ChewTune Stem Generation Payload`：

```json
 {
  "job_type": "chewtune_stem_generation",
  "library_id": "smoothclear_0.72_foreground_0.38_cpm_086",
  "music_preference": {},
  "intervention_goal": {},
  "master_spec": {},
  "stem_requests": [
    {
      "stem": "drum",
      "system_prompt": "固定 Drum 层职责，低打扰轻打击乐...",
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
  ],
  "postprocess_plan": {
    "alignment_check": true,
    "loudness_normalization": true,
    "stem_independence_check": true,
    "playback_controllability_check": true
  },
  "retry_policy": {
    "max_generation_attempts_per_stem": 3
  },
  "fallback_policy": {
    "fallback_order": ["retry_failed_stem_only", "use_nearest_approved_library_item"]
  },
  "playback_policy": {
    "mode": "parameter_modulation",
    "controls": ["volume", "pan", "filter", "crossfade", "cue_trigger"],
    "cue_source": {
      "type": "independent_cue_library",
      "file": "cue_library.json"
    }
  }
}
```

## System Prompt / User Prompt 分工

| 字段 | 来源 | 是否随用户偏好变化 | 作用 |
|---|---|---:|---|
| `system_prompt` | 每层固定模板 | 否 | 限定这一层只能生成什么，不能生成什么 |
| `user_prompt` | 本次 master spec + layer brief | 是 | 提供 BPM、key、mode、chord、drum pattern、loop、维度词等具体参数 |
| `generation_params` | 结构化参数 | 是 | 给 API 或后处理使用，避免只靠自然语言解析 |

## 接 API 时的调用方式

伪代码：

```js
const payload = buildChewTuneGenerationJob(input);

for (const request of payload.stem_requests) {
  const audio = await musicApi.generate({
    system: request.system_prompt,
    prompt: request.user_prompt,
    params: request.generation_params,
    output: request.output
  });

  await saveAudio(payload.library_id, request.output.filename, audio);
}

await saveJson(payload.library_id, "prompts.json", payload);
await saveJson(payload.library_id, "playback_policy.json", payload.playback_policy);
await saveJson(payload.library_id, "cue_library.json", cueLibrary);
await postprocess.alignAndNormalize(payload);
await screening.runChecks(payload);
```

如果单个 stem 生成失败或筛选失败，先按 `retry_policy` 重试失败 stem；仍失败时按 `fallback_policy` 使用最近通过审核的库条目，或进入四层静音加低音量独立 cue 的安全兜底。background 现在是过快干预用的轻微不和谐持续单音，不再作为安全背景层兜底。

`cue_trigger` 不来自四层音乐 stem。它引用独立 `cue_library.json` 中的短提示音，例如 `soft_pop` 和 `soft_ding`。

## 接入 API 时需要替换的部分

你只需要实现：

```js
musicApi.generate({
  system,
  prompt,
  params,
  output
})
```

不同音乐生成服务字段名可能不同，但映射关系保持不变：

```text
system_prompt → system / instructions / negative constraints
user_prompt → prompt / input / generation prompt
generation_params → metadata / tags / model controls
output → 保存文件名与格式
```

## 文件

| 文件 | 用途 |
|---|---|
| `api_payload_schema.json` | 后端 payload 的 JSON Schema |
| `prompt_engine.js` | 可复用的 prompt/payload 生成逻辑 |
