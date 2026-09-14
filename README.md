# 🛡️ CKS9 — Smart Safe/Locker with AI Tamper Detection

A security dashboard system for a smart safe/locker. A camera feed is continuously run
through a pretrained **MobileNet-SSD** CNN (OpenCV DNN). Consecutive "person" detections are
fused with **PIR / vibration / door** sensor inputs to drive a 4-state security machine:

```
NORMAL ──person×4 / PIR / vibration──▶ ATTENTIVE ──person×10──▶ TAMPER ALERT
   │                                                        (door forced = instant)
   └──────────── correct PIN (8 s window) ────────────────▶ ACCESS
```

Every state change is written to a **SQLite** `events` table and shown live on a dark,
phone-friendly dashboard — which doubles as the "remote IoT notification" screen.

## Quick start (demo tonight)

```bash
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000** — from your phone on the same Wi-Fi use
`http://<your-laptop-ip>:5000` to simulate the remote-owner notification view.

On first start the app **auto-downloads** the MobileNet-SSD Caffe model (~23 MB) into
`models/`. If a webcam is found it is used for real CNN person detection (**CNN mode**);
otherwise the camera runs in **SIM mode** with a synthetic CCTV feed, so the entire demo
still works with zero hardware.

Pre-download the model manually (optional):

```bash
python download_models.py
```

## How it works

| Rule (evaluated while locked)                          | Result        |
| ------------------------------------------------------ | ------------- |
| Door opens while locked                                | TAMPER ALERT (immediate) |
| Person in ≥ **10** consecutive CNN frames              | TAMPER ALERT  |
| PIR motion or vibration pulse                          | ATTENTIVE     |
| Person in ≥ **4** consecutive CNN frames               | ATTENTIVE     |
| Correct PIN `1234`                                     | ACCESS — door unlocked for **8 s**, then auto-relock |
| All threats quiet for 4 s                              | back to NORMAL |

- Person detection threshold: CNN confidence > 0.5 counts as a "person frame"; the counter
  resets on any frame without a person (temporal logic).
- Sensor fusion runs in a 0.3 s state-machine tick; the CNN loop runs at ~5 FPS.
- Every state change and sensor/PIN event is logged to SQLite (`events.db`) and streamed to
  the dashboard event panel (last 50, auto-refresh).

## Dashboard features

- Big color-coded status card — green **NORMAL**, yellow **ACCESS**, orange **ATTENTIVE**,
  red **TAMPER ALERT** with a pulsing glow + full-page alarm vignette.
- Live lock icon (locked/unlocked with access-window countdown).
- Camera tile with the live (annotated) feed, person-confidence bar and consecutive-frame
  counter.
- Live tiles: PIR motion, vibration, door state.
- PIN keypad (auto-submits on the 4th digit; physical keyboard works too) with
  granted/denied feedback — failed attempts are logged.
- Simulation buttons for PIR / vibration / door / reset, since the physical sensors aren't
  wired yet.
- Event history panel (last 50 events from SQLite, auto-refresh).
- Polls `GET /api/status` every 800 ms; dark mode; fully mobile-responsive.

In **SIM mode**, pressing *PIR pulse* also makes a "person" appear in the synthetic camera
feed — a nice way to demo the CNN temporal escalation (ATTENTIVE → TAMPER) without a webcam.

## REST API

| Method | Route          | Body / Params                        | Returns |
| ------ | -------------- | ------------------------------------ | ------- |
| GET    | `/api/status`  | —                                    | `{ state, locked, person_detected, confidence, pir, vibration, door_open, last_event, updated, … }` |
| POST   | `/api/sensor`  | `{ pir, vibration, door_open }` (any subset; `door_open` toggles if omitted) | updated status |
| POST   | `/api/auth`    | `{ pin: "1234" }`                    | `{ success, message }` — success ⇒ ACCESS + unlocked 8 s |
| GET    | `/api/events`  | —                                    | last 50 events `[{ id, timestamp, message }]` |
| POST   | `/api/reset`   | —                                    | clears sensors, forces NORMAL |
| GET    | `/video_feed`  | —                                    | MJPEG live stream (annotated) |
| GET    | `/snapshot.jpg`| —                                    | single JPEG frame |

CORS is enabled globally, so the API can be consumed from any origin/port.

## Configuration (env vars, all optional)

| Var                | Default | Purpose                                   |
| ------------------ | ------- | ----------------------------------------- |
| `PORT`             | `5000`  | HTTP port                                 |
| `CKS9_CAM`         | `0`     | webcam index                              |
| `CKS9_PIN`         | `1234`  | the PIN code                              |
| `CKS9_DB`          | `./events.db` | SQLite path                         |
| `CKS9_NO_DOWNLOAD` | unset   | set `1` to skip model auto-download (SIM camera) |

## Project structure

```
app.py               Flask app, REST API, MJPEG, background threads
core.py              state machine + sensor fusion (NORMAL/ACCESS/ATTENTIVE/TAMPER)
detector.py          MobileNet-SSD person detection + synthetic SIM camera
db.py                SQLite events table helpers
download_models.py   manual model downloader
static/              dashboard (index.html, style.css, app.js — no build step)
models/              deploy.prototxt + mobilenet_iter_73000.caffemodel (auto-downloaded)
events.db            SQLite event log (created at runtime)
```

## 60-second demo script

1. Dashboard opens green **NORMAL**, camera tile scanning, event log shows *System armed*.
2. Press **PIR pulse** → PIR tile flashes orange, *ATTENTIVE*; in SIM mode a person appears
   on the synthetic feed and the CNN starts counting frames.
3. Person stays (or press PIR again) → frame counter hits 10 → red **TAMPER ALERT** with
   pulsing card and full-page alarm.
4. Threat clears → auto-recovers to NORMAL after a few seconds.
5. Press **Open door** while locked → instant TAMPER ALERT; close door → recovers.
6. Type PIN `1234` (or click the keypad) → **ACCESS**, lock icon opens with countdown;
   after 8 s it auto-relocks. Wrong PIN → red shake + logged failed attempt.
7. Open the dashboard on your phone to show the "remote owner" notification angle.

## Troubleshooting

- **No webcam / "SIM mode"**: expected on desktops and servers — the synthetic feed drives
  the same state machine. On a laptop, grant the terminal/browser camera permission and
  restart; the app then uses CNN mode.
- **Model download fails** (offline venue): run `python download_models.py` once while
  online, or set `CKS9_NO_DOWNLOAD=1` to demo in SIM mode.
- **macOS camera permission**: allow camera access for Terminal/Python when prompted.
- **Port busy**: `PORT=8080 python app.py`.
- Headless Linux servers may prefer `opencv-python-headless` instead of `opencv-python`.
