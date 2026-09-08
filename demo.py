"""Gradio demo — Face Recognition API (local only; not shipped in Docker)."""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

import gradio as gr
import requests

import demo_ui

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "assets" / "examples" / "samples"
API = os.environ.get(
    "API_BASE",
    f"http://127.0.0.1:{os.environ.get('PORT', os.environ.get('FACESDK_PORT', '8083'))}",
).rstrip("/")
DEMO_PORT = int(os.environ.get("DEMO_PORT", "9003"))

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _list_images() -> list[Path]:
    if not SAMPLES.is_dir():
        return []
    return sorted(
        p for p in SAMPLES.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    )


def _sample_index(path: Path) -> int | None:
    m = re.match(r"(?:odd|even)\((\d+)\)", path.stem, re.I)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d+)$", path.stem)
    if m:
        return int(m.group(1))
    return None


def _sorted_sample_paths() -> list[Path]:
    indexed = [(idx, p) for p in _list_images() if (idx := _sample_index(p)) is not None]
    indexed.sort(key=lambda item: item[0])
    return [p for _, p in indexed]


def _face_examples() -> list[str]:
    numbered = [str(p) for p in _sorted_sample_paths()]
    return numbered or [str(p) for p in _list_images()]


def _odd_examples() -> list[str]:
    return [str(p) for p in _sorted_sample_paths() if (_sample_index(p) or 0) % 2 == 1]


def _even_examples() -> list[str]:
    return [str(p) for p in _sorted_sample_paths() if (_sample_index(p) or 0) % 2 == 0]


def _error(msg: str):
    return f"**Error:** {msg}", [], ""


def _fetch_license_status() -> dict:
    try:
        r = requests.get(f"{API}/api/licenseStatus", timeout=5)
        body = r.json() if r.ok else {}
        data = body.get("data") if isinstance(body, dict) else None
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _license_banner_md(status: dict | None = None) -> str:
    st = status if status is not None else _fetch_license_status()
    label = str(st.get("label") or "").strip()
    if not label:
        if st.get("licensed"):
            label = str(st.get("levelName") or "Licensed")
        else:
            label = "Not licensed / unavailable"
    return f"**License:** {label}"


def _capability_notes_md(status: dict | None = None) -> str:
    st = status if status is not None else _fetch_license_status()
    if not st.get("recognition"):
        return "**Note:** Recognition (Detect / Quality / Match) is **not available** on this license."
    return ""


def _license_header() -> tuple[str, str]:
    st = _fetch_license_status()
    return _license_banner_md(st), _capability_notes_md(st)


def _notes_md(status: dict) -> str | None:
    if not status.get("licensed"):
        return "**Note:** Not licensed — activate with an FP1 key first."
    if not status.get("recognition"):
        return "**Note:** Recognition is not available on this license."
    return None


def _post(path: str, body: dict) -> dict | tuple:
    try:
        r = requests.post(f"{API}{path}", json=body, timeout=120)
        payload = r.json()
    except Exception as ex:  # noqa: BLE001
        return _error(str(ex))
    if not isinstance(payload, dict):
        return "**Unexpected response.**", [], json.dumps(payload, indent=2)
    return payload


def _attr_value(attr: Any) -> str:
    if not isinstance(attr, dict):
        return _cell(attr)
    return _cell(attr.get("value"))


def _attr_extra(attr: Any, *, kind: str) -> str:
    if not isinstance(attr, dict):
        return ""
    if kind == "detect":
        if "confidence" in attr:
            return _cell(attr["confidence"])
        return ""
    # quality
    parts = []
    if "status" in attr:
        parts.append(f"status={_cell(attr['status'])}")
    if "range" in attr:
        parts.append(f"range={_cell(attr['range'])}")
    return ", ".join(parts)


def format_detect(payload: dict) -> tuple[str, list[list[str]], str]:
    """Result = face attributes only (not geometry / full dump)."""
    status = payload.get("status")
    faces = payload.get("data") if isinstance(payload.get("data"), list) else []
    summary = (
        f"**Detect** — status={status}  ·  faces={len(faces)}"
        + (f"  ·  {payload.get('message')}" if payload.get("message") else "")
    )
    rows: list[list[str]] = []
    for face in faces:
        if not isinstance(face, dict):
            continue
        face_id = face.get("faceId", "")
        prefix = f"face[{face_id}]"
        rows.append([f"{prefix}.facePose", _cell(face.get("facePose")), ""])
        rows.append([f"{prefix}.faceRegion", _cell(face.get("faceRegion")), ""])
        attrs = face.get("attributes") or {}
        if isinstance(attrs, dict):
            for name, attr in attrs.items():
                rows.append(
                    [f"{prefix}.{name}", _attr_value(attr), _attr_extra(attr, kind="detect")]
                )
    raw = json.dumps(payload, indent=2, ensure_ascii=False)
    return summary, rows, raw


def format_quality(payload: dict) -> tuple[str, list[list[str]], str]:
    """Result = quality checks only."""
    status = payload.get("status")
    faces = payload.get("data") if isinstance(payload.get("data"), list) else []
    summary = (
        f"**Quality** — status={status}  ·  faces={len(faces)}"
        + (f"  ·  {payload.get('message')}" if payload.get("message") else "")
    )
    rows: list[list[str]] = []
    for face in faces:
        if not isinstance(face, dict):
            continue
        face_id = face.get("faceId", "")
        prefix = f"face[{face_id}]"
        attrs = face.get("attributes") or {}
        if isinstance(attrs, dict):
            for name, attr in attrs.items():
                rows.append(
                    [f"{prefix}.{name}", _attr_value(attr), _attr_extra(attr, kind="quality")]
                )
    raw = json.dumps(payload, indent=2, ensure_ascii=False)
    return summary, rows, raw


