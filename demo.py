#!/usr/bin/env python3
"""FacePlugin Face Recognition demo (Gradio, local).

Talks to the local API server (run.bat on Windows, run.sh on Linux). Tabs:
  Face Detection · Quality Assessment · 1:1 Verification · 1:N Identification · Gallery · API
"""

from __future__ import annotations

import base64
import html
import io
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import gradio as gr
import requests
from PIL import Image

import demo_ui
import quality_gate

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "assets" / "examples" / "samples"

# ---- product settings (the only lines that differ between Face Recognition repos)
PRODUCT = "Face Recognition"
EDITION = "Standard"
DEFAULT_API_PORT = "8092"
DEFAULT_DEMO_PORT = "9092"
HAS_GALLERY = True  # the API serves /api/v1/persons, /identify, /settings and /docs
START_HINT = "run.bat"
# ----

API = os.environ.get(
    "API_BASE",
    f"http://127.0.0.1:{os.environ.get('PORT', os.environ.get('FACESDK_PORT', DEFAULT_API_PORT))}",
).rstrip("/")
DEMO_PORT = int(os.environ.get("DEMO_PORT", DEFAULT_DEMO_PORT))
IMAGE_SUFFIXES = demo_ui.IMAGE_SUFFIXES
PAGE_SIZE = 25
CANDIDATE_THUMBS = 5     # candidates per face that get a face image

DUP_CHOICES = {
    "Skip it": "reject",
    "Add the photo to that person": "add",
    "Enroll as a new person anyway": "new",
}
CLICK_JS = demo_ui.CLICK_JS

HIDE = gr.update(visible=False)
SHOW = gr.update(visible=True)
KEEP = gr.update()


# ---------------------------------------------------------------- API client


# Last user-facing call. Header and gallery refreshes pass track=False so they
# do not replace it. Upload time is reading and sending the photo; process time
# is the server's X-Process-Ms.
_STATUS = {
    "action": "Ready",
    "detail": "Waiting for a call",
    "upload_ms": None,
    "process_ms": None,
    "total_ms": None,
    "upload_kb": None,
    "extra": "",
    "ok": True,
}
_PREP_MS = 0.0


def _remember(action: str, detail: str, *, upload_ms=None, process_ms=None, total_ms=None,
              upload_kb=None, extra: str = "", ok: bool = True) -> None:
    _STATUS.update(action=action, detail=detail, upload_ms=upload_ms, process_ms=process_ms,
                   total_ms=total_ms, upload_kb=upload_kb, extra=extra, ok=ok)


def _take_prep() -> float:
    global _PREP_MS
    spent, _PREP_MS = _PREP_MS, 0.0
    return spent


def _header_ms(response) -> float | None:
    raw = response.headers.get("X-Process-Ms")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _clock(text: float | None) -> str:
    if text is None:
        return "—"
    if text >= 1000:
        return f"{text / 1000:.2f} s"
    return f"{text:.0f} ms"


def status_html() -> str:
    """Status bar next to the header: last call, upload time, process time, context."""
    item = _STATUS
    kind = "" if item["ok"] else " bad"
    times = []
    if item["upload_ms"] is not None:
        size = f" · {item['upload_kb'] / 1024:.1f} MB" if (item["upload_kb"] or 0) >= 1024 else (
            f" · {item['upload_kb']:.0f} KB" if item["upload_kb"] else "")
        times.append(f"<span><em>Upload</em>{_clock(item['upload_ms'])}{size}</span>")
    if item["process_ms"] is not None:
        times.append(f"<span><em>Process</em>{_clock(item['process_ms'])}</span>")
    if item["total_ms"] is not None:
        times.append(f"<span><em>Total</em>{_clock(item['total_ms'])}</span>")
    extra = f'<div class="fp-status-extra">{esc(item["extra"])}</div>' if item["extra"] else ""
    return (
        f'<div class="fp-status{kind}"><div class="fp-status-top">'
        f'<span class="fp-status-fn">{esc(item["action"])}</span>'
        f'<span class="fp-status-detail">{esc(item["detail"])}</span></div>'
        f'<div class="fp-status-times">{"".join(times)}</div>{extra}</div>'
    )


def _action_name(method: str, path: str) -> str:
    leaf = path.split("?")[0].rstrip("/")
    if leaf.endswith("/detect"):
        return "Face detection"
    if leaf.endswith("/quality"):
        return "Quality assessment"
    if leaf.endswith("/match"):
        return "1:1 verification"
    if leaf.endswith("/identify"):
        return "1:N identification"
    if leaf.endswith("/similar"):
        return "Duplicate search"
    if leaf.endswith("/settings"):
        return "Settings"
    if "/faces/" in leaf and method == "DELETE":
        return "Remove photo"
    if leaf.endswith("/persons") and method == "POST":
        return "Enrollment"
    if leaf.endswith("/persons") and method == "DELETE":
        return "Delete gallery"
    if "/persons/" in leaf and method == "DELETE":
        return "Delete person"
    if "/persons/" in leaf and method == "PATCH":
        return "Save person"
    if "/persons/" in leaf and method == "GET":
        return "Open person"
    if leaf.endswith("/persons"):
        return "Gallery"
    return f"{method} {leaf.rsplit('/', 1)[-1]}"


def _face_count(payload) -> int:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict) and isinstance(data.get("faces"), list):
        return len(data["faces"])
    return 0


def _call_context(method: str, path: str, success: bool, payload, message: str) -> tuple[str, str, str]:
    """(action, detail, extra) for the status bar."""
    action = _action_name(method, path)
    if not success:
        return action, message or "Failed", ""
    payload = payload if isinstance(payload, dict) else {}
    if action in ("Face detection", "Quality assessment"):
        count = _face_count(payload if "data" in payload else {"data": payload})
        # enveloped data is the face list; raw SDK JSON keeps it under data
        if isinstance(payload.get("data"), list):
            count = len(payload["data"])
        elif isinstance(payload, dict) and "data" not in payload:
            count = _face_count({"data": payload})
        return action, f"{count} face" + ("" if count == 1 else "s"), ""
    if action == "1:1 verification":
        pairs = [p for p in payload.get("match") or [] if isinstance(p, dict)]
        if not pairs:
            return action, "No face pair", ""
        best = max(float(p.get("similarity") or 0) for p in pairs)
        return action, f"{len(pairs)} pair" + ("" if len(pairs) == 1 else "s") + f" · best {best:.2f}", ""
    if action == "1:N identification":
        faces = payload.get("faces") or []
        titles = []
        for face in faces:
            status = face.get("status")
            if status == "identified":
                titles.append(f"Identified · {(face.get('best') or {}).get('name') or 'match'}")
            elif status == "spoof":
                titles.append("Spoof detected")
            else:
                titles.append("No match")
        timing = payload.get("timing") or {}
        parts = []
        for key, label in (("extract_ms", "Detection + template"),
                           ("liveness_ms", "Liveness"), ("search_ms", "Search")):
            if key in timing:
                parts.append(f"{label} {_clock(timing[key])}")
        gallery = payload.get("gallery") or {}
        if gallery:
            parts.append(f"Gallery {int(gallery.get('persons', 0)):,} persons")
        return action, " · ".join(titles) or "No face", " · ".join(parts)
    if action == "Enrollment":
        person = payload.get("person") or {}
        if payload.get("person_created"):
            detail = f"Enrolled {person.get('name')} · ID {person.get('id')}"
        elif person:
            detail = f"Added photo to {person.get('name')} · ID {person.get('id')}"
        else:
            detail = message or "Enrolled"
        return action, detail, ""
    if action == "Open person":
        return action, f"{payload.get('name') or 'Person'} · ID {payload.get('id')}", f"{len(payload.get('faces') or [])} photos"
    if action == "Gallery":
        return action, f"{int(payload.get('total', 0)):,} persons", ""
    if action == "Duplicate search":
        return action, f"{int(payload.get('pair_count', 0)):,} possible duplicates", f"{int(payload.get('comparisons', 0)):,} comparisons"
    if action == "Delete gallery":
        return action, f"Deleted {int(payload.get('persons_deleted', 0)):,} persons", ""
    if action == "Delete person":
        return action, "Person deleted", ""
    if action == "Remove photo":
        return action, "Photo removed", ""
    if action == "Save person":
        return action, f"Saved {payload.get('name') or ''}".strip(), ""
    if action == "Settings":
        return action, f"Decision threshold {float(payload.get('threshold', 0)):.2f}", ""
    return action, message or "Done", ""


