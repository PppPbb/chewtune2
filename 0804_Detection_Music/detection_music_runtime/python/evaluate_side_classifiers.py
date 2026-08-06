import argparse
import csv
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import joblib
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button
import numpy as np
import pandas as pd

from detector import (
    DEFAULT_SIDE_MODEL_PATH,
    RAW_COLUMNS,
    Sample,
    add_magnitudes,
    build_window_dataframe,
    classify_dual_window,
    classify_dual_window_with_model,
    open_serial_port,
    parse_dual_imu_csv_line,
    update_axis_limits,
)


METHODS = ["cnn", "rule_score", "magnitude_threshold"]


def side_label_from_key(key: str) -> Optional[str]:
    if key == "g":
        return "left_chewing"
    if key == "h":
        return "right_chewing"
    return None


def magnitude_threshold_predict(df: pd.DataFrame, ratio: float, min_std: float) -> dict:
    df = add_magnitudes(df)
    left_gyro = float(np.std(df["l_gyro_mag"]))
    right_gyro = float(np.std(df["r_gyro_mag"]))
    left_acc = float(np.std(df["l_acc_mag"]))
    right_acc = float(np.std(df["r_acc_mag"]))

    left_score = left_gyro + 0.5 * left_acc
    right_score = right_gyro + 0.5 * right_acc
    best = max(left_score, right_score)

    if best < min_std:
        side = "-"
    elif left_score >= right_score * ratio:
        side = "left_chewing"
    elif right_score >= left_score * ratio:
        side = "right_chewing"
    else:
        side = "both_or_unknown"

    return {
        "side": side,
        "left_score": left_score,
        "right_score": right_score,
    }


def correct(pred: str, truth: str) -> bool:
    return pred == truth


def summarize(records: list[dict]) -> dict:
    summary = {}
    for method in METHODS:
        if not records:
            summary[method] = {"correct": 0, "total": 0, "accuracy": 0.0}
            continue
        n_correct = sum(1 for row in records if correct(str(row[f"{method}_pred"]), str(row["truth"])))
        total = len(records)
        summary[method] = {
            "correct": n_correct,
            "total": total,
            "accuracy": n_correct / total if total else 0.0,
        }
    return summary


