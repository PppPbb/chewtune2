import argparse
import sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split


THIS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = THIS_DIR.parents[0]
DEFAULT_DATA_DIR = PROJECT_DIR / "data" / "state"
DEFAULT_MODEL_OUT = PROJECT_DIR / "models" / "chewing_state_rf.pkl"
DEFAULT_FEATURES_OUT = PROJECT_DIR / "data" / "chewing_state_features.csv"

sys.path.insert(0, str(THIS_DIR))
from realtime_dual_mpu6050_detection import RAW_COLUMNS, add_magnitudes, extract_dual_features  # noqa: E402


NON_CHEWING_WORDS = {
    "non",
    "non_chewing",
    "nonchewing",
    "still",
    "quiet",
    "talk",
    "talking",
    "speak",
    "speaking",
    "head",
    "hand",
    "shake",
    "jitter",
}
CHEWING_WORDS = {"chew", "chewing", "left_chewing", "right_chewing"}


def normalize_label(value: str) -> str:
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if any(word in text for word in NON_CHEWING_WORDS):
        return "non_chewing"
    if any(word in text for word in CHEWING_WORDS):
        return "chewing"
    return text


def infer_label(path: Path, df: pd.DataFrame) -> str:
    for col in ("activity_label", "true_label", "label", "state"):
        if col not in df.columns:
            continue
        labels = [normalize_label(value) for value in df[col].dropna().unique()]
        labels = [label for label in labels if label in {"chewing", "non_chewing"}]
        if labels:
            return labels[0]

    label = normalize_label(path.stem)
    if label in {"chewing", "non_chewing"}:
        return label
    raise ValueError(
        f"Cannot infer chewing/non_chewing label for {path}. "
        "Record with --activity-label chewing or --activity-label non_chewing."
    )


def read_dual_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [col for col in RAW_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"{path} missing columns: {missing}")

    for col in RAW_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=RAW_COLUMNS).sort_values("time_ms").drop_duplicates("time_ms")

    out = df[RAW_COLUMNS].copy()
    out["label"] = infer_label(path, df)
    out["source_file"] = path.name
    return add_magnitudes(out)


def iter_windows(df: pd.DataFrame, sample_rate_hz: int, window_seconds: float, step_seconds: float, skip_initial_seconds: float):
    window_samples = int(round(window_seconds * sample_rate_hz))
    step_samples = int(round(step_seconds * sample_rate_hz))

    for _, group in df.groupby("source_file", sort=False):
        group = group.reset_index(drop=True)
        if len(group) < window_samples:
            continue
        start_time_ms = float(group["time_ms"].iloc[0])
        for start in range(0, len(group) - window_samples + 1, step_samples):
            window = group.iloc[start : start + window_samples].copy()
            elapsed_s = (float(window["time_ms"].iloc[0]) - start_time_ms) / 1000.0
            if elapsed_s < skip_initial_seconds:
                continue
            yield window


def build_feature_table(args) -> pd.DataFrame:
    csv_files = sorted(args.data_dir.glob("*.csv"))
    excluded = {name.strip() for name in args.exclude_files.split(",") if name.strip()}
    if excluded:
        csv_files = [path for path in csv_files if path.name not in excluded]
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {args.data_dir}")
    if excluded:
        print(f"Excluded files: {', '.join(sorted(excluded))}")
    print("Training files:")
    for path in csv_files:
        print(f"  {path.name}")

    frames = [read_dual_csv(path) for path in csv_files]
    data = pd.concat(frames, ignore_index=True)
    rows = []

    for window in iter_windows(data, args.sample_rate, args.window_seconds, args.step_seconds, args.skip_initial_seconds):
        row = extract_dual_features(window, args.sample_rate)
        row["label"] = str(window["label"].iloc[0])
        row["source_file"] = str(window["source_file"].iloc[0])
        row["start_time_ms"] = float(window["time_ms"].iloc[0])
        row["end_time_ms"] = float(window["time_ms"].iloc[-1])
        rows.append(row)

    if not rows:
        raise ValueError("No training windows generated. Record longer clips or reduce --skip-initial-seconds.")
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Random Forest chewing/non-chewing classifier for dual MPU6050.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--model-out", type=Path, default=DEFAULT_MODEL_OUT)
    parser.add_argument("--features-out", type=Path, default=DEFAULT_FEATURES_OUT)
    parser.add_argument("--sample-rate", type=int, default=100)
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--step-seconds", type=float, default=0.5)
    parser.add_argument("--skip-initial-seconds", type=float, default=3.0)
    parser.add_argument("--n-estimators", type=int, default=250)
    parser.add_argument("--exclude-files", default="", help="Comma-separated CSV filenames to exclude from training.")
    args = parser.parse_args()

    feature_df = build_feature_table(args)
    print("Window label counts:")
    print(feature_df["label"].value_counts().to_string())

    if feature_df["label"].nunique() < 2:
        raise ValueError("Need both chewing and non_chewing CSV clips.")

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
        max_depth=10,
        min_samples_leaf=2,
        class_weight={"chewing": 1.0, "non_chewing": 1.5},
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)

    print("\nMetrics:")
    print(classification_report(y_test, y_pred, digits=4))
    labels = ["chewing", "non_chewing"]
    print("Confusion matrix [rows=true, cols=pred]:")
    print(pd.DataFrame(confusion_matrix(y_test, y_pred, labels=labels), index=labels, columns=labels))

    final_model = RandomForestClassifier(
        n_estimators=args.n_estimators,
        max_depth=10,
        min_samples_leaf=2,
        class_weight={"chewing": 1.0, "non_chewing": 1.5},
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
        "threshold": 0.55,
        "purpose": "dual_mpu6050_chewing_state_rf",
    }
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, args.model_out)
    print(f"\nSaved chewing state model to {args.model_out}")
    print(f"Saved features to {args.features_out}")


if __name__ == "__main__":
    main()
