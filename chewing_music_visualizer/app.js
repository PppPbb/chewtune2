const canvas = document.getElementById("ringCanvas");
const ctx = canvas.getContext("2d");

const els = {
  statusDot: document.getElementById("statusDot"),
  statusText: document.getElementById("statusText"),
  cpmValue: document.getElementById("cpmValue"),
  ringCenter: document.querySelector(".ring-center"),
  metricCpm: document.getElementById("metricCpm"),
  paceHint: document.getElementById("paceHint"),
  stabilityValue: document.getElementById("stabilityValue"),
  stabilityHint: document.getElementById("stabilityHint"),
  sideValue: document.getElementById("sideValue"),
  sideHint: document.getElementById("sideHint"),
  thresholdValue: document.getElementById("thresholdValue"),
  thresholdControlValue: document.getElementById("thresholdControlValue"),
  musicMode: document.getElementById("musicMode"),
  chewButton: document.getElementById("chewButton"),
  leftButton: document.getElementById("leftButton"),
  rightButton: document.getElementById("rightButton"),
  centerButton: document.getElementById("centerButton"),
  pauseButton: document.getElementById("pauseButton")
};

const colors = {
  normal: "#7b78ff",
  stable: "#64c99a",
  fast: "#ff9b53",
  pause: "#c7c8d0"
};

const state = {
  threshold: 90,
  ppbTarget: 4,
  side: "center",
  targetBias: 0,
  visualBias: 0,
  chewing: false,
  paused: false,
  lastChewAt: 0,
  chewTimes: [],
  cpm: 0,
  stability: 0,
  frameTime: performance.now()
};

const musicLayers = {
  melody: document.querySelector('[data-layer="melody"]'),
  drum: document.querySelector('[data-layer="drum"]'),
  bass: document.querySelector('[data-layer="bass"]'),
  background: document.querySelector('[data-layer="background"]')
};

Object.values(musicLayers).forEach((layer, layerIndex) => {
  const bars = layer.querySelector(".level-bars");
  for (let index = 0; index < 10; index += 1) {
    const bar = document.createElement("span");
    bar.style.height = `${5 + ((index * 7 + layerIndex * 3) % 15)}px`;
    bar.style.animationDelay = `${index * -70}ms`;
    bars.appendChild(bar);
  }
});

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function mixColor(from, to, amount) {
  const ratio = clamp(amount, 0, 1);
  const fromRgb = from.match(/\w\w/g).map((value) => parseInt(value, 16));
  const toRgb = to.match(/\w\w/g).map((value) => parseInt(value, 16));
  const mixed = fromRgb.map((value, index) =>
    Math.round(value + (toRgb[index] - value) * ratio)
      .toString(16)
      .padStart(2, "0")
  );
  return `#${mixed.join("")}`;
}

function setButtonPressed(button) {
  button.classList.add("pressed");
  window.setTimeout(() => button.classList.remove("pressed"), 110);
}

function setSide(side) {
  state.side = side;
  state.targetBias = side === "left" ? -0.95 : side === "right" ? 0.95 : 0;
  els.leftButton.classList.toggle("active", side === "left");
  els.rightButton.classList.toggle("active", side === "right");
  els.centerButton.classList.toggle("active", side === "center");
  els.sideValue.textContent = side === "left" ? "Left" : side === "right" ? "Right" : "Center";
  els.sideHint.textContent =
    side === "left" ? "Left field dominant" :
    side === "right" ? "Right field dominant" :
    "Balanced";
}

function registerChew() {
  const now = performance.now();
  if (state.paused) state.paused = false;

  state.chewing = true;
  state.lastChewAt = now;
  state.chewTimes.push(now);
  state.chewTimes = state.chewTimes.filter((time) => now - time <= 12000).slice(-16);
  calculateMetrics();
  setButtonPressed(els.chewButton);
}

function togglePause() {
  state.paused = !state.paused;
  state.chewing = !state.paused && performance.now() - state.lastChewAt < 1800;
  if (state.paused) clearChewingMetrics();
  els.pauseButton.classList.toggle("active", state.paused);
  setButtonPressed(els.pauseButton);
}

function clearChewingMetrics({ preserveStability = true } = {}) {
  state.chewTimes = [];
  state.cpm = 0;
  if (!preserveStability) state.stability = 0;
}

