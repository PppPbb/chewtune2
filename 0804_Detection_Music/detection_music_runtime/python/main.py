import argparse
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
import joblib
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button
import numpy as np

import detector


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MUSIC_DIR = PROJECT_ROOT / "music"
DEFAULT_SIDE_MODEL_PATH = PROJECT_ROOT / "models" / "dual_side_cnn.pkl"
DEFAULT_SIDE_METADATA_PATH = PROJECT_ROOT / "models" / "dual_side_cnn_meta.pkl"
DEFAULT_CPM_CALIBRATOR_PATH = PROJECT_ROOT / "models" / "cpm_calibrator.pkl"
DEFAULT_CHEWING_STATE_MODEL_PATH = PROJECT_ROOT / "models" / "chewing_state_rf.pkl"
DEFAULT_THRESHOLD_PATH = PROJECT_ROOT / "models" / "chewing_state_threshold.json"

LAYER_NAMES = ("drum", "background", "bass", "melody")
CUE_NAMES = ("pop", "ding", "error")
LAYER_EXTENSIONS = (".wav", ".mp3", ".ogg")
STATE_LAYERS = {
    "pause": set(),
    "normal": {"drum", "melody"},
    "stable": {"bass", "drum", "melody"},
    "fast": {"background", "drum"},
}
INTERVENTION_STATE_VALUES = {"pause": 0, "normal": 1, "stable": 2, "fast": 3}


class MusicLayerPlayer:
    def __init__(self, music_dir: Path, active_volume: float, inactive_volume: float) -> None:
        self.music_dir = music_dir
        self.active_volume = active_volume
        self.inactive_volume = inactive_volume
        self.channels = {}
        self.cue_sounds = {}
        self.cue_channel = None
        self.enabled = False
        self.pan = 0.0
        self.active_layers = set()

    def start(self) -> None:
        try:
            import pygame
        except ImportError as exc:
            raise ImportError("pygame is required for music playback. Install it with: pip install pygame") from exc

        pygame.mixer.init()
        self.music_dir.mkdir(parents=True, exist_ok=True)
        print(f"Music directory: {self.music_dir}")

        for index, layer in enumerate(LAYER_NAMES):
            path = self.find_layer_file(layer)
            if path is None:
                print(f"Music layer missing: {layer}. Put {layer}.wav/.mp3/.ogg in {self.music_dir}")
                continue
            sound = pygame.mixer.Sound(str(path))
            channel = pygame.mixer.Channel(index)
            channel.play(sound, loops=-1)
            channel.set_volume(self.inactive_volume)
            self.channels[layer] = channel
            print(f"Loaded music layer: {layer} -> {path.name}")

        self.cue_channel = pygame.mixer.Channel(len(LAYER_NAMES))
        for cue in CUE_NAMES:
            path = self.find_audio_file(cue)
            if path is None:
                print(f"PPB cue missing: {cue}. Put {cue}.wav/.mp3/.ogg in {self.music_dir}")
                continue
            self.cue_sounds[cue] = pygame.mixer.Sound(str(path))
            print(f"Loaded PPB cue: {cue} -> {path.name}")

        self.enabled = bool(self.channels)
        if not self.enabled:
            print("No music layers were loaded. Detection will still run, but no audio will play.")
        else:
            print(f"Loaded {len(self.channels)} music layer(s). Layers are looping at inactive volume until ChewTune activates them.")

    def find_layer_file(self, layer: str) -> Optional[Path]:
        return self.find_audio_file(layer)

    def find_audio_file(self, name: str) -> Optional[Path]:
        for ext in LAYER_EXTENSIONS:
            direct = self.music_dir / f"{name}{ext}"
            if direct.exists():
                return direct
        for path in self.music_dir.iterdir():
            if path.is_file() and path.suffix.lower() in LAYER_EXTENSIONS and path.stem.lower().startswith(name):
                return path
        return None

    def set_active_layers(self, active_layers: set[str], pan: Optional[float] = None) -> None:
        self.active_layers = set(active_layers)
        if pan is not None:
            self.pan = float(np.clip(pan, -1.0, 1.0))
        left_gain, right_gain = stereo_gains(self.pan)
        for layer, channel in self.channels.items():
            volume = self.active_volume if layer in self.active_layers else self.inactive_volume
            channel.set_volume(volume * left_gain, volume * right_gain)

    def set_pan(self, pan: float) -> None:
        self.set_active_layers(self.active_layers, pan)

    def play_cue(self, cue: str) -> bool:
        sound = self.cue_sounds.get(cue)
        if sound is None or self.cue_channel is None:
            print(f"PPB cue unavailable: {cue}")
            return False
        self.cue_channel.play(sound)
        return True

    def stop(self) -> None:
        if not self.channels and not self.cue_sounds:
            return
        try:
            import pygame

            pygame.mixer.stop()
            pygame.mixer.quit()
        except Exception:
            pass


