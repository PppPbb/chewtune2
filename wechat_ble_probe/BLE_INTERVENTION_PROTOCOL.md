# ChewTune BLE Intervention Protocol

## Responsibilities

### Computer

- Reads and analyzes dual-MPU6050 sensor data.
- Detects chewing state, CPM, chewing side, and rhythm stability.
- Runs the PPB state machine.
- Owns the active CPM and PPB thresholds.
- Decides music state, active music layers, spatial pan, and cue events.
- Sends detection data and final intervention decisions to the mini program.

### ESP32-S3

- Streams raw dual-MPU6050 data to the computer over USB serial.
- Forwards computer `U` and `M` messages to the phone with BLE Notify.
- Forwards phone `C` messages to the computer over USB serial.

### WeChat mini program

- Lets the user review and adjust CPM and PPB thresholds.
- Sends thresholds to the computer after BLE connects.
- Plays the layers and cue selected by the computer.
- Uses computer pan values to drive the left/right waveform visualization.
- Displays live detection and session report data.

The current phone player uses `InnerAudioContext`, which does not provide reliable stereo pan
control. The pan value is transmitted and visualized, while actual phone-side spatial audio
requires a future WebAudio-based player.

## Messages

### Detection packet: computer to phone

```text
U,chewing,cpm,side,stability,ppb_tenths,ppb_state
```

- `chewing`: `0` or `1`
- `side`: `L`, `R`, `B`, or `-`
- `ppb_state`: `I`, `D`, `W`, `R`, `T`, or `-`

### Music decision packet: computer to phone

```text
M,state,pan_percent,layer_mask,cue
```

- `state`: `P` pause, `N` normal, `S` stable, `F` fast
- `pan_percent`: `-100` left to `100` right
- `layer_mask`: background `1`, bass `2`, drum `4`, melody `8`
- `cue`: `O` pop, `D` ding, `E` error, `-` none

### Threshold packet: phone to computer

```text
C,cpm_threshold,ppb_threshold_seconds
```

The mini program sends this message immediately after BLE Notify is enabled.