def _note_call(method: str, path: str, response, success: bool, payload, message: str, sent: bool) -> None:
    prep = _take_prep()
    total_http = float(getattr(response, "elapsed", None).total_seconds() * 1000) if response is not None else 0.0
    process_ms = _header_ms(response) if response is not None else None
    upload_ms = None
    if sent:
        network = max(0.0, total_http - process_ms) if process_ms is not None else prep
        upload_ms = prep + (max(0.0, total_http - process_ms) if process_ms is not None else 0.0)
        if process_ms is None:
            upload_ms = prep
            network = 0.0
        else:
            upload_ms = prep + network
    total_ms = prep + total_http
    upload_kb = None
    if sent and response is not None:
        raw = response.request.body if response.request is not None else None
        if isinstance(raw, bytes):
            upload_kb = len(raw) / 1024
        elif isinstance(raw, str):
            upload_kb = len(raw.encode("utf-8")) / 1024
    action, detail, extra = _call_context(method, path, success, payload, message)
    _remember(action, detail, upload_ms=upload_ms, process_ms=process_ms, total_ms=total_ms,
              upload_kb=upload_kb, extra=extra, ok=success)


def _request(method: str, path: str, *, track: bool = True, **kw) -> tuple[bool, Any, int, str]:
    """(success, data, code, message) for an enveloped API route. Never raises."""
    sent = any(kw.get(key) is not None for key in ("json", "data", "files"))
    try:
        r = requests.request(method, f"{API}{path}", timeout=180, **kw)
        body = r.json()
    except requests.ConnectionError:
        message = f"The API server at {API} is not running. Start it with {START_HINT}."
        if track:
            _take_prep()
            _remember(_action_name(method, path), message, ok=False)
        return False, None, -100, message
    except Exception as exc:  # noqa: BLE001
        if track:
            _take_prep()
            _remember(_action_name(method, path), str(exc), ok=False)
        return False, None, -100, str(exc)
    if not isinstance(body, dict):
        if track:
            _note_call(method, path, r, False, None, "Unexpected response", sent)
        return False, None, -100, "Unexpected response"
    success = bool(body.get("success"))
    data = body.get("data")
    if track:
        _note_call(method, path, r, success, data if success else body, str(body.get("message") or ""), sent)
    return success, data, int(body.get("code") or 0), str(body.get("message") or "")


def _api(method: str, path: str, *, track: bool = True, **kw):
    """Enveloped API call that shows a Gradio error on failure."""
    success, data, _, message = _request(method, path, track=track, **kw)
    if not success:
        raise gr.Error(message or "Request failed")
    return data or {}


def _raw_post(path: str, body: dict) -> dict | str:
    """SDK routes that return raw JSON (Detect, Quality, Match)."""
    try:
        r = requests.post(f"{API}{path}", json=body, timeout=180)
        payload = r.json()
    except requests.ConnectionError:
        message = f"The API server at {API} is not running. Start it with {START_HINT}."
        _take_prep()
        _remember(_action_name("POST", path), message, ok=False)
        return message
    except Exception as exc:  # noqa: BLE001
        _take_prep()
        _remember(_action_name("POST", path), str(exc), ok=False)
        return str(exc)
    if not isinstance(payload, dict):
        _note_call("POST", path, r, False, None, "Unexpected response", True)
        return "Unexpected response"
    if payload.get("success") is False:
        message = str(payload.get("message") or "Request failed")
        _note_call("POST", path, r, False, payload, message, True)
        return message
    _note_call("POST", path, r, True, payload, str(payload.get("message") or ""), True)
    return payload


def _b64_file(path: str | Path) -> str:
    global _PREP_MS
    started = time.perf_counter()
    encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    _PREP_MS += (time.perf_counter() - started) * 1000
    return encoded


def _settings() -> dict:
    success, data, _, _ = _request("GET", "/api/v1/settings", track=False) if HAS_GALLERY else (False, None, 0, "")
    return data if success and data else {"threshold": 0.81,
                                          "duplicate_check": True, "liveness_check": True,
                                          "liveness_available": False}


# ---------------------------------------------------------------- small HTML helpers


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def id_tag(value) -> str:
    return f'<span class="fp-id">ID {esc(value)}</span>'


def count_tag(n: int, noun: str, plural: str | None = None) -> str:
    word = noun if int(n) == 1 else (plural or noun + "s")
    return f'<span class="fp-count">{int(n):,} {word}</span>'