class InterventionState:
    def __init__(
        self,
        fast_cpm_threshold: float,
        stable_threshold: float,
        pause_seconds: float,
        min_interval_s: float,
        max_interval_s: float,
    ) -> None:
        self.fast_cpm_threshold = fast_cpm_threshold
        self.stable_threshold = stable_threshold
        self.pause_seconds = pause_seconds
        self.min_interval_s = min_interval_s
        self.max_interval_s = max_interval_s
        self.intervals = deque(maxlen=5)
        self.last_chew_time: Optional[float] = None
        self.last_interval_update: Optional[float] = None

    def update(self, detection: dict, now_s: float, update_seconds: float) -> dict:
        if detection["state"] == "chewing" and float(detection.get("cpm", 0.0)) > 0.0:
            self.last_chew_time = now_s
            interval = 60.0 / max(float(detection["cpm"]), 1e-9)
            if self.min_interval_s <= interval <= self.max_interval_s:
                min_update_gap = max(update_seconds, min(0.8, interval * 0.65))
                if self.last_interval_update is None or now_s - self.last_interval_update >= min_update_gap:
                    self.intervals.append(interval)
                    self.last_interval_update = now_s

        stability = self.compute_stability()
        recent_gap = float("inf") if self.last_chew_time is None else now_s - self.last_chew_time
        fast = self.is_fast()

        if recent_gap > self.pause_seconds:
            intervention_state = "pause"
        elif fast:
            intervention_state = "fast"
        elif stability >= self.stable_threshold:
            intervention_state = "stable"
        else:
            intervention_state = "normal"

        return {
            "intervention_state": intervention_state,
            "stability": stability,
            "recent_gap": recent_gap,
            "intervals": list(self.intervals),
            "active_layers": STATE_LAYERS[intervention_state],
        }

    def compute_stability(self) -> float:
        if len(self.intervals) < 2:
            return 0.0
        values = np.asarray(self.intervals, dtype=float)
        mean_interval = float(np.mean(values))
        if mean_interval <= 1e-9:
            return 0.0
        variation = float(np.std(values) / mean_interval)
        stability = 1.0 - variation / 0.22
        return float(np.clip(stability, 0.0, 1.0))

    def is_fast(self) -> bool:
        if len(self.intervals) < 4:
            return False
        recent = list(self.intervals)[-4:]
        fast_count = sum(1 for interval in recent if 60.0 / max(interval, 1e-9) > self.fast_cpm_threshold)
        return fast_count >= 2


class SpatialPanState:
    def __init__(self, step: float, smooth_rate: float) -> None:
        self.step = step
        self.smooth_rate = smooth_rate
        self.target_pan = 0.0
        self.current_pan = 0.0
        self.left_events = 0
        self.right_events = 0
        self.left_accumulator = 0.0
        self.right_accumulator = 0.0
        self.last_detection_time: Optional[float] = None
        self.last_update_time: Optional[float] = None

    def update_from_detection(self, detection: dict, now_s: float) -> None:
        if detection.get("state") != "chewing" or float(detection.get("cpm", 0.0)) <= 0.0:
            self.last_detection_time = now_s
            return
        side = str(detection.get("side", ""))
        if side not in {"left_chewing", "right_chewing"}:
            self.last_detection_time = now_s
            return

        if self.last_detection_time is None:
            self.last_detection_time = now_s
            return

        elapsed_s = max(0.0, min(2.0, now_s - self.last_detection_time))
        self.last_detection_time = now_s
        estimated_chews = float(detection["cpm"]) / 60.0 * elapsed_s
        if estimated_chews <= 0.0:
            return

        if side == "left_chewing":
            self.left_accumulator += estimated_chews
        else:
            self.right_accumulator += estimated_chews

        while self.left_accumulator >= 1.0:
            self.target_pan = max(-1.0, self.target_pan - self.step)
            self.left_events += 1
            self.left_accumulator -= 1.0
        while self.right_accumulator >= 1.0:
            self.target_pan = min(1.0, self.target_pan + self.step)
            self.right_events += 1
            self.right_accumulator -= 1.0

    def tick(self, now_s: float) -> float:
        if self.last_update_time is None:
            self.last_update_time = now_s
            return self.current_pan
        dt = max(0.0, now_s - self.last_update_time)
        self.last_update_time = now_s
        alpha = 1.0 - float(np.exp(-self.smooth_rate * dt))
        self.current_pan += (self.target_pan - self.current_pan) * alpha
        self.current_pan = float(np.clip(self.current_pan, -1.0, 1.0))
        return self.current_pan


