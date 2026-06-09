const LAYERS = ["background", "bass", "drum", "melody"];
const CUES = ["pop", "ding", "error"];
const STATE_LAYERS = {
  pause: [],
  normal: ["drum", "melody"],
  stable: ["bass", "drum", "melody"],
  fast: ["background", "drum"]
};
const LAYER_BITS = { background: 1, bass: 2, drum: 4, melody: 8 };
const KEEP_ALIVE_VOLUME = 0.001;

class MusicFeedback {
  constructor(onError, onReady) {
    this.onError = onError || (() => {});
    this.onReady = onReady || (() => {});
    this.enabled = true;
    this.unlocked = false;
    this.state = "pause";
    this.layerMask = 0;
    this.layers = {};
    this.cues = {};
    this.sources = {};
    this.readyNames = new Set();
    this.readyReported = false;
    this.layersStarted = false;
    this.activeLayers = new Set();
    this.preparing = false;
    this.prepared = false;
  }

  prepare() {
    if (this.preparing || this.prepared) return;
    this.preparing = true;
    this.configureAudioSession();
    this.sources = this.resolveSources();
    LAYERS.forEach((name) => {
      const audio = wx.createInnerAudioContext();
      audio.src = this.sources[name];
      audio.loop = true;
      audio.volume = 0;
      audio.onCanplay(() => this.markReady(name));
      audio.onPlay(() => this.onError(`${name}: playing at volume ${audio.volume}`));
      audio.onPause(() => this.onError(`${name}: paused`));
      audio.onWaiting(() => this.onError(`${name}: waiting`));
      audio.onError((error) => this.onError(`${name}: ${error.errMsg || error}`));
      this.layers[name] = audio;
      if (this.layersStarted) this.playLayer(name);
    });

    CUES.forEach((name) => {
      const audio = wx.createInnerAudioContext();
      audio.src = this.sources[name];
      audio.volume = 1;
      audio.onCanplay(() => this.markReady(name));
      audio.onPlay(() => this.onError(`${name}: playing at volume ${audio.volume}`));
      audio.onWaiting(() => this.onError(`${name}: waiting`));
      audio.onError((error) => this.onError(`${name}: ${error.errMsg || error}`));
      this.cues[name] = audio;
    });
    this.preparing = false;
    this.prepared = true;
  }

  setCallbacks(onError, onReady) {
    this.onError = onError || (() => {});
    this.onReady = onReady || (() => {});
    if (this.readyReported) this.onReady(false);
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
    if (!this.readyReported && LAYERS.every((layer) => this.readyNames.has(layer))) {
      this.readyReported = true;
      this.onReady(false);
    }
  }

  resolveSources() {
    const local = {};
    LAYERS.forEach((name) => { local[name] = `/assets/music/${name}.wav`; });
    CUES.forEach((name) => { local[name] = `/assets/music/${name}.mp3`; });
    return local;
  }

  unlockAudio() {
    const wasUnlocked = this.unlocked;
    this.unlocked = true;
    this.resumeFromUserGesture();
    return !wasUnlocked;
  }

  resumeFromUserGesture() {
    this.unlocked = true;
    this.layersStarted = true;
    LAYERS.forEach((name) => {
      const audio = this.layers[name];
      if (!audio) return;
      audio.volume = this.activeLayers.has(name) ? 0.72 : KEEP_ALIVE_VOLUME;
      this.playLayer(name);
    });
    this.applyState();
  }

  startLayers() {
    if (this.layersStarted) return;
    this.layersStarted = true;
    LAYERS.forEach((name) => {
      const audio = this.layers[name];
      if (!audio) return;
      audio.volume = KEEP_ALIVE_VOLUME;
      this.playLayer(name);
    });
    this.applyState();
  }

  playLayer(name) {
    const audio = this.layers[name];
    if (!audio) return;
    try {
      audio.play();
    } catch (error) {
      this.onError(`${name}: play failed ${error.errMsg || error}`);
    }
  }

  setEnabled(enabled) {
    this.enabled = enabled;
    this.applyState();
  }

  setState(state) {
    this.state = STATE_LAYERS[state] ? state : "pause";
    this.layerMask = 0;
    this.applyState();
  }

  applyDecision(state, layerMask) {
    this.state = STATE_LAYERS[state] ? state : "pause";
    const receivedMask = Number(layerMask) || 0;
    this.layerMask = receivedMask || this.maskForState(this.state);
    this.applyState();
    return this.layerMask;
  }

  maskForState(state) {
    return (STATE_LAYERS[state] || []).reduce(
      (mask, name) => mask | LAYER_BITS[name],
      0
    );
  }

  applyState() {
    const active = new Set(
      this.enabled && this.unlocked
        ? LAYERS.filter((name) => (this.layerMask & LAYER_BITS[name]) !== 0)
        : []
    );
    LAYERS.forEach((name) => {
      const audio = this.layers[name];
      if (!audio) return;
      const shouldPlay = active.has(name);
      const wasActive = this.activeLayers.has(name);
      if (shouldPlay) {
        audio.volume = 0.72;
        if (audio.paused) this.playLayer(name);
        if (!wasActive) this.onError(`${name}: activated at volume ${audio.volume}`);
      } else {
        audio.volume = this.unlocked ? KEEP_ALIVE_VOLUME : 0;
        if (this.unlocked && audio.paused) this.playLayer(name);
        if (wasActive) this.onError(`${name}: deactivated`);
      }
    });
    this.activeLayers = active;
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
    this.activeLayers = new Set();
    this.prepared = false;
    this.preparing = false;
  }
}

module.exports = MusicFeedback;
