"""
Person detection for CKS9.

Primary path : webcam -> OpenCV DNN running a pretrained MobileNet-SSD
               (deploy.prototxt + mobilenet_iter_73000.caffemodel).
Fallback path: synthetic "SIM" camera so the whole demo still works with
               no webcam / no model files (e.g. headless servers).
"""
import math
import os
import random
import threading
import time
from datetime import datetime

import numpy as np

try:
    import cv2
except ImportError:            # app still runs; video routes just degrade
    cv2 = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
PROTOTXT = os.path.join(MODELS_DIR, "deploy.prototxt")
CAFFEMODELS = [
    os.path.join(MODELS_DIR, "mobilenet_iter_73000.caffemodel"),
    os.path.join(MODELS_DIR, "VOC0712_MobileNet-SSD.caffemodel"),
]

# PASCAL VOC2007+12 classes, in MobileNet-SSD output order
CLASSES = ["background", "aeroplane", "bicycle", "bird", "boat", "bottle",
           "bus", "car", "cat", "chair", "cow", "diningtable", "dog",
           "horse", "motorbike", "person", "pottedplant", "sheep", "sofa",
           "train", "tvmonitor"]

PERSON_THRESHOLD = 0.5     # CNN confidence cut-off for a valid "person"
DRAW_THRESHOLD = 0.35      # draw boxes above this confidence
FRAME_W, FRAME_H = 640, 480

MODEL_URLS = {
    PROTOTXT: [
        "https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/deploy.prototxt",
    ],
    CAFFEMODELS[0]: [
        "https://github.com/chuanqi305/MobileNet-SSD/raw/master/mobilenet_iter_73000.caffemodel",
    ],
}


def models_present():
    return os.path.exists(PROTOTXT) and any(os.path.exists(p) for p in CAFFEMODELS)


def ensure_models(auto=True):
    """Download the Caffe model files if missing. Returns True when present."""
    if models_present():
        return True
    if not auto:
        print("[models] deploy.prototxt / caffemodel missing -> camera will run in SIM mode")
        print("[models] get them later with:  python download_models.py")
        return False
    print("[models] downloading MobileNet-SSD (~23 MB, one-time)...")
    os.makedirs(MODELS_DIR, exist_ok=True)
    try:
        import urllib.request

        def progress(count, block, total):
            done = min(100, int(count * block * 100 / max(1, total)))
            if done % 10 == 0:
                print(f"\r[models]   {done}%", end="", flush=True)

        for path, urls in MODEL_URLS.items():
            if os.path.exists(path):
                continue
            last_err = None
            for url in urls:
                try:
                    urllib.request.urlretrieve(url, path, reporthook=progress)
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
            print()
            if last_err and not models_present():
                print(f"[models] direct download failed ({last_err}), trying git clone...")
                _git_clone_fallback()
            print(f"[models] saved {os.path.basename(path)}")
        return models_present()
    except Exception as exc:
        print(f"[models] download failed ({exc}) -> camera will run in SIM mode")
        print("[models] retry later with:  python download_models.py")
        for p in (PROTOTXT,) + tuple(CAFFEMODELS):
            if os.path.exists(p) and os.path.getsize(p) < 10_000:
                os.remove(p)          # drop partial downloads
        return False