def find_peak_indices(signal: np.ndarray, fs: float, min_peak_distance_s: float) -> list[int]:
    x = np.asarray(signal, dtype=float)
    if x.size < 3:
        return []
    x = x - np.mean(x)
    std = float(np.std(x))
    if std <= 1e-9:
        return []

    min_distance = max(1, int(round(min_peak_distance_s * fs)))
    threshold = 0.55 * std
    peaks = []
    last_peak = -min_distance
    for idx in range(1, len(x) - 1):
        if idx - last_peak < min_distance:
            continue
        if x[idx] > threshold and x[idx] >= x[idx - 1] and x[idx] > x[idx + 1]:
            peaks.append(idx)
            last_peak = idx
    return peaks


def choose_peak_channel(df, detection: dict) -> str:
    channel = str(detection.get("channel", ""))
    if channel and channel != "-" and channel in df.columns:
        return channel

    side = str(detection.get("side", ""))
    if side == "left_chewing" and "l_gyro_mag" in df.columns:
        return "l_gyro_mag"
    if side == "right_chewing" and "r_gyro_mag" in df.columns:
        return "r_gyro_mag"

    if "l_gyro_mag" in df.columns and "r_gyro_mag" in df.columns:
        left_std = float(np.std(df["l_gyro_mag"].to_numpy(dtype=float)))
        right_std = float(np.std(df["r_gyro_mag"].to_numpy(dtype=float)))
        return "l_gyro_mag" if left_std >= right_std else "r_gyro_mag"
    return "l_gy" if "l_gy" in df.columns else "r_gy"


def estimate_last_chew_peak_time_s(df, detection: dict, fs: float, args, start_time_ms: float) -> Optional[float]:
    if detection.get("state") != "chewing" or float(detection.get("cpm", 0.0)) <= 0.0:
        return None
    channel = choose_peak_channel(df, detection)
    if channel not in df.columns:
        return None
    peaks = find_peak_indices(df[channel].to_numpy(dtype=float), fs, args.counter_min_peak_distance)
    if not peaks:
        return None
    peak_time_ms = float(df["time_ms"].iloc[peaks[-1]])
    return (peak_time_ms - float(start_time_ms)) / 1000.0


class PPBState:
    def __init__(self, threshold_seconds: float, end_debounce_seconds: float, cues_enabled: bool) -> None:
        self.threshold_seconds = threshold_seconds
        self.end_debounce_seconds = end_debounce_seconds
        self.cues_enabled = cues_enabled
        self.last_detection_state = "non_chewing"
        self.last_chew_peak_time_s: Optional[float] = None
        self.candidate_pause_start_s: Optional[float] = None
        self.candidate_non_chewing_start_s: Optional[float] = None
        self.pause_start_s: Optional[float] = None
        self.pop_marks_played = set()
        self.ding_played = False
        self.short_warning_played = False
        self.last_short_ppb_s: Optional[float] = None

    def update(
        self,
        detection: dict,
        df,
        fs: float,
        args,
        start_time_ms: float,
        elapsed_s: float,
        player: MusicLayerPlayer,
    ) -> dict:
        state = str(detection.get("state", "non_chewing"))
        was_chewing = self.last_detection_state == "chewing"
        is_chewing = state == "chewing"

        peak_time_s = estimate_last_chew_peak_time_s(df, detection, fs, args, start_time_ms)
        if peak_time_s is not None:
            self.last_chew_peak_time_s = peak_time_s

        cue = "-"
        if is_chewing:
            self.candidate_pause_start_s = None
            self.candidate_non_chewing_start_s = None
            if self.pause_start_s is not None and not self.ding_played:
                ppb_elapsed = max(0.0, elapsed_s - self.pause_start_s)
                self.last_short_ppb_s = ppb_elapsed
                if self.cues_enabled and not self.short_warning_played:
                    player.play_cue("error")
                    cue = "error"
                self.short_warning_played = True
                ppb_state = "too_short"
            else:
                ppb_elapsed = 0.0
                ppb_state = "idle"
            self.reset_pause()
        else:
            if was_chewing:
                self.candidate_non_chewing_start_s = elapsed_s
                self.candidate_pause_start_s = self.last_chew_peak_time_s or elapsed_s

            if (
                self.pause_start_s is None
                and self.candidate_non_chewing_start_s is not None
                and elapsed_s - self.candidate_non_chewing_start_s >= self.end_debounce_seconds
            ):
                self.pause_start_s = self.candidate_pause_start_s or self.candidate_non_chewing_start_s
                self.candidate_pause_start_s = None
                self.candidate_non_chewing_start_s = None
                self.pop_marks_played.clear()
                self.ding_played = False
                self.short_warning_played = False

            if self.pause_start_s is None:
                ppb_elapsed = 0.0
                ppb_state = "debouncing" if self.candidate_non_chewing_start_s is not None else "idle"
            else:
                ppb_elapsed = max(0.0, elapsed_s - self.pause_start_s)
                if ppb_elapsed >= self.threshold_seconds:
                    ppb_state = "ready"
                    if not self.ding_played:
                        if self.cues_enabled:
                            player.play_cue("ding")
                            cue = "ding"
                        self.ding_played = True
                else:
                    ppb_state = "waiting"
                    for mark in range(1, int(self.threshold_seconds)):
                        if ppb_elapsed >= mark and mark not in self.pop_marks_played:
                            if self.cues_enabled:
                                player.play_cue("pop")
                                cue = "pop"
                            self.pop_marks_played.add(mark)
                            break

        self.last_detection_state = state
        return {
            "ppb_state": ppb_state,
            "ppb_elapsed": ppb_elapsed,
            "ppb_ready": self.ding_played,
            "ppb_short": ppb_state == "too_short",
            "last_short_ppb": self.last_short_ppb_s,
            "pause_start_s": self.pause_start_s,
            "candidate_non_chewing_start_s": self.candidate_non_chewing_start_s,
            "last_chew_peak_time_s": self.last_chew_peak_time_s,
            "cue": cue,
        }

    def reset_pause(self) -> None:
        self.candidate_pause_start_s = None
        self.candidate_non_chewing_start_s = None
        self.pause_start_s = None
        self.pop_marks_played.clear()
        self.ding_played = False
        self.short_warning_played = False


