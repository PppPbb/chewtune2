import argparse
import csv
import json
import time
from collections import deque
from pathlib import Path
from typing import Optional, Tuple

import joblib
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
DERIVED_COLUMNS = ["l_acc_mag", "l_gyro_mag", "r_acc_mag", "r_gyro_mag"]
SEQUENCE_COLUMNS = RAW_COLUMNS[1:] + DERIVED_COLUMNS
DEFAULT_SIDE_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "dual_side_cnn.pkl"
DEFAULT_SIDE_METADATA_PATH = Path(__file__).resolve().parents[1] / "models" / "dual_side_cnn_meta.pkl"
DEFAULT_CPM_CALIBRATOR_PATH = Path(__file__).resolve().parents[1] / "models" / "cpm_calibrator.pkl"
DEFAULT_CHEWING_STATE_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "chewing_state_rf.pkl"
DEFAULT_CHEWING_THRESHOLD_PATH = Path(__file__).resolve().parents[1] / "models" / "chewing_state_threshold.json"
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


def extract_cpm_calibration_features(df: pd.DataFrame, fs: float, args, result: dict) -> dict:
    df = add_magnitudes(df)
    left_gyro_std = float(np.std(df["l_gyro_mag"]))
    right_gyro_std = float(np.std(df["r_gyro_mag"]))
    left_acc_std = float(np.std(df["l_acc_mag"]))
    right_acc_std = float(np.std(df["r_acc_mag"]))
    total_gyro_std = left_gyro_std + right_gyro_std
    total_acc_std = left_acc_std + right_acc_std

    return {
        "raw_cpm": float(result.get("cpm", 0.0)),
        "peaks": float(result.get("peaks", 0)),
        "left_score": float(result.get("left_score", 0.0)),
        "right_score": float(result.get("right_score", 0.0)),
        "dominance": float(result.get("dominance", 0.0)),
        "left_gyro_std": left_gyro_std,
        "right_gyro_std": right_gyro_std,
        "left_acc_std": left_acc_std,
        "right_acc_std": right_acc_std,
        "total_gyro_std": total_gyro_std,
        "total_acc_std": total_acc_std,
        "gyro_std_ratio_l_over_r": left_gyro_std / max(right_gyro_std, 1e-9),
        "acc_std_ratio_l_over_r": left_acc_std / max(right_acc_std, 1e-9),
    }


def apply_cpm_calibrator(df: pd.DataFrame, fs: float, args, result: dict, calibrator_bundle: Optional[dict]) -> dict:
    if calibrator_bundle is None or result.get("state") != "chewing":
        result["raw_cpm"] = float(result.get("cpm", 0.0))
        result["calibrated"] = False
        return result

    features = extract_cpm_calibration_features(df, fs, args, result)
    feature_columns = calibrator_bundle["feature_columns"]
    x = pd.DataFrame([{col: features.get(col, 0.0) for col in feature_columns}])
    cpm = float(calibrator_bundle["model"].predict(x)[0])
    cpm = max(0.0, min(float(calibrator_bundle.get("max_cpm", 220.0)), cpm))
    result["raw_cpm"] = float(result.get("cpm", 0.0))
    result["cpm"] = cpm
    result["calibrated"] = True
    return result


def spectral_features(signal: np.ndarray, fs: float, prefix: str) -> dict:
    x = np.asarray(signal, dtype=float)
    x = x - np.mean(x)
    if x.size < 2 or np.allclose(x, 0):
        return {
            f"{prefix}_dom_freq": 0.0,
            f"{prefix}_band_energy": 0.0,
            f"{prefix}_entropy": 0.0,
        }

    freqs = np.fft.rfftfreq(x.size, d=1.0 / fs)
    power = np.abs(np.fft.rfft(x)) ** 2
    power[0] = 0.0
    dom_idx = int(np.argmax(power))
    band_mask = (freqs >= 0.5) & (freqs <= 3.0)
    total = float(np.sum(power))
    if total <= 0:
        entropy = 0.0
    else:
        p = power / total
        p = p[p > 0]
        entropy = float(-np.sum(p * np.log2(p)) / np.log2(len(p))) if len(p) > 1 else 0.0
    return {
        f"{prefix}_dom_freq": float(freqs[dom_idx]),
        f"{prefix}_band_energy": float(np.sum(power[band_mask])),
        f"{prefix}_entropy": entropy,
    }


