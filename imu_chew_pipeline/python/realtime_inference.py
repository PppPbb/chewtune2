import argparse
import time
from collections import deque
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button
import pandas as pd

from chewing_counter import choose_counter_signal, classify_rate, count_chews
from config import DEFAULT_MODEL_PATH, RAW_COLUMNS, SAMPLE_RATE_HZ, SENSOR_COLUMNS
from feature_extraction import extract_window_features
from preprocess import add_magnitude_columns
from serial_utils import open_serial_port, parse_imu_csv_line


def build_window_dataframe(buffer) -> pd.DataFrame:
    df = pd.DataFrame(list(buffer), columns=RAW_COLUMNS)
    df["label"] = "unknown"
    df["source_file"] = "realtime"
    return add_magnitude_columns(df)


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
    parser = argparse.ArgumentParser(description="Real-time chewing/non-chewing inference.")
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--update-seconds", type=float, default=1.0)
    parser.add_argument("--prob-threshold", type=float, default=0.6)
    parser.add_argument("--smooth-windows", type=int, default=3, help="Number of recent inference windows used for voting.")
    parser.add_argument("--min-chewing-votes", type=int, default=2, help="Votes needed to enter chewing state.")
    parser.add_argument("--plot-window-seconds", type=float, default=10.0)
    parser.add_argument("--manual-min-interval", type=float, default=0.25, help="Shortest valid seconds between Space presses.")
    parser.add_argument("--manual-max-interval", type=float, default=2.0, help="Longest valid seconds between Space presses.")
    parser.add_argument("--counter-channel", default="auto", help="auto, gyro_mag, gz, gy, gx, acc_mag, ax, ay, az")
    args = parser.parse_args()

    bundle = joblib.load(args.model)
    model = bundle["model"]
    feature_columns = bundle["feature_columns"]
    fs = int(bundle.get("sample_rate_hz", SAMPLE_RATE_HZ))
    window_seconds = float(bundle.get("window_seconds", 2.0))
    window_samples = int(round(window_seconds * fs))

    ser = open_serial_port(args.port, args.baud)
    inference_buffer = deque(maxlen=window_samples)
    plot_samples = max(window_samples, int(round(args.plot_window_seconds * fs)))
    plot_buffer = deque(maxlen=plot_samples)
    event_times = deque(maxlen=plot_samples)
    event_states = deque(maxlen=plot_samples)
    cpm_values = deque(maxlen=plot_samples)
    manual_press_times = deque(maxlen=plot_samples)
    manual_press_levels = deque(maxlen=plot_samples)
    manual_valid_intervals = deque(maxlen=3)
    chewing_vote_history = deque(maxlen=max(1, args.smooth_windows))
    chewing_prob_history = deque(maxlen=max(1, args.smooth_windows))

    runtime = {
        "start_time_ms": None,
        "last_update": 0.0,
        "last_manual_press_s": None,
        "state": "warming_up",
        "chewing_prob": 0.0,
        "cpm": 0.0,
        "rate_label": "-",
        "count": 0,
        "channel": "-",
        "cpm_source": "peak",
        "stop": False,
    }

    print(f"Loaded model: {args.model}")
    print("CPM uses peak detection, or Space-key average of the latest 3 valid intervals.")
    print(f"Reading {args.port}. Window={window_seconds:.1f}s, update={args.update_seconds:.1f}s")

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    plt.subplots_adjust(bottom=0.15, hspace=0.35)
    ax_acc, ax_gyro, ax_event = axes
    ax_cpm = ax_event.twinx()

    t_data = deque(maxlen=plot_samples)
    channels = {name: deque(maxlen=plot_samples) for name in SENSOR_COLUMNS}

    acc_lines = [ax_acc.plot([], [], label=name)[0] for name in ["ax", "ay", "az"]]
    gyro_lines = [ax_gyro.plot([], [], label=name)[0] for name in ["gx", "gy", "gz"]]
    event_line, = ax_event.step([], [], where="post", label="event", color="#2d6cdf", linewidth=2)
    cpm_line, = ax_cpm.plot([], [], label="CPM", color="#d14f32", linewidth=2)
    manual_line, = ax_event.plot([], [], linestyle="none", marker="o", color="#1b9e77", label="Space chew", markersize=5)

    ax_acc.set_title("Accelerometer")
    ax_acc.set_ylabel("g")
    ax_gyro.set_title("Gyroscope")
    ax_gyro.set_ylabel("rad/s")
    ax_event.set_title("Detected event and chewing rate")
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

    def on_key_press(event) -> None:
        if event.key not in {" ", "space"}:
            return
        if not t_data:
            return

        current_t = float(t_data[-1])
        manual_press_times.append(current_t)
        manual_press_levels.append(1.08)

        last_t = runtime["last_manual_press_s"]
        if last_t is None:
            runtime["last_manual_press_s"] = current_t
            print("Space chew mark: first press")
            return

        interval_s = current_t - float(last_t)
        if args.manual_min_interval <= interval_s <= args.manual_max_interval:
            manual_valid_intervals.append(interval_s)
            runtime["last_manual_press_s"] = current_t
            print(f"Space chew mark: interval={interval_s:.3f}s, valid_intervals={len(manual_valid_intervals)}/3")
        elif interval_s < args.manual_min_interval:
            print(f"Space chew mark ignored: interval too short ({interval_s:.3f}s)")
        else:
            manual_valid_intervals.clear()
            runtime["last_manual_press_s"] = current_t
            print(f"Space chew mark reset: interval too long ({interval_s:.3f}s)")

    fig.canvas.mpl_connect("key_press_event", on_key_press)

    def infer_once() -> None:
        window_df = build_window_dataframe(inference_buffer)
        row = extract_window_features(window_df, sample_rate_hz=fs)
        X = pd.DataFrame([{col: row.get(col, 0.0) for col in feature_columns}])

        if hasattr(model, "predict_proba"):
            classes = list(model.classes_)
            probabilities = model.predict_proba(X)[0]
            prob_map = dict(zip(classes, probabilities))
            raw_chewing_prob = float(prob_map.get("chewing", 0.0))
        else:
            raw_state = str(model.predict(X)[0])
            raw_chewing_prob = 1.0 if raw_state == "chewing" else 0.0

        chewing_vote_history.append(raw_chewing_prob >= args.prob_threshold)
        chewing_prob_history.append(raw_chewing_prob)
        chewing_votes = sum(1 for vote in chewing_vote_history if vote)
        required_votes = min(args.min_chewing_votes, len(chewing_vote_history))
        chewing_prob = sum(chewing_prob_history) / len(chewing_prob_history)
        state = "chewing" if chewing_votes >= required_votes else "non_chewing"

        cpm = 0.0
        rate_label = "-"
        chew_count = 0
        channel_name = "-"

        if state == "chewing":
            signals = {name: window_df[name].to_numpy(dtype=float) for name in SENSOR_COLUMNS + ["acc_mag", "gyro_mag"]}
            if args.counter_channel == "auto":
                channel_name, signal = choose_counter_signal(signals)
            else:
                channel_name = args.counter_channel
                signal = signals[channel_name]
            count = count_chews(signal, fs=fs)
            cpm = count.chewing_rate_per_min
            rate_label = count.rate_label
            chew_count = count.chewing_count

            current_t = t_data[-1] if t_data else 0.0
            last_manual_t = runtime["last_manual_press_s"]
            manual_is_recent = (
                last_manual_t is not None
                and current_t - float(last_manual_t) <= args.manual_max_interval
            )
            if len(manual_valid_intervals) >= 3 and manual_is_recent:
                avg_interval_s = sum(manual_valid_intervals) / len(manual_valid_intervals)
                cpm = 60.0 / avg_interval_s
                rate_label = classify_rate(cpm)
                channel_name = "space_avg3"

        runtime.update(
            state=state,
            chewing_prob=chewing_prob,
            cpm=cpm,
            rate_label=rate_label,
            count=chew_count,
            channel=channel_name,
            cpm_source="space_avg3" if channel_name == "space_avg3" else "peak",
        )

        event_value = 1 if state == "chewing" else 0
        current_t = t_data[-1] if t_data else 0.0
        event_times.append(current_t)
        event_states.append(event_value)
        cpm_values.append(cpm)

        print(
            f"{state:12s} p={chewing_prob:.2f} | "
            f"CPM={cpm:.1f} count={chew_count:2d} source={runtime['cpm_source']} channel={channel_name} {rate_label}"
        )

    def update(_frame):
        if runtime["stop"]:
            return acc_lines + gyro_lines + [event_line, cpm_line, manual_line]

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
            plot_buffer.append(sample)
            inference_buffer.append(sample)
            for name, value in zip(SENSOR_COLUMNS, sample[1:]):
                channels[name].append(value)

            now = time.monotonic()
            if len(inference_buffer) >= window_samples and now - runtime["last_update"] >= args.update_seconds:
                runtime["last_update"] = now
                infer_once()

        if not t_data:
            return acc_lines + gyro_lines + [event_line, cpm_line, manual_line]

        for line, name in zip(acc_lines, ["ax", "ay", "az"]):
            line.set_data(t_data, channels[name])
        for line, name in zip(gyro_lines, ["gx", "gy", "gz"]):
            line.set_data(t_data, channels[name])

        event_line.set_data(event_times, event_states)
        cpm_line.set_data(event_times, cpm_values)
        manual_line.set_data(manual_press_times, manual_press_levels)

        update_axis_limits(ax_acc, t_data, [channels[name] for name in ["ax", "ay", "az"]], 0.1)
        update_axis_limits(ax_gyro, t_data, [channels[name] for name in ["gx", "gy", "gz"]], 0.05)
        ax_event.set_xlim(t_data[0], max(t_data[-1], t_data[0] + 1.0))
        if cpm_values:
            ax_cpm.set_ylim(0, max(140, max(cpm_values) * 1.25))

        status_text.set_text(
            f"State: {runtime['state']} | CPM: {runtime['cpm']:.1f} | "
            f"p(chewing): {runtime['chewing_prob']:.2f} | "
            f"source: {runtime['cpm_source']} | "
            f"count/window: {runtime['count']} | channel: {runtime['channel']} | {runtime['rate_label']}"
        )
        return acc_lines + gyro_lines + [event_line, cpm_line, manual_line]

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
