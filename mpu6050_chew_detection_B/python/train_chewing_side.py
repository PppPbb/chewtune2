import argparse
import sys
from pathlib import Path
from typing import Iterable

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split


THIS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = THIS_DIR.parents[1]
DEFAULT_PIPELINE_DIR = PROJECT_DIR / "imu_chew_pipeline"
DEFAULT_DATA_DIR = PROJECT_DIR / "mpu6050_chew_detection_B" / "data" / "side"
DEFAULT_MODEL_OUT = PROJECT_DIR / "mpu6050_chew_detection_B" / "models" / "chewing_side.pkl"
DEFAULT_FEATURES_OUT = PROJECT_DIR / "mpu6050_chew_detection_B" / "data" / "side_features.csv"

PIPELINE_PYTHON_DIR = DEFAULT_PIPELINE_DIR / "python"
if PIPELINE_PYTHON_DIR.exists():
    sys.path.insert(0, str(PIPELINE_PYTHON_DIR))

from config import RAW_COLUMNS, SAMPLE_RATE_HZ  # noqa: E402
from feature_extraction import extract_window_features  # noqa: E402
from preprocess import add_magnitude_columns  # noqa: E402


def normalize_side_label(label: str) -> str:
    text = str(label).strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return ""
    if "left" in text or "zuo" in text:
        return "left_chewing"
    if "right" in text or "you" in text:
        return "right_chewing"
    return text


def read_side_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    rename_map = {
        "ax_g": "ax",
        "ay_g": "ay",
        "az_g": "az",
        "gx_rad_s": "gx",
        "gy_rad_s": "gy",
        "gz_rad_s": "gz",
    }
    df = df.rename(columns=rename_map)

    missing = [col for col in RAW_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"{path} missing columns: {missing}")

    for col in RAW_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=RAW_COLUMNS).sort_values("time_ms").drop_duplicates("time_ms")

    label = ""
    for label_col in ("true_label", "label"):
        if label_col in df.columns:
            labels = [normalize_side_label(value) for value in df[label_col].dropna().unique()]
            labels = [value for value in labels if value]
            if labels:
                label = labels[0]
                break
    if not label:
        label = normalize_side_label(path.stem)
    if label not in {"left_chewing", "right_chewing"}:
        raise ValueError(
            f"Cannot infer left/right label for {path}. "
            "Use --activity-label left_chewing/right_chewing while recording, "
            "or include left/right in the filename."
        )

    keep_cols = RAW_COLUMNS.copy()
    for optional_col in ("state", "chewing_prob"):
        if optional_col in df.columns:
            keep_cols.append(optional_col)

    out = df[keep_cols].copy()
    out["label"] = label
    out["source_file"] = path.name
    return add_magnitude_columns(out)


def sliding_windows(
    df: pd.DataFrame,
    sample_rate_hz: int,
    window_seconds: float,
    step_seconds: float,
    skip_initial_seconds: float,
    min_chewing_fraction: float,
):
    window_samples = int(round(window_seconds * sample_rate_hz))
    step_samples = int(round(step_seconds * sample_rate_hz))
    for _, group in df.groupby("source_file", sort=False):
        group = group.reset_index(drop=True)
        start_time_ms = float(group["time_ms"].iloc[0])
        for start in range(0, len(group) - window_samples + 1, step_samples):
            window = group.iloc[start : start + window_samples].copy()
            elapsed_s = (float(window["time_ms"].iloc[0]) - start_time_ms) / 1000.0
            if elapsed_s < skip_initial_seconds:
                continue
            if "state" in window.columns:
                chewing_fraction = (window["state"].astype(str) == "chewing").mean()
                if chewing_fraction < min_chewing_fraction:
                    continue
            yield window


def build_feature_table(
    paths: Iterable[Path],
    sample_rate_hz: int,
    window_seconds: float,
    step_seconds: float,
    skip_initial_seconds: float,
    min_chewing_fraction: float,
) -> pd.DataFrame:
    frames = [read_side_csv(path) for path in paths]
    if not frames:
        raise FileNotFoundError("No CSV files found.")
    data = pd.concat(frames, ignore_index=True)

    rows = [
        extract_window_features(window, sample_rate_hz=sample_rate_hz)
        for window in sliding_windows(
            data,
            sample_rate_hz,
            window_seconds,
            step_seconds,
            skip_initial_seconds,
            min_chewing_fraction,
        )
    ]
    if not rows:
        raise ValueError("No windows generated. Record longer CSV clips or reduce --window-seconds.")
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train left/right chewing classifier for MPU6050 data.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--model-out", type=Path, default=DEFAULT_MODEL_OUT)
    parser.add_argument("--features-out", type=Path, default=DEFAULT_FEATURES_OUT)
    parser.add_argument("--sample-rate", type=int, default=SAMPLE_RATE_HZ)
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--step-seconds", type=float, default=0.5)
    parser.add_argument("--skip-initial-seconds", type=float, default=5.0)
    parser.add_argument("--min-chewing-fraction", type=float, default=0.7)
    parser.add_argument("--n-estimators", type=int, default=160)
    args = parser.parse_args()

    paths = sorted(args.data_dir.glob("*.csv"))
    if not paths:
        raise FileNotFoundError(f"No CSV files found in {args.data_dir}")

    feature_df = build_feature_table(
        paths,
        args.sample_rate,
        args.window_seconds,
        args.step_seconds,
        args.skip_initial_seconds,
        args.min_chewing_fraction,
    )
    print("Window label counts:")
    print(feature_df["label"].value_counts().to_string())

    if feature_df["label"].nunique() < 2:
        raise ValueError("Need both left_chewing and right_chewing CSV data to train side detection.")

    drop_cols = ["label", "source_file", "start_time_ms", "end_time_ms"]
    X = feature_df.drop(columns=[col for col in drop_cols if col in feature_df.columns])
    y = feature_df["label"]

    stratify = y if y.value_counts().min() >= 2 else None
    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.25,
        random_state=42,
        stratify=stratify,
    )

    model = RandomForestClassifier(
        n_estimators=args.n_estimators,
        max_depth=8,
        min_samples_leaf=2,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)

    print("\nMetrics:")
    print(classification_report(y_test, y_pred, digits=4))
    labels = ["left_chewing", "right_chewing"]
    print("Confusion matrix [rows=true, cols=pred]:")
    print(pd.DataFrame(confusion_matrix(y_test, y_pred, labels=labels), index=labels, columns=labels))

    final_model = RandomForestClassifier(
        n_estimators=args.n_estimators,
        max_depth=8,
        min_samples_leaf=2,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    final_model.fit(X, y)

    args.features_out.parent.mkdir(parents=True, exist_ok=True)
    feature_df.to_csv(args.features_out, index=False)

    bundle = {
        "model": final_model,
        "feature_columns": list(X.columns),
        "sample_rate_hz": args.sample_rate,
        "window_seconds": args.window_seconds,
        "step_seconds": args.step_seconds,
        "purpose": "chewing_side",
    }
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, args.model_out)
    print(f"\nSaved side model to {args.model_out}")
    print(f"Saved features to {args.features_out}")


if __name__ == "__main__":
    main()