def stereo_gains(pan: float) -> tuple[float, float]:
    pan = float(np.clip(pan, -1.0, 1.0))
    if pan < 0.0:
        return 1.0, 1.0 + pan
    return 1.0 - pan, 1.0


def load_side_model(path: Path, metadata_path: Path, args: argparse.Namespace) -> Optional[dict]:
    if args.disable_model or not path.exists():
        print(f"Side model is off or missing: {path}")
        return None

    if path.suffix.lower() == ".keras":
        if not metadata_path.exists():
            raise FileNotFoundError(f"CNN metadata not found: {metadata_path}")
        from tensorflow.keras.models import load_model

        bundle = joblib.load(metadata_path)
        bundle["model"] = load_model(path)
        bundle["kind"] = "cnn"
    else:
        bundle = joblib.load(path)
        bundle["kind"] = bundle.get("kind", "random_forest")

    print(f"Loaded side model: {path}")
    return bundle


def make_detection_args(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        counter_min_peak_distance=args.counter_min_peak_distance,
        min_peaks=args.min_peaks,
        min_cpm=args.min_cpm,
        max_cpm=args.max_cpm,
        min_channel_std=args.min_channel_std,
        max_regularity=args.max_regularity,
        side_ratio=args.side_ratio,
        model_threshold=args.model_threshold,
    )


def detect_c3_window(
    df,
    fs: float,
    detection_args: SimpleNamespace,
    side_model: Optional[dict],
    cpm_calibrator: Optional[dict],
    state_bundle: Optional[dict],
    threshold_gate: Optional[dict],
) -> dict:
    if state_bundle is not None:
        state_pred, chewing_prob = detector.predict_chewing_state(df, fs, state_bundle)
        threshold_metrics = {"threshold_score": 0.0}
    else:
        state_pred, chewing_prob, threshold_metrics = detector.predict_chewing_state_by_threshold(df, threshold_gate)

    if state_pred == "non_chewing":
        rule_result = detector.classify_dual_window(df, fs, detection_args)
        result = {
            "state": "non_chewing",
            "side": "-",
            "cpm": 0.0,
            "raw_cpm": 0.0,
            "channel": "-",
            "peaks": 0,
            "left_score": rule_result["left_score"],
            "right_score": rule_result["right_score"],
            "dominance": rule_result["dominance"],
            "model_side": "-",
            "model_prob": 0.0,
            "calibrated": False,
        }
    else:
        if side_model is not None:
            result = detector.classify_dual_window_with_model(df, fs, detection_args, side_model)
        else:
            result = detector.classify_dual_window(df, fs, detection_args)
            result["model_side"] = "-"
            result["model_prob"] = 0.0

        if result.get("state") != "chewing":
            result["state"] = "chewing"
            result["side"] = "-"
            result["cpm"] = 0.0
            result["raw_cpm"] = 0.0
            result["channel"] = "-"
            result["peaks"] = 0
        result = detector.apply_cpm_calibrator(df, fs, detection_args, result, cpm_calibrator)

    result["chewing_prob"] = chewing_prob
    result.update(threshold_metrics)
    return result


def format_layers(active_layers: set[str]) -> str:
    return "+".join(sorted(active_layers)) or "none"


