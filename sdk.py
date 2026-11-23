"""ctypes bindings to FaceRecognitionSDK.dll (Face Recognition, Windows)."""

from __future__ import annotations

import base64
import ctypes
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIB = ROOT / "lib" / "cpu"
DLL = LIB / "FaceRecognitionSDK.dll"

os.chdir(ROOT)

if not DLL.is_file():
    print(
        f"ERROR: missing {DLL.relative_to(ROOT)}\n"
        "Download the Windows runtime into .\\lib\\cpu\\.",
        file=sys.stderr,
    )
    raise SystemExit(1)

os.environ["PATH"] = str(LIB) + os.pathsep + os.environ.get("PATH", "")
if hasattr(os, "add_dll_directory"):
    os.add_dll_directory(str(LIB))

_dll = ctypes.WinDLL(str(DLL))

_dll.FaceSDK_initSDK.restype = ctypes.c_int
_dll.FaceSDK_initSDK.argtypes = []
_dll.FaceSDK_activate.restype = ctypes.c_int
_dll.FaceSDK_activate.argtypes = [ctypes.c_char_p]
_dll.FaceSDK_getMachineCode.restype = ctypes.c_int
_dll.FaceSDK_getMachineCode.argtypes = [ctypes.c_char_p]
_dll.FaceSDK_detect.restype = ctypes.c_int
_dll.FaceSDK_detect.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_bool]
_dll.FaceSDK_quality.restype = ctypes.c_int
_dll.FaceSDK_quality.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_bool]
_dll.FaceSDK_match.restype = ctypes.c_int
_dll.FaceSDK_match.argtypes = [
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_bool,
]
_dll.FaceSDK_getFeature.restype = ctypes.c_int
_dll.FaceSDK_getFeature.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
_dll.FaceSDK_getSimilarity.restype = ctypes.c_float
_dll.FaceSDK_getSimilarity.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]


def _opt_sym(name, restype, argtypes):
    fn = getattr(_dll, name, None)
    if fn is None:
        return None
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


_get_license_status = _opt_sym(
    "FaceSDK_getLicenseStatus", ctypes.c_int, [ctypes.c_char_p]
)

_MC_BUF = 768
_STATUS_BUF = 512
_OUT_MIN = 1_000_000
_OUT_MAX = 16 * 1024 * 1024
_MAX_IMG = 8 * 1024 * 1024


def _b(v) -> bytes:
    if v is None:
        raise ValueError("required")
    if isinstance(v, bytes):
        return v
    return str(v).encode("utf-8")


def _out(*parts: bytes):
    n = min(max(_OUT_MIN, sum(len(p) for p in parts) * 2 + 262144), _OUT_MAX)
    return ctypes.create_string_buffer(n)


def _strip(raw: bytes) -> bytes:
    text = raw.decode("utf-8", errors="ignore").strip()
    if text.startswith("data:") and "base64," in text:
        text = text.split("base64,", 1)[1]
    return text.encode("utf-8")


def _fit_b64(image_b64: str, max_bytes: int = _MAX_IMG) -> bytes:
    raw = _strip(_b(image_b64))
    if len(raw) <= max_bytes:
        return raw
    from PIL import Image

    data = base64.b64decode(raw, validate=False)
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    quality, scale, last = 85, 1.0, raw
    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS
    for _ in range(12):
        cand = img
        if scale < 1.0:
            cand = img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                resample,
            )
        buf = io.BytesIO()
        cand.save(buf, format="JPEG", quality=quality, optimize=True)
        last = base64.b64encode(buf.getvalue())
        if len(last) <= max_bytes:
            return last
        if quality > 40:
            quality -= 10
        else:
            scale *= 0.75
            quality = 75
    return last


def backend() -> str:
    return "cpu"


def get_machine_code() -> str:
    buf = ctypes.create_string_buffer(_MC_BUF)
    _dll.FaceSDK_getMachineCode(buf)
    return buf.value.decode("utf-8", errors="replace")


def get_license_status() -> dict:
    fallback = {
        "licensed": False,
        "level": -1,
        "levelName": "None",
        "recognition": False,
        "label": "Not licensed",
    }
    if _get_license_status is None:
        return fallback
    buf = ctypes.create_string_buffer(_STATUS_BUF)
    if int(_get_license_status(buf)) != 0:
        return fallback
    try:
        data = json.loads(buf.value.decode("utf-8", errors="replace") or "{}")
        return data if isinstance(data, dict) else fallback
    except Exception:  # noqa: BLE001
        return fallback


def activate(license_path: str) -> int:
    return int(_dll.FaceSDK_activate(_b(license_path)))


def init_sdk() -> int:
    return int(_dll.FaceSDK_initSDK())


def detect(image: str, crop_image: bool = False) -> str:
    image_b = _fit_b64(image)
    out = _out(image_b)
    _dll.FaceSDK_detect(image_b, out, bool(crop_image))
    return out.value.decode("utf-8", errors="replace")


