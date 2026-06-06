Page({
  data: {
    starting: false,
    themeOpen: false,
    onboardingOpen: false,
    assessmentSeen: false,
    themeSelected: false,
    themeName: "Select Theme",
    homePage: "/assets/home-page.png",
    homePageSelected: "/assets/home-page-selected.png",
    onboardingModal: "/assets/onboarding-modal.png",
    dotLeft: 34,
    dotTop: 24
  },

  onLoad() {
    require("../../utils/cloud-assets")
      .resolve(
        ["homePage", "homePageSelected", "onboardingModal"],
        {
          homePage: "/assets/home-page.png",
          homePageSelected: "/assets/home-page-selected.png",
          onboardingModal: "/assets/onboarding-modal.png"
        }
      )
      .then((assets) => this.setData(assets));
    const savedTheme = wx.getStorageSync("chewtuneMusicTheme");
    const assessmentSeen = wx.getStorageSync("chewtuneAssessmentOnboardingSeen") === true;
    const updates = { assessmentSeen };

    if (savedTheme && typeof savedTheme === "object") {
      updates.themeSelected = true;
      updates.themeName = savedTheme.name || "Mindful Melodies";
      updates.dotLeft = Number(savedTheme.x) || 34;
      updates.dotTop = Number(savedTheme.y) || 24;
    }

    this.setData(updates);
  },

  openThemePicker() {
    this.setData({ themeOpen: true });
  },

  closeThemePicker() {
    if (!this.data.themeOpen) return;
    this.saveTheme();
  },

  stopCardTap() {},

  onGridTouchStart(event) {
    this.updateThemeDot(event);
  },

  onGridTouchMove(event) {
    this.updateThemeDot(event);
  },

  updateThemeDot(event) {
    const touch = event.touches && event.touches[0];
    if (!touch) return;

    this.createSelectorQuery()
      .select(".theme-grid")
      .boundingClientRect((rect) => {
        if (!rect) return;
        const padding = 18;
        const x = Math.max(padding, Math.min(rect.width - padding, touch.clientX - rect.left));
        const y = Math.max(padding, Math.min(rect.height - padding, touch.clientY - rect.top));
        this.setData({
          dotLeft: Math.round((x / rect.width) * 100),
          dotTop: Math.round((y / rect.height) * 100)
        });
      })
      .exec();
  },

  saveTheme() {
    const horizontal = this.data.dotLeft < 34
      ? "Peaceful"
      : this.data.dotLeft > 66
        ? "Energetic"
        : "Bright";
    const vertical = this.data.dotTop > 62 ? "Deep" : "Mindful";
    const themeName = vertical === "Mindful" && horizontal === "Peaceful"
      ? "Mindful Melodies"
      : `${vertical} ${horizontal}`;

    this.setData({
      themeOpen: false,
      themeSelected: true,
      themeName
    });
    wx.setStorageSync("chewtuneMusicTheme", {
      name: themeName,
      x: this.data.dotLeft,
      y: this.data.dotTop
    });
  },

  startTraining() {
    if (!this.data.assessmentSeen) {
      this.setData({ onboardingOpen: true });
      return;
    }
    this.enterTraining();
  },

  startAssessment() {
    wx.setStorageSync("chewtuneAssessmentOnboardingSeen", true);
    this.setData({
      onboardingOpen: false,
      assessmentSeen: true
    });
    this.enterTraining();
  },

  stopOnboardingTouch() {},

  enterTraining() {
    if (this.data.starting) return;
    this.setData({ starting: true });
    setTimeout(() => {
      wx.navigateTo({
        url: "/pages/index/index?autoStart=1",
        complete: () => this.setData({ starting: false })
      });
    }, 180);
  },

  showComingSoon() {
    wx.showToast({
      title: "功能即将开放",
      icon: "none",
      duration: 1200
    });
  }
});
