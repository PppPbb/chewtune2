from pathlib import Path


SAMPLE_RATE_HZ = 100
WINDOW_SECONDS = 2.0
STEP_SECONDS = 1.0
INFERENCE_STEP_SECONDS = 1.0

RAW_COLUMNS = [
    "time_ms",
    "ax",
    "ay",
    "az",
    "gx",
    "gy",
    "gz",
]

SENSOR_COLUMNS = ["ax", "ay", "az", "gx", "gy", "gz"]
DERIVED_COLUMNS = ["acc_mag", "gyro_mag"]
ALL_SIGNAL_COLUMNS = SENSOR_COLUMNS + DERIVED_COLUMNS

CHEWING_LABELS = {"chewing", "chew", "chew_normal", "chew_fast", "chew_slow"}
NON_CHEWING_LABELS = {
    "non_chewing",
    "talking",
    "talk",
    "still",
    "head_movement",
    "head_turn",
    "drinking",
    "swallowing",
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
MODEL_DIR = PROJECT_ROOT / "models"
DEFAULT_MODEL_PATH = MODEL_DIR / "random_forest.pkl"