def _quality_dll(image: str, crop_image: bool = False) -> str:
    image_b = _fit_b64(image)
    out = _out(image_b)
    _dll.FaceSDK_quality(image_b, out, bool(crop_image))
    return out.value.decode("utf-8", errors="replace")


def _crop_face_b64(image: str, region: dict) -> str | None:
    """JPEG crop of one face, with margin so quality can still see the head."""
    try:
        from PIL import Image

        raw = base64.b64decode(_strip(_b(image)), validate=False)
        img = Image.open(io.BytesIO(raw))
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        x, y = float(region.get("x", 0)), float(region.get("y", 0))
        w, h = float(region.get("width", 0)), float(region.get("height", 0))
        if w <= 1 or h <= 1:
            return None
        pad = max(8.0, min(w, h) * 0.75)
        box = (
            int(max(0, x - pad)),
            int(max(0, y - pad)),
            int(min(img.width, x + w + pad)),
            int(min(img.height, y + h + pad)),
        )
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            return None
        buf = io.BytesIO()
        img.crop(box).save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        return None


def quality(image: str, crop_image: bool = False) -> str:
    """Quality for every face in the photo.

    Older engines return only the largest face. When detection finds more,
    each extra face is measured on its own crop and placed back on the full image.
    """
    first = _quality_dll(image, crop_image)
    try:
        payload = json.loads(first or "{}")
        detected = json.loads(detect(image, False) or "{}")
    except (ValueError, TypeError):
        return first
    regions = [face for face in (detected.get("data") or []) if isinstance(face, dict)]
    got = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), list) else []
    if len(regions) <= max(1, len(got)):
        return first
    merged = []
    for index, face in enumerate(regions):
        region = face.get("faceRegion") if isinstance(face.get("faceRegion"), dict) else None
        if region is None:
            continue
        crop = _crop_face_b64(image, region)
        if not crop:
            continue
        try:
            piece = json.loads(_quality_dll(crop, crop_image) or "{}")
        except ValueError:
            continue
        rows = piece.get("data") if isinstance(piece.get("data"), list) else []
        if not rows or not isinstance(rows[0], dict):
            continue
        row = rows[0]
        row["faceId"] = face.get("faceId", index)
        row["faceRegion"] = region
        if face.get("facePose") is not None:
            row["facePose"] = face["facePose"]
        if face.get("facePoints") is not None:
            row["facePoints"] = face["facePoints"]
        merged.append(row)
    if len(merged) <= len(got):
        return first
    if not isinstance(payload, dict):
        payload = {}
    payload["status"] = payload.get("status", 0)
    payload["message"] = payload.get("message") or "COMPLETED"
    payload["data"] = merged
    return json.dumps(payload)


def match(image1: str, image2: str, crop_image: bool = False) -> str:
    a = _fit_b64(image1, _MAX_IMG // 2)
    b = _fit_b64(image2, _MAX_IMG // 2)
    out = _out(a, b)
    _dll.FaceSDK_match(a, b, out, bool(crop_image))
    return out.value.decode("utf-8", errors="replace")


def feature(image: str) -> str:
    image_b = _fit_b64(image)
    out = _out(image_b)
    _dll.FaceSDK_getFeature(image_b, out)
    return out.value.decode("utf-8", errors="replace")


def similarity(feature1: str, feature2: str) -> str:
    raw1 = base64.b64decode(_b(feature1))
    raw2 = base64.b64decode(_b(feature2))
    if len(raw1) != len(raw2) or not raw1:
        raise ValueError("feature length mismatch")
    value = _dll.FaceSDK_getSimilarity(raw1, raw2, len(raw1))
    return json.dumps({"similarity": value})


# ---------------------------------------------------------------- 1:N helpers

# This edition has no liveness model; the Plus edition sets this to True.
HAS_LIVENESS = False


def similarity_raw(raw1: bytes, raw2: bytes) -> float:
    """Similarity of two already-decoded feature buffers (-1.0 if incompatible)."""
    if len(raw1) != len(raw2) or not raw1:
        return -1.0
    return float(_dll.FaceSDK_getSimilarity(raw1, raw2, len(raw1)))


def features_from_image(image: str) -> list[dict]:
    """Every face in the image with its feature, region and landmarks.

    Raises ValueError("no face") when the image has no usable face.
    """
    payload = json.loads(feature(image) or "{}")
    faces: list[dict] = []
    for result in payload.get("results") or []:
        for item in (result or {}).get("features") or []:
            feat = (item or {}).get("feature")
            if feat:
                faces.append(
                    {
                        "feature": str(feat),
                        "face_region": item.get("faceRegion"),
                        "face_points": item.get("facePoints"),
                    }
                )
    if not faces:
        raise ValueError("no face")
    return faces


def quality_faces(image: str) -> list[dict]:
    """Per-face quality attributes from FaceSDK_quality (empty list on failure)."""
    try:
        payload = json.loads(quality(image) or "{}")
    except ValueError:
        return []
    data = payload.get("data")
    return [face for face in data if isinstance(face, dict)] if isinstance(data, list) else []


def liveness_faces(image: str) -> list[dict]:
    """Not available in this edition."""
    return []