def run_terminal_loop(
    ser,
    args: argparse.Namespace,
    fs: float,
    detection_args: SimpleNamespace,
    side_model: Optional[dict],
    cpm_calibrator: Optional[dict],
    state_bundle: Optional[dict],
    threshold_gate: Optional[dict],
    player: MusicLayerPlayer,
    intervention: InterventionState,
    spatial: SpatialPanState,
    ppb: PPBState,
) -> None:
    window_samples = int(round(args.window_seconds * fs))
    buffer = deque(maxlen=window_samples)
    start_time_ms = None
    last_update_s = 0.0

    print(f"ChewTune running on {args.port}. Fast threshold={args.fast_cpm_threshold:.1f} CPM.")
    print("Music mapping: pause=none, normal=drum+melody, stable=bass+drum+melody, fast=background+drum.")
    print("Press Ctrl+C to stop.")

    while True:
        raw = ser.readline().decode("utf-8", errors="ignore")
        sample = detector.parse_dual_imu_csv_line(raw)
        if sample is None:
            continue

        if start_time_ms is None:
            start_time_ms = sample[0]
        elapsed_s = (sample[0] - start_time_ms) / 1000.0
        buffer.append(sample)

        now_s = time.monotonic()
        if elapsed_s < args.ignore_first_seconds:
            continue
        if len(buffer) < window_samples or now_s - last_update_s < args.update_seconds:
            continue
        last_update_s = now_s

        df = detector.build_window_dataframe(buffer)
        detection = detect_c3_window(df, fs, detection_args, side_model, cpm_calibrator, state_bundle, threshold_gate)
        intervention_result = intervention.update(detection, now_s, args.update_seconds)
        ppb_result = ppb.update(detection, df, fs, args, start_time_ms, elapsed_s, player)
        if detection["state"] != "chewing":
            intervention_result = dict(intervention_result)
            intervention_result["intervention_state"] = "pause"
            intervention_result["active_layers"] = set()
        spatial.update_from_detection(detection, now_s)
        pan = spatial.tick(now_s)
        player.set_active_layers(intervention_result["active_layers"], pan)

        print(
            f"state={detection['state']:12s} side={detection['side']:15s} "
            f"CPM={float(detection['cpm']):5.1f} "
            f"stability={intervention_result['stability']:.2f} "
            f"music={intervention_result['intervention_state']:6s} "
            f"layers={format_layers(intervention_result['active_layers'])} "
            f"pan={pan:+.2f} target={spatial.target_pan:+.2f} "
            f"L/R chews={spatial.left_events}/{spatial.right_events} "
            f"PPB={float(ppb_result['ppb_elapsed']):4.1f}s "
            f"ppb_state={ppb_result['ppb_state']:9s} cue={ppb_result['cue']}"
        )