def peak_regularity_features(signal: np.ndarray, fs: float, prefix: str) -> dict:
    peak_count, estimated_cpm, interval_cv = count_peaks_simple(signal, fs, min_peak_distance_s=0.35)
    x = np.asarray(signal, dtype=float)
    x = x - np.mean(x)
    std = float(np.std(x))
    min_distance = max(1, int(round(0.35 * fs)))
    threshold = 0.55 * std
    peaks = []
    last_peak = -min_distance

    if x.size >= 3 and std > 1e-9:
        for idx in range(1, len(x) - 1):
            if idx - last_peak < min_distance:
                continue
            if x[idx] > threshold and x[idx] >= x[idx - 1] and x[idx] > x[idx + 1]:
                peaks.append(idx)
                last_peak = idx

    intervals = np.diff(peaks) / fs if len(peaks) >= 2 else np.asarray([], dtype=float)
    return {
        f"{prefix}_peak_count": float(peak_count),
        f"{prefix}_estimated_cpm": float(estimated_cpm),
        f"{prefix}_peak_interval_mean": float(np.mean(intervals)) if intervals.size else 0.0,
        f"{prefix}_peak_interval_std": float(np.std(intervals)) if intervals.size else 0.0,
        f"{prefix}_peak_interval_cv": float(interval_cv),
    }


def extract_dual_features(df: pd.DataFrame, fs: float) -> dict:
    df = add_magnitudes(df)
    feature_cols = RAW_COLUMNS[1:] + DERIVED_COLUMNS
    features = {}

    for col in feature_cols:
        x = df[col].to_numpy(dtype=float)
        features[f"{col}_mean"] = float(np.mean(x))
        features[f"{col}_std"] = float(np.std(x))
        features[f"{col}_min"] = float(np.min(x))
        features[f"{col}_max"] = float(np.max(x))
        features[f"{col}_range"] = float(np.max(x) - np.min(x))
        features[f"{col}_rms"] = float(np.sqrt(np.mean(x ** 2)))
        features[f"{col}_energy"] = float(np.mean(x ** 2))
        features.update(spectral_features(x, fs, col))
        features.update(peak_regularity_features(x, fs, col))

    pairs = [
        ("acc_mag", "l_acc_mag", "r_acc_mag"),
        ("gyro_mag", "l_gyro_mag", "r_gyro_mag"),
        ("gx", "l_gx", "r_gx"),
        ("gy", "l_gy", "r_gy"),
        ("gz", "l_gz", "r_gz"),
    ]
    for name, left_col, right_col in pairs:
        l_std = float(np.std(df[left_col]))
        r_std = float(np.std(df[right_col]))
        features[f"{name}_std_diff_l_minus_r"] = l_std - r_std
        features[f"{name}_std_ratio_l_over_r"] = l_std / max(r_std, 1e-9)
        features[f"{name}_energy_diff_l_minus_r"] = float(np.mean(df[left_col] ** 2) - np.mean(df[right_col] ** 2))

    return features


def predict_chewing_state(df: pd.DataFrame, fs: float, state_bundle: Optional[dict]) -> tuple[str, float]:
    if state_bundle is None:
        return "unknown", 1.0

    features = extract_dual_features(df, fs)
    feature_columns = state_bundle["feature_columns"]
    x = pd.DataFrame([{col: features.get(col, 0.0) for col in feature_columns}])
    model = state_bundle["model"]

    if hasattr(model, "predict_proba"):
        probabilities = model.predict_proba(x)[0]
        classes = [str(item) for item in model.classes_]
        prob_map = dict(zip(classes, probabilities))
        chewing_prob = float(prob_map.get("chewing", 0.0))
    else:
        pred = str(model.predict(x)[0])
        chewing_prob = 1.0 if pred == "chewing" else 0.0

    return ("chewing" if chewing_prob >= float(state_bundle.get("threshold", 0.55)) else "non_chewing"), chewing_prob