def fmt_score(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def fmt_ms(value) -> str:
    return "—" if value is None else f"{float(value):.0f} ms"


def score_cell(value, threshold: float | None = None) -> str:
    score = max(0.0, min(1.0, float(value or 0)))
    below = threshold is not None and score < threshold
    mark = (f'<span class="fp-mark" style="left:{threshold * 100:.0f}%"></span>'
            if threshold is not None else "")
    return (f'<span class="fp-score"><span class="fp-track"><span class="fp-fill{" below" if below else ""}" '
            f'style="width:{score * 100:.0f}%"></span>{mark}</span>{fmt_score(value)}</span>')


def jpeg_uri(raw_b64: str | None) -> str | None:
    return f"data:image/jpeg;base64,{raw_b64}" if raw_b64 else None


def face_uri(face_id) -> str | None:
    if face_id is None:
        return None
    try:
        r = requests.get(f"{API}/api/v1/faces/{int(face_id)}/image", timeout=30)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(r.content).decode("ascii")


def img_tag(uri: str | None, cls: str = "", alt: str = "") -> str:
    if uri:
        return f'<img class="{cls}" src="{uri}" alt="{esc(alt)}"/>'
    return f'<span class="{cls or "fp-noimg"}"></span>'


def file_thumb_uri(path: str | Path, size: int = 96) -> str | None:
    try:
        img = Image.open(path)
        img.thumbnail((size, size))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        return None


def crop_uri(path: str, region: dict | None) -> str | None:
    try:
        img = Image.open(path)
        img.load()
        img = img.convert("RGB")
        box = quality_gate.face_box(region)
        if box:
            x, y = float(box.get("x", 0)), float(box.get("y", 0))
            w, h = float(box.get("width", 0)), float(box.get("height", 0))
            pad = min(w, h) * 0.25
            img = img.crop((int(max(0, x - pad)), int(max(0, y - pad)),
                            int(min(img.width, x + w + pad)), int(min(img.height, y + h + pad))))
        img.thumbnail((360, 360))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- header


def _alive(path: str) -> tuple[bool, dict | None]:
    """(server reachable, JSON data). Reachable is false only when nothing answers."""
    try:
        response = requests.get(f"{API}{path}", timeout=8)
        body = response.json()
    except requests.ConnectionError:
        return False, None
    except Exception:  # noqa: BLE001
        return True, None
    data = body.get("data") if isinstance(body, dict) else None
    return True, data if isinstance(data, dict) else None


def header_html() -> str:
    # A license or gallery error still means the API is up. Offline is only a dead port.
    online, _ = _alive("/api/health")
    license_status = backend = people = None
    if online:
        _, license_status = _alive("/api/licenseStatus")
        _, backend = _alive("/api/backend")
        if HAS_GALLERY:
            _, people = _alive("/api/v1/persons?page_size=1")
    extra = ""
    if people:
        extra += count_tag(people.get("total", 0), "person", "persons")
    if online:
        extra += f'<span class="fp-count">{esc(((backend or {}).get("backend") or "cpu").upper())}</span>'
    return demo_ui.header_html(PRODUCT, EDITION, API, status=license_status, online=online,
                               extra=extra, docs=HAS_GALLERY)


# ---------------------------------------------------------------- samples


def _sample_index(path: Path) -> int | None:
    """odd(n) / even(n), or a plain number such as 1.jpg. Odd numbers fill photo 1."""
    m = re.match(r"(?:odd|even)\((\d+)\)$", path.stem, re.I) or re.match(r"(\d+)$", path.stem)
    return int(m.group(1)) if m else None


def _samples() -> list[Path]:
    return demo_ui.list_images(SAMPLES)


def _numbered(parity: int | None = None) -> list[str]:
    items = sorted(((idx, p) for p in _samples() if (idx := _sample_index(p)) is not None), key=lambda t: t[0])
    return [str(p) for idx, p in items if parity is None or idx % 2 == parity]


ALL_EX = _numbered() or [str(p) for p in _samples()]
ODD_EX = _numbered(1)
EVEN_EX = _numbered(0)


# ---------------------------------------------------------------- Detect / Quality / Match


def _attr_rows(face: dict, kind: str) -> list[list[str]]:
    rows = []
    prefix = f"face[{face.get('faceId', '')}]"
    if kind == "detect":
        rows.append([f"{prefix}.facePose", demo_ui.cell(face.get("facePose")), ""])
        rows.append([f"{prefix}.faceRegion", demo_ui.cell(face.get("faceRegion")), ""])
    for name, attr in (face.get("attributes") or {}).items():
        value = attr.get("value") if isinstance(attr, dict) else attr
        extra = ""
        if isinstance(attr, dict):
            if kind == "detect" and "confidence" in attr:
                extra = demo_ui.cell(attr["confidence"])
            elif kind == "quality":
                extra = ", ".join(f"{k}={demo_ui.cell(attr[k])}" for k in ("status", "range") if k in attr)
        rows.append([f"{prefix}.{name}", demo_ui.cell(value), extra])
    return rows


ANALYZE_TITLE = {"detect": "Face detection", "quality": "Quality assessment"}


def _analyze(path: str, image, kind: str):
    if not image:
        return demo_ui.error_outputs("Choose a photo first.")
    payload = _raw_post(path, {"image": _b64_file(image), "cropImage": False})
    if isinstance(payload, str):
        return demo_ui.error_outputs(payload)
    faces = payload.get("data") if isinstance(payload.get("data"), list) else []
    summary = f"**{ANALYZE_TITLE[kind]}** · {len(faces)} face{'s' if len(faces) != 1 else ''} · {payload.get('message', '')}"
    rows = [row for face in faces if isinstance(face, dict) for row in _attr_rows(face, kind)]
    return summary, rows, json.dumps(payload, indent=2, ensure_ascii=False)


def detect(image):
    return _analyze("/api/detect", image, "detect")


def quality(image):
    return _analyze("/api/quality", image, "quality")


def match(image1, image2):
    if not image1 or not image2:
        return "", *demo_ui.error_outputs("Choose both photos first.")
    payload = _raw_post("/api/match", {"image1": _b64_file(image1), "image2": _b64_file(image2), "cropImage": False})
    if isinstance(payload, str):
        return "", *demo_ui.error_outputs(payload)
    threshold = float(_settings().get("threshold", 0.81))
    pairs = [p for p in payload.get("match") or [] if isinstance(p, dict)]
    rows = []
    for i, item in enumerate(pairs):
        label = (f"photo 1 · face {item.get('firstFaceId')}  ↔  photo 2 · face {item.get('secondFaceId')}")
        rows.append([f"pair[{i}]", label, demo_ui.cell(item.get("similarity"))])
    if pairs:
        best = max(float(p.get("similarity") or 0) for p in pairs)
        same = best >= threshold
        verdict = (
            f'<div class="fp-verdict {"ok" if same else "warn"}"><div><strong>{"Match" if same else "No match"}</strong>'
            f'<div class="fp-muted" style="color:inherit">{"The faces belong to the same person." if same else "The faces belong to different persons."}'
            f' Decision threshold {fmt_score(threshold)}.</div></div><span class="fp-big">{fmt_score(best)}</span></div>'
        )
    elif str(payload.get("message") or "COMPLETED").upper() not in ("COMPLETED", "OK", "SUCCESS"):
        verdict = demo_ui.verdict_html("warn", "No result", esc(payload.get("message")))
    else:
        verdict = demo_ui.verdict_html("warn", "No face pair to compare", "Each photo must contain at least one clearly visible face.")
    summary = f"**1:1 verification** · {len(pairs)} face pair{'s' if len(pairs) != 1 else ''} · {payload.get('message', '')}"
    return verdict, summary, rows, json.dumps(payload, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------- Identify


def _status_view(face: dict) -> tuple[str, str, str]:
    """(css class, title, detail) for one identified face."""
    status = face.get("status")
    if status == "spoof":
        return "bad", "Spoof detected", "The liveness check failed, so the gallery was not searched."
    if status == "identified":
        best = face.get("best") or {}
        return "ok", f"Identified · {esc(best.get('name'))}", f"{id_tag(best.get('person_id'))}"
    if not face.get("candidates"):
        return "warn", "No match", "The gallery is empty. Enroll people in the Gallery tab first."
    return "warn", "No match", "No enrolled person reaches the decision threshold."


def _liveness_tag(liveness: dict | None) -> str:
    if not liveness:
        return ""
    cls = "fp-tag-live" if liveness.get("live") else "fp-tag-spoof"
    word = "Live" if liveness.get("live") else "Spoof"
    return f'<span class="{cls}">{word} {fmt_score(liveness.get("score"))}</span>'


def _face_card(face: dict, probe_path: str, threshold: float, multi: bool) -> str:
    cls, title, detail = _status_view(face)
    candidates = face.get("candidates") or []
    best = candidates[0] if candidates else None
    probe_img = img_tag(crop_uri(probe_path, face.get("face_region")), alt="Probe face")
    if best:
        match_img = (f'<button type="button" class="fp-click fp-shot" data-act="person" data-id="{esc(best["person_id"])}" '
                     f'title="Open person"><p class="fp-label">{"Best match" if cls == "ok" else "Closest candidate"}</p>'
                     f'{img_tag(face_uri(best.get("face_id")), alt=best.get("name"))}'
                     f'<span class="fp-cap">{esc(best.get("name"))} {id_tag(best.get("person_id"))}</span></button>')
    else:
        match_img = '<figure class="fp-shot"><p class="fp-label">Match</p><div class="fp-noimg">—</div></figure>'
    big = f'<span class="fp-big">{fmt_score(best.get("similarity"))}</span>' if best and cls != "bad" else ""
    head = (f'<div class="fp-verdict {cls}"><div><strong>{title}</strong><div class="fp-muted" style="color:inherit">{detail}</div></div>'
            f'{_liveness_tag(face.get("liveness"))}{big}</div>')
    label = f'<p class="fp-label">Face {int(face.get("face_index", 0)) + 1}</p>' if multi else ""

    rows = []
    for rank, item in enumerate(candidates, 1):
        thumb = face_uri(item.get("face_id")) if rank <= CANDIDATE_THUMBS else None
        rows.append(
            f'<button type="button" class="fp-click fp-row{" top" if rank == 1 and cls == "ok" else ""}" '
            f'data-act="person" data-id="{esc(item.get("person_id"))}" title="Open person">'
            f'<span class="fp-rank">{rank}</span>{img_tag(thumb, "fp-avatar")}'
            f'<span class="fp-name"><span>{esc(item.get("name"))}</span>{id_tag(item.get("person_id"))}</span>'
            f'{score_cell(item.get("similarity"), threshold)}</button>'
        )
    cand = ""
    if rows:
        cand = (f'<div class="fp-list-head"><span>Candidate list</span><span>Similarity · decision threshold {fmt_score(threshold)}</span></div>'
                f'<div class="fp-rows">{"".join(rows)}</div>')
    return (f'<div class="fp-face">{label}{head}<div class="fp-pair"><figure class="fp-shot"><p class="fp-label">Probe</p>'
            f'{probe_img}</figure>{match_img}</div>{cand}</div>')


def identify_html(result: dict | None, probe_path: str | None) -> str:
    if not result:
        return ('<div class="fp-h2">Result</div><p class="fp-empty">Upload or capture a photo, then press Identify.</p>')
    threshold = float(result.get("threshold") or 0.81)
    faces = result.get("faces") or []
    multi = len(faces) > 1
    cards = "".join(_face_card(face, probe_path, threshold, multi) for face in faces)
    timing = result.get("timing") or {}
    gallery = result.get("gallery") or {}
    live_ms = ""
    if "liveness_ms" in timing:
        live_ms = f'<div class="fp-metric"><p class="fp-k">Liveness</p><p class="fp-v">{fmt_ms(timing["liveness_ms"])}</p></div>'
    metrics = (
        '<div class="fp-metrics">'
        f'<div class="fp-metric"><p class="fp-k">Detection + template</p><p class="fp-v">{fmt_ms(timing.get("extract_ms"))}</p></div>'
        f'{live_ms}'
        f'<div class="fp-metric"><p class="fp-k">Search</p><p class="fp-v">{fmt_ms(timing.get("search_ms"))}</p></div>'
        f'<div class="fp-metric"><p class="fp-k">Gallery</p><p class="fp-v">{int(gallery.get("persons", 0)):,} · {int(gallery.get("faces", 0)):,} photos</p></div>'
        f'<div class="fp-metric"><p class="fp-k">Faces in photo</p><p class="fp-v">{len(faces)}</p></div>'
        "</div>"
    )
    head = f'<div class="fp-h2" style="margin-bottom:10px">Result {count_tag(len(faces), "face")}</div>'
    return head + cards + metrics


def run_identify(probe, threshold, top_k, liveness):
    if not probe:
        raise gr.Error("Upload or capture a photo first.")
    success, data, code, message = _request(
        "POST", "/api/v1/identify",
        json={"image": _b64_file(probe), "threshold": float(threshold), "top_k": int(top_k),
              "liveness": bool(liveness), "quality": False},
    )
    if not success:
        if code == -2:
            message = "No face found. Use a photo in which the face is clearly visible."
        return f'<div class="fp-verdict warn"><strong>{esc(message)}</strong></div>', None, HIDE, ""
    faces = data.get("faces") or []
    can_enroll = len(faces) == 1 and faces[0].get("status") == "no_match"
    return identify_html(data, probe), data, gr.update(visible=can_enroll), ""


def enroll_from_identify(probe, name):
    name = (name or "").strip()
    if not probe:
        raise gr.Error("Upload a photo first.")
    if not name:
        return KEEP, '<p class="fp-alert warn">Enter a name.</p>'
    success, data, code, message = _request(
        "POST", "/api/v1/persons", json={"image": _b64_file(probe), "name": name, "source": Path(probe).name})
    if not success:
        return KEEP, f'<p class="fp-alert danger">{esc(message)}</p>'
    gr.Info(f"{name} enrolled as ID {data['person']['id']}.")
    return HIDE, f'<p class="fp-alert ok">{esc(name)} enrolled as ID {data["person"]["id"]}.</p>'


# ---------------------------------------------------------------- Gallery: grid


def grid_html(page: int, query: str, *, track: bool = False) -> tuple[str, str, int]:
    data = _api("GET", "/api/v1/persons", params={"page": page, "page_size": PAGE_SIZE, "q": query or "", "thumbs": "true"}, track=track)
    persons, total = data.get("persons") or [], int(data.get("total", 0))
    page, pages = int(data.get("page", 1)), int(data.get("pages", 1))
    if not persons:
        empty = f"Nobody matches “{esc(query)}”." if query else "The gallery is empty. Enroll a single face, select images or import a folder to start."
        return f'<p class="fp-empty">{empty}</p>', "", page
    cards = "".join(
        f'<button type="button" class="fp-click fp-card" data-act="person" data-id="{esc(p["id"])}" title="Open {esc(p["name"])}">'
        f'{img_tag(jpeg_uri(p.get("thumb")), alt=p["name"])}<p class="fp-card-name">{esc(p["name"])}</p>'
        f'<p class="fp-card-meta"><span>ID {esc(p["id"])}</span><span>{int(p.get("face_count") or 0)} photo{"s" if int(p.get("face_count") or 0) != 1 else ""}</span></p>'
        "</button>"
        for p in persons
    )
    first = (page - 1) * PAGE_SIZE + 1
    label = (f'<p class="fp-muted">{first:,}–{first + len(persons) - 1:,} of {total:,} persons · '
             f'{int(data.get("face_total", 0)):,} photos · page {page} of {pages}</p>')
    return f'<div class="fp-grid">{"".join(cards)}</div>', label, page


def gallery_nav(page, query, delta):
    return grid_html(max(1, int(page or 1) + delta), query or "", track=True)


# ---------------------------------------------------------------- Gallery: person dialog


def person_body(person: dict) -> str:
    photos = "".join(
        f'<div class="fp-photo">{img_tag(jpeg_uri(f.get("thumb")), alt="Face")}'
        f'<div class="fp-photo-meta"><span title="{esc(f.get("source"))}">#{esc(f["id"])}'
        f'{" · Q " + str(int(f["quality"])) + "%" if f.get("quality") is not None else ""}</span>'
        f'<button type="button" class="fp-textlink danger" data-act="del-face" data-id="{esc(f["id"])}">Remove</button></div></div>'
        for f in person.get("faces") or []
    )
    note = f'<p class="fp-muted" style="margin-top:6px">{esc(person.get("note"))}</p>' if person.get("note") else ""
    return (f'{note}<p class="fp-muted" style="margin-top:8px">{count_tag(person.get("face_count", 0), "photo")} '
            f'Enrolling several photos of the same person improves identification accuracy.</p><div class="fp-photos">{photos}</div>')


def open_person(person_id):
    person = _api("GET", f"/api/v1/persons/{int(person_id)}", params={"thumbs": "true"})
    title = f'<div class="fp-h2">{esc(person["name"])} {id_tag(person["id"])}</div>'
    return SHOW, title, person_body(person), int(person_id), person["name"], person.get("note") or "", HIDE, HIDE, None


def on_html_click(evt: gr.EventData, query, page):
    """Clicks in the identify result, gallery grid, similar list or person dialog."""
    act = getattr(evt, "act", "") or ""
    target = getattr(evt, "id", "") or ""
    # outputs: person_modal, person_title, person_body, person_state, p_name, p_note,
    #          p_confirm, face_confirm, face_state
    if act == "person" and target:
        return open_person(target)
    if act == "del-face" and target:
        return [KEEP] * 7 + [SHOW, int(target)]
    return [KEEP] * 9


def save_person(person_id, name, note, query, page):
    if person_id is None:
        raise gr.Error("No person selected.")
    person = _api("PATCH", f"/api/v1/persons/{int(person_id)}", json={"name": name, "note": note})
    gr.Info("Saved.")
    grid, label, page = grid_html(page, query)
    return f'<div class="fp-h2">{esc(person["name"])} {id_tag(person["id"])}</div>', grid, label, page


def delete_person(person_id, query, page):
    if person_id is None:
        raise gr.Error("No person selected.")
    _api("DELETE", f"/api/v1/persons/{int(person_id)}")
    gr.Info("Person deleted.")
    grid, label, page = grid_html(page, query)
    return HIDE, HIDE, None, grid, label, page, header_html()


def delete_face(face_id, person_id, query, page):
    if face_id is None:
        raise gr.Error("No photo selected.")
    data = _api("DELETE", f"/api/v1/faces/{int(face_id)}")
    grid, label, page = grid_html(page, query)
    if data.get("person_deleted"):
        gr.Info("Last photo removed, so the person was deleted.")
        return HIDE, KEEP, HIDE, None, None, grid, label, page, header_html()
    person = _api("GET", f"/api/v1/persons/{int(person_id)}", params={"thumbs": "true"})
    gr.Info("Photo removed.")
    return KEEP, person_body(person), HIDE, None, person_id, grid, label, page, header_html()


# ---------------------------------------------------------------- Gallery: similar / delete all


def similar_html() -> str:
    data = _api("POST", "/api/v1/similar", json={})
    pairs = data.get("pairs") or []
    if not pairs:
        return (f'<p class="fp-alert ok">No possible duplicates at decision threshold {fmt_score(data.get("threshold"))}. '
                f'{int(data.get("comparisons", 0)):,} photo pairs compared.</p>')

    def side(p):
        return (f'<button type="button" class="fp-click fp-pairside" data-act="person" data-id="{esc(p["person_id"])}">'
                f'{img_tag(face_uri(p.get("face_id")), "fp-avatar")}<span>{esc(p["name"])}</span>{id_tag(p["person_id"])}</button>')

    rows = "".join(
        f'<div class="fp-pairrow">{side(item["a"])}<span class="fp-score" style="justify-content:center">{fmt_score(item["similarity"])}</span>{side(item["b"])}</div>'
        for item in pairs[:50]
    )
    return (f'<p class="fp-muted">{int(data.get("pair_count", 0)):,} pairs of different persons match at or above the '
            f'decision threshold {fmt_score(data.get("threshold"))}. Each pair may be one person enrolled twice. '
            f'Open a person to rename them or remove photos.</p>'
            f'<div class="fp-pairs">{rows}</div>')


def ask_clear():
    total = int(_api("GET", "/api/v1/persons", params={"page_size": 1}).get("total", 0))
    if not total:
        gr.Info("The gallery is already empty.")
        return HIDE, ""
    return SHOW, (f'<p class="fp-alert danger">Delete all {total:,} persons and their photos? '
                  "This can't be undone.</p>")


def clear_all():
    data = _api("DELETE", "/api/v1/persons", params={"confirm": "true"})
    gr.Info(f"Deleted {int(data.get('persons_deleted', 0)):,} persons.")
    grid, label, page = grid_html(1, "")
    return HIDE, grid, label, page, header_html()


# ---------------------------------------------------------------- Gallery: enroll queue


def _collect(paths: list[str]) -> list[Path]:
    found, seen = [], set()
    for raw in paths:
        path = Path(raw)
        items = [path] if path.is_file() else sorted(path.rglob("*")) if path.is_dir() else []
        for item in items:
            if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES:
                key = str(item.resolve()).lower()
                if key not in seen:
                    seen.add(key)
                    found.append(item)
    return found


def _pick_folder() -> str:
    """Native folder picker on this computer (the demo runs locally)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        raise gr.Error("Folder picker is not available. Select the images instead.") from None
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    folder = filedialog.askdirectory(parent=root, title="Choose a folder of face photos")
    root.destroy()
    return folder or ""


def _queue_summary(files: list[Path], source: str) -> str:
    if not files:
        return '<p class="fp-alert warn">No photos found (JPG, PNG, WEBP or BMP).</p>'
    folders = len({f.parent for f in files})
    where = f" in <strong>{esc(source)}</strong>" if source else ""
    extra = f" across {folders:,} folders" if folders > 1 else ""
    return f'<p class="fp-muted">{len(files):,} photo{"s" if len(files) != 1 else ""} ready{where}{extra}. Press Enroll to start.</p>'


def choose_files(files):
    paths = [str(f) for f in (files or [])]
    images = _collect(paths)
    return [str(p) for p in images], _queue_summary(images, ""), ""


def choose_folder():
    folder = _pick_folder()
    if not folder:
        return KEEP, KEEP, KEEP
    images = _collect([folder])
    return [str(p) for p in images], _queue_summary(images, folder), ""


def _progress(text: str, pct: float) -> str:
    pct = max(0, min(100, int(pct)))
    return (f'<div class="fp-progress"><div style="display:flex;justify-content:space-between">'
            f'<span class="fp-muted">{esc(text)}</span><span class="fp-muted">{pct}%</span></div>'
            f'<div class="fp-progress-track"><div class="fp-progress-fill" style="width:{pct}%"></div></div></div>')


def _queue_html(rows: list[dict], counts: dict, text: str, pct: float) -> str:
    chips = (f'<div class="fp-counts"><span class="fp-tag-live">{counts["ok"]:,} enrolled</span>'
             f'<span class="fp-tag-warn">{counts["dup"]:,} already enrolled</span>'
             f'<span class="fp-tag-spoof">{counts["bad"]:,} skipped</span></div>')
    shown = rows[::-1]
    items = "".join(
        f'<div class="fp-q">{img_tag(r.get("thumb"))}<div style="min-width:0"><div>{esc(r["name"]) or "<em>no name</em>"}</div>'
        f'<div class="fp-q-file" title="{esc(r["file"])}">{esc(Path(r["file"]).name)}</div></div>'
        f'<span class="fp-st {r["cls"]}">{esc(r["status"])}</span></div>'
        for r in shown
    )
    queue = f'<div class="fp-queue">{items}</div>' if items else ""
    return _progress(text, pct) + chips + queue


def run_enroll(paths, dup_choice, query, page):
    """Generator: enroll each photo and stream the queue. The person name is the file name."""
    files = [Path(p) for p in paths or []]
    if not files:
        raise gr.Error("Select images or import a folder first.")
    on_dup = DUP_CHOICES.get(dup_choice, "reject")
    rows: list[dict] = []
    counts = {"ok": 0, "dup": 0, "bad": 0}
    started = time.perf_counter()
    total = len(files)
    upload_ms = process_ms = 0.0
    _remember("Batch enrollment", f"{total} photo" + ("" if total == 1 else "s"), ok=True)
    yield _queue_html(rows, counts, "Starting…", 0), KEEP, KEEP, KEEP, KEEP, KEEP, status_html()
    for index, path in enumerate(files, 1):
        name = path.stem
        row = {"file": str(path), "name": name, "thumb": file_thumb_uri(path, 72)}
        success, data, code, message = _request(
            "POST", "/api/v1/persons",
            json={"image": _b64_file(path), "name": name, "merge_by_name": True,
                  "on_duplicate": on_dup, "source": path.name},
        )
        if success:
            counts["ok"] += 1
            person = data["person"]
            if data.get("duplicate") and on_dup == "add":
                row.update(status=f"Added to {person['name']} (ID {person['id']})", cls="ok")
            elif data.get("person_created"):
                row.update(status=f"Enrolled · ID {person['id']}", cls="ok")
            else:
                row.update(status=f"Added photo · ID {person['id']}", cls="ok")
            warnings = (data.get("quality") or {}).get("warnings") or []
            if warnings:
                row["status"] += " · " + warnings[0].split(".")[0].lower()
        elif code == -7:
            counts["dup"] += 1
            dup = ((data or {}).get("duplicate") or {})
            row.update(status=f"Looks like {dup.get('name', '?')} (ID {dup.get('person_id', '?')}) · skipped", cls="warn")
        else:
            counts["bad"] += 1
            short = {-2: "No face", -6: "More than one face", -8: "Low quality"}.get(code) or message.split(".")[0]
            row.update(status=short, cls="bad")
        rows.append(row)
        upload_ms += _STATUS["upload_ms"] or 0
        process_ms += _STATUS["process_ms"] or 0
        yield _queue_html(rows, counts, f"{index:,} of {total:,} · {path.name}", 100 * index / total), KEEP, KEEP, KEEP, KEEP, KEEP, status_html()
    elapsed = time.perf_counter() - started
    grid, label, page = grid_html(1, query)
    done = f"Done in {elapsed:.1f}s · {counts['ok']:,} enrolled, {counts['dup']:,} already enrolled, {counts['bad']:,} skipped"
    _remember(
        "Batch enrollment",
        f"{counts['ok']:,} enrolled, {counts['dup']:,} already enrolled, {counts['bad']:,} skipped",
        upload_ms=upload_ms, process_ms=process_ms, total_ms=elapsed * 1000, ok=counts["bad"] == 0,
    )
    yield _queue_html(rows, counts, done, 100), grid, label, page, header_html(), [], status_html()


def capture_enroll(image, name, note, query, page):
    if not image:
        return '<p class="fp-alert warn">Upload a file or capture a photo first.</p>', KEEP, KEEP, KEEP, KEEP
    name = (name or "").strip() or Path(image).stem.strip()
    if not name:
        return '<p class="fp-alert warn">Enter a name.</p>', KEEP, KEEP, KEEP, KEEP
    success, data, code, message = _request(
        "POST", "/api/v1/persons",
        json={"image": _b64_file(image), "name": name, "note": note or "", "merge_by_name": True, "source": Path(image).name},
    )
    if not success:
        if code == -7:
            dup = (data or {}).get("duplicate") or {}
            message = f"This face is already enrolled as {dup.get('name')} (ID {dup.get('person_id')})."
        return f'<p class="fp-alert danger">{esc(message)}</p>', KEEP, KEEP, KEEP, KEEP
    person = data["person"]
    grid, label, page = grid_html(1, query)
    verb = "enrolled" if data.get("person_created") else "got a new photo"
    return f'<p class="fp-alert ok">{esc(person["name"])} {verb} · ID {person["id"]}.</p>', grid, label, page, header_html()


# ---------------------------------------------------------------- settings


def load_settings():
    s = _settings()
    return (float(s["threshold"]), bool(s["duplicate_check"]),
            gr.update(value=bool(s["liveness_check"]), visible=bool(s.get("liveness_available"))),
            float(s["threshold"]), gr.update(value=bool(s["liveness_check"]), visible=bool(s.get("liveness_available"))))


def save_settings(threshold, duplicate_check, liveness_check):
    s = _api("PATCH", "/api/v1/settings", json={
        "threshold": float(threshold), "duplicate_check": bool(duplicate_check),
        "liveness_check": bool(liveness_check)})
    gr.Info("Settings saved.")
    return HIDE, float(s["threshold"]), gr.update(value=bool(s["liveness_check"]))


# ---------------------------------------------------------------- API tab

API_ROWS = [
    ("POST", "/api/v1/detect", "Face detection: region, landmarks, pose and attributes"),
    ("POST", "/api/v1/quality", "Face image quality assessment"),
    ("POST", "/api/v1/match", "1:1 verification of two photos"),
    ("POST", "/api/v1/identify", "1:N identification against the gallery"),
    ("GET", "/api/v1/persons", "List enrolled persons (paged, searchable)"),
    ("POST", "/api/v1/persons", "Enroll a face (duplicate check; quality check optional)"),
    ("PATCH", "/api/v1/persons/{id}", "Update a person's name or note"),
    ("DELETE", "/api/v1/persons/{id}", "Delete a person and their photos"),
    ("DELETE", "/api/v1/faces/{id}", "Remove one enrolled photo"),
    ("GET", "/api/v1/faces/{id}/image", "Face crop or original photo"),
    ("POST", "/api/v1/similar", "Find possible duplicate persons"),
    ("GET / PATCH", "/api/v1/settings", "Decision threshold and enrollment checks"),
]


def api_html() -> str:
    shown = API_ROWS if HAS_GALLERY else [r for r in API_ROWS[:3]] + [
        ("POST", "/api/v1/feature", "Extract biometric templates"), ("POST", "/api/v1/similarity", "Compare two templates")]
    if not HAS_GALLERY:  # older API: no /v1 segment, no Swagger page
        shown = [(m, p.replace("/api/v1/", "/api/"), d) for m, p, d in shown]
        return ('<div class="fp-api"><p class="fp-muted">Send images as base64 JSON. '
                'Postman collection: <code>postman/</code></p><table><tr><th>Method</th><th>Path</th><th>Description</th></tr>'
                + "".join(f"<tr><td><code>{m}</code></td><td><code>{esc(p)}</code></td><td>{esc(d)}</td></tr>" for m, p, d in shown)
                + "</table></div>")
    rows = "".join(f"<tr><td><code>{m}</code></td><td><code>{esc(p)}</code></td><td>{esc(d)}</td></tr>" for m, p, d in shown)
    curl = (f'curl -X POST {API}/api/v1/persons \\\n  -F "image=@anna.jpg" -F "name=Anna Lee"\n\n'
            f'curl -X POST {API}/api/v1/identify \\\n  -F "image=@probe.jpg" -F "top_k=5"')
    return f"""<div class="fp-api">
    <p><a class="fp-link" href="{API}/docs" target="_blank" rel="noopener">Open the interactive API docs (Swagger)</a> ·
    <a class="fp-link" href="{API}/api/v1/openapi.json" target="_blank" rel="noopener">openapi.json</a> ·
    Postman collection: <code>postman/FaceRecognition-API.postman_collection.json</code></p>
    <p class="fp-muted">Every route also works without <code>/v1</code> for older clients. Send images as base64 JSON or as form-data files.</p>
    <pre>{esc(curl)}</pre>
    <table><tr><th>Method</th><th>Path</th><th>Description</th></tr>{rows}</table></div>"""


# ---------------------------------------------------------------- layout


def info(text: str):
    """One-line description at the top of a tab."""
    return gr.HTML(f'<p class="fp-intro" style="margin:6px 2px 12px">{text}</p>')


with gr.Blocks(title="FacePlugin Face Recognition", fill_width=True) as demo:
    identify_state = gr.State(None)
    person_state = gr.State(None)
    face_state = gr.State(None)
    gal_page = gr.State(1)
    queue_paths = gr.State([])

    with gr.Row(equal_height=True):
        header = gr.HTML(header_html(), scale=8)
        settings_btn = gr.Button("Settings", size="sm", scale=0, min_width=100, elem_id="fp-settings",
                                 visible=HAS_GALLERY)
    status = gr.HTML(status_html(), elem_id="fp-status")
    demo_ui.consent_row(
        "Faces are processed and stored only on this computer. Nothing is sent to FacePlugin or anyone else. "
        "Get consent from people before you enroll them." if HAS_GALLERY else
        "Faces are processed only on this computer. Nothing is sent to FacePlugin or anyone else.")

    with gr.Tabs():
        # ---- Face Detection
        with gr.Tab("Face Detection"):
            info("Locates every face in a photo and returns its bounding box, landmarks, head pose and "
                 "attributes such as age, glasses and mask.")
            with gr.Row():
                with gr.Column(elem_classes=["fp-panel"]):
                    det_in = gr.Image(type="filepath", label="Photo", sources=["upload", "webcam", "clipboard"])
                    if ALL_EX:
                        gr.Examples(ALL_EX, inputs=det_in, label="Samples")
                    det_btn = gr.Button("Detect faces", elem_classes=["primary"])
                with gr.Column(elem_classes=["fp-panel"]):
                    with gr.Tabs():
                        with gr.Tab("Result"):
                            det_summary = gr.Markdown("*Choose a photo and press Detect faces.*")
                            det_table = demo_ui.result_dataframe(headers=["Attribute", "Value", "Confidence"], label="Attributes")
                        with gr.Tab("Raw JSON"):
                            det_raw = gr.Code(language="json", label="API response")
            det_btn.click(detect, det_in, [det_summary, det_table, det_raw]).then(status_html, outputs=[status])

        # ---- Quality Assessment
        with gr.Tab("Quality Assessment"):
            info("Measures face image quality against ICAO-style criteria: sharpness, lighting, head pose, "
                 "occlusion and face size. Use it to decide whether a photo is suitable for enrollment "
                 "or verification.")
            with gr.Row():
                with gr.Column(elem_classes=["fp-panel"]):
                    q_in = gr.Image(type="filepath", label="Photo", sources=["upload", "webcam", "clipboard"])
                    if ALL_EX:
                        gr.Examples(ALL_EX, inputs=q_in, label="Samples")
                    q_btn = gr.Button("Assess quality", elem_classes=["primary"])
                with gr.Column(elem_classes=["fp-panel"]):
                    with gr.Tabs():
                        with gr.Tab("Result"):
                            q_summary = gr.Markdown("*Choose a photo and press Assess quality.*")
                            q_table = demo_ui.result_dataframe(headers=["Check", "Value", "Status / range"], label="Quality checks")
                        with gr.Tab("Raw JSON"):
                            q_raw = gr.Code(language="json", label="API response")
            q_btn.click(quality, q_in, [q_summary, q_table, q_raw]).then(status_html, outputs=[status])

        # ---- 1:1 Verification
        with gr.Tab("1:1 Verification"):
            info("<strong>1:1 verification</strong> compares the faces in two photos and decides whether they "
                 "belong to the same person. The result is a match when the similarity score reaches the "
                 "decision threshold" + (" set in Settings." if HAS_GALLERY else "."))
            with gr.Row():
                with gr.Column(elem_classes=["fp-panel"]):
                    with gr.Row():
                        m1 = gr.Image(type="filepath", label="Photo 1", sources=["upload", "webcam", "clipboard"])
                        m2 = gr.Image(type="filepath", label="Photo 2", sources=["upload", "webcam", "clipboard"])
                    with gr.Row():
                        if ODD_EX:
                            gr.Examples(ODD_EX, inputs=m1, label="Photo 1 samples")
                        if EVEN_EX:
                            gr.Examples(EVEN_EX, inputs=m2, label="Photo 2 samples")
                    m_btn = gr.Button("Verify", elem_classes=["primary"])
                with gr.Column(elem_classes=["fp-panel"]):
                    m_verdict = gr.HTML("")
                    with gr.Tabs():
                        with gr.Tab("Result"):
                            m_summary = gr.Markdown("*Choose two photos and press Verify.*")
                            m_table = demo_ui.result_dataframe(headers=["Pair", "Faces", "Similarity"], label="Similarity per face pair")
                        with gr.Tab("Raw JSON"):
                            m_raw = gr.Code(language="json", label="API response")
            m_btn.click(match, [m1, m2], [m_verdict, m_summary, m_table, m_raw]).then(status_html, outputs=[status])

        # ---- 1:N Identification
        with gr.Tab("1:N Identification", visible=HAS_GALLERY):
            info("<strong>1:N identification</strong> searches the enrollment gallery for every face in the "
                 "photo and returns a ranked candidate list. The gallery is a local database on this computer; "
                 "enroll, edit or remove people in the <strong>Gallery</strong> tab.")
            with gr.Row(equal_height=False):
                with gr.Column(scale=5, elem_classes=["fp-panel"]):
                    probe = gr.Image(type="filepath", label="Probe photo", sources=["upload", "webcam", "clipboard"], height=320)
                    id_threshold = gr.Slider(0.50, 0.99, value=0.81, step=0.01, label="Decision threshold")
                    id_topk = gr.Slider(1, 20, value=5, step=1, label="Candidate list size")
                    id_liveness = gr.Checkbox(value=True, label="Liveness check before search", visible=False)
                    id_btn = gr.Button("Identify", elem_classes=["primary"], size="lg")
                with gr.Column(scale=7, elem_classes=["fp-panel"]):
                    id_result = gr.HTML(identify_html(None, None), js_on_load=CLICK_JS)
                    with gr.Group(visible=False) as id_enroll:
                        gr.HTML('<p class="fp-muted" style="padding:8px 8px 0">No match in the gallery. Enroll this face as a new person?</p>')
                        with gr.Row(equal_height=True):
                            id_name = gr.Textbox(placeholder="Anna Lee", show_label=False, scale=4, container=False)
                            id_enroll_btn = gr.Button("Enroll", size="sm", scale=0, min_width=90, elem_classes=["primary"])
                    id_enroll_msg = gr.HTML("")
            id_btn.click(run_identify, [probe, id_threshold, id_topk, id_liveness],
                         [id_result, identify_state, id_enroll, id_enroll_msg]).then(status_html, outputs=[status])
            id_enroll_btn.click(enroll_from_identify, [probe, id_name], [id_enroll, id_enroll_msg]).then(
                status_html, outputs=[status])

        # ---- Gallery
        with gr.Tab("Gallery", visible=HAS_GALLERY) as gallery_tab:
            info("The enrollment gallery is a local database on this computer (<code>data/gallery.sqlite</code>) "
                 "used by 1:N identification. Enroll a single face or many at once, search, and open a person "
                 "to edit their name and note or remove photos.")
            with gr.Row(equal_height=False):
                with gr.Column(scale=4, elem_classes=["fp-panel"]):
                    gr.HTML('<div class="fp-h2">Single enrollment</div>'
                            '<p class="fp-muted" style="margin-top:4px">Upload or capture one photo of one person. '
                            "If the name is empty, the file name is used.</p>")
                    cap_img = gr.Image(type="filepath", label="Photo", sources=["upload", "webcam", "clipboard"], height=240)
                    cap_name = gr.Textbox(label="Name", placeholder="Anna Lee")
                    cap_note = gr.Textbox(label="Note (optional)", placeholder="Employee 2041")
                    cap_btn = gr.Button("Enroll", size="sm", elem_classes=["primary"])
                    cap_msg = gr.HTML("")
                    gr.HTML('<div class="fp-h2" style="margin-top:18px">Batch enrollment</div>'
                            '<p class="fp-muted" style="margin-top:4px">One person per photo. '
                            "Each person is named after the file name; photos with the same name "
                            "are enrolled under the same person.</p>")
                    files_in = gr.File(file_count="multiple", file_types=["image"], label="Select images (1 or more)", height=120)
                    folder_btn = gr.Button("Import from directory…", size="sm")
                    queue_info = gr.HTML("")
                    dup_mode = gr.Dropdown(list(DUP_CHOICES), value="Skip it", label="When a face matches another enrolled person")
                    enroll_btn = gr.Button("Enroll", elem_classes=["primary"])
                    queue_html = gr.HTML("")
                with gr.Column(scale=7, elem_classes=["fp-panel"]):
                    with gr.Row(equal_height=True):
                        gal_query = gr.Textbox(placeholder="Search name, note or ID", show_label=False, scale=4, container=False)
                        similar_btn = gr.Button("Find duplicates", size="sm", scale=0, min_width=130)
                        clear_btn = gr.Button("Delete all", size="sm", scale=0, min_width=100, elem_classes=["fp-danger-btn"])
                    gal_grid = gr.HTML("", js_on_load=CLICK_JS)
                    with gr.Row(elem_classes=["fp-pager"], equal_height=True):
                        gal_label = gr.HTML("")
                        gal_prev = gr.Button("‹ Previous", size="sm", scale=0, min_width=110)
                        gal_next = gr.Button("Next ›", size="sm", scale=0, min_width=90)

        # ---- API
        with gr.Tab("API"):
            gr.HTML(api_html())

    # ---- dialogs
    with gr.Column(visible=False, elem_classes=["fp-modal"]) as person_modal:
        with gr.Column(elem_classes=["fp-dialog"]):
            with gr.Row(elem_classes=["fp-dialog-head"]):
                person_title = gr.HTML("")
                person_close = gr.Button("Close", size="sm", scale=0, elem_classes=["fp-x", "fp-modal-close"])
            with gr.Row(equal_height=True):
                p_name = gr.Textbox(label="Name", scale=3)
                p_note = gr.Textbox(label="Note", scale=4)
                p_save = gr.Button("Save", size="sm", scale=0, min_width=80)
            person_body_html = gr.HTML("", js_on_load=CLICK_JS)
            with gr.Column(visible=False) as face_confirm:
                gr.HTML('<p class="fp-alert danger">Remove this photo? If it is the last one, the person is deleted too.</p>')
                with gr.Row(elem_classes=["fp-actions"]):
                    face_no = gr.Button("Cancel", size="sm", scale=0)
                    face_yes = gr.Button("Remove photo", size="sm", scale=0, variant="stop")
            with gr.Row():
                p_delete = gr.Button("Delete this person…", size="sm", scale=0, elem_classes=["fp-danger-btn"])
            with gr.Column(visible=False) as p_confirm:
                gr.HTML('<p class="fp-alert danger">Delete this person and all their photos? This can\'t be undone.</p>')
                with gr.Row(elem_classes=["fp-actions"]):
                    p_no = gr.Button("Cancel", size="sm", scale=0)
                    p_yes = gr.Button("Delete person", size="sm", scale=0, variant="stop")

    with gr.Column(visible=False, elem_classes=["fp-modal"]) as similar_modal:
        with gr.Column(elem_classes=["fp-dialog"]):
            with gr.Row(elem_classes=["fp-dialog-head"]):
                gr.HTML('<div class="fp-h2">Possible duplicates</div>')
                similar_close = gr.Button("Close", size="sm", scale=0, elem_classes=["fp-x", "fp-modal-close"])
            similar_body = gr.HTML("", js_on_load=CLICK_JS)

    with gr.Column(visible=False, elem_classes=["fp-modal"]) as clear_modal:
        with gr.Column(elem_classes=["fp-dialog", "narrow"]):
            gr.HTML('<div class="fp-h2">Delete the whole gallery</div>')
            clear_text = gr.HTML("")
            with gr.Row(elem_classes=["fp-actions"]):
                clear_no = gr.Button("Cancel", size="sm", scale=0, elem_classes=["fp-modal-close"])
                clear_yes = gr.Button("Delete all", size="sm", scale=0, variant="stop")

    with gr.Column(visible=False, elem_classes=["fp-modal"]) as settings_modal:
        with gr.Column(elem_classes=["fp-dialog", "narrow"]):
            with gr.Row(elem_classes=["fp-dialog-head"]):
                gr.HTML('<div class="fp-h2">Settings</div>')
                settings_close = gr.Button("Close", size="sm", scale=0, elem_classes=["fp-x", "fp-modal-close"])
            s_threshold = gr.Slider(0.50, 0.99, value=0.81, step=0.01, label="Decision threshold",
                                    info="Used by 1:1 verification and 1:N identification. A higher value lowers the "
                                         "false match rate and raises the false non-match rate.")
            s_duplicate = gr.Checkbox(value=True, label="Duplicate check on enrollment",
                                      info="Blocks enrolling a face that already matches another person.")
            s_liveness = gr.Checkbox(value=True, label="Liveness check before 1:N identification", visible=False)
            with gr.Row(elem_classes=["fp-actions"]):
                settings_save = gr.Button("Save", size="sm", scale=0, elem_classes=["primary"])

    # ---- events
    demo.load(load_settings, outputs=[s_threshold, s_duplicate, s_liveness, id_threshold, id_liveness]).then(
        header_html, outputs=[header])
    settings_btn.click(load_settings, outputs=[s_threshold, s_duplicate, s_liveness, id_threshold, id_liveness]).then(
        lambda: SHOW, outputs=[settings_modal])
    settings_close.click(lambda: HIDE, outputs=[settings_modal])
    settings_save.click(save_settings, [s_threshold, s_duplicate, s_liveness],
                        [settings_modal, id_threshold, id_liveness]).then(status_html, outputs=[status])

    def _refresh(query):
        grid, label, page = grid_html(1, query, track=True)
        return grid, label, page, header_html()

    gallery_tab.select(_refresh, [gal_query], [gal_grid, gal_label, gal_page, header]).then(status_html, outputs=[status])
    gal_query.change(lambda q: grid_html(1, q, track=True), [gal_query], [gal_grid, gal_label, gal_page], trigger_mode="always_last").then(
        status_html, outputs=[status])
    gal_prev.click(lambda p, q: gallery_nav(p, q, -1), [gal_page, gal_query], [gal_grid, gal_label, gal_page]).then(
        status_html, outputs=[status])
    gal_next.click(lambda p, q: gallery_nav(p, q, 1), [gal_page, gal_query], [gal_grid, gal_label, gal_page]).then(
        status_html, outputs=[status])

    files_in.change(choose_files, [files_in], [queue_paths, queue_info, queue_html])
    folder_btn.click(choose_folder, outputs=[queue_paths, queue_info, queue_html])
    enroll_btn.click(run_enroll, [queue_paths, dup_mode, gal_query, gal_page],
                     [queue_html, gal_grid, gal_label, gal_page, header, queue_paths, status])
    cap_btn.click(capture_enroll, [cap_img, cap_name, cap_note, gal_query, gal_page],
                  [cap_msg, gal_grid, gal_label, gal_page, header]).then(status_html, outputs=[status])

    click_outputs = [person_modal, person_title, person_body_html, person_state, p_name, p_note,
                     p_confirm, face_confirm, face_state]
    for source in (id_result, gal_grid, similar_body, person_body_html):
        source.click(on_html_click, [gal_query, gal_page], click_outputs).then(status_html, outputs=[status])

    person_close.click(lambda: (HIDE, HIDE, HIDE), outputs=[person_modal, p_confirm, face_confirm])
    p_save.click(save_person, [person_state, p_name, p_note, gal_query, gal_page],
                 [person_title, gal_grid, gal_label, gal_page]).then(status_html, outputs=[status])
    p_delete.click(lambda: SHOW, outputs=[p_confirm])
    p_no.click(lambda: HIDE, outputs=[p_confirm])
    p_yes.click(delete_person, [person_state, gal_query, gal_page],
                [person_modal, p_confirm, person_state, gal_grid, gal_label, gal_page, header]).then(
        status_html, outputs=[status])
    face_no.click(lambda: (HIDE, None), outputs=[face_confirm, face_state])
    face_yes.click(delete_face, [face_state, person_state, gal_query, gal_page],
                   [person_modal, person_body_html, face_confirm, face_state, person_state,
                    gal_grid, gal_label, gal_page, header]).then(status_html, outputs=[status])

    similar_btn.click(lambda: (SHOW, '<p class="fp-muted">Comparing every photo with every other photo…</p>'),
                      outputs=[similar_modal, similar_body]).then(similar_html, outputs=[similar_body]).then(
        status_html, outputs=[status])
    similar_close.click(lambda: HIDE, outputs=[similar_modal])
    clear_btn.click(ask_clear, outputs=[clear_modal, clear_text]).then(status_html, outputs=[status])
    clear_no.click(lambda: HIDE, outputs=[clear_modal])
    clear_yes.click(clear_all, outputs=[clear_modal, gal_grid, gal_label, gal_page, header]).then(
        status_html, outputs=[status])


if __name__ == "__main__":
    allowed = [str(ROOT), tempfile.gettempdir()]
    if os.name == "nt":
        allowed.extend(f"{d}:\\" for d in "CDEFGHIJKLMNOPQRSTUVWXYZ")
    else:
        allowed.append("/")
    demo_ui.launch(demo, DEMO_PORT, allowed_paths=allowed)
