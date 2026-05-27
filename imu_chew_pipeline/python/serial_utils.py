import time
from typing import Optional, Tuple

import serial


Sample = Tuple[float, float, float, float, float, float, float]


def parse_imu_csv_line(line: str) -> Optional[Sample]:
    line = line.strip()
    if not line:
        return None

    ignored_prefixes = ("BOOT", "IMU", "ERROR", "time_ms")
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

    # Keeping DTR/RTS low avoids repeated ESP32 resets on some Windows setups.
    ser.dtr = False
    ser.rts = False
    ser.open()
    ser.setDTR(False)
    ser.setRTS(False)
    time.sleep(1.0)
    return ser

