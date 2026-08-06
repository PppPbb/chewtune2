import argparse
import csv
import json
import time
import tkinter as tk
from pathlib import Path

from detector import RAW_COLUMNS, open_serial_port, parse_dual_imu_csv_line


DEFAULT_SEQUENCE = ["chewing", "speaking", "still"]


def timestamp_name() -> str:
    return time.strftime("session_%Y%m%d_%H%M%S")


def parse_states(value: str) -> list[str]:
    states = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not states:
        raise argparse.ArgumentTypeError("At least one state is required.")
    valid = {"chewing", "speaking", "still"}
    invalid = [state for state in states if state not in valid]
    if invalid:
        raise argparse.ArgumentTypeError(f"Invalid states: {', '.join(invalid)}")
    return states


class AutoCollector:
    def __init__(self, args) -> None:
        self.args = args
        self.session_id = timestamp_name()
        self.csv_path = args.out_dir / f"{self.session_id}.csv"
        self.meta_path = args.out_dir / f"{self.session_id}_meta.json"
        self.total_segments = len(args.states) * args.cycles
        self.start_monotonic = time.monotonic()
        self.start_time_ms = None
        self.raw_count = 0
        self.saved_count = 0
        self.counts = {state: 0 for state in args.states}
        self.transitions = []
        self.last_state = "ignore"
        self.finished = False
        self.paused = False
        self.pause_started_at = None
        self.paused_total = 0.0

        args.out_dir.mkdir(parents=True, exist_ok=True)
        self.ser = open_serial_port(args.port, args.baud)
        self.csv_file = self.csv_path.open("w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.csv_file)
        self.writer.writerow(RAW_COLUMNS + ["label", "elapsed_s", "session_id"])

        self.root = tk.Tk()
        self.root.title("Chewing State Training Data Collector")
        self.root.geometry("520x300")
        self.root.protocol("WM_DELETE_WINDOW", self.stop)
        self.root.bind("<space>", self.toggle_pause)

        self.state_var = tk.StringVar(value="Preparing...")
        self.next_var = tk.StringVar(value="")
        self.timer_var = tk.StringVar(value="")
        self.sample_var = tk.StringVar(value="")
        self.tip_var = tk.StringVar(value="First 3s ignored. Space = pause/resume. Then auto collect states.")

        tk.Label(self.root, text="Chewing State Training Data", font=("Arial", 20, "bold")).pack(pady=(18, 8))
        tk.Label(self.root, textvariable=self.state_var, font=("Arial", 28, "bold")).pack(pady=4)
        tk.Label(self.root, textvariable=self.timer_var, font=("Arial", 18)).pack(pady=2)
        tk.Label(self.root, textvariable=self.next_var, font=("Arial", 16)).pack(pady=2)
        tk.Label(self.root, textvariable=self.sample_var, font=("Arial", 13)).pack(pady=(14, 4))
        tk.Label(self.root, textvariable=self.tip_var, font=("Arial", 11), fg="#555555").pack(pady=4)
        tk.Button(self.root, text="Stop and Save", command=self.stop, width=18).pack(pady=(10, 0))

    def effective_elapsed(self, now: float) -> float:
        paused_total = self.paused_total
        if self.paused and self.pause_started_at is not None:
            paused_total += now - self.pause_started_at
        return now - self.start_monotonic - paused_total

    def toggle_pause(self, _event=None) -> None:
        if self.finished:
            return
        now = time.monotonic()
        if self.paused:
            if self.pause_started_at is not None:
                self.paused_total += now - self.pause_started_at
            self.pause_started_at = None
            self.paused = False
            event = "resume"
        else:
            self.pause_started_at = now
            self.paused = True
            event = "pause"
        self.transitions.append({"elapsed_s": round(self.effective_elapsed(now), 3), "event": event})

    def state_at(self, elapsed: float) -> tuple[str, str, float, int]:
        if elapsed < self.args.ignore_seconds:
            return "ignore", self.args.states[0], self.args.ignore_seconds - elapsed, -1

        active_elapsed = elapsed - self.args.ignore_seconds
        segment_index = int(active_elapsed // self.args.segment_seconds)
        if segment_index >= self.total_segments:
            return "done", "done", 0.0, segment_index

        current = self.args.states[segment_index % len(self.args.states)]
        next_state = "done"
        if segment_index + 1 < self.total_segments:
            next_state = self.args.states[(segment_index + 1) % len(self.args.states)]
        countdown = self.args.segment_seconds - (active_elapsed % self.args.segment_seconds)
        return current, next_state, countdown, segment_index

    def record_transition(self, state: str, segment_index: int, elapsed: float) -> None:
        if state == self.last_state:
            return
        self.transitions.append(
            {
                "elapsed_s": round(elapsed, 3),
                "from": self.last_state,
                "to": state,
                "segment_index": segment_index,
            }
        )
        self.last_state = state

    def read_serial(self, state: str) -> None:
        for _ in range(120):
            if self.ser.in_waiting <= 0:
                break

            line = self.ser.readline().decode("utf-8", errors="ignore")
            sample = parse_dual_imu_csv_line(line)
            if sample is None:
                continue

            self.raw_count += 1
            if self.start_time_ms is None:
                self.start_time_ms = float(sample[0])

            if state in self.counts:
                sample_elapsed = (float(sample[0]) - self.start_time_ms) / 1000.0
                self.writer.writerow([*sample, state, f"{sample_elapsed:.6f}", self.session_id])
                self.saved_count += 1
                self.counts[state] += 1

        if self.saved_count % 50 == 0:
            self.csv_file.flush()

    def update_window(self) -> None:
        if self.finished:
            return

        now = time.monotonic()
        elapsed = self.effective_elapsed(now)
        state, next_state, countdown, segment_index = self.state_at(elapsed)
        self.record_transition(state, segment_index, elapsed)

        if state == "done":
            self.stop()
            return

        if self.paused:
            self.read_serial("paused")
            self.state_var.set("PAUSED")
            self.timer_var.set("Press Space to resume")
            self.next_var.set(f"Current state will continue: {state}")
        else:
            self.read_serial(state)
            if state == "ignore":
                self.state_var.set("IGNORE")
                self.timer_var.set(f"Collecting starts in: {countdown:04.1f}s")
                self.next_var.set(f"Next state: {next_state}")
            else:
                round_index = segment_index // len(self.args.states) + 1
                self.state_var.set(state.upper())
                self.timer_var.set(f"Switch in: {countdown:04.1f}s")
                self.next_var.set(f"Round {round_index}/{self.args.cycles} | Next state: {next_state}")

        self.sample_var.set(
            f"saved={self.saved_count} raw={self.raw_count} | "
            f"chewing={self.counts.get('chewing', 0)} "
            f"speaking={self.counts.get('speaking', 0)} "
            f"still={self.counts.get('still', 0)}"
        )
        self.root.after(50, self.update_window)

    def write_metadata(self) -> None:
        meta = {
            "session_id": self.session_id,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "port": self.args.port,
            "baud": self.args.baud,
            "subject": self.args.subject,
            "note": self.args.note,
            "ignore_seconds": self.args.ignore_seconds,
            "segment_seconds": self.args.segment_seconds,
            "cycles": self.args.cycles,
            "states": self.args.states,
            "raw_count": self.raw_count,
            "saved_count": self.saved_count,
            "state_sample_counts": self.counts,
            "paused_total_seconds": round(self.paused_total, 3),
            "transitions": self.transitions,
        }
        with self.meta_path.open("w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    def stop(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.csv_file.flush()
        self.csv_file.close()
        if self.ser.is_open:
            self.ser.close()
        self.write_metadata()
        self.root.destroy()
        print(f"Saved {self.saved_count} labeled samples to {self.csv_path}")
        print(f"Saved metadata to {self.meta_path}")

    def run(self) -> None:
        print("Chewing-state training data collection")
        print(f"Ignore first {self.args.ignore_seconds}s, then {self.args.segment_seconds}s per state.")
        print("Press Space in the window to pause/resume.")
        print(f"States: {' -> '.join(self.args.states)} x {self.args.cycles}")
        print(f"CSV:  {self.csv_path}")
        print(f"Meta: {self.meta_path}")
        self.root.after(50, self.update_window)
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect dual IMU chewing-state training data with an automatic state window.")
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--out-dir", type=Path, default=Path("data") / "state")
    parser.add_argument("--subject", default="S001")
    parser.add_argument("--note", default="")
    parser.add_argument("--ignore-seconds", type=float, default=3.0)
    parser.add_argument("--segment-seconds", type=float, default=30.0)
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--states", type=parse_states, default=DEFAULT_SEQUENCE)
    args = parser.parse_args()

    collector = AutoCollector(args)
    collector.run()


if __name__ == "__main__":
    main()
