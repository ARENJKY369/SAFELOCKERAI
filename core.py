"""
CKS9 system core - finite state machine + sensor fusion.

States
------
NORMAL        all quiet, safe locked
ACCESS        correct PIN entered, door unlocked for 8 s, then auto-relock
ATTENTIVE     suspicious but not confirmed (PIR / vibration / person x4 frames)
TAMPER ALERT  attack in progress (door forced open, or person x10 frames)

Fusion rules (evaluated while LOCKED)
-------------------------------------
door_open ................................ -> TAMPER ALERT (immediate)
person in >= 10 consecutive CNN frames ... -> TAMPER ALERT
PIR motion or vibration pulse ............ -> ATTENTIVE
person in >= 4 consecutive CNN frames .... -> ATTENTIVE

Threats auto-clear back to NORMAL after CALM_SECONDS of quiet.
Every state change (and every sensor/PIN event) is written to the
SQLite `events` table.
"""
import os
import threading
import time
from datetime import datetime

import db

NORMAL = "NORMAL"
ACCESS = "ACCESS"
ATTENTIVE = "ATTENTIVE"
TAMPER = "TAMPER ALERT"

PIN_CODE = os.environ.get("CKS9_PIN", "1234")
UNLOCK_SECONDS = 8        # door stays unlocked after a correct PIN
ATTENTIVE_FRAMES = 4      # consecutive person frames -> ATTENTIVE
TAMPER_FRAMES = 10        # consecutive person frames -> TAMPER ALERT
PULSE_SECONDS = 4         # how long a PIR / vibration pulse stays "hot"
CALM_SECONDS = 4          # quiet time before auto-recover to NORMAL


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SystemCore:
    def __init__(self, camera_mode="SIM"):
        self.lock = threading.Lock()
        self.camera_mode = camera_mode
        self.sim_person_cb = None      # app wires this to the SIM camera

        self.state = NORMAL
        self.locked = True
        self.door_open = False

        self.pir = False               # momentary pulse flags (for events)
        self.vibration = False
        self.pir_until = 0.0           # pulse expiry timestamps
        self.vib_until = 0.0

        self.person_detected = False   # latest CNN result
        self.confidence = 0.0
        self.person_frames = 0         # consecutive person frames (temporal logic)

        self.unlock_until = 0.0
        self.last_threat = 0.0

        self.last_event = "System armed — NORMAL, all sensors quiet"
        self.updated = _now_str()

        db.init_db()
        db.log_event(self.last_event)

    # ------------------------------------------------------------ queries
    def status(self):
        now = time.time()
        with self.lock:
            return {
                "state": self.state,
                "locked": self.locked,
                "person_detected": self.person_detected,
                "confidence": round(self.confidence, 3),
                "pir": now < self.pir_until,
                "vibration": now < self.vib_until,
                "door_open": self.door_open,
                "last_event": self.last_event,
                "updated": self.updated,
                # extras the dashboard uses (not required by the spec)
                "person_frames": self.person_frames,
                "camera": self.camera_mode,
                "unlock_remaining": round(max(0.0, self.unlock_until - now), 1),
            }

    # ------------------------------------------------------------ inputs
    def sensor_update(self, pir=None, vibration=None, door_open=None):
        now = time.time()
        events = []
        with self.lock:
            if pir:
                if now >= self.pir_until:
                    events.append("PIR motion detected")
                self.pir_until = now + PULSE_SECONDS
            if vibration:
                if now >= self.vib_until:
                    events.append("Vibration detected on safe body")
                self.vib_until = now + PULSE_SECONDS
            if door_open is not None:                  # omit -> no change
                door_open = bool(door_open)
                if door_open != self.door_open:
                    self.door_open = door_open
                    if door_open:
                        events.append("Door opened (authorized)"
                                      if self.state == ACCESS
                                      else "DOOR OPENED while system locked")
                    else:
                        events.append("Door closed")
            if events:
                self.last_event = events[-1]
                self.updated = _now_str()
            pir_fired = bool(pir)
        for e in events:
            db.log_event(e)
        # demo sugar: in SIM camera mode a PIR pulse makes a person appear
        if pir_fired and self.camera_mode == "SIM" and self.sim_person_cb:
            try:
                self.sim_person_cb(8.0)
            except Exception:
                pass
        return self.status()

    def auth(self, pin):
        with self.lock:
            if pin == PIN_CODE:
                self.state = ACCESS
                self.locked = False
                self.unlock_until = time.time() + UNLOCK_SECONDS
                self.person_frames = 0
                msg = "PIN accepted — ACCESS granted, door unlocked for %d s" % UNLOCK_SECONDS
                ok = True
            else:
                msg = "PIN rejected — failed access attempt logged"
                ok = False
            self.last_event = msg
            self.updated = _now_str()
        db.log_event(msg)
        return ok

    def set_detection(self, person, confidence):
        """Called once per processed camera frame by the detection loop."""
        with self.lock:
            self.person_detected = bool(person)
            self.confidence = float(confidence or 0.0)
            self.person_frames = self.person_frames + 1 if person else 0

    def reset(self):
        with self.lock:
            self.door_open = False
            self.person_frames = 0
            self.pir_until = 0.0
            self.vib_until = 0.0
            self.locked = True
            if self.sim_clear_cb:
                try:
                    self.sim_clear_cb()
                except Exception:
                    pass
            msg = "Manual reset — sensors cleared, system NORMAL"
            if self.state != NORMAL:
                self._transition_locked(NORMAL, msg)
            else:
                self.last_event = msg
                self.updated = _now_str()
                db.log_event(msg)
        return self.status()

    # ------------------------------------------------------------ state loop
    def tick(self):
        """Sensor fusion + temporal escalation. Runs every ~0.3 s."""
        now = time.time()
        with self.lock:
            # 1) ACCESS window expiry -> auto-relock
            if self.state == ACCESS and now >= self.unlock_until:
                self.locked = True
                self.person_frames = 0
                if self.door_open:
                    self._transition_locked(TAMPER, "Door still open after auto-relock — TAMPER ALERT")
                else:
                    self._transition_locked(NORMAL, "Access window ended — door relocked, system NORMAL")

            # 2) fusion while locked
            if self.state != ACCESS and self.locked:
                pir = now < self.pir_until
                vib = now < self.vib_until
                target, reason = None, ""
                if self.door_open:
                    target, reason = TAMPER, "Door opened while locked — TAMPER ALERT"
                elif self.person_frames >= TAMPER_FRAMES:
                    target, reason = TAMPER, ("AI TAMPER: person in frame %d consecutive frames"
                                              % self.person_frames)
                elif pir or vib:
                    target, reason = ATTENTIVE, ("PIR motion detected while locked" if pir
                                                 else "Vibration detected while locked")
                elif self.person_frames >= ATTENTIVE_FRAMES:
                    target, reason = ATTENTIVE, ("AI WATCH: person detected in %d consecutive frames"
                                                 % self.person_frames)

                if target:
                    self.last_threat = now
                    if target != self.state:
                        self._transition_locked(target, reason)
                elif (self.state in (ATTENTIVE, TAMPER)
                      and now - self.last_threat >= CALM_SECONDS):
                    self._transition_locked(NORMAL, "All clear — threat cleared, system NORMAL")

    # ------------------------------------------------------------ helpers
    def _transition_locked(self, new_state, message):
        """Change state + log to SQLite. MUST be called with self.lock held."""
        self.state = new_state
        self.last_event = message
        self.updated = _now_str()
        db.log_event(message)
