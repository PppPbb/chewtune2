import argparse
import csv
import sys
import time
from collections import deque
from pathlib import Path
from typing import Optional, Tuple

import joblib
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button
import numpy as np
import pandas as pd
import serial


THIS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = THIS_DIR.parents[1]
DEFAULT_PIPELINE_DIR = PROJECT_DIR / "imu_chew_pipeline"
DEFAULT_MODEL_PATH = DEFAULT_PIPELINE_DIR / "models" / "random_forest.pkl"
DEFAULT_GATE_MODEL_PATH = PROJECT_DIR / "mpu6050_chew_detection_B" / "models" / "non_chewing_gate.pkl"
DEFAULT_SIDE_MODEL_PATH = PROJECT_DIR / "mpu6050_chew_detection_B" / "models" / "chewing_side.pkl"

PIPELINE_PYTHON_DIR = DEFAULT_PIPELINE_DIR / "python"
if PIPELINE_PYTHON_DIR.exists():
    sys.path.insert(0, str(PIPELINE_PYTHON_DIR))

from chewing_counter import choose_counter_signal, classify_rate, count_chews  # noqa: E402
from config import RAW_COLUMNS, SAMPLE_RATE_HZ, SENSOR_COLUMNS  # noqa: E402
from feature_extraction import extract_window_features  # noqa: E402
from preprocess import add_magnitude_columns  # noqa: E402


Sample = Tuple[float, float, float, float, float, float, float]


def parse_imu_csv_line(line: str) -> Optional[Sample]:
    line = line.strip()
    if not line:
        return None

    ignored_prefixes = ("BOOT", "MPU6050", "ERROR", "time_ms")
    if line.startswith(ignored_prefixes):
        print(line)
        return None

    parts = line.split(",")
    if len(parts) != 7:
        return None

    try:
        return tuple(float(part) for part in parts)  # type: ignore[return-value]
    except ValueError:
        return None


def open_serial_port(port: str, baud_rate: int = 115200, timeout: float = 1.0) -> serial.Serial:
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud_rate
    ser.timeout = timeout

    ser.dtr = False
    ser.rts = False
    ser.open()
    ser.setDTR(False)
    ser.setRTS(False)
    time.sleep(1.0)
    return ser


def build_window_dataframe(buffer: deque[Sample]) -> pd.DataFrame:
    df = pd.DataFrame(list(buffer), columns=RAW_COLUMNS)
    df["label"] = "unknown"
    df["source_file"] = "mpu6050_realtime"
    return add_magnitude_columns(df)


def append_csv_row(
    writer: Optional[csv.writer],
    sample: Sample,
    state: str,
    prob: float,
    cpm: float,
    side: str,
    side_prob: float,
    true_label: str,
) -> None:
    if writer is None:
        return
    writer.writerow([*sample, state, f"{prob:.6f}", f"{cpm:.3f}", side, f"{side_prob:.6f}", true_label])


def update_axis_limits(axis, x_data, values, min_margin: float) -> None:
    if not x_data:
        return
    axis.set_xlim(x_data[0], max(x_data[-1], x_data[0] + 1.0))
    flat_values = [v for series in values for v in series]
    if not flat_values:
        return
    lo = min(flat_values)
    hi = max(flat_values)
    margin = max(min_margin, (hi - lo) * 0.2)
    axis.set_ylim(lo - margin, hi + margin)


