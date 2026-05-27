# IMU Chewing Detection Pipeline

This folder contains a modular pipeline for six-axis IMU chewing detection.

## Layout

```text
arduino/
  xiao_ybmra02_i2c_100hz/xiao_ybmra02_i2c_100hz.ino
python/
  collect_data.py
  plot_realtime.py
  preprocess.py
  feature_extraction.py
  train_random_forest.py
  realtime_inference.py
  chewing_counter.py
data/
  raw/
  processed/
models/
  random_forest.pkl
```

## Install Python Dependencies

Python 3.9 is recommended.

```powershell
pip install -r requirements.txt
```

## Collect Data

Put the Arduino board on `COM4` or pass another port.

```powershell
cd C:\Users\YUN\Desktop\chewtune\imu_chew_pipeline
python python\collect_data.py --port COM4 --label chewing --output data\raw\chew_normal_01.csv
```

While the plot is open, type commands in the terminal:

```text
label talking
label chewing
stop
```

## Train

```powershell
python python\train_random_forest.py --raw-dir data\raw --model-out models\random_forest.pkl
```

## Real-Time Inference

```powershell
python python\realtime_inference.py --port COM4 --model models\random_forest.pkl
```

The model classifies `chewing` / `non_chewing`. Chewing count and rate are estimated only when the window is classified as chewing.

