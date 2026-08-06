import argparse
import sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, recall_score
from sklearn.model_selection import train_test_split


THIS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = THIS_DIR.parents[0]
DEFAULT_DATA_DIR = PROJECT_DIR / "data" / "state"
DEFAULT_MODEL_PATH = PROJECT_DIR / "models" / "chewing_state_rf.pkl"
DEFAULT_REPORT_OUT = PROJECT_DIR / "reports" / "chewing_state_rf_eval.txt"

sys.path.insert(0, str(THIS_DIR))
from train_chewing_state_rf import build_feature_table  # noqa: E402


LABELS = ["chewing", "non_chewing"]
DROP_COLS = ["label", "source_file", "start_time_ms", "end_time_ms"]


def print_metrics(y_true, y_pred, title: str) -> str:
    lines = []
    accuracy = accuracy_score(y_true, y_pred)
    chewing_recall = recall_score(y_true, y_pred, labels=LABELS, pos_label="chewing")
    non_chewing_recall = recall_score(y_true, y_pred, labels=LABELS, pos_label="non_chewing")
    macro_recall = recall_score(y_true, y_pred, labels=LABELS, average="macro")
    matrix = pd.DataFrame(confusion_matrix(y_true, y_pred, labels=LABELS), index=LABELS, columns=LABELS)

    lines.append(f"\n{title}")
    lines.append(f"accuracy: {accuracy:.4f}")
    lines.append(f"recall_chewing: {chewing_recall:.4f}")
    lines.append(f"recall_non_chewing: {non_chewing_recall:.4f}")
    lines.append(f"recall_macro: {macro_recall:.4f}")
    lines.append("\nClassification report:")
    lines.append(classification_report(y_true, y_pred, labels=LABELS, digits=4))
    lines.append("Confusion matrix [rows=true, cols=pred]:")
    lines.append(matrix.to_string())
    return "\n".join(lines)


def evaluate_saved_model(feature_df: pd.DataFrame, model_path: Path) -> str:
    bundle = joblib.load(model_path)
    feature_columns = bundle["feature_columns"]
    missing = [col for col in feature_columns if col not in feature_df.columns]
    if missing:
        raise ValueError(f"Feature table is missing model columns: {missing[:10]}")

    X = feature_df[feature_columns]
    y_true = feature_df["label"]
    model = bundle["model"]

    if hasattr(model, "predict_proba"):
        probabilities = model.predict_proba(X)
        class_names = [str(item) for item in model.classes_]
        chewing_idx = class_names.index("chewing")
        threshold = float(bundle.get("threshold", 0.60))
        y_pred = ["chewing" if prob[chewing_idx] >= threshold else "non_chewing" for prob in probabilities]
    else:
        y_pred = model.predict(X)

    return print_metrics(y_true, y_pred, f"Saved model evaluation: {model_path}")


def evaluate_random_holdout(feature_df: pd.DataFrame, args) -> str:
    X = feature_df.drop(columns=[col for col in DROP_COLS if col in feature_df.columns])
    y = feature_df["label"]
    stratify = y if y.value_counts().min() >= 2 else None
    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=args.test_size,
        random_state=args.random_state,
        stratify=stratify,
    )
    model = RandomForestClassifier(
        n_estimators=args.n_estimators,
        max_depth=10,
        min_samples_leaf=2,
        class_weight={"chewing": 1.0, "non_chewing": 1.5},
        random_state=args.random_state,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)
    return print_metrics(y_test, model.predict(X_test), "Random window holdout evaluation")


def evaluate_leave_one_session_out(feature_df: pd.DataFrame, args) -> str:
    source_files = sorted(feature_df["source_file"].unique())
    if len(source_files) < 2:
        return "\nLeave-one-session-out evaluation skipped: need at least 2 source files."

    all_true = []
    all_pred = []
    section_lines = ["\nLeave-one-session-out evaluation"]
    X_all = feature_df.drop(columns=[col for col in DROP_COLS if col in feature_df.columns])

    for source_file in source_files:
        test_mask = feature_df["source_file"] == source_file
        train_mask = ~test_mask
        y_train = feature_df.loc[train_mask, "label"]
        y_test = feature_df.loc[test_mask, "label"]
        if y_train.nunique() < 2 or y_test.nunique() < 1:
            section_lines.append(f"{source_file}: skipped, insufficient label variety.")
            continue

        model = RandomForestClassifier(
            n_estimators=args.n_estimators,
            max_depth=10,
            min_samples_leaf=2,
            class_weight={"chewing": 1.0, "non_chewing": 1.5},
            random_state=args.random_state,
            n_jobs=-1,
        )
        model.fit(X_all.loc[train_mask], y_train)
        y_pred = model.predict(X_all.loc[test_mask])
        all_true.extend(y_test.tolist())
        all_pred.extend(y_pred.tolist())
        section_lines.append(
            f"{source_file}: windows={len(y_test)}, accuracy={accuracy_score(y_test, y_pred):.4f}, "
            f"chewing_recall={recall_score(y_test, y_pred, labels=LABELS, pos_label='chewing', zero_division=0):.4f}"
        )

    if not all_true:
        section_lines.append("No valid leave-one-session-out folds.")
        return "\n".join(section_lines)

    section_lines.append(print_metrics(all_true, all_pred, "Leave-one-session-out aggregate"))
    return "\n".join(section_lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the Random Forest chewing-state model.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT)
    parser.add_argument("--sample-rate", type=int, default=100)
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--step-seconds", type=float, default=0.5)
    parser.add_argument("--skip-initial-seconds", type=float, default=0.0)
    parser.add_argument("--min-label-fraction", type=float, default=0.7)
    parser.add_argument("--exclude-files", default="")
    parser.add_argument("--n-estimators", type=int, default=300)
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--mode", choices=["saved", "holdout", "loso", "all"], default="all")
    args = parser.parse_args()

    feature_df = build_feature_table(args)
    if feature_df["label"].nunique() < 2:
        raise ValueError("Need both chewing and non_chewing windows for evaluation.")

    output = [
        "Chewing-state RF evaluation",
        f"Data dir: {args.data_dir}",
        "\nWindow label counts:",
        feature_df["label"].value_counts().to_string(),
        "\nWindow counts by source:",
        feature_df.groupby(["source_file", "label"]).size().unstack(fill_value=0).to_string(),
    ]

    if args.mode in {"saved", "all"}:
        if args.model.exists():
            output.append(evaluate_saved_model(feature_df, args.model))
        else:
            output.append(f"\nSaved model evaluation skipped: model not found at {args.model}")

    if args.mode in {"holdout", "all"}:
        output.append(evaluate_random_holdout(feature_df, args))

    if args.mode in {"loso", "all"}:
        output.append(evaluate_leave_one_session_out(feature_df, args))

    text = "\n".join(output)
    print(text)
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(text, encoding="utf-8")
    print(f"\nSaved report to {args.report_out}")


if __name__ == "__main__":
    main()
