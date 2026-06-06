import argparse
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


THIS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = THIS_DIR.parents[0]
DEFAULT_DATA_DIR = PROJECT_DIR / "data" / "side"
DEFAULT_MODEL_OUT = PROJECT_DIR / "models" / "dual_side_cnn.pkl"
DEFAULT_METADATA_OUT = PROJECT_DIR / "models" / "dual_side_cnn_meta.pkl"

sys.path.insert(0, str(THIS_DIR))
from realtime_dual_mpu6050_detection import RAW_COLUMNS, SEQUENCE_COLUMNS, add_magnitudes  # noqa: E402


def normalize_label(value: str) -> str:
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if "left" in text or "zuo" in text:
        return "left_chewing"
    if "right" in text or "you" in text:
        return "right_chewing"
    return text


def infer_label(path: Path, df: pd.DataFrame) -> str:
    for col in ("activity_label", "true_label", "label", "chewing_side"):
        if col not in df.columns:
            continue
        labels = [normalize_label(value) for value in df[col].dropna().unique()]
        labels = [label for label in labels if label in {"left_chewing", "right_chewing"}]
        if labels:
            return labels[0]

    label = normalize_label(path.stem)
    if label in {"left_chewing", "right_chewing"}:
        return label
    raise ValueError(
        f"Cannot infer left/right label for {path}. "
        "Record with --activity-label left_chewing or --activity-label right_chewing."
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
    if "state" in df.columns:
        out["state"] = df["state"].astype(str)
    return add_magnitudes(out)


def iter_windows(
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
        if len(group) < window_samples:
            continue
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


def load_dataset(args):
    csv_files = sorted(args.data_dir.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {args.data_dir}")

    frames = [read_dual_csv(path) for path in csv_files]
    data = pd.concat(frames, ignore_index=True)

    x_rows = []
    y_rows = []
    source_rows = []
    for window in iter_windows(
        data,
        args.sample_rate,
        args.window_seconds,
        args.step_seconds,
        args.skip_initial_seconds,
        args.min_chewing_fraction,
    ):
        x_rows.append(window[SEQUENCE_COLUMNS].to_numpy(dtype=np.float32))
        y_rows.append(str(window["label"].iloc[0]))
        source_rows.append(str(window["source_file"].iloc[0]))

    if not x_rows:
        raise ValueError("No training windows generated. Record longer clips or relax filters.")

    x = np.stack(x_rows, axis=0)
    y_labels = np.array(y_rows)
    sources = np.array(source_rows)
    return x, y_labels, sources


def build_model(window_samples: int, feature_count: int):
    from tensorflow.keras import layers, models, regularizers

    inputs = layers.Input(shape=(window_samples, feature_count))
    x = layers.Conv1D(32, 7, padding="same", activation="relu")(inputs)
    x = layers.BatchNormalization()(x)
    x = layers.MaxPooling1D(2)(x)
    x = layers.Conv1D(64, 5, padding="same", activation="relu")(x)
    x = layers.BatchNormalization()(x)
    x = layers.MaxPooling1D(2)(x)
    x = layers.Conv1D(96, 3, padding="same", activation="relu")(x)
    x = layers.GlobalAveragePooling1D()(x)
    x = layers.Dropout(0.25)(x)
    x = layers.Dense(32, activation="relu", kernel_regularizer=regularizers.l2(1e-4))(x)
    outputs = layers.Dense(1, activation="sigmoid")(x)
    model = models.Model(inputs, outputs)
    model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
    return model


def make_random_kernels(feature_count: int, n_kernels: int, random_state: int) -> list[dict]:
    rng = np.random.default_rng(random_state)
    lengths = np.array([5, 7, 9, 13, 17, 25])
    kernels = []
    for _ in range(n_kernels):
        length = int(rng.choice(lengths))
        weights = rng.normal(0.0, 1.0, size=(length, feature_count))
        weights = weights - weights.mean(axis=0, keepdims=True)
        norm = np.sqrt(np.sum(weights ** 2))
        if norm > 0:
            weights = weights / norm
        kernels.append({
            "weights": weights.astype(np.float32),
            "bias": float(rng.uniform(-0.5, 0.5)),
        })
    return kernels


def random_conv_features_one(sequence: np.ndarray, kernels: list[dict]) -> np.ndarray:
    features = []
    for kernel in kernels:
        weights = np.asarray(kernel["weights"], dtype=np.float32)
        bias = float(kernel["bias"])
        length = weights.shape[0]
        if sequence.shape[0] < length:
            conv = np.array([0.0], dtype=np.float32)
        else:
            conv = np.array([
                np.sum(sequence[start : start + length] * weights) + bias
                for start in range(sequence.shape[0] - length + 1)
            ], dtype=np.float32)
        features.extend([
            float(np.max(conv)),
            float(np.mean(conv > 0.0)),
            float(np.mean(conv)),
            float(np.std(conv)),
        ])
    return np.asarray(features, dtype=np.float32)


def random_conv_features_batch(x: np.ndarray, kernels: list[dict]) -> np.ndarray:
    return np.stack([random_conv_features_one(row, kernels) for row in x], axis=0)


def train_sklearn_conv(args, x: np.ndarray, y: np.ndarray, y_labels: np.ndarray, sources: np.ndarray) -> None:
    stratify = y if min(np.bincount(y.astype(int))) >= 2 else None
    x_train, x_test, y_train, y_test = train_test_split(
        x,
        y,
        test_size=0.25,
        random_state=42,
        stratify=stratify,
    )

    mean = x_train.mean(axis=(0, 1), keepdims=True)
    scale = x_train.std(axis=(0, 1), keepdims=True)
    scale = np.where(scale < 1e-6, 1.0, scale)
    x_train = (x_train - mean) / scale
    x_test = (x_test - mean) / scale

    kernels = make_random_kernels(x.shape[2], args.n_kernels, args.random_state)
    x_train_features = random_conv_features_batch(x_train, kernels)
    x_test_features = random_conv_features_batch(x_test, kernels)

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=5000, class_weight="balanced", random_state=42),
    )
    model.fit(x_train_features, y_train)
    y_pred = model.predict(x_test_features)
    probabilities = model.predict_proba(x_test_features)

    target_names = ["left_chewing", "right_chewing"]
    print("\nMetrics:")
    print(classification_report(y_test.astype(int), y_pred.astype(int), target_names=target_names, digits=4))
    print("Confusion matrix [rows=true, cols=pred]:")
    print(pd.DataFrame(confusion_matrix(y_test.astype(int), y_pred.astype(int)), index=target_names, columns=target_names))

    bundle = {
        "kind": "random_conv_cnn",
        "model": model,
        "feature_columns": SEQUENCE_COLUMNS,
        "mean": mean.reshape(-1),
        "scale": scale.reshape(-1),
        "classes": ["left_chewing", "right_chewing"],
        "kernels": kernels,
        "n_kernels": args.n_kernels,
        "sample_rate_hz": args.sample_rate,
        "window_seconds": args.window_seconds,
        "window_samples": int(round(args.sample_rate * args.window_seconds)),
        "step_seconds": args.step_seconds,
        "sources": sorted(set(sources.tolist())),
        "test_probabilities": probabilities,
    }
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, args.model_out)
    print(f"\nSaved TensorFlow-free CNN-style model to {args.model_out}")


