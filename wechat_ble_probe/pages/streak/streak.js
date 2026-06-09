const DAY_MS = 24 * 60 * 60 * 1000;
const DAY_LABELS = ["S", "M", "T", "W", "T", "F", "S"];
const DAY_COLORS = ["pink", "green", "orange", "green", "purple", "purple", "purple"];

Page({
  data: {
    stage: 0,
    streak: 1,
    displayStreak: 0,
    weekDays: [],
    characterImage: "/assets/chewtune-character.png"
  },

  onLoad() {
    const streak = this.updateStreak();
    this.setData({
      streak,
      weekDays: this.buildWeekDays()
    });
    require("../../utils/cloud-assets")
      .resolve(["character"], { character: "/assets/chewtune-character.png" })
      .then((assets) => this.setData({ characterImage: assets.character }));
  },

  onReady() {
    this.timers = [
      setTimeout(() => this.setData({ stage: 1 }), 250),
      setTimeout(() => this.setData({ stage: 2 }), 850),
      setTimeout(() => this.animateCount(), 1050),
      setTimeout(() => this.setData({ stage: 3 }), 1700),
      setTimeout(() => this.setData({ stage: 4 }), 2250)
    ];
  },

  onUnload() {
    (this.timers || []).forEach((timer) => clearTimeout(timer));
    if (this.countTimer) clearInterval(this.countTimer);
  },

  dateKey(date) {
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  },

  updateStreak() {
    const today = new Date();
    const todayKey = this.dateKey(today);
    const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    const yesterdayKey = this.dateKey(yesterday);
    const previousDate = wx.getStorageSync("chewtuneLastPracticeDate");
    const previousStreak = Number(wx.getStorageSync("chewtuneDayStreak")) || 0;
    let streak = 1;

    if (previousDate === todayKey) streak = Math.max(1, previousStreak);
    else if (previousDate === yesterdayKey) streak = previousStreak + 1;

    const completedDates = wx.getStorageSync("chewtuneCompletedDates");
    const dates = Array.isArray(completedDates) ? completedDates : [];
    if (!dates.includes(todayKey)) dates.push(todayKey);
    wx.setStorageSync("chewtuneCompletedDates", dates.slice(-90));
    wx.setStorageSync("chewtuneLastPracticeDate", todayKey);
    wx.setStorageSync("chewtuneDayStreak", streak);
    return streak;
  },

  buildWeekDays() {
    const today = new Date();
    const start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - today.getDay());
    const saved = wx.getStorageSync("chewtuneCompletedDates");
    const completed = new Set(Array.isArray(saved) ? saved : []);
    return DAY_LABELS.map((label, index) => {
      const date = new Date(start.getFullYear(), start.getMonth(), start.getDate() + index);
      return {
        label,
        completed: completed.has(this.dateKey(date)),
        today: this.dateKey(date) === this.dateKey(today),
        color: DAY_COLORS[index]
      };
    });
  },

  animateCount() {
    const target = this.data.streak;
    const step = Math.max(1, Math.ceil(target / 14));
    this.countTimer = setInterval(() => {
      const next = Math.min(target, this.data.displayStreak + step);
      this.setData({ displayStreak: next });
      if (next >= target) {
        clearInterval(this.countTimer);
        this.countTimer = null;
      }
    }, 55);
  },

  viewReport() {
    if (this.data.stage < 4) return;
    const report = wx.getStorageSync("chewtunePendingReport") || {};
    const query = Object.keys(report)
      .map((key) => `${key}=${encodeURIComponent(report[key])}`)
      .join("&");
    wx.removeStorageSync("chewtunePendingReport");
    wx.redirectTo({ url: `/pages/report/report?${query}` });
  }
});
