"""One-shot helper: download the MobileNet-SSD Caffe model into ./models/

Usage:  python download_models.py
(app.py also auto-downloads on first start; this is the manual retry.)
"""
import detector

if detector.models_present():
    print("MobileNet-SSD model files already present in ./models/ — nothing to do.")
else:
    ok = detector.ensure_models(auto=True)
    raise SystemExit(0 if ok else 1)