def load_threshold_gate(path: Path) -> dict:
    defaults = {
        "total_gyro_std_min": 0.14,
        "max_axis_gyro_std_min": 0.10,
        "total_acc_std_min": 0.018,
    }
    if not path.exists():
        return defaults
    with path.open("r", encoding="utf-8") as f:
        loaded = json.load(f)
    defaults.update({key: float(value) for key, value in loaded.items() if key in defaults})
    return defaults


def predict_chewing_state_by_threshold(df: pd.DataFrame, thresholds: dict) -> tuple[str, float, dict]:
    df = add_magnitudes(df)
    total_gyro_std = float(np.std(df["l_gyro_mag"]) + np.std(df["r_gyro_mag"]))
    total_acc_std = float(np.std(df["l_acc_mag"]) + np.std(df["r_acc_mag"]))
    max_axis_gyro_std = float(max(np.std(df[col]) for col in ["l_gx", "l_gy", "l_gz", "r_gx", "r_gy", "r_gz"]))

    gyro_ratio = total_gyro_std / max(float(thresholds["total_gyro_std_min"]), 1e-9)
    axis_ratio = max_axis_gyro_std / max(float(thresholds["max_axis_gyro_std_min"]), 1e-9)
    acc_ratio = total_acc_std / max(float(thresholds["total_acc_std_min"]), 1e-9)
    score = min(gyro_ratio, axis_ratio, acc_ratio)
    state = "chewing" if score >= 1.0 else "non_chewing"
    pseudo_prob = min(1.0, max(0.0, score / 1.5))
    metrics = {
        "threshold_total_gyro_std": total_gyro_std,
        "threshold_max_axis_gyro_std": max_axis_gyro_std,
        "threshold_total_acc_std": total_acc_std,
        "threshold_score": score,
    }
    return state, pseudo_prob, metrics


def make_cnn_sequence(df: pd.DataFrame, model_bundle: dict) -> np.ndarray:
    window = add_magnitudes(df)
    feature_columns = model_bundle["feature_columns"]
    sequence = window[feature_columns].to_numpy(dtype=float)
    expected_samples = int(model_bundle["window_samples"])
    if sequence.shape[0] != expected_samples:
        raise ValueError(f"CNN expects {expected_samples} samples, got {sequence.shape[0]}")

    mean = np.asarray(model_bundle["mean"], dtype=float)
    scale = np.asarray(model_bundle["scale"], dtype=float)
    sequence = (sequence - mean) / scale
    return sequence[np.newaxis, :, :]


def normalize_side_label(label) -> str:
    text = str(label).strip().lower()
    if text in {"0", "0.0", "left", "left_chewing"}:
        return "left_chewing"
    if text in {"1", "1.0", "right", "right_chewing"}:
        return "right_chewing"
    return str(label)


def random_conv_features(sequence: np.ndarray, kernels: list[dict]) -> np.ndarray:
    features = []
    for kernel in kernels:
        weights = np.asarray(kernel["weights"], dtype=float)
        bias = float(kernel["bias"])
        length = weights.shape[0]
        if sequence.shape[0] < length:
            conv = np.array([0.0])
        else:
            conv = np.array([
                float(np.sum(sequence[start : start + length] * weights) + bias)
                for start in range(sequence.shape[0] - length + 1)
            ])
        features.extend([
            float(np.max(conv)),
            float(np.mean(conv > 0.0)),
            float(np.mean(conv)),
            float(np.std(conv)),
        ])
    return np.asarray(features, dtype=float).reshape(1, -1)


