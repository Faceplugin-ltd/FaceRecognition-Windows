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
        "liveness": False,
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


def quality(image: str, crop_image: bool = False) -> str:
    image_b = _fit_b64(image)
    out = _out(image_b)
    _dll.FaceSDK_quality(image_b, out, bool(crop_image))
    return out.value.decode("utf-8", errors="replace")


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