def train_tensorflow_cnn(args, x: np.ndarray, y: np.ndarray, sources: np.ndarray) -> None:
    try:
        from tensorflow.keras import callbacks
    except Exception as exc:
        raise ImportError(
            "TensorFlow backend failed to load. Use the default sklearn-conv backend, "
            "or fix TensorFlow in this Python environment."
        ) from exc

    stratify = y if min(np.bincount(y.astype(int))) >= 2 else None
    x_train, x_test, y_train, y_test = train_test_split(
        x,
        y,
        test_size=0.25,
        random_state=42,
        stratify=stratify,
    )

    mean = x_train.mean(axis=(0, 1), keepdims=True)
    scale = x_train.std(axis=(0, 1), keepdims=True)
    scale = np.where(scale < 1e-6, 1.0, scale)
    x_train = (x_train - mean) / scale
    x_test = (x_test - mean) / scale

    model = build_model(x.shape[1], x.shape[2])
    history = model.fit(
        x_train,
        y_train,
        validation_data=(x_test, y_test),
        epochs=args.epochs,
        batch_size=args.batch_size,
        callbacks=[
            callbacks.EarlyStopping(monitor="val_loss", patience=10, restore_best_weights=True),
            callbacks.ReduceLROnPlateau(monitor="val_loss", patience=5, factor=0.5),
        ],
        verbose=2,
    )

    prob_right = model.predict(x_test, verbose=0).reshape(-1)
    y_pred = (prob_right >= 0.5).astype(int)
    target_names = ["left_chewing", "right_chewing"]
    print("\nMetrics:")
    print(classification_report(y_test.astype(int), y_pred, target_names=target_names, digits=4))
    print("Confusion matrix [rows=true, cols=pred]:")
    print(pd.DataFrame(confusion_matrix(y_test.astype(int), y_pred), index=target_names, columns=target_names))

    keras_out = args.model_out
    if keras_out.suffix.lower() != ".keras":
        keras_out = keras_out.with_suffix(".keras")
    keras_out.parent.mkdir(parents=True, exist_ok=True)
    model.save(keras_out)

    metadata = {
        "kind": "cnn",
        "feature_columns": SEQUENCE_COLUMNS,
        "mean": mean.reshape(-1),
        "scale": scale.reshape(-1),
        "classes": ["left_chewing", "right_chewing"],
        "sample_rate_hz": args.sample_rate,
        "window_seconds": args.window_seconds,
        "window_samples": int(round(args.sample_rate * args.window_seconds)),
        "step_seconds": args.step_seconds,
        "history": history.history,
        "sources": sorted(set(sources.tolist())),
    }
    args.metadata_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(metadata, args.metadata_out)
    print(f"\nSaved TensorFlow CNN model to {keras_out}")
    print(f"Saved TensorFlow CNN metadata to {args.metadata_out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a CNN left/right chewing model for dual MPU6050 data.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--model-out", type=Path, default=DEFAULT_MODEL_OUT)
    parser.add_argument("--metadata-out", type=Path, default=DEFAULT_METADATA_OUT)
    parser.add_argument("--sample-rate", type=int, default=100)
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--step-seconds", type=float, default=0.5)
    parser.add_argument("--skip-initial-seconds", type=float, default=3.0)
    parser.add_argument("--min-chewing-fraction", type=float, default=0.5)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--backend", choices=["sklearn-conv", "tensorflow"], default="sklearn-conv")
    parser.add_argument("--n-kernels", type=int, default=256)
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()

    x, y_labels, sources = load_dataset(args)
    print("Window label counts:")
    print(pd.Series(y_labels).value_counts().to_string())

    classes = ["left_chewing", "right_chewing"]
    if set(classes) - set(y_labels):
        raise ValueError("Need both left_chewing and right_chewing clips.")
    y = np.array([1 if label == "right_chewing" else 0 for label in y_labels], dtype=np.float32)

    if args.backend == "tensorflow":
        train_tensorflow_cnn(args, x, y, sources)
    else:
        train_sklearn_conv(args, x, y, y_labels, sources)


if __name__ == "__main__":
    main()
