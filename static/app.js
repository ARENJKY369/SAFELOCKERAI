/* CKS9 Smart Safe — dashboard front-end (vanilla JS, no deps) */
const $ = (id) => document.getElementById(id);

const cam = $("cam");
const camOff = $("cam-off");
let lastStatus = null;
let camOk = false;
let snapTimer = null;

const STATE_CLASS = {
  "NORMAL": "s-normal",
  "ACCESS": "s-access",
  "ATTENTIVE": "s-attentive",
  "TAMPER ALERT": "s-tamper",
};
const STATE_ICON = { "NORMAL": "✅", "ACCESS": "🔑", "ATTENTIVE": "⚠️", "TAMPER ALERT": "🚨" };
const STATE_SUB = {
  "NORMAL": "All sensors quiet · perimeter secure",
  "ACCESS": "Authorized access",
  "ATTENTIVE": "Suspicious activity — monitoring closely",
  "TAMPER ALERT": "TAMPERING DETECTED — owner notified!",
};

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function fmtTime(ts) {
  try { return new Date(ts.replace(" ", "T")).toLocaleTimeString(); }
  catch (e) { return ts; }
}

/* ---------------- status rendering (polled every 800 ms) ---------------- */
function render(s) {
  lastStatus = s;
  const cls = STATE_CLASS[s.state] || "s-normal";
  $("status-card").className = "card status-card " + cls;
  $("status-icon").textContent = STATE_ICON[s.state] || "✅";
  $("status-text").textContent = s.state;
  let sub = STATE_SUB[s.state] || "";
  if (s.state === "ACCESS" && s.unlock_remaining > 0)
    sub = `Authorized access · auto-relock in ${Math.ceil(s.unlock_remaining)} s`;
  $("status-sub").textContent = sub;
  $("last-event").textContent = s.last_event;
  $("updated").textContent = fmtTime(s.updated);

  document.body.classList.toggle("alarm", s.state === "TAMPER ALERT");
  document.title = s.state === "TAMPER ALERT" ? "🚨 CKS9 — TAMPER ALERT" : "CKS9 · Smart Safe — AI Tamper Detection";

  /* lock */
  $("lock-icon").textContent = s.locked ? "🔒" : "🔓";
  $("lock-text").textContent = s.locked ? "LOCKED" : "UNLOCKED";
  $("lock-text").style.color = s.locked ? "var(--green)" : "var(--yellow)";
  $("lock-sub").textContent = s.locked
    ? "Deadbolt engaged"
    : `Access window · ${Math.ceil(s.unlock_remaining)} s left`;
  $("lock-card").classList.toggle("unlocked", !s.locked);

  /* sensor tiles */
  setTile("tile-pir", "pir-val", s.pir, "MOTION", "CLEAR", "hot");
  setTile("tile-vib", "vib-val", s.vibration, "DETECTED", "QUIET", "hot");
  setTile("tile-door", "door-val", s.door_open, "OPEN", "CLOSED", "door-open");
  $("btn-door").textContent = s.door_open ? "🚪 Close door" : "🚪 Open door";

  /* camera / CNN tile */
  const pct = Math.round((s.confidence || 0) * 100);
  $("conf-fill").style.width = pct + "%";
  $("conf-val").textContent = pct + "%";
  $("pf").textContent = s.person_frames;
  const det = $("det-badge");
  if (s.person_detected) {
    det.textContent = "PERSON " + pct + "%";
    det.className = "badge small person";
  } else {
    det.textContent = "NO PERSON";
    det.className = "badge small clear";
  }
  const cb = $("cam-badge");
  cb.textContent = s.camera === "CNN" ? "CAM · CNN LIVE" : "CAM · SIM MODE";
  cb.className = "badge " + (s.camera === "CNN" ? "clear" : "sim");
}

function setTile(tileId, valId, active, onText, offText, hotClass) {
  $(valId).textContent = active ? onText : offText;
  $(tileId).classList.toggle(hotClass, !!active);
}

async function poll() {
  try {
    const r = await fetch("/api/status");
    render(await r.json());
  } catch (e) {
    $("status-card").className = "card status-card s-offline";
    $("status-icon").textContent = "📴";
    $("status-text").textContent = "OFFLINE";
    $("status-sub").textContent = "cannot reach CKS9 backend";
    document.body.classList.remove("alarm");
  }
}

