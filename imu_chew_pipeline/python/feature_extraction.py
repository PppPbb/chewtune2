from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from config import ALL_SIGNAL_COLUMNS


def sliding_windows(
    df: pd.DataFrame,
    sample_rate_hz: int = 100,
    window_seconds: float = 2.0,
    step_seconds: float = 1.0,
) -> List[pd.DataFrame]:
    window_samples = int(round(window_seconds * sample_rate_hz))
    step_samples = int(round(step_seconds * sample_rate_hz))
    windows: List[pd.DataFrame] = []

    for _, group in df.groupby("source_file", sort=False):
        group = group.reset_index(drop=True)
        if len(group) < window_samples:
            continue
        for start in range(0, len(group) - window_samples + 1, step_samples):
            windows.append(group.iloc[start : start + window_samples].copy())
    return windows


def _spectral_features(x: np.ndarray, fs: float) -> Dict[str, float]:
    x = np.asarray(x, dtype=float)
    x = x - np.mean(x)
    if x.size < 2 or np.allclose(x, 0):
        return {
            "dominant_frequency": 0.0,
            "dominant_frequency_power": 0.0,
            "band_energy_0_5_3hz": 0.0,
            "spectral_entropy": 0.0,
        }

    freqs = np.fft.rfftfreq(x.size, d=1.0 / fs)
    power = np.abs(np.fft.rfft(x)) ** 2
    power[0] = 0.0

    dom_idx = int(np.argmax(power))
    band_mask = (freqs >= 0.5) & (freqs <= 3.0)
    band_energy = float(np.sum(power[band_mask]))

    total = float(np.sum(power))
    if total <= 0:
        entropy = 0.0
    else:
        p = power / total
        p = p[p > 0]
        entropy = float(-np.sum(p * np.log2(p)) / np.log2(len(p))) if len(p) > 1 else 0.0

    return {
        "dominant_frequency": float(freqs[dom_idx]),
        "dominant_frequency_power": float(power[dom_idx]),
        "band_energy_0_5_3hz": band_energy,
        "spectral_entropy": entropy,
    }


def extract_window_features(window: pd.DataFrame, sample_rate_hz: int = 100) -> Dict[str, float]:
    features: Dict[str, float] = {}
    for col in ALL_SIGNAL_COLUMNS:
        x = window[col].to_numpy(dtype=float)
        features[f"{col}_mean"] = float(np.mean(x))
        features[f"{col}_std"] = float(np.std(x))
        features[f"{col}_min"] = float(np.min(x))
        features[f"{col}_max"] = float(np.max(x))
        features[f"{col}_range"] = float(np.max(x) - np.min(x))
        features[f"{col}_rms"] = float(np.sqrt(np.mean(x ** 2)))
        features[f"{col}_energy"] = float(np.sum(x ** 2) / len(x))
        features[f"{col}_median"] = float(np.median(x))

        spec = _spectral_features(x, fs=sample_rate_hz)
        for name, value in spec.items():
            features[f"{col}_{name}"] = value

    features["label"] = str(window["label"].mode().iloc[0])
    features["source_file"] = str(window["source_file"].iloc[0])
    features["start_time_ms"] = float(window["time_ms"].iloc[0])
    features["end_time_ms"] = float(window["time_ms"].iloc[-1])
    return features


def make_feature_table(
    df: pd.DataFrame,
    sample_rate_hz: int = 100,
    window_seconds: float = 2.0,
    step_seconds: float = 1.0,
) -> pd.DataFrame:
    rows = [
        extract_window_features(window, sample_rate_hz=sample_rate_hz)
        for window in sliding_windows(df, sample_rate_hz, window_seconds, step_seconds)
    ]
    if not rows:
        raise ValueError("No windows generated. Check file length and window settings.")
    return pd.DataFrame(rows)


def split_features_labels(feature_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    drop_cols = ["label", "source_file", "start_time_ms", "end_time_ms"]
    X = feature_df.drop(columns=[col for col in drop_cols if col in feature_df.columns])
    y = feature_df["label"]
    return X, y

