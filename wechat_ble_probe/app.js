const cloudAssets = require("./config/cloud-assets");

App({
  onLaunch() {
    if (cloudAssets.envId && wx.cloud) {
      wx.cloud.init({
        env: cloudAssets.envId,
        traceUser: true
      });
    }
  }
});
