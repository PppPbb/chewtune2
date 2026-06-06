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
    paceClass: "calm",
    musicEnabled: true,
    musicState: "pause",
    audioReady: false,
    audioUnlocked: false,
    audioSource: "local",
    audioStatus: "waiting",
    characterImage: "/assets/chewtune-character.png",
    logsShown: false,
    logs: []
  },

  onLoad(options) {
    this.resetSessionStats();
    require("../../utils/cloud-assets")
      .resolve(["character"], { character: "/assets/chewtune-character.png" })
      .then((assets) => this.setData({ characterImage: assets.character }));
    if (options && options.autoStart === "1") {
      this.autoStartTimer = setTimeout(() => this.start(), 500);
    }
  },

  onReady() {
    this.musicFeedback = new MusicFeedback(
      (message) => {
        this.setData({ audioStatus: message });
        this.addLog(`Audio: ${message}`);
      },
      (cloudEnabled) => this.setData({
        audioReady: true,
        audioSource: cloudEnabled ? "cloud" : "local"
      })
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
      ppbCount: 0
    };
    this.lastReportPpbCode = "I";
  },

  recordSessionSample(chewing, cpm, sideCode, ppbNumber, ppbCode) {
    if (!this.sessionStats) this.resetSessionStats();
    const stats = this.sessionStats;
    if (chewing) {
      stats.samples += 1;
      if (cpm > 0 && cpm <= 120) stats.normalSamples += 1;
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
    const balanceScore = 100 - Math.abs(50 - leftPercent) * 2;
    const score = Math.max(0, Math.min(100, Math.round(normalPercent * 0.75 + balanceScore * 0.25)));
    this.stop();
    wx.navigateTo({
      url: `/pages/report/report?duration=${duration}&normal=${normalPercent}&left=${leftPercent}&ppb=${avgPpb.toFixed(1)}&score=${score}`
    });
  },

  toggleLogs() {
    this.setData({ logsShown: !this.data.logsShown });
  },

  toggleAudio() {
    if (!this.data.audioReady) {
      wx.showToast({ title: "音乐正在加载", icon: "none" });
      return;
    }
    if (!this.data.audioUnlocked) {
      this.musicFeedback.unlockWithTestCue();
      this.setData({ audioUnlocked: true, musicEnabled: true }, () => this.drawMusicRing());
      wx.showToast({ title: "音乐已开启，请确认提示音", icon: "none" });
      return;
    }
    const musicEnabled = !this.data.musicEnabled;
    this.setData({ musicEnabled }, () => this.drawMusicRing());
    if (this.musicFeedback) {
      this.musicFeedback.setEnabled(musicEnabled);
      if (musicEnabled) this.musicFeedback.playCue("ding");
    }
  },

  toggleConnection() {
    if (this.musicFeedback && this.data.audioReady && !this.data.audioUnlocked) {
      this.musicFeedback.unlockWithTestCue();
      this.setData({ audioUnlocked: true });
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
    const musicState = !chewing ? "pause" : cpm > 120 ? "fast" : stability >= 50 ? "stable" : "normal";
    const sideTarget = sideCode === "L" ? -1 : sideCode === "R" ? 1 : 0;
    this.recordSessionSample(chewing, cpm, sideCode, ppbNumber, ppbCode);
    const currentBias = this.ringBias || 0;
    this.ringBias = currentBias + (sideTarget - currentBias) * 0.18;
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
      ppbProgress: Math.min(100, Math.round((ppbNumber / 4) * 100)),
      paceClass: cpm > 120 ? "fast" : chewing ? "active" : "calm",
      musicState
    }, () => this.drawMusicRing());
    this.updateMusicFeedback(musicState, ppbNumber, ppbCode);
  },

  updateMusicFeedback(musicState, ppbNumber, ppbCode) {
    if (!this.musicFeedback) return;
    this.musicFeedback.setState(musicState);

    const previousCode = this.lastPpbCode || "I";
    if (ppbCode === "W") {
      const mark = Math.min(3, Math.floor(ppbNumber));
      if (mark >= 1 && mark > (this.lastPopMark || 0)) {
        this.musicFeedback.playCue("pop");
        this.lastPopMark = mark;
      }
    } else if (ppbCode === "R" && previousCode !== "R") {
      this.musicFeedback.playCue("ding");
    } else if (ppbCode === "T" && previousCode !== "T") {
      this.musicFeedback.playCue("error");
    }

    if (ppbCode !== "W") this.lastPopMark = 0;
    this.lastPpbCode = ppbCode;
  },

  drawMusicRing() {
    const context = wx.createCanvasContext("musicRing", this);
    const size = 320;
    const center = size / 2;
    const baseRadius = 104;
    const segmentCount = 72;
    const playing = this.data.chewing && this.data.musicEnabled;
    const bias = this.ringBias || 0;
    const biasAmount = Math.abs(bias);
    const phase = this.ringPhase || 0;

    context.clearRect(0, 0, size, size);
    context.setLineCap("round");

    for (let index = 0; index < segmentCount; index += 1) {
      const angle = (index / segmentCount) * Math.PI * 2 - Math.PI / 2;
      const onLeft = Math.cos(angle) < 0;
      const sideDirection = onLeft ? -1 : 1;
      const focused = biasAmount < 0.12 || sideDirection === Math.sign(bias);
      const pulse = playing ? (Math.sin(index * 0.72 + phase) + 1) * 3.5 : 0;
      const innerRadius = baseRadius - 5;
      const outerRadius = playing
        ? baseRadius + (focused
          ? 16 + pulse + biasAmount * 12
          : 13 + pulse * 0.25 - biasAmount * 7)
        : baseRadius + 9;

      context.beginPath();
      context.setLineWidth(focused && playing ? 4 : 3);
      context.setStrokeStyle(playing && focused ? "#747dff" : "#c8cad3");
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
