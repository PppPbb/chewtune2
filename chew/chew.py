import serial
import time
from collections import deque

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation


PORT = "COM4"
BAUD_RATE = 115200
WINDOW_SIZE = 500  # 100Hz 下显示最近约 5 秒


def parse_line(line):
    try:
        parts = line.strip().split(",")

        if len(parts) != 7:
            return None

        t = float(parts[0])
        ax = float(parts[1])
        ay = float(parts[2])
        az = float(parts[3])
        gx = float(parts[4])
        gy = float(parts[5])
        gz = float(parts[6])

        return t, ax, ay, az, gx, gy, gz

    except ValueError:
        return None


def open_serial_port():
    ser = serial.Serial()
    ser.port = PORT
    ser.baudrate = BAUD_RATE
    ser.timeout = 1

    ser.dtr = False
    ser.rts = False

    ser.open()

    ser.setDTR(False)
    ser.setRTS(False)

    return ser


def main():
    print(f"Opening serial port: {PORT}")

    ser = open_serial_port()
    time.sleep(1)

    print("Waiting for IMU data...")

    t_data = deque(maxlen=WINDOW_SIZE)

    ax_data = deque(maxlen=WINDOW_SIZE)
    ay_data = deque(maxlen=WINDOW_SIZE)
    az_data = deque(maxlen=WINDOW_SIZE)

    gx_data = deque(maxlen=WINDOW_SIZE)
    gy_data = deque(maxlen=WINDOW_SIZE)
    gz_data = deque(maxlen=WINDOW_SIZE)

    state = {"start_time": None}

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)

    ax_acc = axes[0]
    ax_gyro = axes[1]

    line_ax, = ax_acc.plot([], [], label="ax")
    line_ay, = ax_acc.plot([], [], label="ay")
    line_az, = ax_acc.plot([], [], label="az")

    line_gx, = ax_gyro.plot([], [], label="gx")
    line_gy, = ax_gyro.plot([], [], label="gy")
    line_gz, = ax_gyro.plot([], [], label="gz")

    ax_acc.set_title("Accelerometer")
    ax_acc.set_ylabel("Acceleration / g")
    ax_acc.legend(loc="upper right")
    ax_acc.grid(True)

    ax_gyro.set_title("Gyroscope")
    ax_gyro.set_xlabel("Time / s")
    ax_gyro.set_ylabel("Angular velocity / rad/s")
    ax_gyro.legend(loc="upper right")
    ax_gyro.grid(True)

    plt.tight_layout()

    def update(frame):
        try:
            for _ in range(50):
                if ser.in_waiting <= 0:
                    break

                raw_line = ser.readline().decode("utf-8", errors="ignore").strip()

                if not raw_line:
                    continue

                if raw_line.startswith("BOOT"):
                    print(raw_line)
                    continue

                if raw_line.startswith("IMU"):
                    print(raw_line)
                    continue

                if raw_line.startswith("ERROR"):
                    print(raw_line)
                    continue

                if raw_line.startswith("time_ms"):
                    continue

                parsed = parse_line(raw_line)

                if parsed is None:
                    continue

                t_ms, ax, ay, az, gx, gy, gz = parsed

                if state["start_time"] is None:
                    state["start_time"] = t_ms

                t = (t_ms - state["start_time"]) / 1000.0

                t_data.append(t)

                ax_data.append(ax)
                ay_data.append(ay)
                az_data.append(az)

                gx_data.append(gx)
                gy_data.append(gy)
                gz_data.append(gz)

        except serial.SerialException as e:
            print("Serial read error:")
            print(e)

        if len(t_data) == 0:
            return line_ax, line_ay, line_az, line_gx, line_gy, line_gz

        line_ax.set_data(t_data, ax_data)
        line_ay.set_data(t_data, ay_data)
        line_az.set_data(t_data, az_data)

        line_gx.set_data(t_data, gx_data)
        line_gy.set_data(t_data, gy_data)
        line_gz.set_data(t_data, gz_data)

        ax_acc.set_xlim(t_data[0], max(t_data[-1], t_data[0] + 1))
        ax_gyro.set_xlim(t_data[0], max(t_data[-1], t_data[0] + 1))

        acc_values = list(ax_data) + list(ay_data) + list(az_data)
        gyro_values = list(gx_data) + list(gy_data) + list(gz_data)

        if len(acc_values) > 10:
            acc_min = min(acc_values)
            acc_max = max(acc_values)
            acc_margin = max(0.1, (acc_max - acc_min) * 0.2)
            ax_acc.set_ylim(acc_min - acc_margin, acc_max + acc_margin)

            gyro_min = min(gyro_values)
            gyro_max = max(gyro_values)
            gyro_margin = max(0.05, (gyro_max - gyro_min) * 0.2)
            ax_gyro.set_ylim(gyro_min - gyro_margin, gyro_max + gyro_margin)

        return line_ax, line_ay, line_az, line_gx, line_gy, line_gz

    ani = FuncAnimation(
        fig,
        update,
        interval=30,
        blit=False,
        cache_frame_data=False
    )

    try:
        plt.show()
    finally:
        if ser.is_open:
            ser.close()
        print("Serial closed.")


if __name__ == "__main__":
    main()