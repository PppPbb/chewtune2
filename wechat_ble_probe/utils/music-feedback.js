const cloudAssets = require("./cloud-assets");

const LAYERS = ["background", "bass", "drum", "melody"];
const CUES = ["pop", "ding", "error"];
const STATE_LAYERS = {
  pause: [],
  normal: ["drum", "melody"],
  stable: ["bass", "drum", "melody"],
  fast: ["background", "drum"]
};

class MusicFeedback {
  constructor(onError, onReady) {
    this.onError = onError || (() => {});
    this.onReady = onReady || (() => {});
    this.enabled = true;
    this.unlocked = false;
    this.state = "pause";
    this.layers = {};
    this.cues = {};
    this.sources = {};
    this.readyNames = new Set();
    this.readyReported = false;
    this.layersStarted = false;
  }

  async prepare() {
    await this.configureAudioSession();
    this.sources = await this.resolveSources();
    LAYERS.forEach((name) => {
      if (!this.sources[name]) {
        this.onError(`${name}: cloud download unavailable`);
        return;
      }
      const audio = wx.createInnerAudioContext();
      audio.src = this.sources[name];
      audio.loop = true;
      audio.volume = 0;
      audio.onCanplay(() => this.markReady(name));
      audio.onPlay(() => this.onError(`${name}: playing at volume ${audio.volume}`));
      audio.onWaiting(() => this.onError(`${name}: waiting`));
      audio.onError((error) => this.onError(`${name}: ${error.errMsg || error}`));
      this.layers[name] = audio;
    });

    CUES.forEach((name) => {
      if (!this.sources[name]) {
        this.onError(`${name}: cloud download unavailable`);
        return;
      }
      const audio = wx.createInnerAudioContext();
      audio.src = this.sources[name];
      audio.volume = 1;
      audio.onCanplay(() => this.markReady(name));
      audio.onPlay(() => {
        this.onError(`${name}: playing at volume ${audio.volume}`);
        if (name === "ding" && this.unlocked) this.startLayers();
      });
      audio.onWaiting(() => this.onError(`${name}: waiting`));
      audio.onError((error) => this.onError(`${name}: ${error.errMsg || error}`));
      this.cues[name] = audio;
    });
  }

  configureAudioSession() {
    return new Promise((resolve) => {
      wx.setInnerAudioOption({
        mixWithOther: true,
        obeyMuteSwitch: false,
        success: () => {
          this.onError("audio session ready; mute switch ignored");
          resolve();
        },
        fail: (error) => {
          this.onError(`audio session: ${error.errMsg || error}`);
          resolve();
        }
      });
    });
  }

  markReady(name) {
    if (this.readyNames.has(name)) return;
    this.readyNames.add(name);
    this.onError(`${name}: ready`);
    if (!this.readyReported && this.readyNames.size === LAYERS.length + CUES.length) {
      this.readyReported = true;
      this.onReady(Boolean(cloudAssets.config.envId));
    }
  }

  async resolveSources() {
    const local = {};
    LAYERS.forEach((name) => { local[name] = `/assets/music/${name}.wav`; });
    CUES.forEach((name) => { local[name] = `/assets/music/${name}.mp3`; });

    return cloudAssets.download(
      [...LAYERS, ...CUES],
      local,
      (message) => this.onError(message)
    );
  }

  unlockWithTestCue() {
    if (this.unlocked) return false;
    this.unlocked = true;
    const ding = this.cues.ding;
    if (ding) {
      ding.volume = 1;
      ding.play();
      setTimeout(() => this.startLayers(), 1200);
      return true;
    }
    this.startLayers();
    return false;
  }

  startLayers() {
    if (this.layersStarted) return;
    this.layersStarted = true;
    LAYERS.forEach((name) => {
      const audio = this.layers[name];
      if (!audio) return;
      audio.volume = 0;
      audio.play();
    });
    this.applyState();
  }

  setEnabled(enabled) {
    this.enabled = enabled;
    this.applyState();
  }

  setState(state) {
    this.state = STATE_LAYERS[state] ? state : "pause";
    this.applyState();
  }

  applyState() {
    const active = new Set(this.enabled && this.unlocked ? STATE_LAYERS[this.state] : []);
    LAYERS.forEach((name) => {
      if (this.layers[name]) this.layers[name].volume = active.has(name) ? 0.72 : 0;
    });
  }

  playCue(name) {
    const cue = this.cues[name];
    if (!this.enabled || !this.unlocked || !cue) return;
    cue.volume = 1;
    if (!cue.paused) cue.stop();
    cue.play();
  }

  destroy() {
    Object.values(this.layers).forEach((audio) => audio.destroy());
    Object.values(this.cues).forEach((audio) => audio.destroy());
    this.layers = {};
    this.cues = {};
  }
}

module.exports = MusicFeedback;
