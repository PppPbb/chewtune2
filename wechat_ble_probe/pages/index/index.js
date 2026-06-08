const SERVICE_UUID = "7b100001-7c6a-4d91-a461-9c987d97b100";
const UI_DATA_UUID = "7b100002-7c6a-4d91-a461-9c987d97b100";
const DEVICE_NAMES = ["ChewTune-S2", "ChewTune-S3"];
const MusicFeedback = require("../../utils/music-feedback");

const SIDE_LABELS = { L: "左侧", R: "右侧", B: "双侧", "-": "等待检测" };
const PPB_LABELS = {
  I: "等待本次咀嚼结束",
  D: "正在确认停顿",
  W: "保持停顿",
  R: "可以开始下一口",
  T: "停顿时间较短",
  "-": "等待检测"
};

Page({
  data: {
    status: "未连接",
    statusClass: "idle",
    deviceName: "ChewTune",
    notifyCount: 0,
    heartbeat: "-",
    chewing: false,
    chewingLabel: "准备开始",
    cpm: 0,
    side: "等待检测",
    sideCode: "-",
    stability: 0,
    ppb: "0.0",
    ppbState: "等待检测",
    ppbCode: "I",
    ppbProgress: 0,
    speedThreshold: 86,
    ppbThreshold: 4,
    assessmentMode: false,
    paceClass: "calm",
    musicEnabled: true,
    musicState: "pause",
    audioReady: false,
    audioUnlocked: false,
    audioSource: "local",
    audioStatus: "waiting",
    musicDecisionStatus: "waiting for computer",
    characterImage: "/assets/chewtune-character.png",
    logsShown: false,
    logs: []
  },

  onLoad(options) {
    this.resetSessionStats();
    const assessmentMode = Boolean(options && options.assessment === "1");
    this.setData({
      speedThreshold: Number(wx.getStorageSync("chewtuneSpeedThreshold")) || 86,
      ppbThreshold: Number(wx.getStorageSync("chewtunePpbThreshold")) || 4,
      assessmentMode,
      musicEnabled: !assessmentMode,
      audioStatus: assessmentMode ? "no-intervention assessment" : "waiting"
    });
    require("../../utils/cloud-assets")
      .resolve(["character"], { character: "/assets/chewtune-character.png" })
      .then((assets) => this.setData({ characterImage: assets.character }));
    if (options && options.autoStart === "1") {
      this.autoStartTimer = setTimeout(() => this.start(), 500);
    }
  },

  onReady() {
    const app = getApp();
    this.musicFeedback = app.globalData.musicFeedback || new MusicFeedback(() => {}, () => {});
    app.globalData.musicFeedback = this.musicFeedback;
    this.musicFeedback.setCallbacks(
      (message) => {
        this.setData({ audioStatus: message });
        this.addLog(`Audio: ${message}`);
      },
      (cloudEnabled) => {
        const audioUnlocked = !this.data.assessmentMode && this.musicFeedback.unlocked;
        if (!this.data.assessmentMode) {
          this.musicFeedback.setEnabled(true);
        }
        this.setData({
          audioReady: true,
          audioUnlocked,
          musicEnabled: !this.data.assessmentMode,
          audioSource: cloudEnabled ? "cloud" : "local"
        }, () => this.drawMusicRing());
      }
    );
    this.musicFeedback.prepare();
    this.ringPhase = 0;
    this.drawMusicRing();
    this.ringTimer = setInterval(() => {
      this.ringPhase += 0.22;
      this.drawMusicRing();
    }, 120);
  },

  onUnload() {
    if (this.autoStartTimer) {
      clearTimeout(this.autoStartTimer);
      this.autoStartTimer = null;
    }
    if (this.ringTimer) {
      clearInterval(this.ringTimer);
      this.ringTimer = null;
    }
    if (this.musicFeedback) {
      this.musicFeedback.destroy();
      getApp().globalData.musicFeedback = null;
      this.musicFeedback = null;
    }
    this.stop();
  },

  addLog(message) {
    const time = new Date().toLocaleTimeString();
    this.setData({ logs: [`${time}  ${message}`, ...this.data.logs].slice(0, 30) });
  },

  resetSessionStats() {
    this.sessionStartAt = Date.now();
    this.sessionStats = {
      samples: 0,
      normalSamples: 0,
      leftSamples: 0,
      rightSamples: 0,
      ppbTotal: 0,
      ppbCount: 0,
      cpmTotal: 0,
      cpmCount: 0
    };
    this.lastReportPpbCode = "I";
  },

  recordSessionSample(chewing, cpm, sideCode, ppbNumber, ppbCode) {
    if (!this.sessionStats) this.resetSessionStats();
    const stats = this.sessionStats;
    if (chewing) {
      stats.samples += 1;
      if (cpm > 0) {
        stats.cpmTotal += cpm;
        stats.cpmCount += 1;
      }
      if (cpm > 0 && cpm <= this.data.speedThreshold) stats.normalSamples += 1;
      if (sideCode === "L") stats.leftSamples += 1;
      if (sideCode === "R") stats.rightSamples += 1;
    }
    if (ppbNumber > 0 && (ppbCode === "R" || ppbCode === "T") && ppbCode !== this.lastReportPpbCode) {
      stats.ppbTotal += ppbNumber;
      stats.ppbCount += 1;
    }
    this.lastReportPpbCode = ppbCode;
  },

  finishOrConnect() {
    if (this.data.statusClass !== "connected") {
      this.toggleConnection();
      return;
    }
    const stats = this.sessionStats || {};
    const duration = Math.max(1, Math.round((Date.now() - this.sessionStartAt) / 1000));
    const normalPercent = stats.samples ? Math.round((stats.normalSamples / stats.samples) * 100) : 0;
    const sideTotal = (stats.leftSamples || 0) + (stats.rightSamples || 0);
    const leftPercent = sideTotal ? Math.round(((stats.leftSamples || 0) / sideTotal) * 100) : 50;
    const avgPpb = stats.ppbCount ? stats.ppbTotal / stats.ppbCount : Number(this.data.ppb) || 0;
    const avgCpm = stats.cpmCount ? stats.cpmTotal / stats.cpmCount : Number(this.data.cpm) || 86;
    const recommendedCpm = Math.max(30, Math.min(180, Math.round(avgCpm * 0.9)));
    const recommendedPpb = Math.max(2, Math.min(8, avgPpb || 4));
    const balanceScore = 100 - Math.abs(50 - leftPercent) * 2;
    const score = Math.max(0, Math.min(100, Math.round(normalPercent * 0.75 + balanceScore * 0.25)));
    this.stop();
    wx.navigateTo({
      url: `/pages/report/report?duration=${duration}&normal=${normalPercent}&left=${leftPercent}&ppb=${avgPpb.toFixed(1)}&score=${score}&assessment=${this.data.assessmentMode ? 1 : 0}&recommendedCpm=${recommendedCpm}&recommendedPpb=${recommendedPpb.toFixed(1)}`
    });
  },

  toggleLogs() {
    this.setData({ logsShown: !this.data.logsShown });
  },

  toggleAudio() {
    if (this.data.assessmentMode) {
      wx.showToast({ title: "无干预检测期间不播放音乐", icon: "none" });
      return;
    }
    if (!this.data.audioReady) {
      wx.showToast({ title: "音乐正在加载", icon: "none" });
      return;
    }
    if (!this.data.audioUnlocked) {
      this.musicFeedback.unlockAudio();
      this.setData({ audioUnlocked: true, musicEnabled: true }, () => this.drawMusicRing());
      wx.showToast({ title: "音乐已开启", icon: "none" });
      return;
    }
    const musicEnabled = !this.data.musicEnabled;
    this.setData({ musicEnabled }, () => this.drawMusicRing());
    if (this.musicFeedback) {
      this.musicFeedback.setEnabled(musicEnabled);
    }
  },

  toggleConnection() {
    if (!this.data.assessmentMode && this.musicFeedback && !this.data.audioUnlocked) {
      this.musicFeedback.unlockAudio();
      this.musicFeedback.setEnabled(true);
      this.setData({ audioUnlocked: true, musicEnabled: true });
    }
    if (this.data.statusClass === "connected" || this.data.statusClass === "scanning") {
      this.stop();
      return;
    }
    this.start();
  },

  async start() {
    this.stop();
    this.setData({ status: "正在寻找设备", statusClass: "scanning" });
    this.addLog("开始扫描 ChewTune");
    try {
      await this.wxCall("openBluetoothAdapter");
      wx.onBluetoothDeviceFound(this.onDeviceFound.bind(this));
      wx.onBLECharacteristicValueChange(this.onValue.bind(this));
      await this.wxCall("startBluetoothDevicesDiscovery", {
        services: [SERVICE_UUID],
        allowDuplicatesKey: false
      });
    } catch (error) {
      this.fail("蓝牙启动失败", error);
    }
  },

  stop() {
    wx.stopBluetoothDevicesDiscovery();
    if (this.deviceId) {
      wx.closeBLEConnection({ deviceId: this.deviceId });
    }
    this.deviceId = "";
    if (this.musicFeedback) this.musicFeedback.setState("pause");
    this.setData({ status: "未连接", statusClass: "idle" });
  },

  onDeviceFound(result) {
    const device = (result.devices || []).find((item) =>
      DEVICE_NAMES.includes(item.name || item.localName)
    );
    if (!device || this.deviceId) return;
    this.deviceId = device.deviceId;
    this.connect(device);
  },

  async connect(device) {
    try {
      wx.stopBluetoothDevicesDiscovery();
      this.setData({
        status: "正在连接",
        statusClass: "scanning",
        deviceName: device.name || device.localName
      });
      await this.wxCall("createBLEConnection", { deviceId: device.deviceId });
      const services = await this.wxCall("getBLEDeviceServices", { deviceId: device.deviceId });
      const service = services.services.find((item) => item.uuid.toLowerCase() === SERVICE_UUID);
      if (!service) throw new Error("未找到 ChewTune Service");

      const chars = await this.wxCall("getBLEDeviceCharacteristics", {
        deviceId: device.deviceId,
        serviceId: service.uuid
      });
      const characteristic = chars.characteristics.find(
        (item) => item.uuid.toLowerCase() === UI_DATA_UUID
      );
      if (!characteristic) throw new Error("未找到 UI Notify 特征");

      this.serviceId = service.uuid;
      this.characteristicId = characteristic.uuid;
      await this.wxCall("notifyBLECharacteristicValueChange", {
        state: true,
        deviceId: device.deviceId,
        serviceId: service.uuid,
        characteristicId: characteristic.uuid
      });
      await this.syncThresholds();
      this.setData({ status: "实时同步中", statusClass: "connected" });
      this.resetSessionStats();
      this.addLog(`已连接 ${device.name || device.localName}`);
    } catch (error) {
      this.fail("连接失败", error);
      this.deviceId = "";
    }
  },

  onValue(result) {
    const text = this.decodeAscii(result.value).trim();
    if (!text) return;
    this.setData({ notifyCount: this.data.notifyCount + 1 });
    if (text.startsWith("H,")) {
      this.setData({ heartbeat: text.slice(2) });
      return;
    }
    if (text.startsWith("U,")) {
      this.parseUiPacket(text);
      return;
    }
    if (text.startsWith("M,")) {
      this.parseMusicPacket(text);
      return;
    }
    this.addLog(`未知消息: ${text}`);
  },

  parseUiPacket(text) {
    const fields = text.split(",");
    if (fields.length !== 7) {
      this.addLog(`数据格式错误: ${text}`);
      return;
    }
    const [, chewingValue, cpmValue, sideCode, stabilityValue, ppbTenths, ppbCode] = fields;
    const chewing = chewingValue === "1";
    const cpm = Number(cpmValue) || 0;
    const ppbNumber = (Number(ppbTenths) || 0) / 10;
    const stability = Number(stabilityValue) || 0;
    this.recordSessionSample(chewing, cpm, sideCode, ppbNumber, ppbCode);
    this.setData({
      chewing,
      chewingLabel: chewing ? "正在咀嚼" : ppbCode === "R" ? "呼吸一下，准备下一口" : "享受这一刻",
      cpm,
      side: SIDE_LABELS[sideCode] || sideCode,
      sideCode,
      stability,
      ppb: ppbNumber.toFixed(1),
      ppbState: PPB_LABELS[ppbCode] || ppbCode,
      ppbCode,
      ppbProgress: Math.min(100, Math.round((ppbNumber / this.data.ppbThreshold) * 100))
    }, () => this.drawMusicRing());
  },

  parseMusicPacket(text) {
    const fields = text.split(",");
    if (fields.length !== 5) {
      this.addLog(`音乐决策格式错误: ${text}`);
      return;
    }
    const state = { P: "pause", N: "normal", S: "stable", F: "fast" }[fields[1]] || "pause";
    const pan = Math.max(-1, Math.min(1, (Number(fields[2]) || 0) / 100));
    const layerMask = Number(fields[3]) || 0;
    const cue = { O: "pop", D: "ding", E: "error" }[fields[4]] || "";
    this.lastMusicDecisionAt = Date.now();
    const currentBias = this.ringBias || 0;
    this.ringBias = currentBias + (pan - currentBias) * 0.35;
    this.setData({
      musicState: state,
      musicDecisionStatus: `computer ${state} · layers ${layerMask} · pan ${fields[2]}`,
      paceClass: !this.data.assessmentMode && state === "fast" ? "fast" : !this.data.assessmentMode && state !== "pause" ? "active" : "calm"
    }, () => this.drawMusicRing());
    if (this.data.assessmentMode || !this.musicFeedback) return;
    this.musicFeedback.applyDecision(state, layerMask);
    if (cue) this.musicFeedback.playCue(cue);
  },

  drawMusicRing() {
    const context = wx.createCanvasContext("musicRing", this);
    const size = 320;
    const center = size / 2;
    const baseRadius = 104;
    const segmentCount = 72;
    const playing = this.data.chewing && this.data.musicEnabled && !this.data.assessmentMode;
    const bias = this.ringBias || 0;
    const biasAmount = Math.abs(bias);
    const phase = this.ringPhase || 0;
    const ringColor = this.data.paceClass === "fast" ? "#f39a45" : "#747dff";

    context.clearRect(0, 0, size, size);
    context.setLineCap("round");

    for (let index = 0; index < segmentCount; index += 1) {
      const angle = (index / segmentCount) * Math.PI * 2 - Math.PI / 2;
      const onLeft = Math.cos(angle) < 0;
      const sideDirection = onLeft ? -1 : 1;
      const sideGain = biasAmount < 0.04
        ? 0
        : sideDirection === Math.sign(bias)
          ? biasAmount * 14
          : -biasAmount * 5;
      const pulse = playing ? (Math.sin(index * 0.72 + phase) + 1) * 3.5 : 0;
      const innerRadius = baseRadius - 5;
      const outerRadius = playing ? baseRadius + 15 + pulse + sideGain : baseRadius + 9;

      context.beginPath();
      context.setLineWidth(playing ? 4 : 3);
      context.setStrokeStyle(playing ? ringColor : "#c8cad3");
      context.moveTo(
        center + Math.cos(angle) * innerRadius,
        center + Math.sin(angle) * innerRadius
      );
      context.lineTo(
        center + Math.cos(angle) * outerRadius,
        center + Math.sin(angle) * outerRadius
      );
      context.stroke();
    }

    context.draw();
  },

  decodeAscii(buffer) {
    return Array.from(new Uint8Array(buffer))
      .map((value) => String.fromCharCode(value))
      .join("");
  },

  encodeAscii(text) {
    const buffer = new ArrayBuffer(text.length);
    const bytes = new Uint8Array(buffer);
    for (let index = 0; index < text.length; index += 1) {
      bytes[index] = text.charCodeAt(index);
    }
    return buffer;
  },

  async syncThresholds() {
    if (!this.deviceId || !this.serviceId || !this.characteristicId) return;
    const command = `C,${this.data.speedThreshold},${this.data.ppbThreshold}`;
    try {
      await this.wxCall("writeBLECharacteristicValue", {
        deviceId: this.deviceId,
        serviceId: this.serviceId,
        characteristicId: this.characteristicId,
        value: this.encodeAscii(command)
      });
      this.addLog(`阈值已同步到电脑: ${command}`);
    } catch (error) {
      this.addLog(`阈值同步失败: ${error.errMsg || error}`);
    }
  },

  wxCall(method, options = {}) {
    return new Promise((resolve, reject) => {
      wx[method]({ ...options, success: resolve, fail: reject });
    });
  },

  fail(label, error) {
    const detail = error.errMsg || error.message || String(error);
    this.setData({ status: label, statusClass: "error" });
    this.addLog(`${label}: ${detail}`);
  }
});
