Page({
  data: {
    score: 82,
    durationText: "00:00",
    normalPercent: 0,
    leftPercent: 50,
    avgPpb: "0.0",
    speedThreshold: 72,
    ppbThreshold: "4.0",
    assessmentMode: false,
    mealTime: "",
    characterImage: "/assets/chewtune-character.png"
  },

  onLoad(options) {
    const assessmentMode = Boolean(options.assessment === "1");
    const savedSpeed = Number(wx.getStorageSync("chewtuneSpeedThreshold")) || 72;
    const savedPpb = Number(wx.getStorageSync("chewtunePpbThreshold")) || 4;
    const recommendedCpm = Number(options.recommendedCpm) || savedSpeed;
    const recommendedPpb = Number(options.recommendedPpb) || savedPpb;
    const duration = Number(options.duration) || 0;
    const now = new Date();
    this.setData({
      score: Number(options.score) || 0,
      durationText: this.formatDuration(duration),
      normalPercent: Number(options.normal) || 0,
      leftPercent: Number(options.left) || 50,
      avgPpb: (Number(options.ppb) || 0).toFixed(1),
      assessmentMode,
      speedThreshold: assessmentMode ? recommendedCpm : savedSpeed,
      ppbThreshold: (assessmentMode ? recommendedPpb : savedPpb).toFixed(1),
      mealTime: `Today ${this.pad(now.getHours())}:${this.pad(now.getMinutes())} - Meal`
    });
    if (assessmentMode) {
      wx.setStorageSync("chewtuneAssessmentOnboardingSeen", true);
      wx.setStorageSync("chewtuneSpeedThreshold", recommendedCpm);
      wx.setStorageSync("chewtunePpbThreshold", recommendedPpb);
    }
    require("../../utils/cloud-assets")
      .resolve(["character"], { character: "/assets/chewtune-character.png" })
      .then((assets) => this.setData({ characterImage: assets.character }));
  },

  pad(value) {
    return String(value).padStart(2, "0");
  },

  formatDuration(totalSeconds) {
    return `${this.pad(Math.floor(totalSeconds / 60))}:${this.pad(totalSeconds % 60)}`;
  },

  adjustSpeed(event) {
    const speedThreshold = Math.max(30, Math.min(180, this.data.speedThreshold + Number(event.currentTarget.dataset.delta)));
    this.setData({ speedThreshold });
    wx.setStorageSync("chewtuneSpeedThreshold", speedThreshold);
  },

  adjustPpb(event) {
    const value = Math.max(1, Math.min(10, Number(this.data.ppbThreshold) + Number(event.currentTarget.dataset.delta)));
    const ppbThreshold = value.toFixed(1);
    this.setData({ ppbThreshold });
    wx.setStorageSync("chewtunePpbThreshold", Number(ppbThreshold));
  },

  finish() {
    wx.reLaunch({ url: "/pages/home/home" });
  },

  onShareAppMessage() {
    return { title: `ChewTune Meal Report - ${this.data.score}/100`, path: "/pages/home/home" };
  }
});
