import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split


THIS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = THIS_DIR.parents[0]
DEFAULT_DATA_DIR = PROJECT_DIR / "data" / "rate_calibration"
DEFAULT_MODEL_OUT = PROJECT_DIR / "models" / "cpm_calibrator.pkl"
DEFAULT_FEATURES_OUT = PROJECT_DIR / "data" / "rate_calibration_features.csv"

sys.path.insert(0, str(THIS_DIR))
from realtime_dual_mpu6050_detection import (  # noqa: E402
    RAW_COLUMNS,
    add_magnitudes,
    classify_dual_window,
    extract_cpm_calibration_features,
)


def load_raw_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [col for col in RAW_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"{path} missing columns: {missing}")
    for col in RAW_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=RAW_COLUMNS).sort_values("time_ms").drop_duplicates("time_ms")
    df["source_file"] = path.name
    return add_magnitudes(df[RAW_COLUMNS + ["source_file"]])


def matching_events_path(raw_path: Path) -> Path:
    candidate = raw_path.with_name(f"{raw_path.stem}_events.csv")
    if candidate.exists():
        return candidate
    alt = raw_path.with_name(raw_path.name.replace(".csv", "_events.csv"))
    if alt.exists():
        return alt
    raise FileNotFoundError(f"No events CSV found for {raw_path}. Expected {candidate.name}")


def load_events(path: Path) -> np.ndarray:
    events = pd.read_csv(path)
    if "elapsed_s" not in events.columns:
        raise ValueError(f"{path} missing elapsed_s")
    values = pd.to_numeric(events["elapsed_s"], errors="coerce").dropna().to_numpy(dtype=float)
    return np.sort(values)


def manual_cpm_for_window(events_s: np.ndarray, start_s: float, end_s: float, padding_s: float) -> float | None:
    in_window = events_s[(events_s >= start_s - padding_s) & (events_s <= end_s + padding_s)]
    if len(in_window) < 2:
        return None
    intervals = np.diff(in_window)
    intervals = intervals[(intervals >= 0.2) & (intervals <= 2.5)]
    if len(intervals) == 0:
        return None
    return float(60.0 / np.mean(intervals))


def build_training_table(args) -> pd.DataFrame:
    raw_files = sorted(path for path in args.data_dir.glob("*.csv") if not path.name.endswith("_events.csv"))
    if not raw_files:
        event_files = sorted(args.data_dir.glob("*_events.csv"))
        event_names = ", ".join(path.name for path in event_files) if event_files else "none"
        raise FileNotFoundError(
            f"No raw calibration CSV files found in {args.data_dir}. "
            f"Found event files: {event_names}. "
            "Each calibration clip needs both rate_calibration_NN.csv and "
            "rate_calibration_NN_events.csv. Re-run collect_rate_calibration.py "
            "with both --raw-out and --events-out."
        )

    rule_args = SimpleNamespace(
        counter_min_peak_distance=args.counter_min_peak_distance,
        min_channel_std=args.min_channel_std,
        min_peaks=args.min_peaks,
        min_cpm=args.min_cpm,
        max_cpm=args.max_cpm,
        max_regularity=args.max_regularity,
        side_ratio=args.side_ratio,
    )

    rows = []
    window_samples = int(round(args.window_seconds * args.sample_rate))
    step_samples = int(round(args.step_seconds * args.sample_rate))

    for raw_path in raw_files:
        raw_df = load_raw_csv(raw_path)
        events_s = load_events(matching_events_path(raw_path))
        if len(raw_df) < window_samples:
            continue
        start_time_ms = float(raw_df["time_ms"].iloc[0])

        for start in range(0, len(raw_df) - window_samples + 1, step_samples):
            window = raw_df.iloc[start : start + window_samples].copy()
            start_s = (float(window["time_ms"].iloc[0]) - start_time_ms) / 1000.0
            end_s = (float(window["time_ms"].iloc[-1]) - start_time_ms) / 1000.0
            target_cpm = manual_cpm_for_window(events_s, start_s, end_s, args.event_padding_seconds)
            if target_cpm is None:
                continue

            result = classify_dual_window(window, args.sample_rate, rule_args)
            features = extract_cpm_calibration_features(window, args.sample_rate, rule_args, result)
            features["target_cpm"] = target_cpm
            features["source_file"] = raw_path.name
            features["start_s"] = start_s
            features["end_s"] = end_s
            rows.append(features)

    if not rows:
        raise ValueError("No calibration windows generated. Press Space more often during chewing clips.")
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train CPM calibrator from Space-key chew marks.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--model-out", type=Path, default=DEFAULT_MODEL_OUT)
    parser.add_argument("--features-out", type=Path, default=DEFAULT_FEATURES_OUT)
    parser.add_argument("--sample-rate", type=int, default=100)
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--step-seconds", type=float, default=0.5)
    parser.add_argument("--event-padding-seconds", type=float, default=0.25)
    parser.add_argument("--counter-min-peak-distance", type=float, default=0.45)
    parser.add_argument("--min-peaks", type=int, default=1)
    parser.add_argument("--min-cpm", type=float, default=30.0)
    parser.add_argument("--max-cpm", type=float, default=180.0)
    parser.add_argument("--min-channel-std", type=float, default=0.015)
    parser.add_argument("--max-regularity", type=float, default=1.25)
    parser.add_argument("--side-ratio", type=float, default=1.12)
    parser.add_argument("--n-estimators", type=int, default=200)
    args = parser.parse_args()

    table = build_training_table(args)
    print(f"Calibration windows: {len(table)}")
    print(f"Manual CPM range: {table['target_cpm'].min():.1f} - {table['target_cpm'].max():.1f}")
    print(f"Raw CPM MAE before calibration: {mean_absolute_error(table['target_cpm'], table['raw_cpm']):.2f}")

    drop_cols = ["target_cpm", "source_file", "start_s", "end_s"]
    X = table.drop(columns=drop_cols)
    y = table["target_cpm"]

    if len(table) >= 8:
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, random_state=42)
    else:
        X_train, X_test, y_train, y_test = X, X, y, y

    model = RandomForestRegressor(
        n_estimators=args.n_estimators,
        max_depth=8,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)
    pred = model.predict(X_test)
    print(f"Calibrated CPM MAE: {mean_absolute_error(y_test, pred):.2f}")
    if len(y_test) >= 2:
        print(f"Calibrated CPM R2: {r2_score(y_test, pred):.3f}")

    final_model = RandomForestRegressor(
        n_estimators=args.n_estimators,
        max_depth=8,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
    )
    final_model.fit(X, y)

    args.features_out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.features_out, index=False)

    bundle = {
        "model": final_model,
        "feature_columns": list(X.columns),
        "sample_rate_hz": args.sample_rate,
        "window_seconds": args.window_seconds,
        "max_cpm": args.max_cpm,
        "purpose": "dual_mpu6050_cpm_calibrator",
    }
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, args.model_out)
    print(f"Saved CPM calibrator to {args.model_out}")
    print(f"Saved calibration features to {args.features_out}")


if __name__ == "__main__":
    main()
