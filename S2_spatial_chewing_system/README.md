# Dual MPU6050 Chewing Side Detection

This version uses two MPU6050 sensors, one on the left side and one on the right side.

## Wiring For XIAO ESP32S3

Both sensors share the same I2C bus:

```text
Both MPU6050 VCC -> XIAO 3V3
Both MPU6050 GND -> XIAO GND
Both MPU6050 SDA -> XIAO D4 / GPIO5 / SDA
Both MPU6050 SCL -> XIAO D5 / GPIO6 / SCL
```

Important address wiring:

```text
Left  MPU6050 AD0 -> GND   address 0x68
Right MPU6050 AD0 -> 3V3   address 0x69
```

If both AD0 pins are left floating or tied to GND, both sensors will use `0x68` and cannot work together on the same I2C bus.

## Arduino

Open and upload:

```text
arduino/xiao_dual_mpu6050_i2c_100hz/xiao_dual_mpu6050_i2c_100hz.ino
```

Serial output:

```text
time_ms,l_ax,l_ay,l_az,l_gx,l_gy,l_gz,r_ax,r_ay,r_az,r_gx,r_gy,r_gz
```

## Python

Install dependencies:

```powershell
cd C:\Users\YUN\Desktop\chewtune\chewtune2\S2_spatial_chewing_system
pip install -r requirements.txt
```

Run:

```powershell
python python\realtime_dual_mpu6050_detection.py --port COM4
```

The plot shows left/right accelerometer magnitude, left/right gyroscope magnitude, chewing event, and CPM.

## S2 Spatial Music Intervention

S2 keeps the C3 detection method, keeps the S1 layered music intervention, and adds a left/right headphone sound-position intervention.

Put loop files in:

```text
C:\Users\YUN\Desktop\chewtune\chewtune2\S2_spatial_chewing_system\music
```

Expected names are `drum`, `background`, `bass`, and `melody` with `.wav`, `.mp3`, or `.ogg`. The muffled background sound in the flowchart is `background`.

Run S2:

```powershell
cd C:\Users\YUN\Desktop\chewtune\chewtune2\S2_spatial_chewing_system
python python\s2_spatial_intervention.py --port COM4
```

This opens a visual interface showing:

```text
C3 chewing / non chewing
C3 left / right side
CPM and raw CPM
model side probability
threshold chewing score
S2 intervention state
stability
active music layers
sound pan position from left (-1.00) to right (+1.00)
```

If you only want terminal output:

```powershell
python python\s2_spatial_intervention.py --port COM4 --no-gui
```

Spatial intervention:

```text
Initial sound pan = 0.00 center
left_chewing  -> target pan moves left by 1/30 per estimated chew
right_chewing -> target pan moves right by 1/30 per estimated chew
current pan smoothly follows target pan
30 continuous left chews -> target pan reaches -1.00
30 continuous right chews -> target pan reaches +1.00
```

This uses stereo left/right level difference for real-time headphone panning. It is not a full HRTF renderer, but it gives a clear left/right spatial movement cue with ordinary stereo headphones.

Test music and pan without sensors:

```powershell
python python\s2_spatial_intervention.py --test-music-state normal --test-pan -1
python python\s2_spatial_intervention.py --test-music-state normal --test-pan 0
python python\s2_spatial_intervention.py --test-music-state normal --test-pan 1
```

Layer mapping:

```text
pause  -> none
normal -> drum + melody
stable -> bass + drum + melody
fast   -> background + drum
```

Fast chewing uses the threshold `120 CPM`: in the latest 4 valid chewing intervals, if at least 2 instantaneous CPM values are above the threshold, S2 switches to `fast`.

Pause uses a 5 second gap: only after 5 seconds without chewing will S2 switch to `pause` and silence the music layers.

Stability uses the latest up to 5 valid intervals:

```text
variation = std(intervals) / mean(intervals)
stability = 1 - variation / 0.22
stable if stability >= 0.50
```

Save a recording:

```powershell
python python\realtime_dual_mpu6050_detection.py --port COM4 --activity-label left_chewing --save-csv data\left_test_01.csv
```

## Train A Left/Right Model

The current rule-only version was backed up here:

```text
C:\Users\YUN\Desktop\chewtune\chewtune2\dual_mpu6050_chew_detection_rule_backup
```

Collect new left/right chewing clips into `data\side`:

```powershell
cd C:\Users\YUN\Desktop\chewtune\chewtune2\dual_mpu6050_chew_detection

python python\realtime_dual_mpu6050_detection.py --port COM4 --disable-model --activity-label left_chewing --save-csv data\side\left_chewing_01.csv

python python\realtime_dual_mpu6050_detection.py --port COM4 --disable-model --activity-label right_chewing --save-csv data\side\right_chewing_01.csv
```

Suggested recording: 20-30 seconds per clip. Keep both sensors fixed in the same positions.

Train a TensorFlow-free convolution model:

```powershell
python python\train_dual_side_cnn.py
```

This creates:

```text
models\dual_side_cnn.pkl
```

Run with the trained model:

```powershell
python python\realtime_dual_mpu6050_detection.py --port COM4
```

The realtime script automatically loads `models\dual_side_cnn.pkl` when it exists.

This default backend uses random 1D convolution kernels plus a lightweight classifier, so it does not need TensorFlow. If you specifically want TensorFlow Conv1D later, install TensorFlow and run:

```powershell
python python\train_dual_side_cnn.py --backend tensorflow --model-out models\dual_side_cnn.keras
```

Optional: train the older random-forest feature model:

```powershell
python python\train_dual_side_model.py
```

This creates:

```text
models\dual_side_model.pkl
data\dual_side_features.csv
```

If you want to force the old rule-only behavior:

```powershell
python python\realtime_dual_mpu6050_detection.py --port COM4 --disable-model
```

## Compare Accuracy Live

To compare the CNN-style model against two threshold-only methods:

```powershell
python python\evaluate_dual_methods.py --port COM4
```

While the window is open:

```text
G = mark current chewing as left_chewing
H = mark current chewing as right_chewing
```

The script compares:

```text
cnn                  trained convolution-time-series model
rule_score           original peak/rhythm/side-score threshold method
magnitude_threshold  simple left/right motion-amplitude threshold method
```

Results are saved to:

```text
data\method_eval_results.csv
```

## Calibrate Chewing Rate

The current CNN side model was backed up as:

```text
models\dual_side_cnn_C.pkl
```

Collect calibration data. Press Space once for every real chew:

```powershell
python python\collect_rate_calibration.py --port COM4 --raw-out data\rate_calibration\rate_calibration_01.csv --events-out data\rate_calibration\rate_calibration_01_events.csv
```

Record several clips with slow, normal, and fast chewing. Use a new number each time:

```powershell
python python\collect_rate_calibration.py --port COM4 --raw-out data\rate_calibration\rate_calibration_02.csv --events-out data\rate_calibration\rate_calibration_02_events.csv
```

Train the CPM calibrator:

```powershell
python python\train_rate_calibrator.py
```

This creates:

```text
models\cpm_calibrator.pkl
data\rate_calibration_features.csv
```

Normal realtime detection automatically loads `models\cpm_calibrator.pkl` when it exists:

```powershell
python python\realtime_dual_mpu6050_detection.py --port COM4
```

To compare with the old uncalibrated CPM:

```powershell
python python\realtime_dual_mpu6050_detection.py --port COM4 --disable-cpm-calibrator
```

## Chewing / Still Threshold Gate

The C2 model set is preserved here:

```text
models\C2
```

Realtime detection now uses a simple threshold gate for chewing vs stillness, instead of automatically loading the Random Forest state model. The threshold config is:

```text
models\chewing_state_threshold.json
```

Default thresholds:

```text
total_gyro_std_min: 0.15
max_axis_gyro_std_min: 0.115
total_acc_std_min: 0.02
```

All three motion metrics must exceed threshold before a window can enter side detection or CPM. Otherwise it is forced to `non_chewing`, so stillness does not enter side detection or CPM. To explicitly use the old Random Forest state model:

```powershell
python python\realtime_dual_mpu6050_detection.py --port COM4 --enable-chewing-state-model
```

## How Side Detection Works

The first version is rule-based:

```text
1. Read both MPU6050 sensors at 100 Hz.
2. In each 2 second window, score the left and right sensors.
3. A side score is based on waveform strength, peak count, CPM range, and rhythm regularity.
4. If the stronger side clearly exceeds the other side, output left_chewing or right_chewing.
5. If both sides are too similar, output both_or_unknown.
```

Useful tuning options:

```powershell
python python\realtime_dual_mpu6050_detection.py --port COM4 --side-ratio 1.05
python python\realtime_dual_mpu6050_detection.py --port COM4 --min-channel-std 0.01
python python\realtime_dual_mpu6050_detection.py --port COM4 --counter-min-peak-distance 0.4
```
