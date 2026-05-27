import argparse
import time
from collections import deque

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button, Slider
import numpy as np

from chewing_counter import choose_counter_signal, count_chews
from config import RAW_COLUMNS, SAMPLE_RATE_HZ, SENSOR_COLUMNS
from preprocess import add_magnitude_columns
from serial_utils import open_serial_port, parse_imu_csv_line


def update_axis_limits(axis, x_data, values, min_margin: float) -> None:
    if not x_data:
        return
    axis.set_xlim(x_data[0], max(x_data[-1], x_data[0] + 1.0))
    flat = [v for series in values for v in series]
    if not flat:
        return
    lo = min(flat)
    hi = max(flat)
    margin = max(min_margin, (hi - lo) * 0.2)
    axis.set_ylim(lo - margin, hi + margin)


def build_window_dict(buffer):
    import pandas as pd

    df = pd.DataFrame(list(buffer), columns=RAW_COLUMNS)
    df = add_magnitude_columns(df)
    return {name: df[name].to_numpy(dtype=float) for name in SENSOR_COLUMNS + ["acc_mag", "gyro_mag"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Real-time chewing detection without ML model.")
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--sample-rate", type=int, default=SAMPLE_RATE_HZ)
    parser.add_argument("--window-seconds", type=float, default=4.0)
    parser.add_argument("--update-seconds", type=float, default=0.5)
    parser.add_argument("--plot-window-seconds", type=float, default=12.0)
    parser.add_argument("--counter-channel", default="auto")
    parser.add_argument("--min-peaks", type=int, default=3)
    parser.add_argument("--min-cpm", type=float, default=35.0)
    parser.add_argument("--max-cpm", type=float, default=140.0)
    parser.add_argument("--min-signal-std", type=float, default=0.04)
    parser.add_argument("--max-regularity", type=float, default=0.45)
    args = parser.parse_args()

    fs = args.sample_rate
    window_samples = int(round(args.window_seconds * fs))
    plot_samples = max(window_samples, int(round(args.plot_window_seconds * fs)))

    ser = open_serial_port(args.port, args.baud)
    inference_buffer = deque(maxlen=window_samples)
    t_data = deque(maxlen=plot_samples)
    channels = {name: deque(maxlen=plot_samples) for name in SENSOR_COLUMNS}
    event_times = deque(maxlen=plot_samples)
    event_states = deque(maxlen=plot_samples)
    cpm_values = deque(maxlen=plot_samples)
    filtered_times = deque(maxlen=window_samples)
    filtered_values = deque(maxlen=window_samples)

    runtime = {
        "start_time_ms": None,
        "last_update": 0.0,
        "state": "warming_up",
        "cpm": 0.0,
        "count": 0,
        "channel": "-",
        "regularity": 0.0,
        "signal_std": 0.0,
        "stop": False,
    }

    fig, axes = plt.subplots(4, 1, figsize=(12, 9), sharex=False)
    plt.subplots_adjust(bottom=0.23, hspace=0.45)
    ax_acc, ax_gyro, ax_filtered, ax_event = axes
    ax_cpm = ax_event.twinx()

    acc_lines = [ax_acc.plot([], [], label=name)[0] for name in ["ax", "ay", "az"]]
    gyro_lines = [ax_gyro.plot([], [], label=name)[0] for name in ["gx", "gy", "gz"]]
    filtered_line, = ax_filtered.plot([], [], label="bandpassed signal", color="#444444")
    event_line, = ax_event.step([], [], where="post", label="event", color="#2d6cdf", linewidth=2)
    cpm_line, = ax_cpm.plot([], [], label="CPM", color="#d14f32", linewidth=2)

    ax_acc.set_title("Accelerometer")
    ax_acc.set_ylabel("g")
    ax_gyro.set_title("Gyroscope")
    ax_gyro.set_ylabel("rad/s")
    ax_filtered.set_title("Counter signal after 0.5-3 Hz bandpass")
    ax_filtered.set_ylabel("filtered")
    ax_event.set_title("Signal-rule chewing detection")
    ax_event.set_yticks([0, 1])
    ax_event.set_yticklabels(["non chewing", "chewing"])
    ax_event.set_ylim(-0.2, 1.2)
    ax_event.set_xlabel("Time / s")
    ax_cpm.set_ylabel("CPM")
    ax_cpm.set_ylim(0, 150)

    for axis in axes:
        axis.grid(True)
    ax_acc.legend(loc="upper right")
    ax_gyro.legend(loc="upper right")
    ax_filtered.legend(loc="upper right")
    ax_event.legend(loc="upper left")
    ax_cpm.legend(loc="upper right")

    status_text = fig.text(0.02, 0.055, "State: warming_up | CPM: 0.0", fontsize=12, weight="bold")
    stop_axis = fig.add_axes([0.86, 0.03, 0.1, 0.05])
    stop_button = Button(stop_axis, "Stop")

    std_axis = fig.add_axes([0.18, 0.12, 0.28, 0.03])
    reg_axis = fig.add_axes([0.60, 0.12, 0.28, 0.03])
    std_slider = Slider(std_axis, "min std", 0.005, 0.20, valinit=args.min_signal_std)
    reg_slider = Slider(reg_axis, "max reg", 0.10, 1.00, valinit=args.max_regularity)

    def stop(_event=None) -> None:
        runtime["stop"] = True
        plt.close(fig)

    stop_button.on_clicked(stop)

    def infer_once() -> None:
        signals = build_window_dict(inference_buffer)
        if args.counter_channel == "auto":
            channel_name, signal = choose_counter_signal(signals)
        else:
            channel_name = args.counter_channel
            signal = signals[channel_name]

        count = count_chews(signal, fs=fs)
        cpm = count.chewing_rate_per_min
        is_chewing = (
            count.chewing_count >= args.min_peaks
            and args.min_cpm <= cpm <= args.max_cpm
            and count.signal_std >= std_slider.val
            and count.regularity <= reg_slider.val
        )

        runtime.update(
            state="chewing" if is_chewing else "non_chewing",
            cpm=cpm if is_chewing else 0.0,
            count=count.chewing_count,
            channel=channel_name,
            regularity=count.regularity,
            signal_std=count.signal_std,
        )

        current_t = t_data[-1] if t_data else 0.0
        event_times.append(current_t)
        event_states.append(1 if is_chewing else 0)
        cpm_values.append(runtime["cpm"])

        filtered_times.clear()
        filtered_values.clear()
        if count.filtered_signal is not None and len(t_data) > 0:
            start_t = current_t - len(count.filtered_signal) / fs
            for idx, value in enumerate(count.filtered_signal):
                filtered_times.append(start_t + idx / fs)
                filtered_values.append(float(value))

        print(
            f"{runtime['state']:12s} CPM={runtime['cpm']:.1f} "
            f"peaks={runtime['count']} std={runtime['signal_std']:.3f} "
            f"reg={runtime['regularity']:.2f} channel={runtime['channel']}"
        )

    def update(_frame):
        if runtime["stop"]:
            return acc_lines + gyro_lines + [filtered_line, event_line, cpm_line]

        for _ in range(80):
            if ser.in_waiting <= 0:
                break
            sample = parse_imu_csv_line(ser.readline().decode("utf-8", errors="ignore"))
            if sample is None:
                continue
            if runtime["start_time_ms"] is None:
                runtime["start_time_ms"] = sample[0]
            t_s = (sample[0] - runtime["start_time_ms"]) / 1000.0
            t_data.append(t_s)
            inference_buffer.append(sample)
            for name, value in zip(SENSOR_COLUMNS, sample[1:]):
                channels[name].append(value)

            now = time.monotonic()
            if len(inference_buffer) >= window_samples and now - runtime["last_update"] >= args.update_seconds:
                runtime["last_update"] = now
                infer_once()

        if not t_data:
            return acc_lines + gyro_lines + [filtered_line, event_line, cpm_line]

        for line, name in zip(acc_lines, ["ax", "ay", "az"]):
            line.set_data(t_data, channels[name])
        for line, name in zip(gyro_lines, ["gx", "gy", "gz"]):
            line.set_data(t_data, channels[name])
        filtered_line.set_data(filtered_times, filtered_values)
        event_line.set_data(event_times, event_states)
        cpm_line.set_data(event_times, cpm_values)

        update_axis_limits(ax_acc, t_data, [channels[name] for name in ["ax", "ay", "az"]], 0.1)
        update_axis_limits(ax_gyro, t_data, [channels[name] for name in ["gx", "gy", "gz"]], 0.05)
        if filtered_times:
            update_axis_limits(ax_filtered, filtered_times, [filtered_values], 0.02)
        ax_event.set_xlim(t_data[0], max(t_data[-1], t_data[0] + 1.0))
        if cpm_values:
            ax_cpm.set_ylim(0, max(150, max(cpm_values) * 1.25))

        status_text.set_text(
            f"State: {runtime['state']} | CPM: {runtime['cpm']:.1f} | "
            f"peaks: {runtime['count']} | std: {runtime['signal_std']:.3f} | "
            f"regularity: {runtime['regularity']:.2f} | channel: {runtime['channel']}"
        )
        return acc_lines + gyro_lines + [filtered_line, event_line, cpm_line]

    ani = FuncAnimation(fig, update, interval=30, blit=False, cache_frame_data=False)
    try:
        plt.show()
    finally:
        runtime["stop"] = True
        if ser.is_open:
            ser.close()
        print("Serial closed.")


if __name__ == "__main__":
    main()

