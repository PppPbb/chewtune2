const FLUSH_INTERVAL_MS = 5000;
const BATCH_SIZE = 20;

function formatLocalTime(timestamp) {
  const date = new Date(timestamp);
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

class MealRecorder {
  constructor(onStatus = () => {}) {
    this.onStatus = onStatus;
    this.sessionId = "";
    this.startTimeMs = 0;
    this.sequence = 0;
    this.buffer = [];
    this.flushing = false;
    this.starting = null;
    this.flushTimer = null;
    this.music = { state: "pause", layerMask: 0, pan: 0 };
  }

  async start(metadata = {}) {
    if (this.sessionId) return this.sessionId;
    if (this.starting) return this.starting;
    this.startTimeMs = Date.now();
    this.starting = wx.cloud.callFunction({
      name: "meal-data",
      data: {
        action: "start",
        startTimeMs: this.startTimeMs,
        startTimeClient: new Date(this.startTimeMs).toISOString(),
        startTimeLocal: formatLocalTime(this.startTimeMs),
        ...metadata
      }
    }).then((response) => {
      this.sessionId = response.result.sessionId;
      this.onStatus(`云端餐次已创建 ${this.sessionId}`);
      this.flushTimer = setInterval(() => this.flush(), FLUSH_INTERVAL_MS);
      this.flush();
      return this.sessionId;
    }).catch((error) => {
      this.onStatus(`云端餐次创建失败: ${error.errMsg || error}`);
      throw error;
    }).finally(() => {
      this.starting = null;
    });
    return this.starting;
  }

  updateMusic(state, layerMask, pan) {
    this.music = {
      state: state || "pause",
      layerMask: Number(layerMask) || 0,
      pan: Number(pan) || 0
    };
  }

  record(sample) {
    const recordedAtMs = Date.now();
    this.buffer.push({
      sequence: this.sequence,
      recordedAtMs,
      recordedAtClient: new Date(recordedAtMs).toISOString(),
      recordedAtLocal: formatLocalTime(recordedAtMs),
      offsetMs: this.startTimeMs ? recordedAtMs - this.startTimeMs : 0,
      ...sample,
      musicState: this.music.state,
      layerMask: this.music.layerMask,
      pan: this.music.pan
    });
    this.sequence += 1;
    if (this.buffer.length > 500) {
      this.buffer.splice(0, this.buffer.length - 500);
      this.onStatus("云端缓冲已满，已保留最近 500 条数据");
    }
    if (this.buffer.length >= BATCH_SIZE) this.flush();
  }

  async flush() {
    if (this.flushing || !this.sessionId || this.buffer.length === 0) return;
    this.flushing = true;
    const batch = this.buffer.splice(0, BATCH_SIZE);
    try {
      await wx.cloud.callFunction({
        name: "meal-data",
        data: { action: "append", sessionId: this.sessionId, samples: batch }
      });
      this.onStatus(`云端已保存 ${batch.length} 条咀嚼数据`);
    } catch (error) {
      this.buffer.unshift(...batch);
      this.onStatus(`云端数据保存失败: ${error.errMsg || error}`);
    } finally {
      this.flushing = false;
      if (this.buffer.length >= BATCH_SIZE) this.flush();
    }
  }

  async finish(summary = {}, status = "completed") {
    if (this.flushTimer) clearInterval(this.flushTimer);
    this.flushTimer = null;
    if (this.starting) {
      try {
        await this.starting;
      } catch (error) {
        return "";
      }
    }
    const flushDeadline = Date.now() + 10000;
    while ((this.buffer.length > 0 || this.flushing) && Date.now() < flushDeadline) {
      if (!this.flushing) await this.flush();
      else await new Promise((resolve) => setTimeout(resolve, 100));
      if (!this.sessionId) break;
    }
    if (!this.sessionId) return "";
    const endTimeMs = Date.now();
    const sessionId = this.sessionId;
    await wx.cloud.callFunction({
      name: "meal-data",
      data: {
        action: "finish",
        sessionId,
        status,
        endTimeMs,
        endTimeClient: new Date(endTimeMs).toISOString(),
        endTimeLocal: formatLocalTime(endTimeMs),
        durationSeconds: Math.max(0, Math.round((endTimeMs - this.startTimeMs) / 1000)),
        pendingSampleCount: this.buffer.length,
        timePeriod: {
          start: new Date(this.startTimeMs).toISOString(),
          end: new Date(endTimeMs).toISOString()
        },
        timePeriodLocal: {
          start: formatLocalTime(this.startTimeMs),
          end: formatLocalTime(endTimeMs)
        },
        summary
      }
    });
    this.onStatus(`云端餐次已完成 ${sessionId}`);
    this.sessionId = "";
    return sessionId;
  }
}

module.exports = MealRecorder;