function calculateMetrics() {
  const times = state.chewTimes;
  if (times.length < 2) {
    state.cpm = 0;
    return;
  }

  const intervals = [];
  for (let index = 1; index < times.length; index += 1) {
    intervals.push((times[index] - times[index - 1]) / 1000);
  }

  const recent = intervals.slice(-8);
  const average = recent.reduce((sum, value) => sum + value, 0) / recent.length;
  state.cpm = clamp(Math.round(60 / average), 0, 220);

  if (recent.length >= 2) {
    const variance = recent.reduce((sum, value) => sum + ((value - average) ** 2), 0) / recent.length;
    const coefficient = Math.sqrt(variance) / Math.max(average, 0.001);
    state.stability = clamp(1 - coefficient, 0, 1);
  }
}

function setThreshold(delta) {
  state.threshold = clamp(state.threshold + delta, 40, 180);
  els.thresholdValue.textContent = state.threshold;
  els.thresholdControlValue.textContent = state.threshold;
}

function reset() {
  state.side = "center";
  state.targetBias = 0;
  state.visualBias = 0;
  state.chewing = false;
  state.paused = false;
  state.lastChewAt = 0;
  state.chewTimes = [];
  state.cpm = 0;
  state.stability = 0;
  setSide("center");
  els.pauseButton.classList.remove("active");
}

function getMode() {
  if (!state.chewing) return "pause";
  if (state.cpm > state.threshold) return "fast";
  if (state.stability >= 0.82 && state.cpm > 0) return "stable";
  return "normal";
}

function updateMusicLayers(mode) {
  const active = {
    pause: [],
    normal: ["melody", "drum"],
    stable: ["melody", "drum", "bass"],
    fast: ["drum", "background"]
  }[mode];

  Object.entries(musicLayers).forEach(([name, layer]) => {
    layer.classList.toggle("active", active.includes(name));
  });
  els.musicMode.textContent = {
    pause: "Paused",
    normal: "Full mix",
    stable: "Bass unlocked",
    fast: "Fast warning"
  }[mode];
}

function updateInterface(now) {
  const sinceLastChew = state.lastChewAt ? (now - state.lastChewAt) / 1000 : 0;
  if (!state.paused && state.chewing && sinceLastChew > 1.8) {
    state.chewing = false;
    clearChewingMetrics();
  }
  calculateMetrics();

  const mode = getMode();
  const ppb = state.chewing || !state.lastChewAt ? 0 : sinceLastChew;
  const ppbRemaining = Math.max(0, state.ppbTarget - ppb);

  const countdownActive = mode === "pause" && state.lastChewAt && ppbRemaining > 0;
  els.cpmValue.textContent = countdownActive ? ppbRemaining.toFixed(1) : state.cpm;
  els.metricCpm.textContent = state.cpm;
  els.stabilityValue.textContent = state.stability.toFixed(2);

  els.paceHint.textContent =
    mode === "fast" ? `${state.cpm - state.threshold} CPM above target` :
    state.cpm ? "Within personalized target" : "No active rhythm";
  els.stabilityHint.textContent =
    state.chewTimes.length < 3 ? "Insufficient data" :
    state.stability >= 0.82 ? "Stable rhythm · Bass unlocked" : "Rhythm fluctuating";
  els.ringCenter.classList.toggle("fast", mode === "fast");
  els.statusDot.className = `status-dot ${mode === "fast" ? "fast" : state.chewing ? "active" : ""}`;
  els.statusText.textContent = {
    pause: "Idle",
    normal: "Normal pace",
    stable: "Stable pace",
    fast: "Above threshold"
  }[mode];

  updateMusicLayers(mode);
  return { mode, ppb };
}

