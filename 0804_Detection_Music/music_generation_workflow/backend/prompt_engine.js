const minimumWeight = 0.15;

const masterOptionPools = {
  keys: [
    { tonic: "C", key: "C major", clearBias: 0.72 },
    { tonic: "D", key: "D major", clearBias: 0.86 },
    { tonic: "F", key: "F major", clearBias: 0.55 },
    { tonic: "G", key: "G major", clearBias: 0.76 },
    { tonic: "A", key: "A major", clearBias: 0.9 },
  ],
  modes: [
    { id: "major", label: "major", clarityBias: 0.55, smoothBias: 0.45 },
    { id: "major_pentatonic", label: "major pentatonic", clarityBias: 0.35, smoothBias: 0.75 },
    { id: "lydian_major", label: "lydian-inflected major", clarityBias: 0.75, smoothBias: 0.35 },
  ],
  chordProgressions: [
    { value: "Imaj7 | V6 | vi7 | IVmaj7", clearBias: 0.55, activeBias: 0.45 },
    { value: "I6 | V6 | vi7 | IVmaj7", clearBias: 0.72, activeBias: 0.35 },
    { value: "Imaj7 | iii7 | IVmaj7 | V6", clearBias: 0.8, activeBias: 0.5 },
    { value: "I6 | IVmaj7 | V6 | Imaj7", clearBias: 0.45, activeBias: 0.2 },
    { value: "Imaj7 | vi7 | ii7 | V6", clearBias: 0.5, activeBias: 0.72 },
  ],
  harmonicRhythms: [
    { value: "8 bars per chord; two chords per 16-bar loop", backgroundBias: 0.86 },
    { value: "4 bars per chord; one full progression per 16-bar loop", backgroundBias: 0.55 },
    { value: "2 bars per chord; two full progressions per 16-bar loop", backgroundBias: 0.22 },
  ],
  loopBars: [8, 16, 32],
};

const wordLibrary = {
  smoothness: {
    low: ["defined articulation", "clean note boundaries", "less blended phrasing"],
    mid: ["smooth but still readable", "rounded articulation"],
    high: ["legato-like motion", "warm connected phrasing", "soft transitions", "silky contour"],
  },
  clarity: {
    low: ["gentle clarity", "softened edges", "less percussive articulation"],
    mid: ["clear but smooth", "balanced note definition"],
    high: ["crisp but non-harsh", "well-defined melody", "transparent upper harmonics", "clear bell-like sweetness"],
  },
  backgroundness: {
    low: ["more foreground", "more audible motif", "clearer rhythmic identity"],
    mid: ["moderate presence", "supportive but noticeable"],
    high: ["low presence", "reduced reward", "subtle tension", "fade-in intervention"],
  },
  foregroundActivity: {
    low: ["low presence", "sparse melody", "minimal rhythmic motion", "non-intrusive"],
    mid: ["moderate foreground presence", "recognizable but gentle motif", "light pulse"],
    high: ["active foreground motif", "more melodic movement", "clearer rhythmic pulse", "engaging but not aggressive"],
  },
};

const stemSystemPrompts = {
  background: [
    "You are generating only the BACKGROUND stem for ChewTune, defined as a sustained intervention tone.",
    "Create a single continuous tone layer that can fade in when chewing CPM is too fast.",
    "The tone should carry mild, non-harsh tension or slight dissonance against the shared key/chord so it reduces reward without feeling alarming.",
    "Do not generate melody, chords, pad harmony, bass, percussion, rhythmic pulse, or atmospheric bed.",
    "Keep it seamless-loop ready, sparse, stable, eating-safe, and easy to mute or fade out.",
  ].join(" "),
  melody: [
    "You are generating only the MELODY stem for ChewTune.",
    "Create a sweet high-register motif with positive-feedback value, aligned to the shared BPM, key, chord progression, downbeat, and loop length.",
    "Do not generate drums, bass line, background pad, or full arrangement.",
    "Keep the melody sparse enough for eating, bright, consonant, pleasant, and non-harsh.",
  ].join(" "),
  drum: [
    "You are generating only the DRUM stem for ChewTune.",
    "This is a controllable low-disturbance percussion layer for adjusting rhythmic presence, not an aggressive beat layer.",
    "Use only timing parameters: BPM, meter, downbeat, loop length, drum pattern, and rhythmic regularness.",
    "Do not generate melody, chords, pads, bass, or pitched musical hooks.",
    "The rhythm must be gentle, sparse, non-aggressive, eating-friendly, and useful as a subtle chewing rhythm cue.",
  ].join(" "),
  bass: [
    "You are generating only the BASS stem for ChewTune.",
    "Use only harmonic and timing parameters: key, chord progression, BPM, downbeat, and loop length.",
    "Do not generate melody, high-register sweet timbre, pads, drums, or full arrangement.",
    "Keep it warm, simple, consonant, supportive, and suitable to fade in as a reward when chewing rhythm is stable.",
  ].join(" "),
};

