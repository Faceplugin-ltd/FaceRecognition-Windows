"""Enrollment quality checks (ISO/IEC 29794-5 style) built on FaceSDK_quality.

`assess()` turns one face from the SDK quality output into a pass/fail verdict
with a human-readable reason for each failed check.
"""

from __future__ import annotations

import re

# Default limits. Loose enough for typical phone / webcam photos, strict enough
# to keep blurry, tiny, dark or strongly turned faces out of the gallery.
RULES = {
    "min_face_px": 80,      # shorter side of the face box, in pixels
    "min_quality": 40,      # SDK overall face quality, percent
    "max_blur": 3,          # SDK blur level, 1 (sharp) … 5 (very blurry)
    "max_yaw": 40,          # degrees, left/right turn
    "max_pitch": 30,        # degrees, up/down tilt
    "max_roll": 45,         # degrees, head tilt to the shoulder
}

_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_LEVEL = re.compile(r"(\d)\s*/\s*5")


def _attr(face: dict, name: str):
    attr = (face.get("attributes") or {}).get(name)
    return attr.get("value") if isinstance(attr, dict) else attr


def _number(value, pattern: re.Pattern | None = None) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if pattern is not None:
            found = pattern.search(value)
            return float(found.group(1)) if found else None
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "yes", "true")
    return bool(value)


def face_box(region) -> dict:
    """Face box as {x, y, width, height}.

    The fast SDK sends that object. The Plus engine sends the vendor roi:
    two corners, either {x, y} points or [x, y] pairs, or four numbers
    [x, y, width, height].
    """
    if isinstance(region, dict):
        return region
    if not isinstance(region, (list, tuple)) or len(region) < 2:
        return {}
    first, second = region[0], region[1]
    if isinstance(first, dict) and isinstance(second, dict):
        try:
            x1, y1 = float(first["x"]), float(first["y"])
            x2, y2 = float(second["x"]), float(second["y"])
        except (KeyError, TypeError, ValueError):
            return {}
        return {"x": min(x1, x2), "y": min(y1, y2), "width": abs(x2 - x1), "height": abs(y2 - y1)}
    if isinstance(first, (list, tuple)) and isinstance(second, (list, tuple)):
        try:
            x1, y1 = float(first[0]), float(first[1])
            x2, y2 = float(second[0]), float(second[1])
        except (IndexError, TypeError, ValueError):
            return {}
        return {"x": min(x1, x2), "y": min(y1, y2), "width": abs(x2 - x1), "height": abs(y2 - y1)}
    if len(region) >= 4:
        try:
            return {
                "x": float(region[0]),
                "y": float(region[1]),
                "width": float(region[2]),
                "height": float(region[3]),
            }
        except (TypeError, ValueError):
            return {}
    return {}


def face_pose(pose) -> dict:
    """Pose as {yaw, pitch, roll}. Plus sends rotationAngle as three numbers."""
    if isinstance(pose, dict):
        return pose
    if not isinstance(pose, (list, tuple)):
        return {}
    numbers = []
    for item in pose:
        if isinstance(item, (int, float)):
            numbers.append(float(item))
        elif isinstance(item, dict) and "angle" in item:
            try:
                numbers.append(float(item["angle"]))
            except (TypeError, ValueError):
                continue
    keys = ("yaw", "pitch", "roll")
    return {keys[i]: numbers[i] for i in range(min(len(keys), len(numbers)))}


# Checks that only warn. The SDK lighting flags are sensitive (they fire on many
# usable photos) and already lower the overall quality score; roll is undone
# by face alignment. Everything else blocks enrollment.
WARN_ONLY = {"too_dark", "too_light", "roll"}


def assess(face: dict | None, rules: dict | None = None) -> dict:
    """Return {"ok", "score", "reasons", "warnings", "checks"} for one SDK quality face.

    ok is False when a blocking check fails; `reasons` explains each one.
    `warnings` lists failed checks that do not block.
    """
    limits = {**RULES, **(rules or {})}
    if not face:
        return {"ok": True, "score": None, "reasons": [], "warnings": [], "checks": []}

    checks: list[dict] = []
    reasons: list[str] = []
    warnings: list[str] = []

    def check(key: str, label: str, value, ok: bool, reason: str, limit=None) -> None:
        blocking = key not in WARN_ONLY
        checks.append({"check": key, "label": label, "value": value, "limit": limit,
                       "ok": ok, "blocking": blocking})
        if not ok:
            (reasons if blocking else warnings).append(reason)
    region = face_box(face.get("faceRegion"))
    size = min(float(region.get("width") or 0), float(region.get("height") or 0))
    if size:
        check("face_size", "Face size", round(size), size >= limits["min_face_px"],
              "Face is too small. Move closer or use a larger photo.", limits["min_face_px"])

    score = _number(_attr(face, "FaceQuality"), _PERCENT)
    if score is not None:
        check("quality", "Overall quality", score, score >= limits["min_quality"],
              "Overall face quality is low.", limits["min_quality"])

    blur = _number(_attr(face, "BlurLevel"), _LEVEL)
    if blur is not None:
        check("blur", "Blur", blur, blur <= limits["max_blur"],
              "Photo is blurry. Hold still or use a sharper photo.", limits["max_blur"])

    if _attr(face, "TooDark") is not None:
        dark = _flag(_attr(face, "TooDark"))
        check("too_dark", "Too dark", dark, not dark, "Face is too dark. Add more light.")
    if _attr(face, "TooLight") is not None:
        light = _flag(_attr(face, "TooLight"))
        check("too_light", "Too bright", light, not light, "Face is overexposed. Reduce the light.")

    pose = face_pose(face.get("facePose"))
    for key, label, limit, reason in (
        ("yaw", "Yaw", limits["max_yaw"], "Face is turned sideways. Look at the camera."),
        ("pitch", "Pitch", limits["max_pitch"], "Head is tilted up or down. Look straight ahead."),
        ("roll", "Roll", limits["max_roll"], "Head is tilted to the side. Keep it upright."),
    ):
        value = _number(pose.get(key))
        if value is not None:
            check(key, label, round(value, 1), abs(value) <= limit, reason, limit)

    return {"ok": not reasons, "score": score, "reasons": reasons, "warnings": warnings,
            "checks": checks}