function drawRing(now, mode, ppb) {
  const width = canvas.width;
  const center = width / 2;
  const baseRadius = 238;
  const bars = 96;
  const color = colors[mode];
  const elapsedMs = clamp(now - state.frameTime, 0, 100);
  state.frameTime = now;

  ctx.clearRect(0, 0, width, width);
  // Time-based smoothing reaches about 95% of the selected side after 10 seconds.
  const sideTransitionAlpha = 1 - Math.exp(-elapsedMs / 3340);
  state.visualBias += (state.targetBias - state.visualBias) * sideTransitionAlpha;
  ctx.save();
  ctx.translate(center, center);

  for (let index = 0; index < bars; index += 1) {
    const angle = (index / bars) * Math.PI * 2 - Math.PI / 2;
    const horizontalPosition = Math.cos(angle);
    const sideAlignment = horizontalPosition * state.visualBias;
    const favoredAmount = 1 + sideAlignment * 1.08;
    const organicWave =
      Math.sin(index * 1.73 + now * 0.004) * 0.18 +
      Math.sin(index * 0.47 - now * 0.002) * 0.12;
    const activity = mode === "pause" ? 0.15 : 0.72 + organicWave;
    const length = clamp(13 + 45 * activity * favoredAmount, 5, 112);
    const inner = baseRadius - length / 2;
    const outer = baseRadius + length / 2;

    ctx.beginPath();
    ctx.moveTo(Math.cos(angle) * inner, Math.sin(angle) * inner);
    ctx.lineTo(Math.cos(angle) * outer, Math.sin(angle) * outer);
    const biasStrength = clamp((Math.abs(state.visualBias) - 0.05) / 0.9, 0, 1);
    const oppositeStrength = clamp((-sideAlignment - 0.02) / 0.78, 0, 1);
    const grayBlend = biasStrength * oppositeStrength;
    ctx.strokeStyle = mixColor(color, "#c7c8d0", grayBlend);
    ctx.globalAlpha = mode === "pause" ? 0.72 : 0.96 - grayBlend * 0.5;
    ctx.lineWidth = 7;
    ctx.lineCap = "round";
    ctx.stroke();
  }

  const ppbRadius = 164;
  const ppbProgress = state.lastChewAt && mode === "pause"
    ? clamp(ppb / state.ppbTarget, 0, 1)
    : 0;

  ctx.globalAlpha = 1;
  ctx.lineCap = "round";
  ctx.lineWidth = 9;
  ctx.beginPath();
  ctx.arc(0, 0, ppbRadius, 0, Math.PI * 2);
  ctx.strokeStyle = "#eeeeF3";
  ctx.stroke();

  if (ppbProgress > 0) {
    ctx.beginPath();
    ctx.arc(
      0,
      0,
      ppbRadius,
      -Math.PI / 2,
      -Math.PI / 2 + Math.PI * 2 * ppbProgress
    );
    ctx.strokeStyle = ppbProgress >= 1 ? colors.stable : colors.normal;
    ctx.stroke();
  }

  ctx.beginPath();
  ctx.arc(0, 0, 145, 0, Math.PI * 2);
  ctx.strokeStyle = mode === "pause" ? "#f0f0f3" : `${color}18`;
  ctx.globalAlpha = 1;
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.restore();
}

function animate(now) {
  const view = updateInterface(now);
  drawRing(now, view.mode, view.ppb);
  requestAnimationFrame(animate);
}

document.addEventListener("keydown", (event) => {
  if (event.repeat) return;
  if (event.code === "Space") {
    event.preventDefault();
    registerChew();
  } else if (event.key.toLowerCase() === "a") {
    setSide("left");
    setButtonPressed(els.leftButton);
  } else if (event.key.toLowerCase() === "d") {
    setSide("right");
    setButtonPressed(els.rightButton);
  } else if (event.key.toLowerCase() === "s") {
    setSide("center");
    setButtonPressed(els.centerButton);
  } else if (event.key.toLowerCase() === "p") {
    togglePause();
  } else if (event.key === "ArrowUp") {
    setThreshold(5);
  } else if (event.key === "ArrowDown") {
    setThreshold(-5);
  }
});

els.chewButton.addEventListener("click", registerChew);
els.leftButton.addEventListener("click", () => setSide("left"));
els.rightButton.addEventListener("click", () => setSide("right"));
els.centerButton.addEventListener("click", () => setSide("center"));
els.pauseButton.addEventListener("click", togglePause);
document.getElementById("thresholdDown").addEventListener("click", () => setThreshold(-5));
document.getElementById("thresholdUp").addEventListener("click", () => setThreshold(5));
document.getElementById("resetButton").addEventListener("click", reset);

requestAnimationFrame(animate);