def main() -> None:
    parser = argparse.ArgumentParser(description="Real-time chewing detection for XIAO + GY-521 MPU6050.")
    parser.add_argument("--port", default="COM4", help="Serial port, for example COM4.")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--detector", choices=["signal", "model", "hybrid"], default="signal")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--gate-model", type=Path, default=DEFAULT_GATE_MODEL_PATH)
    parser.add_argument("--gate-threshold", type=float, default=0.9)
    parser.add_argument("--disable-gate", action="store_true")
    parser.add_argument("--side-model", type=Path, default=DEFAULT_SIDE_MODEL_PATH)
    parser.add_argument("--side-threshold", type=float, default=0.65)
    parser.add_argument("--disable-side", action="store_true")
    parser.add_argument("--update-seconds", type=float, default=1.0)
    parser.add_argument("--prob-threshold", type=float, default=0.5)
    parser.add_argument("--smooth-windows", type=int, default=2)
    parser.add_argument("--min-chewing-votes", type=int, default=1)
    parser.add_argument("--counter-channel", default="auto", help="auto, gyro_mag, gz, gy, gx, acc_mag, ax, ay, az")
    parser.add_argument("--counter-min-peak-distance", type=float, default=0.55, help="Shortest valid seconds between chewing peaks.")
    parser.add_argument("--plot-window-seconds", type=float, default=10.0, help="Seconds of waveform visible in the plot.")
    parser.add_argument("--ignore-first-seconds", type=float, default=5.0, help="Ignore startup motion before this elapsed time.")
    parser.add_argument("--signal-min-peaks", type=int, default=1)
    parser.add_argument("--signal-min-cpm", type=float, default=30.0)
    parser.add_argument("--signal-max-cpm", type=float, default=160.0)
    parser.add_argument("--signal-min-channel-std", type=float, default=0.03, help="Minimum std of the selected counter channel.")
    parser.add_argument("--signal-min-gyro-std", type=float, default=0.015, help="Minimum gyro magnitude std in rad/s.")
    parser.add_argument("--signal-min-acc-std", type=float, default=0.015, help="Minimum acceleration magnitude std in g.")
    parser.add_argument("--signal-max-regularity", type=float, default=1.25)
    parser.add_argument("--activity-label", default="", help="Optional true label written to --save-csv, such as chewing or non_chewing.")
    parser.add_argument("--save-csv", type=Path, help="Optional path to save raw samples plus detection output.")
    args = parser.parse_args()

    use_model = args.detector in {"model", "hybrid"}
    if use_model and not args.model.exists():
        raise FileNotFoundError(f"Model not found: {args.model}")

    model = None
    feature_columns = []
    gate_model = None
    gate_feature_columns = []
    use_gate = not args.disable_gate and args.gate_model.exists()
    side_model = None
    side_feature_columns = []
    use_side = not args.disable_side and args.side_model.exists()
    fs = SAMPLE_RATE_HZ
    window_seconds = 2.0
    if use_model:
        bundle = joblib.load(args.model)
        model = bundle["model"]
        feature_columns = bundle["feature_columns"]
        fs = int(bundle.get("sample_rate_hz", SAMPLE_RATE_HZ))
        window_seconds = float(bundle.get("window_seconds", 2.0))
    if use_gate:
        gate_bundle = joblib.load(args.gate_model)
        gate_model = gate_bundle["model"]
        gate_feature_columns = gate_bundle["feature_columns"]
        fs = int(gate_bundle.get("sample_rate_hz", fs))
        window_seconds = float(gate_bundle.get("window_seconds", window_seconds))
    if use_side:
        side_bundle = joblib.load(args.side_model)
        side_model = side_bundle["model"]
        side_feature_columns = side_bundle["feature_columns"]
        fs = int(side_bundle.get("sample_rate_hz", fs))
        window_seconds = float(side_bundle.get("window_seconds", window_seconds))
    window_samples = int(round(window_seconds * fs))

    inference_buffer: deque[Sample] = deque(maxlen=window_samples)
    plot_samples = max(window_samples, int(round(args.plot_window_seconds * fs)))
    t_data: deque[float] = deque(maxlen=plot_samples)
    channels = {name: deque(maxlen=plot_samples) for name in SENSOR_COLUMNS}
    event_times: deque[float] = deque(maxlen=plot_samples)
    event_states: deque[int] = deque(maxlen=plot_samples)
    cpm_values: deque[float] = deque(maxlen=plot_samples)
    chewing_vote_history: deque[bool] = deque(maxlen=max(1, args.smooth_windows))
    chewing_prob_history: deque[float] = deque(maxlen=max(1, args.smooth_windows))

    csv_file = None
    csv_writer = None
    if args.save_csv:
        args.save_csv.parent.mkdir(parents=True, exist_ok=True)
        csv_file = args.save_csv.open("w", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(
            [
                "time_ms",
                "ax",
                "ay",
                "az",
                "gx",
                "gy",
                "gz",
                "state",
                "chewing_prob",
                "cpm",
                "chewing_side",
                "side_prob",
                "true_label",
            ]
        )

    ser = open_serial_port(args.port, args.baud)
    runtime = {
        "start_time_ms": None,
        "last_update": 0.0,
        "state": "warming_up",
        "chewing_prob": 0.0,
        "cpm": 0.0,
        "rate_label": "-",
        "count": 0,
        "channel": "-",
        "side": "-",
        "side_prob": 0.0,
        "stop": False,
    }

    if use_model:
        print(f"Loaded model: {args.model}")
    else:
        print("Detector: signal waveform rule. Model is not used.")
    if use_gate:
        print(f"Loaded non-chewing gate: {args.gate_model}")
    else:
        print(f"Non-chewing gate is off. Train or pass a gate model at: {args.gate_model}")
    if use_side:
        print(f"Loaded chewing side model: {args.side_model}")
    else:
        print(f"Chewing side model is off. Train or pass a side model at: {args.side_model}")
    print(
        f"Reading {args.port}. Detector={args.detector}, window={window_seconds:.1f}s, "
        f"sample_rate={fs}Hz, update={args.update_seconds:.1f}s"
    )
    print("Close the waveform window or press Stop to quit.")

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    plt.subplots_adjust(bottom=0.15, hspace=0.35)
    ax_acc, ax_gyro, ax_event = axes
    ax_cpm = ax_event.twinx()

    acc_lines = [ax_acc.plot([], [], label=name)[0] for name in ["ax", "ay", "az"]]
    gyro_lines = [ax_gyro.plot([], [], label=name)[0] for name in ["gx", "gy", "gz"]]
    event_line, = ax_event.step([], [], where="post", label="event", color="#2d6cdf", linewidth=2)
    cpm_line, = ax_cpm.plot([], [], label="CPM", color="#d14f32", linewidth=2)

    ax_acc.set_title("MPU6050 accelerometer waveform")
    ax_acc.set_ylabel("g")
    ax_gyro.set_title("MPU6050 gyroscope waveform")
    ax_gyro.set_ylabel("rad/s")
    ax_event.set_title("Detected chewing event and chewing rate")
    ax_event.set_ylabel("event")
    ax_event.set_xlabel("Time / s")
    ax_event.set_yticks([0, 1])
    ax_event.set_yticklabels(["non chewing", "chewing"])
    ax_event.set_ylim(-0.2, 1.2)
    ax_cpm.set_ylabel("CPM")
    ax_cpm.set_ylim(0, 140)

    for axis in axes:
        axis.grid(True)
    ax_acc.legend(loc="upper right")
    ax_gyro.legend(loc="upper right")
    ax_event.legend(loc="upper left")
    ax_cpm.legend(loc="upper right")

    status_text = fig.text(
        0.02,
        0.04,
        "State: warming_up | CPM: 0.0 | p(chewing): 0.00",
        fontsize=12,
        weight="bold",
    )
    button_axis = fig.add_axes([0.86, 0.03, 0.1, 0.05])
    stop_button = Button(button_axis, "Stop")

    def stop(_event=None) -> None:
        runtime["stop"] = True
        plt.close(fig)

    stop_button.on_clicked(stop)

    def infer_model(window_df: pd.DataFrame) -> float:
        if model is None:
            return 0.0
        row = extract_window_features(window_df, sample_rate_hz=fs)
        features = {col: row.get(col, 0.0) for col in feature_columns}
        x = pd.DataFrame([features])

        if hasattr(model, "predict_proba"):
            classes = list(model.classes_)
            probabilities = model.predict_proba(x)[0]
            prob_map = dict(zip(classes, probabilities))
            return float(prob_map.get("chewing", 0.0))

        raw_state = str(model.predict(x)[0])
        return 1.0 if raw_state == "chewing" else 0.0

    def infer_non_chewing_gate(window_df: pd.DataFrame) -> float:
        if gate_model is None:
            return 0.0

        row = extract_window_features(window_df, sample_rate_hz=fs)
        features = {col: row.get(col, 0.0) for col in gate_feature_columns}
        x = pd.DataFrame([features])

        if hasattr(gate_model, "predict_proba"):
            classes = list(gate_model.classes_)
            probabilities = gate_model.predict_proba(x)[0]
            prob_map = dict(zip(classes, probabilities))
            return float(prob_map.get("non_chewing", 0.0))

        raw_state = str(gate_model.predict(x)[0])
        return 1.0 if raw_state == "non_chewing" else 0.0

    def infer_chewing_side(window_df: pd.DataFrame) -> tuple[str, float]:
        if side_model is None:
            return "-", 0.0

        row = extract_window_features(window_df, sample_rate_hz=fs)
        features = {col: row.get(col, 0.0) for col in side_feature_columns}
        x = pd.DataFrame([features])

        if hasattr(side_model, "predict_proba"):
            classes = list(side_model.classes_)
            probabilities = side_model.predict_proba(x)[0]
            best_idx = int(np.argmax(probabilities))
            side = str(classes[best_idx])
            prob = float(probabilities[best_idx])
        else:
            side = str(side_model.predict(x)[0])
            prob = 1.0

        if prob < args.side_threshold:
            return "unknown", prob
        return side, prob

    def infer_signal(window_df: pd.DataFrame) -> tuple[float, float, str, int, str]:
        signals = {
            name: window_df[name].to_numpy(dtype=float)
            for name in SENSOR_COLUMNS + ["acc_mag", "gyro_mag"]
        }

        if args.counter_channel == "auto":
            candidates = ["gy", "gz", "gx", "gyro_mag", "acc_mag", "ay", "az", "ax"]
        else:
            candidates = [args.counter_channel]

        gyro_std = float(np.std(signals["gyro_mag"]))
        acc_std = float(np.std(signals["acc_mag"]))
        motion_ok = gyro_std >= args.signal_min_gyro_std or acc_std >= args.signal_min_acc_std

        best = {
            "score": -1.0,
            "is_chewing": False,
            "cpm": 0.0,
            "channel": "-",
            "count": 0,
            "rate": "-",
        }

        for channel_name in candidates:
            signal = signals[channel_name]
            count = count_chews(signal, fs=fs, min_peak_distance_s=args.counter_min_peak_distance)
            cpm = count.chewing_rate_per_min
            channel_std = float(np.std(signal))
            channel_ok = channel_std >= args.signal_min_channel_std
            peaks_ok = count.chewing_count >= args.signal_min_peaks
            cpm_ok = args.signal_min_cpm <= cpm <= args.signal_max_cpm
            regularity_ok = count.regularity <= args.signal_max_regularity
            is_chewing = channel_ok and motion_ok and peaks_ok and cpm_ok and regularity_ok

            score = 0.0
            score += 0.25 if channel_ok else 0.0
            score += 0.25 if motion_ok else 0.0
            score += 0.25 if peaks_ok else 0.0
            score += 0.15 if cpm_ok else 0.0
            score += 0.10 if regularity_ok else 0.0
            if is_chewing:
                score += 1.0

            if score > float(best["score"]):
                best = {
                    "score": score,
                    "is_chewing": is_chewing,
                    "cpm": cpm,
                    "channel": channel_name,
                    "count": count.chewing_count,
                    "rate": count.rate_label,
                }

        is_chewing = bool(best["is_chewing"])
        non_chewing_cap = max(0.0, args.prob_threshold - 0.05)
        signal_prob = 1.0 if is_chewing else min(float(best["score"]), non_chewing_cap)
        return (
            signal_prob,
            float(best["cpm"]),
            str(best["channel"]),
            int(best["count"]),
            str(best["rate"]),
        )

    def infer_once() -> None:
        window_df = build_window_dataframe(inference_buffer)

        signal_prob, signal_cpm, signal_channel, signal_count, signal_rate = infer_signal(window_df)
        model_prob = infer_model(window_df) if use_model else 0.0
        gate_prob = infer_non_chewing_gate(window_df) if use_gate else 0.0

        if args.detector == "model":
            raw_chewing_prob = model_prob
        elif args.detector == "hybrid":
            raw_chewing_prob = max(signal_prob, model_prob)
        else:
            raw_chewing_prob = signal_prob

        gate_veto = raw_chewing_prob >= args.prob_threshold and gate_prob >= args.gate_threshold
        if gate_veto:
            raw_chewing_prob = max(0.0, args.prob_threshold - 0.05)
            chewing_vote_history.clear()
            chewing_prob_history.clear()

        chewing_vote_history.append(raw_chewing_prob >= args.prob_threshold)
        chewing_prob_history.append(raw_chewing_prob)
        required_votes = min(args.min_chewing_votes, len(chewing_vote_history))
        chewing_votes = sum(1 for vote in chewing_vote_history if vote)
        chewing_prob = sum(chewing_prob_history) / len(chewing_prob_history)
        state = "chewing" if chewing_votes >= required_votes else "non_chewing"

        cpm = signal_cpm if state == "chewing" else 0.0
        rate_label = signal_rate if state == "chewing" else "-"
        chew_count = signal_count if state == "chewing" else 0
        channel_name = signal_channel if state == "chewing" else "-"
        side, side_prob = infer_chewing_side(window_df) if state == "chewing" and use_side else ("-", 0.0)

        runtime.update(
            state=state,
            chewing_prob=chewing_prob,
            cpm=cpm,
            rate_label=rate_label,
            count=chew_count,
            channel=channel_name,
            side=side,
            side_prob=side_prob,
        )

        event_value = 1 if state == "chewing" else 0
        current_t = t_data[-1] if t_data else 0.0
        event_times.append(current_t)
        event_states.append(event_value)
        cpm_values.append(cpm)

        print(
            f"{state:12s} p={chewing_prob:.2f} signal={signal_prob:.2f} "
            f"model={model_prob:.2f} gate_non={gate_prob:.2f} veto={int(gate_veto)} | "
            f"CPM={cpm:5.1f} count={chew_count:2d} channel={channel_name} "
            f"side={side}({side_prob:.2f}) {rate_label}"
        )

    def update(_frame):
        if runtime["stop"]:
            return acc_lines + gyro_lines + [event_line, cpm_line]

        for _ in range(80):
            if ser.in_waiting <= 0:
                break

            sample = parse_imu_csv_line(ser.readline().decode("utf-8", errors="ignore"))
            if sample is None:
                continue

            if runtime["start_time_ms"] is None:
                runtime["start_time_ms"] = sample[0]

            start_time_ms = float(runtime["start_time_ms"])
            t_s = (sample[0] - start_time_ms) / 1000.0
            t_data.append(t_s)
            inference_buffer.append(sample)
            for name, value in zip(SENSOR_COLUMNS, sample[1:]):
                channels[name].append(value)

            now = time.monotonic()
            if len(inference_buffer) >= window_samples and now - float(runtime["last_update"]) >= args.update_seconds:
                runtime["last_update"] = now
                if t_s >= args.ignore_first_seconds:
                    infer_once()

            append_csv_row(
                csv_writer,
                sample,
                str(runtime["state"]),
                float(runtime["chewing_prob"]),
                float(runtime["cpm"]),
                str(runtime["side"]),
                float(runtime["side_prob"]),
                args.activity_label,
            )

        if not t_data:
            return acc_lines + gyro_lines + [event_line, cpm_line]

        for line, name in zip(acc_lines, ["ax", "ay", "az"]):
            line.set_data(t_data, channels[name])
        for line, name in zip(gyro_lines, ["gx", "gy", "gz"]):
            line.set_data(t_data, channels[name])

        event_line.set_data(event_times, event_states)
        cpm_line.set_data(event_times, cpm_values)

        update_axis_limits(ax_acc, t_data, [channels[name] for name in ["ax", "ay", "az"]], 0.1)
        update_axis_limits(ax_gyro, t_data, [channels[name] for name in ["gx", "gy", "gz"]], 0.05)
        ax_event.set_xlim(t_data[0], max(t_data[-1], t_data[0] + 1.0))
        if cpm_values:
            ax_cpm.set_ylim(0, max(140, max(cpm_values) * 1.25))

        status_text.set_text(
            f"State: {runtime['state']} | CPM: {float(runtime['cpm']):.1f} | "
            f"p(chewing): {float(runtime['chewing_prob']):.2f} | "
            f"side: {runtime['side']} ({float(runtime['side_prob']):.2f}) | "
            f"count/window: {runtime['count']} | channel: {runtime['channel']} | {runtime['rate_label']}"
        )
        return acc_lines + gyro_lines + [event_line, cpm_line]

    ani = FuncAnimation(fig, update, interval=30, blit=False, cache_frame_data=False)
    try:
        plt.show()
    finally:
        runtime["stop"] = True
        if ser.is_open:
            ser.close()
        if csv_file is not None:
            csv_file.close()
        print("Serial closed.")


if __name__ == "__main__":
    main()
