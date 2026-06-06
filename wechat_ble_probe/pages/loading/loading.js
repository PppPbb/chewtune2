Page({
  data: {
    progress: 0,
    loadingScreen: "/assets/loading-screen.png"
  },

  onLoad() {
    require("../../utils/cloud-assets")
      .resolve(["loadingScreen"], { loadingScreen: "/assets/loading-screen.png" })
      .then((assets) => this.setData(assets));
    this.startLoading();
  },

  onUnload() {
    this.clearLoadingTimers();
  },

  startLoading() {
    const startedAt = Date.now();
    const duration = 3000;

    this.progressTimer = setInterval(() => {
      const elapsed = Date.now() - startedAt;
      const progress = Math.min(100, Math.round((elapsed / duration) * 100));
      this.setData({ progress });

      if (progress >= 100) {
        this.clearLoadingTimers();
        this.navigationTimer = setTimeout(() => {
          wx.redirectTo({ url: "/pages/home/home" });
        }, 240);
      }
    }, 50);
  },

  clearLoadingTimers() {
    if (this.progressTimer) {
      clearInterval(this.progressTimer);
      this.progressTimer = null;
    }
    if (this.navigationTimer) {
      clearTimeout(this.navigationTimer);
      this.navigationTimer = null;
    }
  }
});
