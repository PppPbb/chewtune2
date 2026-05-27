import argparse
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import GroupShuffleSplit, train_test_split

from config import DEFAULT_MODEL_PATH, PROCESSED_DIR, RAW_DIR, SAMPLE_RATE_HZ, STEP_SECONDS, WINDOW_SECONDS
from feature_extraction import make_feature_table, split_features_labels
from preprocess import load_dataset, to_binary_label


def print_feature_importance(model: RandomForestClassifier, feature_names, top_k: int = 30) -> None:
    importance = pd.DataFrame(
        {"feature": feature_names, "importance": model.feature_importances_}
    ).sort_values("importance", ascending=False)
    print("\nTop feature importance:")
    print(importance.head(top_k).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Random Forest chewing classifier.")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--model-out", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--features-out", type=Path, default=PROCESSED_DIR / "features.csv")
    parser.add_argument("--sample-rate", type=int, default=SAMPLE_RATE_HZ)
    parser.add_argument("--window-seconds", type=float, default=WINDOW_SECONDS)
    parser.add_argument("--step-seconds", type=float, default=STEP_SECONDS)
    parser.add_argument("--filter", choices=["none", "lowpass", "bandpass"], default="none")
    parser.add_argument("--label-from-name", action="store_true")
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--test-size", type=float, default=0.25)
    args = parser.parse_args()

    csv_files = sorted(args.raw_dir.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {args.raw_dir}")

    print(f"Loading {len(csv_files)} CSV files from {args.raw_dir}")
    data = load_dataset(
        csv_files,
        sample_rate_hz=args.sample_rate,
        filter_type=args.filter,
        label_from_name=args.label_from_name,
    )
    data["label"] = data["label"].map(to_binary_label)

    print("Extracting sliding-window features...")
    feature_df = make_feature_table(
        data,
        sample_rate_hz=args.sample_rate,
        window_seconds=args.window_seconds,
        step_seconds=args.step_seconds,
    )
    feature_df["label"] = feature_df["label"].map(to_binary_label)

    args.features_out.parent.mkdir(parents=True, exist_ok=True)
    feature_df.to_csv(args.features_out, index=False)
    print(f"Saved features to {args.features_out} ({len(feature_df)} windows)")
    print("\nWindow label counts:")
    print(feature_df["label"].value_counts().to_string())

    X, y = split_features_labels(feature_df)
    groups = feature_df["source_file"]

    if len(groups.unique()) >= 3:
        splitter = GroupShuffleSplit(n_splits=1, test_size=args.test_size, random_state=42)
        train_idx, test_idx = next(splitter.split(X, y, groups=groups))
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
    else:
        X_train, X_test, y_train, y_test = train_test_split(
            X,
            y,
            test_size=args.test_size,
            stratify=y if y.nunique() > 1 else None,
            random_state=42,
        )

    model = RandomForestClassifier(
        n_estimators=args.n_estimators,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)

    print("\nMetrics:")
    print(f"Accuracy: {accuracy_score(y_test, y_pred):.4f}")
    print(classification_report(y_test, y_pred, digits=4))
    print("Confusion matrix [rows=true, cols=pred]:")
    labels = ["chewing", "non_chewing"]
    print(pd.DataFrame(confusion_matrix(y_test, y_pred, labels=labels), index=labels, columns=labels))
    print_feature_importance(model, X.columns)

    final_model = RandomForestClassifier(
        n_estimators=args.n_estimators,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    final_model.fit(X, y)

    bundle = {
        "model": final_model,
        "feature_columns": list(X.columns),
        "sample_rate_hz": args.sample_rate,
        "window_seconds": args.window_seconds,
        "step_seconds": args.step_seconds,
        "filter": args.filter,
    }
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, args.model_out)
    print(f"\nSaved model bundle to {args.model_out}")


if __name__ == "__main__":
    main()