def format_match(payload: dict) -> tuple[str, list[list[str]], str]:
    """Result = similarity pairs only (geometry stays in Raw JSON)."""
    status = payload.get("status")
    matches = payload.get("match") if isinstance(payload.get("match"), list) else []
    summary = (
        f"**Match** — status={status}  ·  pairs={len(matches)}"
        + (f"  ·  {payload.get('message')}" if payload.get("message") else "")
    )
    rows: list[list[str]] = []
    for i, item in enumerate(matches):
        if not isinstance(item, dict):
            continue
        label = (
            f"img{item.get('firstImageId')}#face{item.get('firstFaceId')}"
            f" ↔ img{item.get('secondImageId')}#face{item.get('secondFaceId')}"
        )
        rows.append([f"pair[{i}]", label, _cell(item.get("similarity"))])
    if not rows and status == 0:
        rows.append(["similarity", "—", "no face pair"])
    raw = json.dumps(payload, indent=2, ensure_ascii=False)
    return summary, rows, raw


def detect(image):
    note = _notes_md(_fetch_license_status())
    if note:
        return note, [], ""
    if not image:
        return _error("Image required")
    out = _post("/api/detect", {"image": _b64(image), "cropImage": False})
    if isinstance(out, tuple):
        return out
    return format_detect(out)


def quality(image):
    note = _notes_md(_fetch_license_status())
    if note:
        return note, [], ""
    if not image:
        return _error("Image required")
    out = _post("/api/quality", {"image": _b64(image), "cropImage": False})
    if isinstance(out, tuple):
        return out
    return format_quality(out)


def match(image1, image2):
    note = _notes_md(_fetch_license_status())
    if note:
        return note, [], ""
    if not image1 or not image2:
        return _error("Both images required")
    out = _post(
        "/api/match",
        {"image1": _b64(image1), "image2": _b64(image2), "cropImage": False},
    )
    if isinstance(out, tuple):
        return out
    return format_match(out)


def _result_panel(*, headers: list[str], label: str):
    with gr.Tabs():
        with gr.Tab("Result"):
            summary = gr.Markdown(value="*Run an action to see fields.*")
            table = demo_ui.result_dataframe(headers=headers, label=label)
        with gr.Tab("Raw JSON"):
            raw = gr.Code(language="json", label="API response")
    return summary, table, raw


_DETECT_EX = _face_examples()
_ODD_EX = _odd_examples()
_EVEN_EX = _even_examples()

with gr.Blocks(title="Face Recognition Demo", css=demo_ui.RESULT_CSS) as demo:
    gr.Markdown(
        "# FacePlugin Face Recognition\n"
        "Upload a face photo, or use an example below.\n\n"
        "Tabs: **Detect** (attributes) · **Quality** (ICAO-style checks) · **Match** (compare two faces).\n\n"
        f"API: `{API}` — start with `run.bat` first."
    )
    license_md = gr.Markdown(value=_license_banner_md())
    notes_md = gr.Markdown(value=_capability_notes_md())

    with gr.Tab("Detect"):
        with gr.Row():
            with gr.Column():
                det_in = gr.Image(type="filepath", label="Face")
                if _DETECT_EX:
                    gr.Examples(_DETECT_EX, inputs=det_in, label="Examples")
                det_btn = gr.Button("Detect", variant="primary")
            with gr.Column():
                det_summary, det_table, det_raw = _result_panel(
                    headers=["Attribute", "Value", "Confidence"],
                    label="Detect attributes",
                )
        det_btn.click(detect, inputs=det_in, outputs=[det_summary, det_table, det_raw])
        det_btn.click(_license_header, outputs=[license_md, notes_md])

    with gr.Tab("Quality"):
        with gr.Row():
            with gr.Column():
                q_in = gr.Image(type="filepath", label="Face")
                if _DETECT_EX:
                    gr.Examples(_DETECT_EX, inputs=q_in, label="Examples")
                q_btn = gr.Button("Quality", variant="primary")
            with gr.Column():
                q_summary, q_table, q_raw = _result_panel(
                    headers=["Check", "Value", "Status / range"],
                    label="Quality checks",
                )
        q_btn.click(quality, inputs=q_in, outputs=[q_summary, q_table, q_raw])
        q_btn.click(_license_header, outputs=[license_md, notes_md])

    with gr.Tab("Match"):
        gr.Markdown("Pick **one Odd** image and **one Even** image, then click Match.")
        with gr.Row():
            with gr.Column():
                with gr.Row():
                    m1 = gr.Image(type="filepath", label="Odd")
                    m2 = gr.Image(type="filepath", label="Even")
                with gr.Row():
                    if _ODD_EX:
                        gr.Examples(_ODD_EX, inputs=m1, label="Odd")
                    if _EVEN_EX:
                        gr.Examples(_EVEN_EX, inputs=m2, label="Even")
                match_btn = gr.Button("Match", variant="primary")
            with gr.Column():
                m_summary, m_table, m_raw = _result_panel(
                    headers=["Pair", "Faces", "Similarity"],
                    label="Match scores",
                )
        match_btn.click(match, inputs=[m1, m2], outputs=[m_summary, m_table, m_raw])
        match_btn.click(_license_header, outputs=[license_md, notes_md])

    demo.load(_license_header, outputs=[license_md, notes_md])

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=DEMO_PORT)
