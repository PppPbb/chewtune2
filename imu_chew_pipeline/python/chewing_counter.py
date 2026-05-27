from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np


try:
    from scipy.signal import butter, filtfilt, find_peaks
except ImportError:  # pragma: no cover - fallback for minimal installs
    butter = None
    filtfilt = None
    find_peaks = None


@dataclass
class ChewingCountResult:
    chewing_count: int
    chewing_frequency_hz: float
    chewing_rate_per_min: float
    rate_label: str
    peak_indices: np.ndarray
    regularity: float = 0.0
    signal_std: float = 0.0
    filtered_signal: np.ndarray = None


def bandpass_filter(
    signal: np.ndarray,
    fs: float = 100.0,
    low_hz: float = 0.5,
    high_hz: float = 3.0,
    order: int = 3,
) -> np.ndarray:
    signal = np.asarray(signal, dtype=float)
    if signal.size < fs or butter is None or filtfilt is None:
        return signal - np.nanmean(signal)

    nyq = fs / 2.0
    b, a = butter(order, [low_hz / nyq, high_hz / nyq], btype="bandpass")
    return filtfilt(b, a, signal)


def classify_rate(rate_per_min: float) -> str:
    if rate_per_min < 60:
        return "slow"
    if rate_per_min <= 90:
        return "normal"
    return "fast"


def count_chews(
    signal: np.ndarray,
    fs: float = 100.0,
    low_hz: float = 0.5,
    high_hz: float = 3.0,
    min_peak_distance_s: float = 0.25,
) -> ChewingCountResult:
    clean = np.asarray(signal, dtype=float)
    clean = clean[np.isfinite(clean)]
    if clean.size == 0:
        return ChewingCountResult(0, 0.0, 0.0, "slow", np.array([], dtype=int), 0.0, 0.0, np.array([]))

    filtered = bandpass_filter(clean, fs=fs, low_hz=low_hz, high_hz=high_hz)
    duration_s = clean.size / fs

    if find_peaks is None:
        threshold = np.mean(filtered) + 0.5 * np.std(filtered)
        candidates = np.where(filtered > threshold)[0]
        peak_indices = candidates[:: max(1, int(min_peak_distance_s * fs))]
    else:
        distance = max(1, int(min_peak_distance_s * fs))
        prominence = max(1e-6, 0.35 * np.std(filtered))
        peak_indices, _ = find_peaks(filtered, distance=distance, prominence=prominence)

    peak_count = int(len(peak_indices))
    if peak_count >= 2:
        intervals_s = np.diff(peak_indices) / fs
        frequency_hz = 1.0 / float(np.mean(intervals_s))
        regularity = float(np.std(intervals_s) / np.mean(intervals_s)) if np.mean(intervals_s) > 0 else 1.0
    else:
        frequency_hz = peak_count / duration_s if duration_s > 0 else 0.0
        regularity = 1.0
    rate_per_min = frequency_hz * 60.0
    return ChewingCountResult(
        chewing_count=peak_count,
        chewing_frequency_hz=frequency_hz,
        chewing_rate_per_min=rate_per_min,
        rate_label=classify_rate(rate_per_min),
        peak_indices=peak_indices,
        regularity=regularity,
        signal_std=float(np.std(filtered)),
        filtered_signal=filtered,
    )


def choose_counter_signal(window: Dict[str, np.ndarray]) -> Tuple[str, np.ndarray]:
    preferred = ("gyro_mag", "gz", "gy", "gx", "acc_mag", "az", "ay", "ax")
    for name in preferred:
        if name in window:
            return name, np.asarray(window[name], dtype=float)
    raise ValueError("No usable signal channel found for chewing counter.")
