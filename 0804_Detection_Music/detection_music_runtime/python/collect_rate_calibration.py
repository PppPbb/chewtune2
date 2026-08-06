import argparse
import csv
import time
from collections import deque
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button
import numpy as np

from detector import (
    RAW_COLUMNS,
    Sample,
    open_serial_port,
    parse_dual_imu_csv_line,
    update_axis_limits,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect dual MPU6050 data with Space-key chew marks for CPM calibration.")
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--plot-window-seconds", type=float, default=10.0)
    parser.add_argument("--sample-rate", type=int, default=100)
    parser.add_argument("--raw-out", type=Path, default=Path("data") / "rate_calibration" / "rate_calibration_01.csv")
    parser.add_argument("--events-out", type=Path, default=Path("data") / "rate_calibration" / "rate_calibration_01_events.csv")
    parser.add_argument("--activity-label", default="chewing")
    args = parser.parse_args()

    plot_samples = int(round(args.plot_window_seconds * args.sample_rate))
    ser = open_serial_port(args.port, args.baud)

    args.raw_out.parent.mkdir(parents=True, exist_ok=True)
    args.events_out.parent.mkdir(parents=True, exist_ok=True)
    raw_file = args.raw_out.open("w", newline="", encoding="utf-8")
    events_file = args.events_out.open("w", newline="", encoding="utf-8")
    raw_writer = csv.writer(raw_file)
    events_writer = csv.writer(events_file)
    raw_writer.writerow(RAW_COLUMNS + ["activity_label"])
    events_writer.writerow(["event_index", "elapsed_s", "time_ms", "event", "activity_label"])

    t_data: deque[float] = deque(maxlen=plot_samples)
    channels = {name: deque(maxlen=plot_samples) for name in RAW_COLUMNS[1:]}
    event_times: deque[float] = deque(maxlen=plot_samples)
    event_levels: deque[float] = deque(maxlen=plot_samples)

    runtime = {
        "start_time_ms": None,
        "last_time_ms": None,
        "last_elapsed_s": 0.0,
        "event_count": 0,
        "raw_count": 0,
        "stop": False,
    }

    print("Press Space once per chew. Close the window or press Stop to finish.")
    print(f"Raw data: {args.raw_out}")
    print(f"Events:   {args.events_out}")

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    plt.subplots_adjust(bottom=0.15, hspace=0.35)
    ax_acc, ax_gyro, ax_event = axes

    left_acc_line, = ax_acc.plot([], [], label="left acc_mag", color="#2d6cdf")
    right_acc_line, = ax_acc.plot([], [], label="right acc_mag", color="#d14f32")
    left_gyro_line, = ax_gyro.plot([], [], label="left gyro_mag", color="#2d6cdf")
    right_gyro_line, = ax_gyro.plot([], [], label="right gyro_mag", color="#d14f32")
    event_line, = ax_event.plot([], [], linestyle="none", marker="o", color="#222222", label="Space chew")

    ax_acc.set_title("Dual MPU6050 accelerometer magnitude")
    ax_acc.set_ylabel("g")
    ax_gyro.set_title("Dual MPU6050 gyroscope magnitude")
    ax_gyro.set_ylabel("rad/s")
    ax_event.set_title("Manual chew marks")
    ax_event.set_ylabel("mark")
    ax_event.set_xlabel("Time / s")
    ax_event.set_ylim(-0.2, 1.2)

    for axis in axes:
        axis.grid(True)
        axis.legend(loc="upper right")

    status_text = fig.text(0.02, 0.04, "Marks: 0", fontsize=12, weight="bold")
    button_axis = fig.add_axes([0.86, 0.03, 0.1, 0.05])
    stop_button = Button(button_axis, "Stop")

    def stop(_event=None) -> None:
        runtime["stop"] = True
        plt.close(fig)

    stop_button.on_clicked(stop)

    def on_key_press(event) -> None:
        if event.key not in {" ", "space"}:
            return
        if runtime["last_time_ms"] is None:
            print("No sample yet; Space ignored.")
            return

        runtime["event_count"] = int(runtime["event_count"]) + 1
        elapsed_s = float(runtime["last_elapsed_s"])
        time_ms = float(runtime["last_time_ms"])
        events_writer.writerow([runtime["event_count"], f"{elapsed_s:.6f}", f"{time_ms:.3f}", "chew", args.activity_label])
        events_file.flush()
        event_times.append(elapsed_s)
        event_levels.append(1.0)
        print(f"Chew mark {runtime['event_count']}: t={elapsed_s:.3f}s")

    fig.canvas.mpl_connect("key_press_event", on_key_press)

    def update(_frame):
        if runtime["stop"]:
            return [left_acc_line, right_acc_line, left_gyro_line, right_gyro_line, event_line]

        for _ in range(80):
            if ser.in_waiting <= 0:
                break
            sample = parse_dual_imu_csv_line(ser.readline().decode("utf-8", errors="ignore"))
            if sample is None:
                continue

            if runtime["start_time_ms"] is None:
                runtime["start_time_ms"] = sample[0]

            elapsed_s = (sample[0] - float(runtime["start_time_ms"])) / 1000.0
            runtime["last_time_ms"] = sample[0]
            runtime["last_elapsed_s"] = elapsed_s
            t_data.append(elapsed_s)
            for name, value in zip(RAW_COLUMNS[1:], sample[1:]):
                channels[name].append(value)
            raw_writer.writerow([*sample, args.activity_label])
            runtime["raw_count"] = int(runtime["raw_count"]) + 1

        raw_file.flush()

        if not t_data:
            return [left_acc_line, right_acc_line, left_gyro_line, right_gyro_line, event_line]

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
        event_line.set_data(event_times, event_levels)
        update_axis_limits(ax_acc, t_data, [l_acc, r_acc], 0.05)
        update_axis_limits(ax_gyro, t_data, [l_gyro, r_gyro], 0.03)
        ax_event.set_xlim(t_data[0], max(t_data[-1], t_data[0] + 1.0))
        status_text.set_text(
            f"Marks: {runtime['event_count']} | raw samples: {runtime['raw_count']} | "
            f"t={float(runtime['last_elapsed_s']):.1f}s"
        )
        return [left_acc_line, right_acc_line, left_gyro_line, right_gyro_line, event_line]

    ani = FuncAnimation(fig, update, interval=30, blit=False, cache_frame_data=False)
    try:
        plt.show()
    finally:
        runtime["stop"] = True
        if ser.is_open:
            ser.close()
        raw_file.close()
        events_file.close()
        print("Serial closed.")
        print(f"Saved {runtime['raw_count']} raw samples.")
        print(f"Saved {runtime['event_count']} chew marks.")
        if int(runtime["raw_count"]) == 0:
            print("WARNING: No raw IMU samples were saved. Check Arduino output and serial port.")


if __name__ == "__main__":
    main()
