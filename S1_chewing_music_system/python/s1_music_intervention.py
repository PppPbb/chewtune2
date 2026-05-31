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

import realtime_dual_mpu6050_detection as c3


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MUSIC_DIR = PROJECT_ROOT / "music"
DEFAULT_SIDE_MODEL_PATH = PROJECT_ROOT / "models" / "dual_side_cnn.pkl"
DEFAULT_SIDE_METADATA_PATH = PROJECT_ROOT / "models" / "dual_side_cnn_meta.pkl"
DEFAULT_CPM_CALIBRATOR_PATH = PROJECT_ROOT / "models" / "cpm_calibrator.pkl"
DEFAULT_THRESHOLD_PATH = PROJECT_ROOT / "models" / "chewing_state_threshold.json"

LAYER_NAMES = ("drum", "background", "bass", "melody")
LAYER_EXTENSIONS = (".wav", ".mp3", ".ogg")
STATE_LAYERS = {
    "pause": set(),
    "normal": {"drum", "melody"},
    "stable": {"bass", "drum", "melody"},
    "fast": {"background", "drum"},
}
S1_STATE_VALUES = {"pause": 0, "normal": 1, "stable": 2, "fast": 3}
S1_STATE_LABELS = ["pause", "normal", "stable", "fast"]


class MusicLayerPlayer:
    def __init__(self, music_dir: Path, active_volume: float, inactive_volume: float) -> None:
        self.music_dir = music_dir
        self.active_volume = active_volume
        self.inactive_volume = inactive_volume
        self.channels = {}
        self.enabled = False

    def start(self) -> None:
        try:
            import pygame
        except ImportError as exc:
            raise ImportError("pygame is required for S1 music playback. Install it with: pip install pygame") from exc

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

        self.enabled = bool(self.channels)
        if not self.enabled:
            print("No music layers were loaded. Detection will still run, but no audio will play.")
        else:
            print(f"Loaded {len(self.channels)} music layer(s). Layers are looping at inactive volume until S1 activates them.")

    def find_layer_file(self, layer: str) -> Optional[Path]:
        for ext in LAYER_EXTENSIONS:
            direct = self.music_dir / f"{layer}{ext}"
            if direct.exists():
                return direct
        for path in self.music_dir.iterdir():
            if path.is_file() and path.suffix.lower() in LAYER_EXTENSIONS and path.stem.lower().startswith(layer):
                return path
        return None

    def set_active_layers(self, active_layers: set[str]) -> None:
        for layer, channel in self.channels.items():
            channel.set_volume(self.active_volume if layer in active_layers else self.inactive_volume)

    def stop(self) -> None:
        if not self.channels:
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

    print(f"Loaded C3 side model: {path}")
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
    threshold_gate: dict,
) -> dict:
    state_pred, chewing_prob, threshold_metrics = c3.predict_chewing_state_by_threshold(df, threshold_gate)
    if state_pred == "non_chewing":
        rule_result = c3.classify_dual_window(df, fs, detection_args)
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
            result = c3.classify_dual_window_with_model(df, fs, detection_args, side_model)
        else:
            result = c3.classify_dual_window(df, fs, detection_args)
            result["model_side"] = "-"
            result["model_prob"] = 0.0
        result = c3.apply_cpm_calibrator(df, fs, detection_args, result, cpm_calibrator)

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
    threshold_gate: dict,
    player: MusicLayerPlayer,
    intervention: InterventionState,
) -> None:
    window_samples = int(round(args.window_seconds * fs))
    buffer = deque(maxlen=window_samples)
    start_time_ms = None
    last_update_s = 0.0

    print(f"S1 running on {args.port}. Fast threshold={args.fast_cpm_threshold:.1f} CPM.")
    print("Music mapping: pause=none, normal=drum+melody, stable=bass+drum+melody, fast=background+drum.")
    print("Press Ctrl+C to stop.")

    while True:
        raw = ser.readline().decode("utf-8", errors="ignore")
        sample = c3.parse_dual_imu_csv_line(raw)
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

        df = c3.build_window_dataframe(buffer)
        detection = detect_c3_window(df, fs, detection_args, side_model, cpm_calibrator, threshold_gate)
        intervention_result = intervention.update(detection, now_s, args.update_seconds)
        player.set_active_layers(intervention_result["active_layers"])

        print(
            f"C3={detection['state']:12s} side={detection['side']:15s} "
            f"CPM={float(detection['cpm']):5.1f} "
            f"stability={intervention_result['stability']:.2f} "
            f"S1={intervention_result['intervention_state']:6s} "
            f"layers={format_layers(intervention_result['active_layers'])}"
        )


