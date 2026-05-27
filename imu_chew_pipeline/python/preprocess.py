import argparse
import re
from pathlib import Path
from typing import Iterable, List, Optional

import numpy as np
import pandas as pd

from config import CHEWING_LABELS, NON_CHEWING_LABELS, PROCESSED_DIR, RAW_COLUMNS, RAW_DIR, SENSOR_COLUMNS


try:
    from scipy.signal import butter, filtfilt
except ImportError:  # pragma: no cover
    butter = None
    filtfilt = None


def infer_label_from_filename(path: Path) -> str:
    stem = path.stem.lower()
    tokens = re.split(r"[_\-\s]+", stem)
    joined = "_".join(tokens)

    for label in sorted(CHEWING_LABELS | NON_CHEWING_LABELS, key=len, reverse=True):
        if label in joined or label in tokens:
            return "chewing" if label in CHEWING_LABELS else label

    if "chew" in joined:
        return "chewing"
    if "talk" in joined:
        return "talking"
    if "head" in joined:
        return "head_movement"
    return "unknown"


def to_binary_label(label: str) -> str:
    normalized = str(label).strip().lower()
    if normalized in CHEWING_LABELS or "chew" in normalized and "non" not in normalized:
        return "chewing"
    return "non_chewing"


def read_imu_csv(path: Path, label_from_name: bool = True) -> pd.DataFrame:
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

    keep_cols = RAW_COLUMNS + (["label"] if "label" in df.columns else [])
    df = df[keep_cols].copy()
    for col in RAW_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=RAW_COLUMNS)
    df = df.sort_values("time_ms").drop_duplicates(subset=["time_ms"])

    if "label" not in df.columns or label_from_name:
        df["label"] = infer_label_from_filename(path)
    else:
        df["label"] = df["label"].fillna(infer_label_from_filename(path))

    df["source_file"] = path.name
    return df.reset_index(drop=True)


def add_magnitude_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["acc_mag"] = np.sqrt(out["ax"] ** 2 + out["ay"] ** 2 + out["az"] ** 2)
    out["gyro_mag"] = np.sqrt(out["gx"] ** 2 + out["gy"] ** 2 + out["gz"] ** 2)
    return out


def resample_to_rate(df: pd.DataFrame, sample_rate_hz: int = 100) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    start_ms = float(df["time_ms"].iloc[0])
    end_ms = float(df["time_ms"].iloc[-1])
    step_ms = 1000.0 / sample_rate_hz
    new_time = np.arange(start_ms, end_ms + 0.5 * step_ms, step_ms)

    out = pd.DataFrame({"time_ms": new_time})
    for col in SENSOR_COLUMNS:
        out[col] = np.interp(new_time, df["time_ms"].to_numpy(), df[col].to_numpy())

    original_time = df["time_ms"].to_numpy()
    nearest_idx = np.searchsorted(original_time, new_time, side="left")
    nearest_idx = np.clip(nearest_idx, 0, len(df) - 1)
    previous_idx = np.clip(nearest_idx - 1, 0, len(df) - 1)
    use_previous = np.abs(new_time - original_time[previous_idx]) < np.abs(new_time - original_time[nearest_idx])
    label_idx = np.where(use_previous, previous_idx, nearest_idx)

    out["label"] = df["label"].to_numpy()[label_idx]
    out["source_file"] = df["source_file"].to_numpy()[label_idx]
    return add_magnitude_columns(out)


def apply_filter(
    df: pd.DataFrame,
    sample_rate_hz: int = 100,
    filter_type: Optional[str] = None,
    low_hz: float = 0.5,
    high_hz: float = 3.0,
) -> pd.DataFrame:
    if filter_type is None or filter_type == "none":
        return df
    if butter is None or filtfilt is None:
        print("SciPy is not installed; skipping filter.")
        return df

    out = df.copy()
    nyq = sample_rate_hz / 2.0
    if filter_type == "lowpass":
        b, a = butter(3, high_hz / nyq, btype="lowpass")
    elif filter_type == "bandpass":
        b, a = butter(3, [low_hz / nyq, high_hz / nyq], btype="bandpass")
    else:
        raise ValueError("filter_type must be none, lowpass, or bandpass")

    for col in SENSOR_COLUMNS:
        if len(out[col]) > 20:
            out[col] = filtfilt(b, a, out[col].to_numpy())
    return add_magnitude_columns(out)


def load_dataset(
    paths: Iterable[Path],
    sample_rate_hz: int = 100,
    filter_type: Optional[str] = None,
    label_from_name: bool = False,
) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for path in paths:
        df = read_imu_csv(path, label_from_name=label_from_name)
        df = resample_to_rate(df, sample_rate_hz=sample_rate_hz)
        df = apply_filter(df, sample_rate_hz=sample_rate_hz, filter_type=filter_type)
        frames.append(df)
    if not frames:
        raise FileNotFoundError("No CSV files found.")
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean and resample raw IMU CSV files.")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--out-dir", type=Path, default=PROCESSED_DIR / "cleaned")
    parser.add_argument("--sample-rate", type=int, default=100)
    parser.add_argument("--filter", choices=["none", "lowpass", "bandpass"], default="none")
    parser.add_argument("--label-from-name", action="store_true")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(args.raw_dir.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No CSV files in {args.raw_dir}")

    for path in files:
        df = read_imu_csv(path, label_from_name=args.label_from_name)
        df = resample_to_rate(df, sample_rate_hz=args.sample_rate)
        df = apply_filter(df, sample_rate_hz=args.sample_rate, filter_type=args.filter)
        out_path = args.out_dir / path.name
        df.to_csv(out_path, index=False)
        print(f"Saved {out_path} ({len(df)} rows)")


if __name__ == "__main__":
    main()
