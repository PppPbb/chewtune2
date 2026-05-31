# MPU6050 Chewing Detection B

Version A is the backup of the current model:

```text
C:\Users\YUN\Desktop\chewtune\chewtune2\mpu6050_chew_detection_A
```

Version B is extended from A:

```text
C:\Users\YUN\Desktop\chewtune\chewtune2\mpu6050_chew_detection_B
```

B keeps the original flow:

```text
signal peaks -> chewing/non_chewing
non_chewing_gate.pkl -> veto talking, head movement, hand micro-shake
chewing_side.pkl -> only when chewing, classify left_chewing/right_chewing
```

## Collect Left And Right Chewing

Run from PowerShell:

```powershell
cd C:\Users\YUN\Desktop\chewtune\chewtune2\mpu6050_chew_detection_B
```

Record one left-side chewing clip:

```powershell
python python\realtime_mpu6050_detection.py --port COM4 --disable-gate --activity-label left_chewing --save-csv data\side\left_chewing_01.csv
```

Record one right-side chewing clip:

```powershell
python python\realtime_mpu6050_detection.py --port COM4 --disable-gate --activity-label right_chewing --save-csv data\side\right_chewing_01.csv
```

Suggested recording length: 20-30 seconds each. Keep the sensor wearing position unchanged between left and right clips.

## Train Side Model

After both clips exist:

```powershell
python python\train_chewing_side.py
```

The trainer skips the first 5 seconds and only uses windows where most samples were detected as `chewing`, so startup motion and non-chewing windows do not leak into the left/right classifier.

This creates:

```text
models\chewing_side.pkl
data\side_features.csv
```

## Run B

After training, B automatically loads the side model:

```powershell
python python\realtime_mpu6050_detection.py --port COM4
```

The status line and terminal output will show:

```text
side=left_chewing(...)
side=right_chewing(...)
side=unknown(...)
```
