const cloudAssets = require("./config/cloud-assets");
const MusicFeedback = require("./utils/music-feedback");

App({
  globalData: {
    musicFeedback: null
  },

  onLaunch() {
    if (cloudAssets.envId && wx.cloud) {
      wx.cloud.init({
        env: cloudAssets.envId,
        traceUser: true
      });
    }
    this.globalData.musicFeedback = new MusicFeedback(() => {}, () => {});
    this.globalData.musicFeedback.prepare();
  }
});
