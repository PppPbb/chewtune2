import argparse
import csv
import queue
import threading
from collections import deque
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

from serial_utils import open_serial_port, parse_imu_csv_line


def stdin_worker(commands: "queue.Queue[str]") -> None:
    while True:
        try:
            command = input().strip()
        except EOFError:
            return
        commands.put(command)
        if command.lower() in {"s", "stop", "quit", "exit"}:
            return


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect labeled IMU CSV data with live plotting.")
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--label", default="chewing")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plot-window", type=int, default=500, help="Samples shown in plot.")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    ser = open_serial_port(args.port, args.baud)

    command_queue: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=stdin_worker, args=(command_queue,), daemon=True).start()

    current_label = {"value": args.label}
    stop_flag = {"value": False}
    state = {"start_time": None, "rows": 0}

    t_data = deque(maxlen=args.plot_window)
    channels = {name: deque(maxlen=args.plot_window) for name in ["ax", "ay", "az", "gx", "gy", "gz"]}

    print(f"Saving to {args.output}")
    print("Commands: label chewing | label talking | label still | s")

    csv_file = args.output.open("w", newline="", encoding="utf-8")
    writer = csv.writer(csv_file)
    writer.writerow(["time_ms", "ax", "ay", "az", "gx", "gy", "gz", "label"])

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    acc_lines = [axes[0].plot([], [], label=name)[0] for name in ["ax", "ay", "az"]]
    gyro_lines = [axes[1].plot([], [], label=name)[0] for name in ["gx", "gy", "gz"]]

    axes[0].set_title("Accelerometer")
    axes[0].set_ylabel("g")
    axes[1].set_title("Gyroscope")
    axes[1].set_ylabel("rad/s")
    axes[1].set_xlabel("Time / s")
    for ax in axes:
        ax.legend(loc="upper right")
        ax.grid(True)

    def handle_commands() -> None:
        while not command_queue.empty():
            command = command_queue.get_nowait()
            lower = command.lower()
            if lower.startswith("label "):
                current_label["value"] = command.split(maxsplit=1)[1].strip()
                print(f"Label changed to: {current_label['value']}")
            elif lower in {"s", "stop", "quit", "exit"}:
                stop_flag["value"] = True
                print("Stopping collection. Close the plot window to finish saving.")

    def update(_frame):
        handle_commands()
        if stop_flag["value"]:
            return acc_lines + gyro_lines

        for _ in range(80):
            if ser.in_waiting <= 0:
                break
            raw_line = ser.readline().decode("utf-8", errors="ignore")
            sample = parse_imu_csv_line(raw_line)
            if sample is None:
                continue

            t_ms, ax, ay, az, gx, gy, gz = sample
            if state["start_time"] is None:
                state["start_time"] = t_ms
            t_s = (t_ms - state["start_time"]) / 1000.0

            writer.writerow([t_ms, ax, ay, az, gx, gy, gz, current_label["value"]])
            state["rows"] += 1

            t_data.append(t_s)
            for name, value in zip(["ax", "ay", "az", "gx", "gy", "gz"], [ax, ay, az, gx, gy, gz]):
                channels[name].append(value)

        if not t_data:
            return acc_lines + gyro_lines

        for line, name in zip(acc_lines, ["ax", "ay", "az"]):
            line.set_data(t_data, channels[name])
        for line, name in zip(gyro_lines, ["gx", "gy", "gz"]):
            line.set_data(t_data, channels[name])

        for axis, names, margin_min in [(axes[0], ["ax", "ay", "az"], 0.1), (axes[1], ["gx", "gy", "gz"], 0.05)]:
            values = [v for name in names for v in channels[name]]
            axis.set_xlim(t_data[0], max(t_data[-1], t_data[0] + 1.0))
            if values:
                lo, hi = min(values), max(values)
                margin = max(margin_min, (hi - lo) * 0.2)
                axis.set_ylim(lo - margin, hi + margin)

        fig.suptitle(f"Label: {current_label['value']} | Rows: {state['rows']}")
        return acc_lines + gyro_lines

    ani = FuncAnimation(fig, update, interval=30, blit=False, cache_frame_data=False)
    try:
        plt.tight_layout()
        plt.show()
    finally:
        csv_file.close()
        if ser.is_open:
            ser.close()
        print(f"Saved {state['rows']} rows to {args.output}")


if __name__ == "__main__":
    main()
