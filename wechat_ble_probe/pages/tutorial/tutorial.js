const STEPS = [
  {
    type: "threshold",
    title: "AI-Recommended Thresholds",
    description: "Based on your latest intervention-free assessment, AI recommends starting CPM and PPB thresholds.",
    button: "See How Music Guides You"
  },
  {
    type: "follow",
    title: "Follow the Beat",
    description: "Start chewing to play the music. Near the target pace, the music stays full and smooth.",
    button: "Next"
  },
  {
    type: "bass",
    title: "Unlock the Bass",
    description: "Stay near the target pace and keep it steady. A bass layer joins the full, smooth music.",
    button: "Next"
  },
  {
    type: "slow",
    title: "Slow Down",
    description: "Chew above the threshold and the melody fades. The muffled sound guides you back.",
    button: "Next"
  },
  {
    type: "balance",
    title: "Balance Both Sides",
    description: "When one side works too long, the sound shifts. Switch sides to bring the rhythm back.",
    button: "Next"
  },
  {
    type: "pause",
    title: "Pause Between Bites",
    description: "Hear one pop each second. Wait for the ding at your set threshold before chewing again.",
    button: "Start Training"
  }
];
const MusicFeedback = require("../../utils/music-feedback");

Page({
  data: {
    stepIndex: 0,
    step: STEPS[0],
    guideIndex: 0,
    dots: [0, 1, 2, 3, 4],
    speedThreshold: 86,
    ppbThreshold: "4.0",
    beatDots: Array.from({ length: 18 }, (_, index) => index),
    leftDots: Array.from({ length: 9 }, (_, index) => index),
    rightDots: Array.from({ length: 9 }, (_, index) => index)
  },

  onLoad() {
    const speedThreshold = Number(wx.getStorageSync("chewtuneSpeedThreshold")) || 86;
    const ppbThreshold = Number(wx.getStorageSync("chewtunePpbThreshold")) || 4;
    this.setData({ speedThreshold, ppbThreshold: ppbThreshold.toFixed(1) });
    const app = getApp();
    if (!app.globalData.musicFeedback) {
      app.globalData.musicFeedback = new MusicFeedback(() => {}, () => {});
      app.globalData.musicFeedback.prepare();
    }
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

  next() {
    if (this.data.stepIndex === STEPS.length - 1) {
      const musicFeedback = getApp().globalData.musicFeedback;
      if (musicFeedback) {
        musicFeedback.unlockAudio();
        musicFeedback.setEnabled(true);
      }
      wx.redirectTo({ url: "/pages/index/index?autoStart=1" });
      return;
    }
    const stepIndex = this.data.stepIndex + 1;
    this.setData({
      stepIndex,
      step: STEPS[stepIndex],
      guideIndex: Math.max(0, stepIndex - 1)
    });
  },

  back() {
    if (this.data.stepIndex === 0) {
      wx.navigateBack();
      return;
    }
    const stepIndex = this.data.stepIndex - 1;
    this.setData({
      stepIndex,
      step: STEPS[stepIndex],
      guideIndex: Math.max(0, stepIndex - 1)
    });
  }
});
