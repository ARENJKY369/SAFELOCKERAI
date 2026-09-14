"""
CKS9 Smart Safe/Locker with AI Tamper Detection
-----------------------------------------------
Flask backend:
  * REST API   - /api/status, /api/sensor, /api/auth, /api/events (+ /api/reset)
  * Live video - /video_feed (MJPEG), /snapshot.jpg
  * Background threads:
      - detection loop : webcam -> OpenCV MobileNet-SSD CNN -> person yes/no
      - state loop     : temporal logic (4/10 consecutive frames) + sensor fusion

Run:  pip install -r requirements.txt && python app.py
Open: http://localhost:5000
"""
import os
import threading
import time

from flask import Flask, Response, jsonify, request
from flask_cors import CORS

import db
from core import SystemCore
from detector import SmartCamera, ensure_models

PORT = int(os.environ.get("PORT", 5000))
CAM_INDEX = int(os.environ.get("CKS9_CAM", "0"))
# Set CKS9_NO_DOWNLOAD=1 to skip the one-time model download (camera will run in SIM mode).
AUTO_DOWNLOAD = not os.environ.get("CKS9_NO_DOWNLOAD")

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)  # allow the dashboard to be served/called from any origin

core: SystemCore = None
camera: SmartCamera = None


# ---------------------------------------------------------------- pages
@app.route("/")
def index():
    resp = app.send_static_file("index.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- REST API
@app.route("/api/status", methods=["GET"])
def api_status():
    """Current fused system state."""
    return jsonify(core.status())


@app.route("/api/sensor", methods=["POST"])
def api_sensor():
    """Simulated hardware sensor updates: { pir, vibration, door_open }.

    pir / vibration  -> momentary pulses (stay hot for a few seconds)
    door_open        -> latching toggle (pass true/false, omit to toggle)
    """
    data = request.get_json(silent=True) or {}
    return jsonify(core.sensor_update(
        pir=data.get("pir"),
        vibration=data.get("vibration"),
        door_open=data.get("door_open"),
    ))


@app.route("/api/auth", methods=["POST"])
def api_auth():
    """PIN check. Correct PIN -> ACCESS state, door unlocked for 8 seconds."""
    data = request.get_json(silent=True) or {}
    ok = core.auth(str(data.get("pin", "")))
    return jsonify({
        "success": bool(ok),
        "message": "ACCESS GRANTED" if ok else "ACCESS DENIED",
    })


@app.route("/api/events", methods=["GET"])
def api_events():
    """Last 50 events from the SQLite events table (newest first)."""
    return jsonify(db.last_events(50))


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Convenience for the demo: clear sensors and force NORMAL."""
    return jsonify(core.reset())


# ---------------------------------------------------------------- live camera
def mjpeg_stream():
    boundary = b"--frame\r\n"
    while True:
        jpg = camera.get_jpeg() if camera else None
        if jpg:
            yield (boundary +
                   b"Content-Type: image/jpeg\r\n" +
                   b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" +
                   jpg + b"\r\n")
        time.sleep(0.12)


@app.route("/video_feed")
def video_feed():
    if camera is None:
        return Response("camera unavailable", status=503)
    return Response(mjpeg_stream(),
                    mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store"})


@app.route("/snapshot.jpg")
def snapshot():
    jpg = camera.get_jpeg() if camera else None
    if not jpg:
        return Response("camera unavailable", status=503)
    return Response(jpg, mimetype="image/jpeg",
                    headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- boot
def detection_loop():
    """Webcam -> CNN person detector, feeds the state machine (~5 FPS)."""
    while True:
        try:
            person, conf = camera.read()
            core.set_detection(person, conf)
            core.camera_mode = camera.mode  # may fall back CNN -> SIM mid-run
            # CNN already runs ~5 FPS (capture+forward dominate); in SIM mode
            # this sleep sets the frame rate so the 4/10-frame temporal logic
            # plays out on a natural, demo-friendly timescale.
            time.sleep(0.18)
        except Exception as exc:  # never let the loop die
            print("[detector] error:", exc)
            time.sleep(1.0)


def state_loop():
    """Temporal logic + sensor fusion tick."""
    while True:
        try:
            core.tick()
        except Exception as exc:  # never let the state thread die silently
            print("[state] error:", exc)
        time.sleep(0.3)


def build_system():
    global core, camera
    have_models = ensure_models(auto=AUTO_DOWNLOAD)
    camera = SmartCamera(CAM_INDEX, want_cnn=have_models)
    core = SystemCore(camera_mode=camera.mode)
    if camera.mode == "SIM":
        # in SIM mode a PIR pulse makes a "person" appear in the synthetic feed
        core.sim_person_cb = camera.trigger_person
        core.sim_clear_cb = camera.clear_person
    threading.Thread(target=detection_loop, daemon=True).start()
    threading.Thread(target=state_loop, daemon=True).start()


build_system()


if __name__ == "__main__":
    print("\n  ================================================")
    print("   CKS9 Smart Safe  ->  http://0.0.0.0:%d" % PORT)
    print("   camera mode      :  %s" % camera.mode)
    print("   PIN              :  ****  (see core.PIN_CODE)")
    print("  ================================================\n")
    app.run(host="0.0.0.0", port=PORT, debug=False,
            use_reloader=False, threaded=True)