def run_visual_loop(
    ser,
    args: argparse.Namespace,
    fs: float,
    detection_args: SimpleNamespace,
    side_model: Optional[dict],
    cpm_calibrator: Optional[dict],
    threshold_gate: dict,
    player: MusicLayerPlayer,
    intervention: InterventionState,
) -> None:
    window_samples = int(round(args.window_seconds * fs))
    plot_samples = max(window_samples, int(round(args.plot_window_seconds * fs)))
    buffer = deque(maxlen=window_samples)
    t_data = deque(maxlen=plot_samples)
    event_times = deque(maxlen=plot_samples)
    chewing_values = deque(maxlen=plot_samples)
    s1_values = deque(maxlen=plot_samples)
    cpm_values = deque(maxlen=plot_samples)
    stability_values = deque(maxlen=plot_samples)

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
    }

    print(f"S1 visual interface running on {args.port}. Close the window or press Stop to quit.")

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    plt.subplots_adjust(top=0.78, bottom=0.14, hspace=0.35)
    ax_cpm, ax_stability, ax_state = axes

    cpm_line, = ax_cpm.plot([], [], color="#1f77b4", linewidth=2, label="CPM")
    ax_cpm.axhline(args.fast_cpm_threshold, color="#d62728", linestyle="--", linewidth=1.5, label="fast threshold")
    stability_line, = ax_stability.plot([], [], color="#2ca02c", linewidth=2, label="stability")
    ax_stability.axhline(args.stable_threshold, color="#ff7f0e", linestyle="--", linewidth=1.5, label="stable threshold")
    chewing_line, = ax_state.step([], [], where="post", color="#222222", linewidth=2, label="C3 chewing")
    s1_line, = ax_state.step([], [], where="post", color="#9467bd", linewidth=2, label="S1 state")

    ax_cpm.set_title("Chewing rate")
    ax_cpm.set_ylabel("CPM")
    ax_cpm.set_ylim(0, max(180.0, args.fast_cpm_threshold * 1.4))
    ax_stability.set_title("Chewing stability")
    ax_stability.set_ylabel("stability")
    ax_stability.set_ylim(-0.05, 1.05)
    ax_state.set_title("Detection and intervention state")
    ax_state.set_ylabel("state")
    ax_state.set_xlabel("Time / s")
    ax_state.set_yticks([0, 1, 2, 3])
    ax_state.set_yticklabels(["pause/non", "normal", "stable", "fast"])
    ax_state.set_ylim(-0.25, 3.25)

    for axis in axes:
        axis.grid(True)
        axis.legend(loc="upper right")

    status_text = fig.text(0.02, 0.93, "C3: warming_up | Side: - | CPM: 0.0", fontsize=15, weight="bold")
    status_text_2 = fig.text(0.02, 0.885, "S1: pause | stability: 0.00 | layers: none", fontsize=13)
    status_text_3 = fig.text(0.02, 0.845, "Model: - (0.00) | threshold: 0.00 | p(chew): 0.00", fontsize=11)

    button_axis = fig.add_axes([0.86, 0.035, 0.1, 0.055])
    stop_button = Button(button_axis, "Stop")

    def stop(_event=None) -> None:
        runtime["stop"] = True
        plt.close(fig)

    stop_button.on_clicked(stop)

    def update(_frame):
        if runtime["stop"]:
            return [cpm_line, stability_line, chewing_line, s1_line]

        for _ in range(80):
            if ser.in_waiting <= 0:
                break

            sample = c3.parse_dual_imu_csv_line(ser.readline().decode("utf-8", errors="ignore"))
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

            df = c3.build_window_dataframe(buffer)
            detection = detect_c3_window(df, fs, detection_args, side_model, cpm_calibrator, threshold_gate)
            intervention_result = intervention.update(detection, now_s, args.update_seconds)
            player.set_active_layers(intervention_result["active_layers"])
            runtime["detection"] = detection
            runtime["intervention"] = intervention_result

            event_times.append(elapsed_s)
            chewing_values.append(1 if detection["state"] == "chewing" else 0)
            s1_values.append(S1_STATE_VALUES[intervention_result["intervention_state"]])
            cpm_values.append(float(detection["cpm"]))
            stability_values.append(float(intervention_result["stability"]))

            print(
                f"C3={detection['state']:12s} side={detection['side']:15s} "
                f"CPM={float(detection['cpm']):5.1f} "
                f"stability={intervention_result['stability']:.2f} "
                f"S1={intervention_result['intervention_state']:6s} "
                f"layers={format_layers(intervention_result['active_layers'])}"
            )

        if not t_data:
            return [cpm_line, stability_line, chewing_line, s1_line]

        detection = runtime["detection"]
        intervention_result = runtime["intervention"]
        cpm_line.set_data(event_times, cpm_values)
        stability_line.set_data(event_times, stability_values)
        chewing_line.set_data(event_times, chewing_values)
        s1_line.set_data(event_times, s1_values)

        x_min = t_data[0]
        x_max = max(t_data[-1], x_min + 1.0)
        for axis in axes:
            axis.set_xlim(x_min, x_max)
        if cpm_values:
            ax_cpm.set_ylim(0, max(180.0, args.fast_cpm_threshold * 1.4, max(cpm_values) * 1.25))

        status_text.set_text(
            f"C3: {detection['state']} | Side: {detection['side']} | CPM: {float(detection['cpm']):.1f} "
            f"(raw {float(detection.get('raw_cpm', 0.0)):.1f})"
        )
        status_text_2.set_text(
            f"S1: {intervention_result['intervention_state']} | "
            f"stability: {float(intervention_result['stability']):.2f} | "
            f"layers: {format_layers(intervention_result['active_layers'])}"
        )
        status_text_3.set_text(
            f"Model side: {detection.get('model_side', '-')} ({float(detection.get('model_prob', 0.0)):.2f}) | "
            f"threshold score: {float(detection.get('threshold_score', 0.0)):.2f} | "
            f"p(chew): {float(detection.get('chewing_prob', 0.0)):.2f}"
        )
        return [cpm_line, stability_line, chewing_line, s1_line]

    _animation = FuncAnimation(fig, update, interval=30, blit=False, cache_frame_data=False)
    plt.show()