def run_visual_loop(
    ser,
    args: argparse.Namespace,
    fs: float,
    detection_args: SimpleNamespace,
    side_model: Optional[dict],
    cpm_calibrator: Optional[dict],
    state_bundle: Optional[dict],
    threshold_gate: Optional[dict],
    player: MusicLayerPlayer,
    intervention: InterventionState,
    spatial: SpatialPanState,
    ppb: PPBState,
) -> None:
    window_samples = int(round(args.window_seconds * fs))
    plot_samples = max(window_samples, int(round(args.plot_window_seconds * fs)))
    buffer = deque(maxlen=window_samples)
    t_data = deque(maxlen=plot_samples)
    event_times = deque(maxlen=plot_samples)
    chewing_values = deque(maxlen=plot_samples)
    intervention_values = deque(maxlen=plot_samples)
    cpm_values = deque(maxlen=plot_samples)
    stability_values = deque(maxlen=plot_samples)
    pan_values = deque(maxlen=plot_samples)

    runtime = {
        "start_time_ms": None,
        "last_update_s": 0.0,
        "stop": False,
        "detection": {
            "state": "warming_up",
            "side": "-",
            "cpm": 0.0,
            "raw_cpm": 0.0,
            "chewing_prob": 0.0,
            "threshold_score": 0.0,
            "model_side": "-",
            "model_prob": 0.0,
        },
        "intervention": {
            "intervention_state": "pause",
            "stability": 0.0,
            "recent_gap": float("inf"),
            "active_layers": set(),
        },
        "ppb": {
            "ppb_state": "idle",
            "ppb_elapsed": 0.0,
            "ppb_ready": False,
            "ppb_short": False,
            "cue": "-",
        },
        "pan": 0.0,
        "target_pan": 0.0,
    }

    print(f"ChewTune visual interface running on {args.port}. Close the window or press Stop to quit.")

    fig, axes = plt.subplots(4, 1, figsize=(12, 9), sharex=True)
    plt.subplots_adjust(top=0.78, bottom=0.12, hspace=0.38)
    ax_cpm, ax_stability, ax_pan, ax_state = axes

    cpm_line, = ax_cpm.plot([], [], color="#1f77b4", linewidth=2, label="CPM")
    ax_cpm.axhline(args.fast_cpm_threshold, color="#d62728", linestyle="--", linewidth=1.5, label="fast threshold")
    stability_line, = ax_stability.plot([], [], color="#2ca02c", linewidth=2, label="stability")
    ax_stability.axhline(args.stable_threshold, color="#ff7f0e", linestyle="--", linewidth=1.5, label="stable threshold")
    pan_line, = ax_pan.plot([], [], color="#17becf", linewidth=2, label="sound pan")
    ax_pan.axhline(0.0, color="#666666", linestyle="--", linewidth=1.0, label="center")
    chewing_line, = ax_state.step([], [], where="post", color="#222222", linewidth=2, label="chewing")
    intervention_line, = ax_state.step([], [], where="post", color="#9467bd", linewidth=2, label="music state")

    ax_cpm.set_title("Chewing rate")
    ax_cpm.set_ylabel("CPM")
    ax_cpm.set_ylim(0, max(180.0, args.fast_cpm_threshold * 1.4))
    ax_stability.set_title("Chewing stability")
    ax_stability.set_ylabel("stability")
    ax_stability.set_ylim(-0.05, 1.05)
    ax_pan.set_title("Headphone sound position")
    ax_pan.set_ylabel("pan")
    ax_pan.set_ylim(-1.05, 1.05)
    ax_pan.set_yticks([-1, 0, 1])
    ax_pan.set_yticklabels(["left", "center", "right"])
    ax_state.set_title("Detection and intervention state")
    ax_state.set_ylabel("state")
    ax_state.set_xlabel("Time / s")
    ax_state.set_yticks([0, 1, 2, 3])
    ax_state.set_yticklabels(["pause/non", "normal", "stable", "fast"])
    ax_state.set_ylim(-0.25, 3.25)

    for axis in axes:
        axis.grid(True)
        axis.legend(loc="upper right")

    status_text = fig.text(0.02, 0.93, "State: warming_up | Side: - | CPM: 0.0", fontsize=15, weight="bold")
    status_text_2 = fig.text(0.02, 0.885, "Music: pause | stability: 0.00 | layers: none", fontsize=13)
    status_text_3 = fig.text(0.02, 0.845, "Pan: +0.00 target +0.00 | Model: - (0.00) | threshold: 0.00", fontsize=11)

    button_axis = fig.add_axes([0.86, 0.035, 0.1, 0.055])
    stop_button = Button(button_axis, "Stop")

    def stop(_event=None) -> None:
        runtime["stop"] = True
        plt.close(fig)

    stop_button.on_clicked(stop)

    def update(_frame):
        if runtime["stop"]:
            return [cpm_line, stability_line, pan_line, chewing_line, intervention_line]

        pan = spatial.tick(time.monotonic())
        player.set_pan(pan)
        runtime["pan"] = pan
        runtime["target_pan"] = spatial.target_pan

        for _ in range(80):
            if ser.in_waiting <= 0:
                break

            sample = detector.parse_dual_imu_csv_line(ser.readline().decode("utf-8", errors="ignore"))
            if sample is None:
                continue

            if runtime["start_time_ms"] is None:
                runtime["start_time_ms"] = sample[0]
            elapsed_s = (sample[0] - float(runtime["start_time_ms"])) / 1000.0
            t_data.append(elapsed_s)
            buffer.append(sample)

            now_s = time.monotonic()
            if elapsed_s < args.ignore_first_seconds:
                continue
            if len(buffer) < window_samples or now_s - float(runtime["last_update_s"]) < args.update_seconds:
                continue
            runtime["last_update_s"] = now_s

            df = detector.build_window_dataframe(buffer)
            detection = detect_c3_window(df, fs, detection_args, side_model, cpm_calibrator, state_bundle, threshold_gate)
            intervention_result = intervention.update(detection, now_s, args.update_seconds)
            ppb_result = ppb.update(detection, df, fs, args, float(runtime["start_time_ms"]), elapsed_s, player)
            if detection["state"] != "chewing":
                intervention_result = dict(intervention_result)
                intervention_result["intervention_state"] = "pause"
                intervention_result["active_layers"] = set()
            spatial.update_from_detection(detection, now_s)
            pan = spatial.tick(now_s)
            player.set_active_layers(intervention_result["active_layers"], pan)
            runtime["detection"] = detection
            runtime["intervention"] = intervention_result
            runtime["ppb"] = ppb_result
            runtime["pan"] = pan
            runtime["target_pan"] = spatial.target_pan

            event_times.append(elapsed_s)
            chewing_values.append(1 if detection["state"] == "chewing" else 0)
            intervention_values.append(INTERVENTION_STATE_VALUES[intervention_result["intervention_state"]])
            cpm_values.append(float(detection["cpm"]))
            stability_values.append(float(intervention_result["stability"]))
            pan_values.append(pan)

            print(
                f"state={detection['state']:12s} side={detection['side']:15s} "
                f"CPM={float(detection['cpm']):5.1f} "
                f"stability={intervention_result['stability']:.2f} "
                f"music={intervention_result['intervention_state']:6s} "
                f"layers={format_layers(intervention_result['active_layers'])} "
                f"pan={pan:+.2f} target={spatial.target_pan:+.2f} "
                f"L/R chews={spatial.left_events}/{spatial.right_events} "
                f"PPB={float(ppb_result['ppb_elapsed']):4.1f}s "
                f"ppb_state={ppb_result['ppb_state']:9s} cue={ppb_result['cue']}"
            )

        if not t_data:
            return [cpm_line, stability_line, pan_line, chewing_line, intervention_line]

        detection = runtime["detection"]
        intervention_result = runtime["intervention"]
        ppb_result = runtime["ppb"]
        cpm_line.set_data(event_times, cpm_values)
        stability_line.set_data(event_times, stability_values)
        pan_line.set_data(event_times, pan_values)
        chewing_line.set_data(event_times, chewing_values)
        intervention_line.set_data(event_times, intervention_values)

        x_min = t_data[0]
        x_max = max(t_data[-1], x_min + 1.0)
        for axis in axes:
            axis.set_xlim(x_min, x_max)
        if cpm_values:
            ax_cpm.set_ylim(0, max(180.0, args.fast_cpm_threshold * 1.4, max(cpm_values) * 1.25))

        status_text.set_text(
            f"State: {detection['state']} | Side: {detection['side']} | CPM: {float(detection['cpm']):.1f} "
            f"(raw {float(detection.get('raw_cpm', 0.0)):.1f})"
        )
        status_text_2.set_text(
            f"Music: {intervention_result['intervention_state']} | "
            f"stability: {float(intervention_result['stability']):.2f} | "
            f"layers: {format_layers(intervention_result['active_layers'])}"
        )
        status_text_3.set_text(
            f"Pan: {float(runtime['pan']):+.2f} target {float(runtime['target_pan']):+.2f} | "
            f"L/R chews: {spatial.left_events}/{spatial.right_events} | "
            f"PPB: {float(ppb_result['ppb_elapsed']):.1f}s {ppb_result['ppb_state']} | "
            f"Model side: {detection.get('model_side', '-')} ({float(detection.get('model_prob', 0.0)):.2f}) | "
            f"threshold score: {float(detection.get('threshold_score', 0.0)):.2f}"
        )
        return [cpm_line, stability_line, pan_line, chewing_line, intervention_line]

    _animation = FuncAnimation(fig, update, interval=30, blit=False, cache_frame_data=False)
    plt.show()