def classify_dual_window_with_model(df: pd.DataFrame, fs: float, args, model_bundle: dict) -> dict:
    rule_result = classify_dual_window(df, fs, args)
    if rule_result["state"] != "chewing":
        rule_result["model_side"] = "-"
        rule_result["model_prob"] = 0.0
        return rule_result

    model = model_bundle["model"]
    if model_bundle.get("kind") == "random_conv_cnn":
        sequence = make_cnn_sequence(df, model_bundle)[0]
        x = random_conv_features(sequence, model_bundle["kernels"])
        probabilities = model.predict_proba(x)[0]
        classes = list(model.classes_)
        best_idx = int(np.argmax(probabilities))
        side = normalize_side_label(classes[best_idx])
        prob = float(probabilities[best_idx])
    elif model_bundle.get("kind") == "cnn":
        sequence = make_cnn_sequence(df, model_bundle)
        prob_right = float(model.predict(sequence, verbose=0)[0][0])
        if prob_right >= 0.5:
            side = "right_chewing"
            prob = prob_right
        else:
            side = "left_chewing"
            prob = 1.0 - prob_right
    else:
        features = extract_dual_features(df, fs)
        feature_columns = model_bundle["feature_columns"]
        x = pd.DataFrame([{col: features.get(col, 0.0) for col in feature_columns}])
        probabilities = model.predict_proba(x)[0]
        classes = list(model.classes_)
        best_idx = int(np.argmax(probabilities))
        side = normalize_side_label(classes[best_idx])
        prob = float(probabilities[best_idx])

    rule_result["side"] = side if prob >= args.model_threshold else "unknown"
    rule_result["model_side"] = side
    rule_result["model_prob"] = prob
    return rule_result


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
    parser.add_argument("--side-model", type=Path, default=DEFAULT_SIDE_MODEL_PATH)
    parser.add_argument("--side-metadata", type=Path, default=DEFAULT_SIDE_METADATA_PATH)
    parser.add_argument("--cpm-calibrator", type=Path, default=DEFAULT_CPM_CALIBRATOR_PATH)
    parser.add_argument("--disable-cpm-calibrator", action="store_true")
    parser.add_argument("--chewing-state-model", type=Path, default=DEFAULT_CHEWING_STATE_MODEL_PATH)
    parser.add_argument("--chewing-threshold-config", type=Path, default=DEFAULT_CHEWING_THRESHOLD_PATH)
    parser.add_argument("--chewing-threshold", type=float, default=0.60)
    parser.add_argument("--enable-chewing-state-model", action="store_true")
    parser.add_argument("--disable-chewing-state-model", action="store_true")
    parser.add_argument("--model-threshold", type=float, default=0.6)
    parser.add_argument("--disable-model", action="store_true")
    parser.add_argument("--activity-label", default="")
    parser.add_argument("--save-csv", type=Path)
    args = parser.parse_args()

    fs = args.sample_rate

    model_bundle = None
    if not args.disable_model and args.side_model.exists():
        if args.side_model.suffix.lower() == ".keras":
            if not args.side_metadata.exists():
                raise FileNotFoundError(f"CNN metadata not found: {args.side_metadata}")
            from tensorflow.keras.models import load_model

            model_bundle = joblib.load(args.side_metadata)
            model_bundle["model"] = load_model(args.side_model)
            model_bundle["kind"] = "cnn"
        else:
            model_bundle = joblib.load(args.side_model)
            model_bundle["kind"] = model_bundle.get("kind", "random_forest")
        fs = int(model_bundle.get("sample_rate_hz", fs))
        args.window_seconds = float(model_bundle.get("window_seconds", args.window_seconds))
        print(f"Loaded side model: {args.side_model}")
    else:
        print(f"Side model is off. Train or pass a model at: {args.side_model}")

    cpm_calibrator_bundle = None
    if not args.disable_cpm_calibrator and args.cpm_calibrator.exists():
        cpm_calibrator_bundle = joblib.load(args.cpm_calibrator)
        print(f"Loaded CPM calibrator: {args.cpm_calibrator}")
    else:
        print(f"CPM calibrator is off. Train or pass one at: {args.cpm_calibrator}")

    threshold_gate = load_threshold_gate(args.chewing_threshold_config)
    print(f"Loaded threshold chewing gate: {args.chewing_threshold_config}")
    print(
        "Thresholds: "
        f"total_gyro>={threshold_gate['total_gyro_std_min']}, "
        f"max_axis_gyro>={threshold_gate['max_axis_gyro_std_min']}, "
        f"total_acc>={threshold_gate['total_acc_std_min']}"
    )

    chewing_state_bundle = None
    if args.enable_chewing_state_model and not args.disable_chewing_state_model and args.chewing_state_model.exists():
        chewing_state_bundle = joblib.load(args.chewing_state_model)
        chewing_state_bundle["threshold"] = args.chewing_threshold
        print(f"Loaded chewing state model: {args.chewing_state_model}")
    else:
        print("Random Forest chewing state model is off; using threshold gate.")

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
        "model_side": "-",
        "model_prob": 0.0,
        "raw_cpm": 0.0,
        "calibrated": False,
        "chewing_prob": 0.0,
        "threshold_score": 0.0,
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
        if chewing_state_bundle is not None:
            state_pred, chewing_prob = predict_chewing_state(df, fs, chewing_state_bundle)
            threshold_metrics = {"threshold_score": 0.0}
        else:
            state_pred, chewing_prob, threshold_metrics = predict_chewing_state_by_threshold(df, threshold_gate)
        if state_pred == "non_chewing":
            rule_result = classify_dual_window(df, fs, args)
            result = {
                "state": "non_chewing",
                "side": "-",
                "cpm": 0.0,
                "raw_cpm": 0.0,
                "channel": "-",
                "peaks": 0,
                "left_score": rule_result["left_score"],
                "right_score": rule_result["right_score"],
                "dominance": rule_result["dominance"],
                "model_side": "-",
                "model_prob": 0.0,
                "calibrated": False,
            }
        else:
            if model_bundle is not None:
                result = classify_dual_window_with_model(df, fs, args, model_bundle)
            else:
                result = classify_dual_window(df, fs, args)
                result["model_side"] = "-"
                result["model_prob"] = 0.0
            result = apply_cpm_calibrator(df, fs, args, result, cpm_calibrator_bundle)
        result["chewing_prob"] = chewing_prob
        result.update(threshold_metrics)
        runtime.update(result)

        current_t = t_data[-1] if t_data else 0.0
        event_times.append(current_t)
        event_states.append(1 if runtime["state"] == "chewing" else 0)
        cpm_values.append(float(runtime["cpm"]))

        print(
            f"{runtime['state']:12s} side={runtime['side']:15s} "
            f"CPM={float(runtime['cpm']):5.1f} raw={float(runtime['raw_cpm']):5.1f} peaks={runtime['peaks']} "
            f"rule_channel={runtime['channel']} L={float(runtime['left_score']):.4f} "
            f"R={float(runtime['right_score']):.4f} "
            f"model={runtime['model_side']}({float(runtime['model_prob']):.2f}) "
            f"p_chew={float(runtime['chewing_prob']):.2f} thr={float(runtime['threshold_score']):.2f}"
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
            f"p(chew): {float(runtime['chewing_prob']):.2f} | "
            f"thr: {float(runtime['threshold_score']):.2f} | "
            f"CPM: {float(runtime['cpm']):.1f} raw: {float(runtime['raw_cpm']):.1f} | rule_channel: {runtime['channel']} | "
            f"L: {float(runtime['left_score']):.3f} R: {float(runtime['right_score']):.3f} | "
            f"model: {runtime['model_side']} ({float(runtime['model_prob']):.2f})"
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
