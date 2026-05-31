import argparse
import csv
import time
from collections import deque
from pathlib import Path
from typing import Optional, Tuple

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button
import numpy as np
import pandas as pd
import serial


RAW_COLUMNS = [
    "time_ms",
    "l_ax",
    "l_ay",
    "l_az",
    "l_gx",
    "l_gy",
    "l_gz",
    "r_ax",
    "r_ay",
    "r_az",
    "r_gx",
    "r_gy",
    "r_gz",
]
LEFT_SENSOR_COLUMNS = ["l_ax", "l_ay", "l_az", "l_gx", "l_gy", "l_gz"]
RIGHT_SENSOR_COLUMNS = ["r_ax", "r_ay", "r_az", "r_gx", "r_gy", "r_gz"]
Sample = Tuple[float, ...]


def parse_dual_imu_csv_line(line: str) -> Optional[Sample]:
    line = line.strip()
    if not line:
        return None

    ignored_prefixes = ("BOOT", "LEFT", "RIGHT", "Both", "ERROR", "Check", "time_ms")
    if line.startswith(ignored_prefixes):
        print(line)
        return None

    parts = line.split(",")
    if len(parts) != len(RAW_COLUMNS):
        return None

    try:
        return tuple(float(part) for part in parts)
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


def add_magnitudes(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["l_acc_mag"] = np.sqrt(out["l_ax"] ** 2 + out["l_ay"] ** 2 + out["l_az"] ** 2)
    out["l_gyro_mag"] = np.sqrt(out["l_gx"] ** 2 + out["l_gy"] ** 2 + out["l_gz"] ** 2)
    out["r_acc_mag"] = np.sqrt(out["r_ax"] ** 2 + out["r_ay"] ** 2 + out["r_az"] ** 2)
    out["r_gyro_mag"] = np.sqrt(out["r_gx"] ** 2 + out["r_gy"] ** 2 + out["r_gz"] ** 2)
    return out


def build_window_dataframe(buffer: deque[Sample]) -> pd.DataFrame:
    return add_magnitudes(pd.DataFrame(list(buffer), columns=RAW_COLUMNS))


def count_peaks_simple(signal: np.ndarray, fs: float, min_peak_distance_s: float) -> tuple[int, float, float]:
    x = np.asarray(signal, dtype=float)
    if x.size < 3:
        return 0, 0.0, 1.0

    x = x - np.mean(x)
    std = float(np.std(x))
    if std <= 1e-9:
        return 0, 0.0, 1.0

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

    if len(peaks) >= 2:
        intervals = np.diff(peaks) / fs
        cpm = 60.0 / float(np.mean(intervals))
        regularity = float(np.std(intervals) / np.mean(intervals)) if np.mean(intervals) > 0 else 1.0
    else:
        duration_s = len(x) / fs
        cpm = len(peaks) / duration_s * 60.0 if duration_s > 0 else 0.0
        regularity = 1.0
    return len(peaks), cpm, regularity


def side_score(df: pd.DataFrame, side: str, fs: float, min_peak_distance_s: float) -> dict:
    prefix = "l" if side == "left" else "r"
    candidates = [
        f"{prefix}_gy",
        f"{prefix}_gz",
        f"{prefix}_gx",
        f"{prefix}_gyro_mag",
        f"{prefix}_acc_mag",
    ]

    best = {
        "score": 0.0,
        "channel": "-",
        "peaks": 0,
        "cpm": 0.0,
        "std": 0.0,
        "regularity": 1.0,
    }

    for channel in candidates:
        signal = df[channel].to_numpy(dtype=float)
        peaks, cpm, regularity = count_peaks_simple(signal, fs, min_peak_distance_s)
        std = float(np.std(signal))
        cpm_ok = 30.0 <= cpm <= 180.0
        regularity_ok = regularity <= 1.25
        score = std * (1.0 + 0.35 * peaks)
        if cpm_ok:
            score *= 1.25
        if regularity_ok:
            score *= 1.15
        if score > best["score"]:
            best = {
                "score": score,
                "channel": channel,
                "peaks": peaks,
                "cpm": cpm,
                "std": std,
                "regularity": regularity,
            }
    return best


def classify_dual_window(df: pd.DataFrame, fs: float, args) -> dict:
    left = side_score(df, "left", fs, args.counter_min_peak_distance)
    right = side_score(df, "right", fs, args.counter_min_peak_distance)

    best = left if left["score"] >= right["score"] else right
    other = right if best is left else left
    side = "left_chewing" if best is left else "right_chewing"

    enough_motion = best["std"] >= args.min_channel_std
    enough_peaks = best["peaks"] >= args.min_peaks
    cpm_ok = args.min_cpm <= best["cpm"] <= args.max_cpm
    regularity_ok = best["regularity"] <= args.max_regularity
    dominance = best["score"] / max(other["score"], 1e-9)
    dominance_ok = dominance >= args.side_ratio

    state = "chewing" if enough_motion and enough_peaks and cpm_ok and regularity_ok else "non_chewing"
    if state != "chewing":
        side = "-"
    elif not dominance_ok:
        side = "both_or_unknown"

    return {
        "state": state,
        "side": side,
        "cpm": best["cpm"] if state == "chewing" else 0.0,
        "channel": best["channel"] if state == "chewing" else "-",
        "peaks": best["peaks"] if state == "chewing" else 0,
        "left_score": left["score"],
        "right_score": right["score"],
        "dominance": dominance,
    }


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
    parser = argparse.ArgumentParser(description="Real-time chewing side detection with two MPU6050 sensors.")
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--sample-rate", type=int, default=100)
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--update-seconds", type=float, default=0.5)
    parser.add_argument("--plot-window-seconds", type=float, default=10.0)
    parser.add_argument("--ignore-first-seconds", type=float, default=3.0)
    parser.add_argument("--counter-min-peak-distance", type=float, default=0.45)
    parser.add_argument("--min-peaks", type=int, default=1)
    parser.add_argument("--min-cpm", type=float, default=30.0)
    parser.add_argument("--max-cpm", type=float, default=180.0)
    parser.add_argument("--min-channel-std", type=float, default=0.015)
    parser.add_argument("--max-regularity", type=float, default=1.25)
    parser.add_argument("--side-ratio", type=float, default=1.12, help="Best side score must exceed the other side by this ratio.")
    parser.add_argument("--activity-label", default="")
    parser.add_argument("--save-csv", type=Path)
    args = parser.parse_args()

    fs = args.sample_rate
    window_samples = int(round(args.window_seconds * fs))
    plot_samples = max(window_samples, int(round(args.plot_window_seconds * fs)))

    ser = open_serial_port(args.port, args.baud)
    inference_buffer: deque[Sample] = deque(maxlen=window_samples)
    t_data: deque[float] = deque(maxlen=plot_samples)
    channels = {name: deque(maxlen=plot_samples) for name in RAW_COLUMNS[1:]}
    event_times: deque[float] = deque(maxlen=plot_samples)
    event_states: deque[int] = deque(maxlen=plot_samples)
    cpm_values: deque[float] = deque(maxlen=plot_samples)

    csv_file = None
    csv_writer = None
    if args.save_csv:
        args.save_csv.parent.mkdir(parents=True, exist_ok=True)
        csv_file = args.save_csv.open("w", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(RAW_COLUMNS + ["state", "chewing_side", "cpm", "channel", "activity_label"])

    runtime = {
        "start_time_ms": None,
        "last_update": 0.0,
        "state": "warming_up",
        "side": "-",
        "cpm": 0.0,
        "channel": "-",
        "peaks": 0,
        "left_score": 0.0,
        "right_score": 0.0,
        "stop": False,
    }

    print(f"Reading {args.port}. Window={args.window_seconds:.1f}s, update={args.update_seconds:.1f}s")
    print("Close the waveform window or press Stop to quit.")

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    plt.subplots_adjust(bottom=0.15, hspace=0.35)
    ax_acc, ax_gyro, ax_event = axes
    ax_cpm = ax_event.twinx()

    left_acc_line, = ax_acc.plot([], [], label="left acc_mag", color="#2d6cdf")
    right_acc_line, = ax_acc.plot([], [], label="right acc_mag", color="#d14f32")
    left_gyro_line, = ax_gyro.plot([], [], label="left gyro_mag", color="#2d6cdf")
    right_gyro_line, = ax_gyro.plot([], [], label="right gyro_mag", color="#d14f32")
    event_line, = ax_event.step([], [], where="post", label="event", color="#222222", linewidth=2)
    cpm_line, = ax_cpm.plot([], [], label="CPM", color="#1b9e77", linewidth=2)

    ax_acc.set_title("Dual MPU6050 accelerometer magnitude")
    ax_acc.set_ylabel("g")
    ax_gyro.set_title("Dual MPU6050 gyroscope magnitude")
    ax_gyro.set_ylabel("rad/s")
    ax_event.set_title("Chewing event and side")
    ax_event.set_ylabel("event")
    ax_event.set_xlabel("Time / s")
    ax_event.set_yticks([0, 1])
    ax_event.set_yticklabels(["non chewing", "chewing"])
    ax_event.set_ylim(-0.2, 1.2)
    ax_cpm.set_ylabel("CPM")
    ax_cpm.set_ylim(0, 180)

    for axis in axes:
        axis.grid(True)
        axis.legend(loc="upper right")
    ax_event.legend(loc="upper left")
    ax_cpm.legend(loc="upper right")

    status_text = fig.text(
        0.02,
        0.04,
        "State: warming_up | Side: - | CPM: 0.0",
        fontsize=12,
        weight="bold",
    )
    button_axis = fig.add_axes([0.86, 0.03, 0.1, 0.05])
    stop_button = Button(button_axis, "Stop")

    def stop(_event=None) -> None:
        runtime["stop"] = True
        plt.close(fig)

    stop_button.on_clicked(stop)

    def infer_once() -> None:
        df = build_window_dataframe(inference_buffer)
        result = classify_dual_window(df, fs, args)
        runtime.update(result)

        current_t = t_data[-1] if t_data else 0.0
        event_times.append(current_t)
        event_states.append(1 if runtime["state"] == "chewing" else 0)
        cpm_values.append(float(runtime["cpm"]))

        print(
            f"{runtime['state']:12s} side={runtime['side']:15s} "
            f"CPM={float(runtime['cpm']):5.1f} peaks={runtime['peaks']} "
            f"channel={runtime['channel']} L={float(runtime['left_score']):.4f} "
            f"R={float(runtime['right_score']):.4f}"
        )

    def update(_frame):
        if runtime["stop"]:
            return [left_acc_line, right_acc_line, left_gyro_line, right_gyro_line, event_line, cpm_line]

        for _ in range(80):
            if ser.in_waiting <= 0:
                break

            sample = parse_dual_imu_csv_line(ser.readline().decode("utf-8", errors="ignore"))
            if sample is None:
                continue

            if runtime["start_time_ms"] is None:
                runtime["start_time_ms"] = sample[0]

            start_time_ms = float(runtime["start_time_ms"])
            t_s = (sample[0] - start_time_ms) / 1000.0
            t_data.append(t_s)
            inference_buffer.append(sample)
            for name, value in zip(RAW_COLUMNS[1:], sample[1:]):
                channels[name].append(value)

            now = time.monotonic()
            if (
                len(inference_buffer) >= window_samples
                and now - float(runtime["last_update"]) >= args.update_seconds
            ):
                runtime["last_update"] = now
                if t_s >= args.ignore_first_seconds:
                    infer_once()

            if csv_writer is not None:
                csv_writer.writerow(
                    [
                        *sample,
                        runtime["state"],
                        runtime["side"],
                        f"{float(runtime['cpm']):.3f}",
                        runtime["channel"],
                        args.activity_label,
                    ]
                )

        if not t_data:
            return [left_acc_line, right_acc_line, left_gyro_line, right_gyro_line, event_line, cpm_line]

        l_acc = np.sqrt(
            np.array(channels["l_ax"]) ** 2 + np.array(channels["l_ay"]) ** 2 + np.array(channels["l_az"]) ** 2
        )
        r_acc = np.sqrt(
            np.array(channels["r_ax"]) ** 2 + np.array(channels["r_ay"]) ** 2 + np.array(channels["r_az"]) ** 2
        )
        l_gyro = np.sqrt(
            np.array(channels["l_gx"]) ** 2 + np.array(channels["l_gy"]) ** 2 + np.array(channels["l_gz"]) ** 2
        )
        r_gyro = np.sqrt(
            np.array(channels["r_gx"]) ** 2 + np.array(channels["r_gy"]) ** 2 + np.array(channels["r_gz"]) ** 2
        )

        left_acc_line.set_data(t_data, l_acc)
        right_acc_line.set_data(t_data, r_acc)
        left_gyro_line.set_data(t_data, l_gyro)
        right_gyro_line.set_data(t_data, r_gyro)
        event_line.set_data(event_times, event_states)
        cpm_line.set_data(event_times, cpm_values)

        update_axis_limits(ax_acc, t_data, [l_acc, r_acc], 0.05)
        update_axis_limits(ax_gyro, t_data, [l_gyro, r_gyro], 0.03)
        ax_event.set_xlim(t_data[0], max(t_data[-1], t_data[0] + 1.0))
        if cpm_values:
            ax_cpm.set_ylim(0, max(180, max(cpm_values) * 1.25))

        status_text.set_text(
            f"State: {runtime['state']} | Side: {runtime['side']} | "
            f"CPM: {float(runtime['cpm']):.1f} | channel: {runtime['channel']} | "
            f"L: {float(runtime['left_score']):.3f} R: {float(runtime['right_score']):.3f}"
        )
        return [left_acc_line, right_acc_line, left_gyro_line, right_gyro_line, event_line, cpm_line]

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