def main() -> None:
    parser = argparse.ArgumentParser(description="ChewTune chewing detection and music intervention system.")
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--sample-rate", type=int, default=100)
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--update-seconds", type=float, default=0.5)
    parser.add_argument("--plot-window-seconds", type=float, default=30.0)
    parser.add_argument("--ignore-first-seconds", type=float, default=3.0)
    parser.add_argument("--counter-min-peak-distance", type=float, default=0.45)
    parser.add_argument("--min-peaks", type=int, default=1)
    parser.add_argument("--min-cpm", type=float, default=30.0)
    parser.add_argument("--max-cpm", type=float, default=180.0)
    parser.add_argument("--min-channel-std", type=float, default=0.015)
    parser.add_argument("--max-regularity", type=float, default=1.25)
    parser.add_argument("--side-ratio", type=float, default=1.12)
    parser.add_argument("--model-threshold", type=float, default=0.6)
    parser.add_argument("--disable-model", action="store_true")
    parser.add_argument("--disable-cpm-calibrator", action="store_true")
    parser.add_argument("--side-model", type=Path, default=DEFAULT_SIDE_MODEL_PATH)
    parser.add_argument("--side-metadata", type=Path, default=DEFAULT_SIDE_METADATA_PATH)
    parser.add_argument("--cpm-calibrator", type=Path, default=DEFAULT_CPM_CALIBRATOR_PATH)
    parser.add_argument("--chewing-state-model", type=Path, default=DEFAULT_CHEWING_STATE_MODEL_PATH)
    parser.add_argument("--chewing-threshold", type=float, default=0.60)
    parser.add_argument("--disable-chewing-state-model", action="store_true")
    parser.add_argument("--chewing-threshold-config", type=Path, default=DEFAULT_THRESHOLD_PATH)
    parser.add_argument("--music-dir", type=Path, default=DEFAULT_MUSIC_DIR)
    parser.add_argument("--active-volume", type=float, default=0.9)
    parser.add_argument("--inactive-volume", type=float, default=0.0)
    parser.add_argument("--test-music-state", choices=sorted(STATE_LAYERS.keys()))
    parser.add_argument("--test-ppb-cue", choices=CUE_NAMES)
    parser.add_argument("--test-music-seconds", type=float, default=8.0)
    parser.add_argument("--test-pan", type=float, default=0.0)
    parser.add_argument("--no-gui", action="store_true")
    parser.add_argument("--fast-cpm-threshold", type=float, default=120.0)
    parser.add_argument("--stable-threshold", type=float, default=0.50)
    parser.add_argument("--pause-seconds", type=float, default=5.0)
    parser.add_argument("--pan-step", type=float, default=1.0 / 30.0)
    parser.add_argument("--pan-smooth-rate", type=float, default=2.2)
    parser.add_argument("--ppb-threshold-seconds", type=float, default=4.0)
    parser.add_argument("--ppb-end-debounce-seconds", type=float, default=1.5)
    parser.add_argument("--disable-ppb-cues", action="store_true")
    args = parser.parse_args()

    fs = args.sample_rate
    side_model = load_side_model(args.side_model, args.side_metadata, args)
    if side_model is not None:
        fs = int(side_model.get("sample_rate_hz", fs))
        args.window_seconds = float(side_model.get("window_seconds", args.window_seconds))

    cpm_calibrator = None
    if not args.disable_cpm_calibrator and args.cpm_calibrator.exists():
        cpm_calibrator = joblib.load(args.cpm_calibrator)
        print(f"Loaded CPM calibrator: {args.cpm_calibrator}")
    else:
        print(f"CPM calibrator is off or missing: {args.cpm_calibrator}")

    state_bundle = None
    threshold_gate = None
    if not args.disable_chewing_state_model and args.chewing_state_model.exists():
        state_bundle = joblib.load(args.chewing_state_model)
        state_bundle["threshold"] = args.chewing_threshold
        print(f"Loaded RF chewing state model: {args.chewing_state_model}")
        print(f"RF chewing threshold: {args.chewing_threshold:.2f}")
    else:
        threshold_gate = detector.load_threshold_gate(args.chewing_threshold_config)
        print(f"RF model is off or missing: {args.chewing_state_model}")
        print(f"Loaded fallback threshold gate: {args.chewing_threshold_config}")

    player = MusicLayerPlayer(args.music_dir, args.active_volume, args.inactive_volume)
    player.start()
    if args.test_ppb_cue:
        print(f"Testing PPB cue={args.test_ppb_cue}, duration={args.test_music_seconds:.1f}s")
        try:
            player.play_cue(args.test_ppb_cue)
            time.sleep(max(0.1, args.test_music_seconds))
        finally:
            player.stop()
        return

    if args.test_music_state:
        active_layers = STATE_LAYERS[args.test_music_state]
        player.set_active_layers(active_layers, args.test_pan)
        layers = "+".join(sorted(active_layers)) or "none"
        print(
            f"Testing music state={args.test_music_state}, layers={layers}, "
            f"pan={float(np.clip(args.test_pan, -1.0, 1.0)):+.2f}, duration={args.test_music_seconds:.1f}s"
        )
        try:
            time.sleep(max(0.1, args.test_music_seconds))
        finally:
            player.stop()
        return

    intervention = InterventionState(
        fast_cpm_threshold=args.fast_cpm_threshold,
        stable_threshold=args.stable_threshold,
        pause_seconds=args.pause_seconds,
        min_interval_s=60.0 / max(args.max_cpm, 1e-9),
        max_interval_s=60.0 / max(args.min_cpm, 1e-9),
    )

    detection_args = make_detection_args(args)
    spatial = SpatialPanState(
        step=args.pan_step,
        smooth_rate=args.pan_smooth_rate,
    )
    ppb = PPBState(
        threshold_seconds=args.ppb_threshold_seconds,
        end_debounce_seconds=args.ppb_end_debounce_seconds,
        cues_enabled=not args.disable_ppb_cues,
    )
    ser = detector.open_serial_port(args.port, args.baud)

    try:
        if args.no_gui:
            run_terminal_loop(
                ser, args, fs, detection_args, side_model, cpm_calibrator, state_bundle, threshold_gate,
                player, intervention, spatial, ppb
            )
        else:
            run_visual_loop(
                ser, args, fs, detection_args, side_model, cpm_calibrator, state_bundle, threshold_gate,
                player, intervention, spatial, ppb
            )
    except KeyboardInterrupt:
        print("Stopping ChewTune.")
    finally:
        player.stop()
        if ser.is_open:
            ser.close()
        print("Serial closed.")


if __name__ == "__main__":
    main()