def format_summary(records: list[dict]) -> str:
    summary = summarize(records)
    parts = []
    for method in METHODS:
        item = summary[method]
        parts.append(f"{method}: {item['correct']}/{item['total']}={item['accuracy']:.2%}")
    return " | ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare CNN and threshold-only side detection methods.")
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--sample-rate", type=int, default=100)
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--update-seconds", type=float, default=0.5)
    parser.add_argument("--plot-window-seconds", type=float, default=10.0)
    parser.add_argument("--ignore-first-seconds", type=float, default=3.0)
    parser.add_argument("--side-model", type=Path, default=DEFAULT_SIDE_MODEL_PATH)
    parser.add_argument("--model-threshold", type=float, default=0.6)
    parser.add_argument("--counter-min-peak-distance", type=float, default=0.45)
    parser.add_argument("--min-peaks", type=int, default=1)
    parser.add_argument("--min-cpm", type=float, default=30.0)
    parser.add_argument("--max-cpm", type=float, default=180.0)
    parser.add_argument("--min-channel-std", type=float, default=0.015)
    parser.add_argument("--max-regularity", type=float, default=1.25)
    parser.add_argument("--side-ratio", type=float, default=1.12)
    parser.add_argument("--magnitude-ratio", type=float, default=1.12)
    parser.add_argument("--magnitude-min-std", type=float, default=0.015)
    parser.add_argument("--results-csv", type=Path, default=Path("data") / "method_eval_results.csv")
    args = parser.parse_args()

    if not args.side_model.exists():
        raise FileNotFoundError(f"CNN model not found: {args.side_model}")
    model_bundle = joblib.load(args.side_model)
    fs = int(model_bundle.get("sample_rate_hz", args.sample_rate))
    args.window_seconds = float(model_bundle.get("window_seconds", args.window_seconds))
    window_samples = int(round(args.window_seconds * fs))
    plot_samples = max(window_samples, int(round(args.plot_window_seconds * fs)))

    rule_args = SimpleNamespace(
        counter_min_peak_distance=args.counter_min_peak_distance,
        min_channel_std=args.min_channel_std,
        min_peaks=args.min_peaks,
        min_cpm=args.min_cpm,
        max_cpm=args.max_cpm,
        max_regularity=args.max_regularity,
        side_ratio=args.side_ratio,
        model_threshold=args.model_threshold,
    )

    ser = open_serial_port(args.port, args.baud)
    inference_buffer: deque[Sample] = deque(maxlen=window_samples)
    t_data: deque[float] = deque(maxlen=plot_samples)
    channels = {name: deque(maxlen=plot_samples) for name in RAW_COLUMNS[1:]}
    records: list[dict] = []

    args.results_csv.parent.mkdir(parents=True, exist_ok=True)
    csv_file = args.results_csv.open("w", newline="", encoding="utf-8")
    csv_writer = csv.DictWriter(
        csv_file,
        fieldnames=[
            "mark_index",
            "elapsed_s",
            "truth",
            "cnn_pred",
            "cnn_prob",
            "rule_score_pred",
            "rule_left_score",
            "rule_right_score",
            "magnitude_threshold_pred",
            "magnitude_left_score",
            "magnitude_right_score",
        ],
    )
    csv_writer.writeheader()

    runtime = {
        "start_time_ms": None,
        "last_update": 0.0,
        "elapsed_s": 0.0,
        "cnn_pred": "-",
        "cnn_prob": 0.0,
        "rule_score_pred": "-",
        "rule_left_score": 0.0,
        "rule_right_score": 0.0,
        "magnitude_threshold_pred": "-",
        "magnitude_left_score": 0.0,
        "magnitude_right_score": 0.0,
        "stop": False,
    }

    print("Press G for left chewing, H for right chewing. Close window or press Stop to quit.")
    print(f"Saving evaluation marks to {args.results_csv}")

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    plt.subplots_adjust(bottom=0.18, hspace=0.35)
    ax_acc, ax_gyro, ax_score = axes

    left_acc_line, = ax_acc.plot([], [], label="left acc_mag", color="#2d6cdf")
    right_acc_line, = ax_acc.plot([], [], label="right acc_mag", color="#d14f32")
    left_gyro_line, = ax_gyro.plot([], [], label="left gyro_mag", color="#2d6cdf")
    right_gyro_line, = ax_gyro.plot([], [], label="right gyro_mag", color="#d14f32")
    cnn_points, = ax_score.plot([], [], linestyle="none", marker="o", label="marks", color="#222222")

    ax_acc.set_title("Dual MPU6050 accelerometer magnitude")
    ax_acc.set_ylabel("g")
    ax_gyro.set_title("Dual MPU6050 gyroscope magnitude")
    ax_gyro.set_ylabel("rad/s")
    ax_score.set_title("Evaluation marks")
    ax_score.set_ylabel("truth")
    ax_score.set_xlabel("Time / s")
    ax_score.set_yticks([0, 1])
    ax_score.set_yticklabels(["left", "right"])
    ax_score.set_ylim(-0.2, 1.2)

    for axis in axes:
        axis.grid(True)
        axis.legend(loc="upper right")

    status_text = fig.text(0.02, 0.055, "Waiting for data...", fontsize=11, weight="bold")
    summary_text = fig.text(0.02, 0.025, "No marks yet.", fontsize=10)
    button_axis = fig.add_axes([0.86, 0.03, 0.1, 0.05])
    stop_button = Button(button_axis, "Stop")

    mark_times: deque[float] = deque(maxlen=plot_samples)
    mark_values: deque[int] = deque(maxlen=plot_samples)

    def stop(_event=None) -> None:
        runtime["stop"] = True
        plt.close(fig)

    stop_button.on_clicked(stop)

    def current_result_row(truth: str) -> dict:
        return {
            "mark_index": len(records) + 1,
            "elapsed_s": f"{float(runtime['elapsed_s']):.3f}",
            "truth": truth,
            "cnn_pred": runtime["cnn_pred"],
            "cnn_prob": f"{float(runtime['cnn_prob']):.6f}",
            "rule_score_pred": runtime["rule_score_pred"],
            "rule_left_score": f"{float(runtime['rule_left_score']):.6f}",
            "rule_right_score": f"{float(runtime['rule_right_score']):.6f}",
            "magnitude_threshold_pred": runtime["magnitude_threshold_pred"],
            "magnitude_left_score": f"{float(runtime['magnitude_left_score']):.6f}",
            "magnitude_right_score": f"{float(runtime['magnitude_right_score']):.6f}",
        }

    def on_key_press(event) -> None:
        truth = side_label_from_key(str(event.key).lower())
        if truth is None:
            return
        if not inference_buffer:
            print("No prediction window yet; mark ignored.")
            return

        row = current_result_row(truth)
        records.append(row)
        csv_writer.writerow(row)
        csv_file.flush()
        mark_times.append(float(runtime["elapsed_s"]))
        mark_values.append(0 if truth == "left_chewing" else 1)
        print(f"MARK {row['mark_index']:03d} truth={truth} | {format_summary(records)}")

    fig.canvas.mpl_connect("key_press_event", on_key_press)

    def infer_once() -> None:
        df = build_window_dataframe(inference_buffer)

        cnn_result = classify_dual_window_with_model(df, fs, rule_args, model_bundle)
        rule_result = classify_dual_window(df, fs, rule_args)
        mag_result = magnitude_threshold_predict(df, args.magnitude_ratio, args.magnitude_min_std)

        runtime.update(
            cnn_pred=cnn_result["side"],
            cnn_prob=float(cnn_result.get("model_prob", 0.0)),
            rule_score_pred=rule_result["side"],
            rule_left_score=float(rule_result["left_score"]),
            rule_right_score=float(rule_result["right_score"]),
            magnitude_threshold_pred=mag_result["side"],
            magnitude_left_score=float(mag_result["left_score"]),
            magnitude_right_score=float(mag_result["right_score"]),
        )

    def update(_frame):
        if runtime["stop"]:
            return [left_acc_line, right_acc_line, left_gyro_line, right_gyro_line, cnn_points]

        for _ in range(80):
            if ser.in_waiting <= 0:
                break

            sample = parse_dual_imu_csv_line(ser.readline().decode("utf-8", errors="ignore"))
            if sample is None:
                continue

            if runtime["start_time_ms"] is None:
                runtime["start_time_ms"] = sample[0]

            start_time_ms = float(runtime["start_time_ms"])
            elapsed_s = (sample[0] - start_time_ms) / 1000.0
            runtime["elapsed_s"] = elapsed_s
            t_data.append(elapsed_s)
            inference_buffer.append(sample)
            for name, value in zip(RAW_COLUMNS[1:], sample[1:]):
                channels[name].append(value)

            now = time.monotonic()
            if (
                len(inference_buffer) >= window_samples
                and now - float(runtime["last_update"]) >= args.update_seconds
            ):
                runtime["last_update"] = now
                if elapsed_s >= args.ignore_first_seconds:
                    infer_once()

        if not t_data:
            return [left_acc_line, right_acc_line, left_gyro_line, right_gyro_line, cnn_points]

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
        cnn_points.set_data(mark_times, mark_values)

        update_axis_limits(ax_acc, t_data, [l_acc, r_acc], 0.05)
        update_axis_limits(ax_gyro, t_data, [l_gyro, r_gyro], 0.03)
        ax_score.set_xlim(t_data[0], max(t_data[-1], t_data[0] + 1.0))

        status_text.set_text(
            f"CNN: {runtime['cnn_pred']} ({float(runtime['cnn_prob']):.2f}) | "
            f"Rule: {runtime['rule_score_pred']} "
            f"L={float(runtime['rule_left_score']):.3f} R={float(runtime['rule_right_score']):.3f} | "
            f"Mag: {runtime['magnitude_threshold_pred']} "
            f"L={float(runtime['magnitude_left_score']):.3f} R={float(runtime['magnitude_right_score']):.3f}"
        )
        summary_text.set_text(format_summary(records) if records else "Press G=left, H=right to record accuracy marks.")
        return [left_acc_line, right_acc_line, left_gyro_line, right_gyro_line, cnn_points]

    ani = FuncAnimation(fig, update, interval=30, blit=False, cache_frame_data=False)
    try:
        plt.show()
    finally:
        runtime["stop"] = True
        if ser.is_open:
            ser.close()
        csv_file.close()
        print("Serial closed.")
        print(format_summary(records) if records else "No marks recorded.")


if __name__ == "__main__":
    main()