function clamp(value, min = 0, max = 1) {
  return Math.min(max, Math.max(min, value));
}

function round(value, digits = 2) {
  return Number(value.toFixed(digits));
}

function seededValue(input, label) {
  const source = `${label}|${input.music_preference.smooth_clear.toFixed(2)}|${input.music_preference.foreground_activity.toFixed(2)}|${input.intervention_goal.target_cpm}|${input.intervention_goal.training_goal.join(",")}`;
  let hash = 2166136261;
  for (let i = 0; i < source.length; i += 1) {
    hash ^= source.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

function weightedPick(input, items, scoreFn, label) {
  const scored = items.map((item) => ({ item, score: Math.max(0.01, scoreFn(item)) }));
  const total = scored.reduce((sum, entry) => sum + entry.score, 0);
  let cursor = seededValue(input, label) * total;
  for (const entry of scored) {
    cursor -= entry.score;
    if (cursor <= 0) return entry.item;
  }
  return scored[scored.length - 1].item;
}

function normalizeChordProgression(progression, tonic) {
  const chordsByTonic = {
    C: { Imaj7: "Cmaj7", I6: "C6", V6: "G6", vi7: "Am7", IVmaj7: "Fmaj7", iii7: "Em7", ii7: "Dm7" },
    D: { Imaj7: "Dmaj7", I6: "D6", V6: "A6", vi7: "Bm7", IVmaj7: "Gmaj7", iii7: "F#m7", ii7: "Em7" },
    F: { Imaj7: "Fmaj7", I6: "F6", V6: "C6", vi7: "Dm7", IVmaj7: "Bbmaj7", iii7: "Am7", ii7: "Gm7" },
    G: { Imaj7: "Gmaj7", I6: "G6", V6: "D6", vi7: "Em7", IVmaj7: "Cmaj7", iii7: "Bm7", ii7: "Am7" },
    A: { Imaj7: "Amaj7", I6: "A6", V6: "E6", vi7: "F#m7", IVmaj7: "Dmaj7", iii7: "C#m7", ii7: "Bm7" },
  };
  return progression.split(" | ").map((roman) => chordsByTonic[tonic][roman] || roman).join(" | ");
}

function scaleForMode(tonic, mode) {
  if (mode.id === "major_pentatonic") return `${tonic} major pentatonic`;
  if (mode.id === "lydian_major") return `${tonic} lydian color over a consonant major base, avoid harsh tension`;
  const leadingTone = { C: "B", D: "C#", F: "E", G: "F#", A: "G#" }[tonic];
  return `${tonic} major pentatonic plus ${leadingTone} for gentle leading tone`;
}

function calculateWeights(input) {
  const x = clamp(input.music_preference.smooth_clear);
  const y = clamp(input.music_preference.foreground_activity);
  const m = minimumWeight;
  const weights = {
    clarity: m + (1 - m) * x,
    smoothness: m + (1 - m) * (1 - x),
    foregroundActivity: m + (1 - m) * y,
    backgroundness: m + (1 - m) * (1 - y),
  };

  return {
    weights,
    derived: {
      drumClarity: 0.3 + 0.45 * weights.clarity,
      rhythmicRegularness: 0.78 + 0.17 * weights.backgroundness,
    },
  };
}

function calculatePromptWordCounts(weights) {
  const totalWords = 12;
  const groups = [
    ["clarity", weights.clarity],
    ["smoothness", weights.smoothness],
    ["foregroundActivity", weights.foregroundActivity],
    ["backgroundness", weights.backgroundness],
  ];
  const total = groups.reduce((sum, [, value]) => sum + value, 0);
  const counts = Object.fromEntries(groups.map(([name, value]) => [name, Math.max(1, Math.round((value / total) * totalWords))]));
  let diff = totalWords - Object.values(counts).reduce((sum, value) => sum + value, 0);
  const sorted = [...groups].sort((a, b) => b[1] - a[1]);
  let index = 0;
  while (diff !== 0) {
    const key = sorted[index % sorted.length][0];
    if (diff > 0) {
      counts[key] += 1;
      diff -= 1;
    } else if (counts[key] > 1) {
      counts[key] -= 1;
      diff += 1;
    }
    index += 1;
  }
  return counts;
}

function pickWeightedWords(input, group, count) {
  const candidates = [...wordLibrary[group].low, ...wordLibrary[group].mid, ...wordLibrary[group].high];
  const start = Math.floor(seededValue(input, `${group}-words`) * candidates.length);
  return Array.from({ length: count }, (_, i) => candidates[(start + i) % candidates.length]).join(", ");
}

function generateMasterParameters(input, weights) {
  const key = weightedPick(input, masterOptionPools.keys, (item) => 1 - Math.abs(item.clearBias - weights.clarity), "key");
  const mode = weightedPick(
    input,
    masterOptionPools.modes,
    (item) => 0.55 * (1 - Math.abs(item.clarityBias - weights.clarity)) + 0.45 * (1 - Math.abs(item.smoothBias - weights.smoothness)),
    "mode"
  );
  const chord = weightedPick(
    input,
    masterOptionPools.chordProgressions,
    (item) => 0.5 * (1 - Math.abs(item.clearBias - weights.clarity)) + 0.5 * (1 - Math.abs(item.activeBias - weights.foregroundActivity)),
    "chord"
  );
  const harmonicRhythm = weightedPick(
    input,
    masterOptionPools.harmonicRhythms,
    (item) => 1 - Math.abs(item.backgroundBias - weights.backgroundness),
    "harmonic-rhythm"
  );
  const loopBars = masterOptionPools.loopBars[Math.floor(seededValue(input, "loop-bars") * masterOptionPools.loopBars.length)];

  return {
    generation_seed: Math.round(seededValue(input, "master-seed") * 1000000),
    master_logic: "constrained weighted sampling",
    target_bpm: input.intervention_goal.target_cpm,
    key: key.key,
    tonic: key.tonic,
    mode: mode.label,
    scale: scaleForMode(key.tonic, mode),
    chord_progression: normalizeChordProgression(chord.value, key.tonic),
    harmonic_rhythm: harmonicRhythm.value,
    loop_bars: loopBars,
    meter: "4/4",
    drum_pattern: "soft sparse pulse with clear downbeat",
    drum_density: 0.1 + 0.45 * weights.foregroundActivity,
    downbeat_strength: 0.35 + 0.35 * weights.clarity,
    percussion_timbre: weights.clarity > 0.68 ? "soft clear taps, non-sharp" : "rounded muted soft taps",
    syncopation_level: 0.08 + 0.18 * weights.foregroundActivity,
    transient_sharpness: 0.15 + 0.45 * weights.clarity,
    downbeat: "bar 1 beat 1, no pickup",
    stem_start: "0:00.000",
    loop_end: `exactly after bar ${loopBars} beat 4`,
  };
}

function calculateMusicParams(input, weights) {
  const master = generateMasterParameters(input, weights);
  const counts = calculatePromptWordCounts(weights);
  return {
    ...master,
    prompt_word_ratio: `clarity ${counts.clarity} / smoothness ${counts.smoothness} / foreground ${counts.foregroundActivity} / background ${counts.backgroundness}`,
    sweetness_register: 0.65 + 0.2 * weights.clarity,
    consonance: 0.85,
    melody_density: 0.18 + 0.55 * weights.foregroundActivity,
    melody_articulation: 0.25 + 0.6 * weights.clarity,
    note_connection: 0.25 + 0.6 * weights.smoothness,
    high_frequency_detail: 0.55 + 0.35 * weights.clarity,
    bass_activity: 0.15 + 0.3 * weights.foregroundActivity,
    background_tension: 0.18 + 0.22 * weights.backgroundness,
    background_presence_when_fast: 0.45 + 0.35 * weights.backgroundness,
    sustained_tone_stability: 0.75 + 0.2 * weights.smoothness,
    mix_target_lufs: -19,
  };
}

function buildMasterPrompt(input, params, weights) {
  const counts = calculatePromptWordCounts(weights);
  const smoothClearWords = [
    pickWeightedWords(input, "clarity", counts.clarity),
    pickWeightedWords(input, "smoothness", counts.smoothness),
  ].join(", ");
  const foregroundWords = [
    pickWeightedWords(input, "foregroundActivity", counts.foregroundActivity),
    pickWeightedWords(input, "backgroundness", counts.backgroundness),
  ].join(", ");

  return `Shared master specification brief for ChewTune stems. This is NOT a request to create a standalone master audio track. Use this brief only to keep separately generated layers consistent.

Master specification logic: constrained weighted sampling. Seed: ${params.generation_seed}.
Prompt word ratio: ${params.prompt_word_ratio}.
Loop structure: ${params.loop_bars}-bar seamless loop.
BPM: ${params.target_bpm}.
Key: ${params.key}.
Tonic: ${params.tonic}.
Mode: ${params.mode}.
Scale: ${params.scale}.
Meter: ${params.meter}.
Chord progression: ${params.chord_progression}.
Harmonic rhythm: ${params.harmonic_rhythm}.
Drum pattern: ${params.drum_pattern}; downbeat strength ${round(params.downbeat_strength)}; percussion timbre ${params.percussion_timbre}; syncopation level ${round(params.syncopation_level)}; transient sharpness ${round(params.transient_sharpness)}.
Shared alignment: ${params.downbeat}; stem starts at ${params.stem_start}; loop ends ${params.loop_end}; same loop length, same BPM, and same key reference. Use the same chord progression as a reference only where the stem role needs harmony.
Sweet taste identity: high-pitched where relevant, bright, consonant, harmonious, pleasant, and non-harsh.
Smooth/Clear setting: ${smoothClearWords}.
Background/Foreground setting: ${foregroundWords}.
Eating context: comfortable dynamics, no aggressive percussion, no cinematic tension, no distracting lead unless the requested layer is melody.`;
}

function buildLayerBrief(stem, params, weights) {
  const common = [
    "Shared master specification brief for ChewTune stems.",
    "This is NOT a standalone master audio request.",
    `Master specification logic: constrained weighted sampling. Seed: ${params.generation_seed}.`,
    `Loop structure: ${params.loop_bars}-bar seamless loop.`,
    `BPM: ${params.target_bpm}.`,
    "Shared alignment: same downbeat, same loop length, same BPM.",
  ];

  const layerBriefs = {
    drum: [
      ...common,
      `Meter: ${params.meter}.`,
      `Downbeat: ${params.downbeat}.`,
      `Stem start: ${params.stem_start}.`,
      `Loop end: ${params.loop_end}.`,
      `Rhythmic regularness target: ${round(0.78 + 0.17 * weights.backgroundness)}.`,
      `Drum density: ${round(params.drum_density)}.`,
      `Drum pattern: ${params.drum_pattern}.`,
      `Downbeat strength: ${round(params.downbeat_strength)}.`,
      `Percussion timbre: ${params.percussion_timbre}.`,
      `Syncopation level: ${round(params.syncopation_level)}.`,
      `Transient sharpness: ${round(params.transient_sharpness)}.`,
      "Use timing parameters only. Ignore melody, chord, bass, pad, and high-register timbre descriptors from the master brief.",
    ],
    bass: [
      ...common,
      `Key: ${params.key}.`,
      `Tonic: ${params.tonic}.`,
      `Mode: ${params.mode}.`,
      `Chord progression: ${params.chord_progression}.`,
      `Harmonic rhythm: ${params.harmonic_rhythm}.`,
      `Downbeat: ${params.downbeat}.`,
      `Stem start: ${params.stem_start}.`,
      `Loop end: ${params.loop_end}.`,
      `Consonance target: ${round(params.consonance)}.`,
      `Bass activity: ${round(params.bass_activity)}.`,
      "Use harmonic and timing parameters only. Ignore melody, high-register timbre, pad texture, and drum descriptors from the master brief.",
    ],
    background: [
      ...common,
      `Key reference: ${params.key}.`,
      `Tonic reference: ${params.tonic}.`,
      `Mode reference: ${params.mode}.`,
      `Meter: ${params.meter}.`,
      `Chord progression reference for controlled tension: ${params.chord_progression}.`,
      `Downbeat: ${params.downbeat}.`,
      `Background role: sustained single-tone intervention for cpm_too_fast.`,
      `Tone type: one continuous tone only; no chord stack and no pad bed.`,
      `Mild tension target: ${round(params.background_tension)}.`,
      `Presence when CPM is too fast: ${round(params.background_presence_when_fast)}.`,
      `Sustained tone stability: ${round(params.sustained_tone_stability)}.`,
      `Smoothness: ${round(weights.smoothness)}.`,
      "Use a non-harsh slightly dissonant or unresolved tone against the key/chord reference.",
      "Do not generate ambient pad, harmony bed, melody, rhythm, percussion, bass, or foreground motif.",
      "The layer should be neutral when muted and clearly perceivable only when faded in by playback_policy.cpm_too_fast.",
    ],
    melody: [
      ...common,
      `Key: ${params.key}.`,
      `Tonic: ${params.tonic}.`,
      `Mode: ${params.mode}.`,
      `Scale: ${params.scale}.`,
      `Meter: ${params.meter}.`,
      `Chord progression: ${params.chord_progression}.`,
      `Harmonic rhythm: ${params.harmonic_rhythm}.`,
      `Downbeat: ${params.downbeat}.`,
      `Stem start: ${params.stem_start}.`,
      `Loop end: ${params.loop_end}.`,
      `Sweetness register: ${round(params.sweetness_register)}.`,
      `Melody density: ${round(params.melody_density)}.`,
      `Melody articulation: ${round(params.melody_articulation)}.`,
      `Note connection: ${round(params.note_connection)}.`,
      `High-frequency detail: ${round(params.high_frequency_detail)}.`,
      `Clarity: ${round(weights.clarity)}.`,
      `Smoothness: ${round(weights.smoothness)}.`,
      "Sweet taste identity: high-pitched where relevant, bright, consonant, harmonious, pleasant, and non-harsh.",
      "Use melody parameters only. Ignore drum, bass, and background pad generation.",
    ],
  };

  return layerBriefs[stem].join("\n");
}

function buildStemGenerationParams(stem, params, weights) {
  const base = {
    generation_seed: params.generation_seed,
    target_bpm: params.target_bpm,
    meter: params.meter,
    loop_bars: params.loop_bars,
    downbeat: params.downbeat,
    stem_start: params.stem_start,
    loop_end: params.loop_end,
    mix_target_lufs: params.mix_target_lufs,
  };

  const layerParams = {
    drum: {
      rhythmic_regularness: round(0.78 + 0.17 * weights.backgroundness),
      drum_density: round(params.drum_density),
      downbeat_strength: round(params.downbeat_strength),
      percussion_timbre: params.percussion_timbre,
      syncopation_level: round(params.syncopation_level),
      transient_sharpness: round(params.transient_sharpness),
      drum_pattern: params.drum_pattern,
    },
    bass: {
      key: params.key,
      tonic: params.tonic,
      mode: params.mode,
      chord_progression: params.chord_progression,
      harmonic_rhythm: params.harmonic_rhythm,
      consonance: round(params.consonance),
      bass_activity: round(params.bass_activity),
    },
    background: {
      key_reference: params.key,
      tonic_reference: params.tonic,
      mode_reference: params.mode,
      chord_progression_reference: params.chord_progression,
      background_role: "sustained_single_tone_intervention_for_cpm_too_fast",
      tone_type: "single_continuous_tone",
      background_tension: round(params.background_tension),
      background_presence_when_fast: round(params.background_presence_when_fast),
      sustained_tone_stability: round(params.sustained_tone_stability),
      smoothness: round(weights.smoothness),
    },
    melody: {
      key: params.key,
      tonic: params.tonic,
      mode: params.mode,
      scale: params.scale,
      chord_progression: params.chord_progression,
      harmonic_rhythm: params.harmonic_rhythm,
      sweetness_register: round(params.sweetness_register),
      melody_density: round(params.melody_density),
      melody_articulation: round(params.melody_articulation),
      note_connection: round(params.note_connection),
      high_frequency_detail: round(params.high_frequency_detail),
      clarity: round(weights.clarity),
      smoothness: round(weights.smoothness),
    },
  };

  return { ...base, ...layerParams[stem] };
}

function buildStemRequest(stem, params, weights) {
  return {
    stem,
    system_prompt: stemSystemPrompts[stem],
    user_prompt: buildLayerBrief(stem, params, weights),
    generation_params: buildStemGenerationParams(stem, params, weights),
    output: {
      filename: `${stem}.wav`,
      format: "wav",
      sample_rate: 48000,
      channels: 2,
      loopable: true,
    },
  };
}

function normalizeInput(input) {
  const musicPreference = input.music_preference || input;
  const interventionGoal = input.intervention_goal || input;
  return {
    music_preference: {
      smooth_clear: clamp(Number(musicPreference.smooth_clear ?? 0.5)),
      foreground_activity: clamp(Number(musicPreference.foreground_activity ?? 0.5)),
    },
    intervention_goal: {
      target_cpm: Math.min(120, Math.max(80, Number(interventionGoal.target_cpm ?? 86))),
      training_goal: Array.isArray(interventionGoal.training_goal) ? interventionGoal.training_goal : [],
    },
  };
}

function buildPlaybackPolicy() {
  return {
    mode: "parameter_modulation",
    controls: ["volume", "pan", "filter", "crossfade", "ducking", "cue_trigger"],
    cue_source: {
      type: "independent_cue_library",
      file: "cue_library.json",
      available_cues: ["soft_pop", "soft_ding"],
    },
    presets: {
      normal: {
        background: { volume: 0, pan: 0, lowpass_hz: null },
        melody: { volume: 0.55, pan: 0, lowpass_hz: null },
        drum: { volume: 0.35, pan: 0, lowpass_hz: null },
        bass: { volume: 0.4, pan: 0 },
        transition: { crossfade_ms: 3000 },
      },
      cpm_too_fast: {
        background: { volume: 0.58, pan: 0, lowpass_hz: 2600 },
        melody: { volume: 0.35, pan: 0, lowpass_hz: 2200 },
        drum: { volume: 0.1, pan: 0, lowpass_hz: 1200 },
        bass: { volume: 0.3, pan: 0 },
        transition: { crossfade_ms: 4000 },
      },
      stable_target: {
        background: { volume: 0, pan: 0 },
        melody: { volume: 0.65, pan: 0 },
        drum: { volume: 0.35, pan: 0 },
        bass: { volume: 0.6, pan: 0 },
        transition: { crossfade_ms: 3000 },
      },
      pause_too_long: {
        background: { volume: 0, pan: 0 },
        melody: { volume: 0.2, pan: 0, lowpass_hz: 1800 },
        drum: { volume: 0, pan: 0 },
        bass: { volume: 0.2, pan: 0 },
        cue_trigger: { cue_id: "soft_ding", volume: 0.25 },
        transition: { crossfade_ms: 2500 },
      },
      left_side_bias: {
        background: { volume: 0, pan: 0 },
        melody: { volume: 0.5, pan: -0.2 },
        drum: { volume: 0.3, pan: 0 },
        bass: { volume: 0.4, pan: 0 },
        transition: { crossfade_ms: 3000 },
      },
      right_side_bias: {
        background: { volume: 0, pan: 0 },
        melody: { volume: 0.5, pan: 0.2 },
        drum: { volume: 0.3, pan: 0 },
        bass: { volume: 0.4, pan: 0 },
        transition: { crossfade_ms: 3000 },
      },
    },
  };
}

function buildRetryPolicy() {
  return {
    max_generation_attempts_per_stem: 3,
    retry_triggers: [
      "api_error",
      "empty_or_silent_audio",
      "duration_mismatch_over_20ms",
      "bpm_mismatch_over_2",
      "downbeat_misaligned",
      "clipping_or_loudness_out_of_range",
      "stem_role_contamination",
      "loop_boundary_click",
      "playback_controllability_failed"
    ],
    retry_strategy: {
      attempt_1: "use original stem request",
      attempt_2: "reuse same master_spec and generation_seed; strengthen stem system_prompt exclusions",
      attempt_3: "reuse same master_spec; lower density/activity values for the failed stem"
    },
    preserve_across_retries: [
      "library_id",
      "music_preference",
      "intervention_goal",
      "master_spec",
      "meter",
      "target_bpm",
      "key",
      "chord_progression",
      "loop_bars",
      "downbeat"
    ]
  };
}

function buildFallbackPolicy() {
  return {
    fallback_order: [
      "retry_failed_stem_only",
      "regenerate_all_stems_with_same_master_spec",
      "use_nearest_approved_library_item",
      "use_default_safe_silence_with_cue"
    ],
    default_safe_silence_with_cue: {
      enabled: true,
      playback_state: {
        background: { volume: 0, pan: 0, lowpass_hz: null },
        melody: { volume: 0, pan: 0 },
        drum: { volume: 0, pan: 0 },
        bass: { volume: 0, pan: 0 },
        cue_trigger: { cue_id: "soft_ding", volume: 0.18 },
        transition: { crossfade_ms: 3000 }
      }
    },
    cue_fallback: {
      if_cue_missing: "skip cue_trigger and continue layer playback",
      if_cue_too_sharp: "replace with soft_ding at lower volume"
    }
  };
}

function buildChewTuneGenerationJob(rawInput) {
  const input = normalizeInput(rawInput);
  const { weights, derived } = calculateWeights(input);
  const params = calculateMusicParams(input, weights);
  const stems = ["background", "melody", "drum", "bass"];

  return {
    job_type: "chewtune_stem_generation",
    api_version: "2026-06-13",
    library_id: `smoothclear_${input.music_preference.smooth_clear.toFixed(2)}_foreground_${input.music_preference.foreground_activity.toFixed(2)}_cpm_${String(input.intervention_goal.target_cpm).padStart(3, "0")}`,
    music_preference: input.music_preference,
    intervention_goal: input.intervention_goal,
    style_weights: {
      smoothness: round(weights.smoothness),
      clarity: round(weights.clarity),
      foreground_activity: round(weights.foregroundActivity),
      backgroundness: round(weights.backgroundness),
    },
    derived_weights: {
      drum_clarity: round(derived.drumClarity),
      rhythmic_regularness: round(derived.rhythmicRegularness),
    },
    master_spec_brief: buildMasterPrompt(input, params, weights),
    master_spec: params,
    stem_requests: stems.map((stem) => buildStemRequest(stem, params, weights)),
    postprocess_plan: {
      alignment_check: true,
      loudness_normalization: true,
      stem_independence_check: true,
      playback_controllability_check: true,
    },
    retry_policy: buildRetryPolicy(),
    fallback_policy: buildFallbackPolicy(),
    playback_policy: buildPlaybackPolicy(),
  };
}

if (typeof module !== "undefined") {
  module.exports = {
    buildChewTuneGenerationJob,
    calculateWeights,
    calculateMusicParams,
    buildStemRequest,
    stemSystemPrompts,
  };
}