/* ---------------- event log (auto-refresh) ---------------- */
function evClass(m) {
  if (/tamper|denied|rejected|fail|still open/i.test(m)) return "bad";
  if (/granted|accepted|armed|all clear|cleared|closed/i.test(m)) return "good";
  if (/motion|vibration|watch|open/i.test(m)) return "warn";
  return "";
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function refreshEvents() {
  try {
    const r = await fetch("/api/events");
    const list = await r.json();
    $("ev-count").textContent = list.length;
    $("events").innerHTML = list.map((ev) =>
      `<li class="${evClass(ev.message)}"><span class="t">${ev.timestamp.split(" ").pop()}</span><span>${escapeHtml(ev.message)}</span></li>`
    ).join("") || `<li class="dim">no events yet</li>`;
  } catch (e) { /* backend briefly away — keep old list */ }
}

/* ---------------- PIN keypad ---------------- */
let entry = "";
const cells = [...document.querySelectorAll(".pin-cell")];

function drawEntry() {
  cells.forEach((c, i) => (c.textContent = i < entry.length ? "●" : ""));
}

function pinMsg(text, kind) {
  const el = $("pin-msg");
  el.textContent = text;
  el.className = "pin-msg " + (kind || "dim");
}

async function submitPin() {
  if (!entry) return;
  const pin = entry;
  entry = "";
  drawEntry();
  try {
    const res = await postJSON("/api/auth", { pin });
    if (res.success) {
      pinMsg("✅ ACCESS GRANTED — door unlocked 8 s", "good");
    } else {
      pinMsg("❌ WRONG PIN — attempt logged", "bad");
      const d = $("pin-display");
      d.classList.remove("shake");
      void d.offsetWidth;          // restart the animation
      d.classList.add("shake");
    }
    poll();
    refreshEvents();
  } catch (e) {
    pinMsg("backend unreachable", "bad");
  }
}

function press(k) {
  if (k === "C") { entry = ""; drawEntry(); pinMsg("enter 4-digit PIN", "dim"); return; }
  if (k === "#") { submitPin(); return; }
  if (entry.length < 4) {
    entry += k;
    drawEntry();
    if (entry.length === 4) submitPin();   // auto-submit on 4th digit
  }
}

document.querySelectorAll(".key").forEach((b) =>
  b.addEventListener("click", () => press(b.dataset.k)));

document.addEventListener("keydown", (e) => {
  if (/^[0-9]$/.test(e.key)) press(e.key);
  else if (e.key === "Backspace") press("C");
  else if (e.key === "Enter") press("#");
});

/* ---------------- simulation buttons ---------------- */
function flash(btn) {
  btn.classList.add("flashed");
  setTimeout(() => btn.classList.remove("flashed"), 300);
}

$("btn-pir").onclick = async (e) => {
  flash(e.currentTarget);
  await postJSON("/api/sensor", { pir: true }).catch(() => {});
  poll(); refreshEvents();
};
$("btn-vib").onclick = async (e) => {
  flash(e.currentTarget);
  await postJSON("/api/sensor", { vibration: true }).catch(() => {});
  poll(); refreshEvents();
};
$("btn-door").onclick = async (e) => {
  flash(e.currentTarget);
  const open = !(lastStatus && lastStatus.door_open);
  await postJSON("/api/sensor", { door_open: open }).catch(() => {});
  poll(); refreshEvents();
};
$("btn-reset").onclick = async (e) => {
  flash(e.currentTarget);
  await postJSON("/api/reset", {}).catch(() => {});
  poll(); refreshEvents();
};

/* ---------------- camera fallback (MJPEG -> snapshot) ---------------- */
cam.addEventListener("load", () => { camOk = true; camOff.classList.add("hidden"); });
cam.addEventListener("error", () => { camOk = false; startSnapshots(); });
setTimeout(() => { if (!camOk) startSnapshots(); }, 4500);

function startSnapshots() {
  if (snapTimer) return;
  camOff.textContent = "snapshot mode";
  snapTimer = setInterval(() => { cam.src = "/snapshot.jpg?t=" + Date.now(); }, 900);
}

/* ---------------- clock + boot ---------------- */
setInterval(() => { $("clock").textContent = new Date().toLocaleTimeString(); }, 1000);

poll();
refreshEvents();
setInterval(poll, 800);
setInterval(refreshEvents, 2000);
