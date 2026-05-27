import argparse
from collections import deque

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

from serial_utils import open_serial_port, parse_imu_csv_line


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot live six-axis IMU data without saving.")
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--plot-window", type=int, default=500)
    args = parser.parse_args()

    ser = open_serial_port(args.port, args.baud)
    state = {"start_time": None}
    t_data = deque(maxlen=args.plot_window)
    channels = {name: deque(maxlen=args.plot_window) for name in ["ax", "ay", "az", "gx", "gy", "gz"]}

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

    def update(_frame):
        for _ in range(80):
            if ser.in_waiting <= 0:
                break
            sample = parse_imu_csv_line(ser.readline().decode("utf-8", errors="ignore"))
            if sample is None:
                continue
            t_ms, ax, ay, az, gx, gy, gz = sample
            if state["start_time"] is None:
                state["start_time"] = t_ms
            t_data.append((t_ms - state["start_time"]) / 1000.0)
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
        return acc_lines + gyro_lines

    ani = FuncAnimation(fig, update, interval=30, blit=False, cache_frame_data=False)
    try:
        plt.tight_layout()
        plt.show()
    finally:
        if ser.is_open:
            ser.close()
        print("Serial closed.")


if __name__ == "__main__":
    main()