def main() -> None:
    parser = argparse.ArgumentParser(description="S1 chewing music intervention system based on C3 detection.")
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
    parser.add_argument("--chewing-threshold-config", type=Path, default=DEFAULT_THRESHOLD_PATH)
    parser.add_argument("--music-dir", type=Path, default=DEFAULT_MUSIC_DIR)
    parser.add_argument("--active-volume", type=float, default=0.9)
    parser.add_argument("--inactive-volume", type=float, default=0.0)
    parser.add_argument("--test-music-state", choices=sorted(STATE_LAYERS.keys()))
    parser.add_argument("--test-music-seconds", type=float, default=8.0)
    parser.add_argument("--no-gui", action="store_true")
    parser.add_argument("--fast-cpm-threshold", type=float, default=120.0)
    parser.add_argument("--stable-threshold", type=float, default=0.50)
    parser.add_argument("--pause-seconds", type=float, default=2.6)
    args = parser.parse_args()

    fs = args.sample_rate
    side_model = load_side_model(args.side_model, args.side_metadata, args)
    if side_model is not None:
        fs = int(side_model.get("sample_rate_hz", fs))
        args.window_seconds = float(side_model.get("window_seconds", args.window_seconds))

    cpm_calibrator = None
    if not args.disable_cpm_calibrator and args.cpm_calibrator.exists():
        cpm_calibrator = joblib.load(args.cpm_calibrator)
        print(f"Loaded C3 CPM calibrator: {args.cpm_calibrator}")
    else:
        print(f"CPM calibrator is off or missing: {args.cpm_calibrator}")

    threshold_gate = c3.load_threshold_gate(args.chewing_threshold_config)
    print(f"Loaded C3 threshold gate: {args.chewing_threshold_config}")

    player = MusicLayerPlayer(args.music_dir, args.active_volume, args.inactive_volume)
    player.start()
    if args.test_music_state:
        active_layers = STATE_LAYERS[args.test_music_state]
        player.set_active_layers(active_layers)
        layers = "+".join(sorted(active_layers)) or "none"
        print(f"Testing music state={args.test_music_state}, layers={layers}, duration={args.test_music_seconds:.1f}s")
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
    ser = c3.open_serial_port(args.port, args.baud)

    try:
        if args.no_gui:
            run_terminal_loop(
                ser, args, fs, detection_args, side_model, cpm_calibrator, threshold_gate, player, intervention
            )
        else:
            run_visual_loop(
                ser, args, fs, detection_args, side_model, cpm_calibrator, threshold_gate, player, intervention
            )
    except KeyboardInterrupt:
        print("Stopping S1.")
    finally:
        player.stop()
        if ser.is_open:
            ser.close()
        print("Serial closed.")


if __name__ == "__main__":
    main()