def _git_clone_fallback():
    """Some networks block the raw-file CDN but allow github.com — git clone then."""
    import shutil
    import subprocess
    import tempfile

    repo = "https://github.com/chuanqi305/MobileNet-SSD.git"
    tmp = tempfile.mkdtemp(prefix="mnssd_")
    try:
        subprocess.run(["git", "clone", "--depth", "1", repo, tmp],
                       check=True, capture_output=True, timeout=300)
        for src, dst in ((os.path.join(tmp, "deploy.prototxt"), PROTOTXT),
                         (os.path.join(tmp, "mobilenet_iter_73000.caffemodel"), CAFFEMODELS[0])):
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
    except Exception as exc:
        print(f"[models] git clone fallback failed: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class SmartCamera:
    """Reads frames from a webcam (CNN mode) or synthesizes them (SIM mode).

    read() -> (person_detected: bool, confidence: float)
    and refreshes the annotated JPEG served at /video_feed + /snapshot.jpg.
    """

    def __init__(self, cam_index=0, want_cnn=True):
        self.mode = "SIM"
        self.net = None
        self.cap = None
        self.sim_person_until = 0.0
        self._consecutive_failures = 0
        self._jpeg = None
        self._jpeg_lock = threading.Lock()

        if cv2 is None:
            print("[camera] OpenCV not available -> SIM mode")
            return
        if want_cnn:
            self._try_cnn(cam_index)
        if self.mode != "CNN":
            print("[camera] running in SIM mode (synthetic feed, PIR pulse = person appears)")

    # ------------------------------------------------------------- setup
    def _try_cnn(self, cam_index):
        try:
            caffemodel = next(p for p in CAFFEMODELS if os.path.exists(p))
            self.net = cv2.dnn.readNetFromCaffe(PROTOTXT, caffemodel)
        except Exception as exc:
            print(f"[camera] could not load MobileNet-SSD ({exc})")
            return
        cap = cv2.VideoCapture(cam_index)
        if not cap.isOpened():
            print(f"[camera] webcam index {cam_index} not available")
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
        self.cap = cap
        self.mode = "CNN"
        print(f"[camera] CNN mode: webcam {cam_index} + MobileNet-SSD loaded")

    # ------------------------------------------------------------- public
    def trigger_person(self, seconds=8.0):
        """SIM mode only: make a 'person' appear in frame for a while."""
        self.sim_person_until = time.time() + seconds

    def clear_person(self):
        """SIM mode only: remove the synthetic person immediately."""
        self.sim_person_until = 0.0

    def read(self):
        if self.mode == "CNN":
            return self._read_cnn()
        return self._read_sim()

    def get_jpeg(self):
        with self._jpeg_lock:
            return self._jpeg

    def _store_jpeg(self, frame):
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with self._jpeg_lock:
                self._jpeg = buf.tobytes()

    # ------------------------------------------------------------- CNN path
    def _read_cnn(self):
        ret, frame = self.cap.read()
        if not ret or frame is None:
            self._consecutive_failures += 1
            if self._consecutive_failures > 40:
                print("[camera] webcam signal lost -> falling back to SIM mode")
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None
                self.mode = "SIM"
            self._store_jpeg(self._sim_base())
            return False, 0.0
        self._consecutive_failures = 0

        frame = cv2.resize(frame, (FRAME_W, FRAME_H))
        blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)),
                                     0.007843, (300, 300), 127.5)
        self.net.setInput(blob)
        detections = self.net.forward()[0, 0]          # (N, 7)
        h, w = frame.shape[:2]

        person_conf, best_box = 0.0, None
        boxes = []
        for det in detections:
            conf = float(det[2])
            if conf < DRAW_THRESHOLD:
                continue
            cls = CLASSES[int(det[1]) % len(CLASSES)]
            x1, y1, x2, y2 = (det[3:7] * [w, h, w, h]).astype(int)
            boxes.append((cls, conf, x1, y1, x2, y2))
            if cls == "person" and conf > person_conf:
                person_conf, best_box = conf, (x1, y1, x2, y2)

        # annotate: green box for the best person, dim boxes for everything else
        for cls, conf, x1, y1, x2, y2 in boxes:
            if (x1, y1, x2, y2) == best_box:
                continue
            cv2.rectangle(frame, (x1, y1), (x2, y2), (110, 110, 110), 1)
            self._label(frame, f"{cls} {conf:.0%}", x1, y1 - 24, (170, 170, 170))
        if best_box:
            x1, y1, x2, y2 = best_box
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 230, 90), 2)
            self._label(frame, f"PERSON {person_conf:.0%}", x1, y1 - 26, (0, 230, 90))

        person = person_conf > PERSON_THRESHOLD
        self._draw_hud(frame, person, person_conf, sim=False)
        self._store_jpeg(frame)
        return person, round(person_conf, 3)

    # ------------------------------------------------------------- SIM path
    def _read_sim(self):
        person = time.time() < self.sim_person_until
        conf = 0.0
        frame = self._sim_base()
        if person:
            conf = random.uniform(0.78, 0.96)
            self._draw_person(frame, conf)
        self._draw_hud(frame, person, conf, sim=True)
        self._store_jpeg(frame)
        return person, round(conf, 3)

    def _sim_base(self):
        img = np.full((FRAME_H, FRAME_W, 3), (14, 12, 10), np.uint8)
        for x in range(0, FRAME_W, 40):
            cv2.line(img, (x, 0), (x, FRAME_H), (22, 20, 18), 1)
        for y in range(0, FRAME_H, 40):
            cv2.line(img, (0, y), (FRAME_W, y), (22, 20, 18), 1)
        noise = np.random.randint(0, 14, img.shape, dtype=np.uint8)
        img = cv2.add(img, noise)                       # cctv sensor noise
        y = int((time.time() * 90) % FRAME_H)           # scanning line
        cv2.line(img, (0, y), (FRAME_W, y), (35, 80, 35), 1)
        return img

    def _draw_person(self, img, conf):
        t = time.time()
        cx = int(FRAME_W / 2 + math.sin(t * 1.1) * 14)  # gentle sway
        body = (58, 54, 50)
        cv2.ellipse(img, (cx, 300), (50, 86), 0, 0, 360, body, -1)   # torso
        cv2.circle(img, (cx, 186), 27, body, -1)                     # head
        cv2.rectangle(img, (cx - 34, 380), (cx - 14, 468), body, -1)  # legs
        cv2.rectangle(img, (cx + 14, 380), (cx + 34, 468), body, -1)
        x1, y1, x2, y2 = cx - 64, 150, cx + 64, 476
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 230, 90), 2)
        self._label(img, f"PERSON {conf:.0%}", x1, y1 - 26, (0, 230, 90))

    # ------------------------------------------------------------- drawing
    def _label(self, img, text, x, y, color):
        x = max(0, min(x, FRAME_W - 10))
        y = max(16, y)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(img, (x, y), (min(FRAME_W - 1, x + tw + 10), y + th + 10),
                      (10, 10, 10), -1)
        cv2.putText(img, text, (x + 5, y + th + 4), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, color, 1, cv2.LINE_AA)

    def _draw_hud(self, img, person, conf, sim=False, signal_lost=False):
        title = "CKS9 CAM-01 - " + ("SIM" if sim else "CNN LIVE")
        cv2.putText(img, title, (14, 26), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (200, 220, 235), 1, cv2.LINE_AA)
        cv2.putText(img, datetime.now().strftime("%H:%M:%S"),
                    (FRAME_W - 116, 26), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (200, 220, 235), 1, cv2.LINE_AA)
        if int(time.time() * 2) % 2 == 0:
            cv2.circle(img, (FRAME_W - 30, FRAME_H - 22), 7, (60, 60, 240), -1)
        cv2.putText(img, "REC", (FRAME_W - 74, FRAME_H - 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 220, 235), 1)
        if signal_lost:
            cv2.putText(img, "NO SIGNAL", (FRAME_W // 2 - 92, FRAME_H // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (80, 90, 110), 2)
        elif not person:
            cv2.putText(img, "SCANNING - NO PERSON", (FRAME_W // 2 - 120, FRAME_H - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 140, 160), 1, cv2.LINE_AA)
