"""Face gallery for 1:N identification.

A gallery holds *persons*; each person has one or more enrolled *faces*
(templates). Identification scores a probe against every face and ranks
persons by their best-matching face.

Storage: SQLite (data/gallery.sqlite) plus face crops and original photos in
data/images/. Features are cached in memory, already base64-decoded.
"""

from __future__ import annotations

import base64
import heapq
import io
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import quality_gate
import sdk

DEFAULT_THRESHOLD = 0.81
DEFAULT_TOP_K = 5
MAX_TOP_K = 100
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
DEFAULT_PAIR_LIMIT = 100
MAX_PAIR_LIMIT = 1000
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
IMAGES = DATA / "images"
DB_PATH = DATA / "gallery.sqlite"

# Settings stored in the database, with their defaults and types.
SETTINGS = {
    "threshold": (DEFAULT_THRESHOLD, float),
    "duplicate_check": (True, bool),
    "liveness_check": (True, bool),
}


# ---------------------------------------------------------------- errors


class GalleryError(ValueError):
    """Base class: an enrollment or search problem with a caller-safe message."""

    code = -1

    def __init__(self, message: str, data: dict | None = None):
        super().__init__(message)
        self.data = data or {}


class NoFaceError(GalleryError):
    code = -2


class MultipleFacesError(GalleryError):
    code = -6


class DuplicateError(GalleryError):
    code = -7


class LowQualityError(GalleryError):
    code = -8


class NotFoundError(GalleryError):
    code = -10


# ---------------------------------------------------------------- database


def _open() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    IMAGES.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            image_path TEXT NOT NULL DEFAULT '',
            full_image_path TEXT NOT NULL DEFAULT '',
            feature_b64 TEXT NOT NULL,
            quality REAL,
            source TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS faces_person ON faces(person_id);
        CREATE INDEX IF NOT EXISTS persons_name ON persons(name);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    return conn


@contextmanager
def _connect():
    """One connection per call: commit on success, always close."""
    conn = _open()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- settings


def get_settings() -> dict:
    with _connect() as conn:
        stored = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    out = {}
    for key, (default, kind) in SETTINGS.items():
        raw = stored.get(key)
        if raw is None:
            out[key] = default
        elif kind is bool:
            out[key] = raw == "1"
        else:
            try:
                out[key] = kind(raw)
            except ValueError:
                out[key] = default
    out["liveness_available"] = bool(sdk.HAS_LIVENESS)
    return out


def set_settings(values: dict) -> dict:
    with _connect() as conn:
        for key, value in values.items():
            if key not in SETTINGS or value is None:
                continue
            kind = SETTINGS[key][1]
            if kind is bool:
                text = "1" if value in (True, 1, "1", "true", "True", "yes") else "0"
            else:
                text = str(max(0.0, min(1.0, float(value))))
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, text),
            )
    return get_settings()


def get_threshold() -> float:
    return float(get_settings()["threshold"])


# ---------------------------------------------------------------- feature cache

_cache: list[dict] | None = None
_cache_lock = threading.Lock()


def _invalidate() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def _faces_cache() -> list[dict]:
    """All enrolled faces as {face_id, person_id, name, raw}, decoded once."""
    global _cache
    with _cache_lock:
        if _cache is None:
            with _connect() as conn:
                rows = conn.execute(
                    "SELECT f.id, f.person_id, p.name, f.feature_b64 "
                    "FROM faces f JOIN persons p ON p.id = f.person_id ORDER BY f.id"
                ).fetchall()
            _cache = [
                {
                    "face_id": int(row["id"]),
                    "person_id": int(row["person_id"]),
                    "name": row["name"],
                    "raw": base64.b64decode(row["feature_b64"]),
                }
                for row in rows
            ]
        return _cache


def _rank_persons(probe_b64: str, top_k: int, exclude_person: int | None = None) -> list[dict]:
    """Best `top_k` persons for one probe feature; a person scores its best face."""
    probe = base64.b64decode(probe_b64)
    best: dict[int, tuple[float, dict]] = {}
    for row in _faces_cache():
        if row["person_id"] == exclude_person:
            continue
        score = sdk.similarity_raw(probe, row["raw"])
        current = best.get(row["person_id"])
        if current is None or score > current[0]:
            best[row["person_id"]] = (score, row)
    top = heapq.nlargest(top_k, best.values(), key=lambda pair: pair[0])
    return [
        {
            "person_id": row["person_id"],
            "name": row["name"],
            "face_id": row["face_id"],
            "similarity": round(score, 4),
        }
        for score, row in top
    ]


# ---------------------------------------------------------------- images


def _decode_image(image_b64: str) -> bytes:
    raw = image_b64.strip()
    if raw.startswith("data:") and "base64," in raw:
        raw = raw.split("base64,", 1)[1]
    return base64.b64decode(raw)


def _crop(image_bytes: bytes, region) -> bytes:
    """Face crop with a small margin; the full image if cropping is not possible."""
    if not isinstance(region, dict):
        return image_bytes
    try:
        from PIL import Image

        x, y = float(region.get("x", 0)), float(region.get("y", 0))
        w, h = float(region.get("width", 0)), float(region.get("height", 0))
        if w <= 0 or h <= 0:
            return image_bytes
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        pad = max(4.0, min(w, h) * 0.15)
        box = (
            int(max(0, x - pad)),
            int(max(0, y - pad)),
            int(min(img.width, x + w + pad)),
            int(min(img.height, y + h + pad)),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            return image_bytes
        buf = io.BytesIO()
        img.crop(box).save(buf, format="JPEG", quality=92)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - a crop is cosmetic; keep the original
        return image_bytes


def _write_image(data: bytes, name: str) -> str:
    path = IMAGES / name
    path.write_bytes(data)
    return str(path.relative_to(ROOT)).replace("\\", "/")


def image_file(face_id: int, kind: str = "crop") -> Path | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT image_path, full_image_path FROM faces WHERE id=?", (face_id,)
        ).fetchone()
    if not row:
        return None
    rel = row["full_image_path"] if kind == "full" else row["image_path"]
    rel = rel or row["image_path"]
    path = ROOT / rel if rel else None
    return path if path and path.is_file() else None


def _thumb_b64(rel: str) -> str | None:
    path = ROOT / rel if rel else None
    if not path or not path.is_file():
        return None
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _remove_files(rows) -> None:
    for row in rows:
        for rel in (row["image_path"], row["full_image_path"]):
            path = ROOT / rel if rel else None
            if path and path.is_file():
                path.unlink()


# ---------------------------------------------------------------- probe analysis


def _center(region) -> tuple[float, float] | None:
    box = quality_gate.face_box(region)
    if not box:
        return None
    try:
        return (
            float(box["x"]) + float(box["width"]) / 2,
            float(box["y"]) + float(box["height"]) / 2,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _nearest(region, candidates: list[dict], key: str = "faceRegion") -> dict | None:
    """The candidate whose face box center is closest to `region`."""
    origin = _center(region)
    if origin is None or not candidates:
        return candidates[0] if len(candidates) == 1 else None
    scored = []
    for item in candidates:
        center = _center(item.get(key))
        if center is not None:
            scored.append(((center[0] - origin[0]) ** 2 + (center[1] - origin[1]) ** 2, item))
    return min(scored, key=lambda pair: pair[0])[1] if scored else None


def _liveness_view(item: dict | None) -> dict | None:
    if not item:
        return None
    score = item.get("score")
    return {
        "live": bool(item.get("live")),
        "score": round(float(score), 4) if score is not None else None,
        "status": "live" if item.get("live") else "spoof",
    }


# ---------------------------------------------------------------- persons


def _person_row(conn, person_id: int):
    return conn.execute(
        "SELECT id, name, note, created_at FROM persons WHERE id=?", (person_id,)
    ).fetchone()


def _person_summary_sql(where: str = "") -> str:
    return (
        "SELECT p.id, p.name, p.note, p.created_at, "
        "COUNT(f.id) AS face_count, MIN(f.id) AS cover_face_id "
        "FROM persons p LEFT JOIN faces f ON f.person_id = p.id"
        + where
        + " GROUP BY p.id"
    )


def list_persons(page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, query: str = "",
                 thumbs: bool = False) -> dict:
    page_size = max(1, min(MAX_PAGE_SIZE, int(page_size or DEFAULT_PAGE_SIZE)))
    query = (query or "").strip()
    where, args = "", ()
    if query:
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where = " WHERE p.name LIKE ? ESCAPE '\\' OR p.note LIKE ? ESCAPE '\\'"
        args = (like, like)
        if query.isdigit():
            where += " OR p.id = ?"
            args = (*args, int(query))
    with _connect() as conn:
        total = int(
            conn.execute("SELECT COUNT(*) FROM persons p" + where, args).fetchone()[0]
        )
        pages = max(1, -(-total // page_size))
        page = max(1, min(int(page or 1), pages))
        rows = conn.execute(
            _person_summary_sql(where) + " ORDER BY p.id DESC LIMIT ? OFFSET ?",
            (*args, page_size, (page - 1) * page_size),
        ).fetchall()
        persons = [dict(row) for row in rows]
        if thumbs:
            for person in persons:
                cover = conn.execute(
                    "SELECT image_path FROM faces WHERE id=?", (person["cover_face_id"],)
                ).fetchone()
                person["thumb"] = _thumb_b64(cover["image_path"]) if cover else None
        face_total = int(conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0])
    return {
        "persons": persons,
        "query": query,
        "total": total,
        "face_total": face_total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def counts() -> dict:
    with _connect() as conn:
        persons = int(conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0])
        faces = int(conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0])
    return {"persons": persons, "faces": faces}


def get_person(person_id: int, thumbs: bool = False) -> dict:
    with _connect() as conn:
        person = _person_row(conn, person_id)
        if not person:
            raise NotFoundError("Person not found")
        faces = conn.execute(
            "SELECT id, image_path, full_image_path, quality, source, created_at "
            "FROM faces WHERE person_id=? ORDER BY id",
            (person_id,),
        ).fetchall()
    out = dict(person)
    out["faces"] = []
    for face in faces:
        item = {
            "id": face["id"],
            "quality": face["quality"],
            "source": face["source"],
            "created_at": face["created_at"],
        }
        if thumbs:
            item["thumb"] = _thumb_b64(face["image_path"])
        out["faces"].append(item)
    out["face_count"] = len(out["faces"])
    return out


def update_person(person_id: int, name: str | None = None, note: str | None = None) -> dict:
    with _connect() as conn:
        if not _person_row(conn, person_id):
            raise NotFoundError("Person not found")
        if name is not None:
            name = name.strip()
            if not name:
                raise GalleryError("Name can't be empty")
            conn.execute("UPDATE persons SET name=? WHERE id=?", (name, person_id))
        if note is not None:
            conn.execute("UPDATE persons SET note=? WHERE id=?", (note.strip(), person_id))
    _invalidate()
    return get_person(person_id)


def _restart_ids_if_empty(conn) -> None:
    """An empty gallery hands out ID 1 again. SQLite otherwise keeps the old counter."""
    persons = int(conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0])
    faces = int(conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0])
    if persons or faces:
        return
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone():
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('persons', 'faces')")


def delete_person(person_id: int) -> dict:
    with _connect() as conn:
        if not _person_row(conn, person_id):
            raise NotFoundError("Person not found")
        files = conn.execute(
            "SELECT image_path, full_image_path FROM faces WHERE person_id=?", (person_id,)
        ).fetchall()
        conn.execute("DELETE FROM faces WHERE person_id=?", (person_id,))
        conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
        _restart_ids_if_empty(conn)
    _invalidate()
    _remove_files(files)
    return {"person_id": person_id, "faces_deleted": len(files)}


def delete_face(face_id: int) -> dict:
    """Remove one photo. A person left with no photos is removed too."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT person_id, image_path, full_image_path FROM faces WHERE id=?", (face_id,)
        ).fetchone()
        if not row:
            raise NotFoundError("Face not found")
        conn.execute("DELETE FROM faces WHERE id=?", (face_id,))
        left = int(
            conn.execute(
                "SELECT COUNT(*) FROM faces WHERE person_id=?", (row["person_id"],)
            ).fetchone()[0]
        )
        if left == 0:
            conn.execute("DELETE FROM persons WHERE id=?", (row["person_id"],))
        _restart_ids_if_empty(conn)
    _invalidate()
    _remove_files([row])
    return {"face_id": face_id, "person_id": row["person_id"], "person_deleted": left == 0}


def clear_all() -> dict:
    with _connect() as conn:
        persons = int(conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0])
        faces = int(conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0])
        conn.execute("DELETE FROM faces")
        conn.execute("DELETE FROM persons")
        _restart_ids_if_empty(conn)
    _invalidate()
    if IMAGES.is_dir():
        for path in IMAGES.iterdir():
            if path.is_file():
                path.unlink()
    return {"persons_deleted": persons, "faces_deleted": faces}


# ---------------------------------------------------------------- enrollment


def _find_person_by_name(conn, name: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM persons WHERE name = ? COLLATE NOCASE ORDER BY id LIMIT 1", (name,)
    ).fetchone()
    return int(row["id"]) if row else None


def enroll(
    image_b64: str,
    name: str = "",
    note: str = "",
    person_id: int | None = None,
    merge_by_name: bool = False,
    on_duplicate: str = "reject",
    check_quality: bool = False,
    source: str = "",
) -> dict:
    """Enroll one face.

    person_id      add this photo to an existing person.
    merge_by_name  add to the person with the same name when one exists.
    on_duplicate   the face already looks like another person:
                   "reject" (default) raise DuplicateError,
                   "add" add the photo to that person,
                   "new" enroll as a separate person anyway.
    check_quality  also run the quality checks and reject poor photos (off by
                   default: it calls the quality model and slows enrollment).
    """
    settings = get_settings()
    if on_duplicate not in ("reject", "add", "new"):
        raise GalleryError("on_duplicate must be reject, add or new")
    image_bytes = _decode_image(image_b64)

    try:
        faces = sdk.features_from_image(image_b64)
    except ValueError:
        raise NoFaceError("No face found. Use a clear, front-facing photo.") from None
    if len(faces) > 1:
        raise MultipleFacesError(
            f"{len(faces)} faces found. Use a photo that shows only one person.",
            {"face_count": len(faces)},
        )
    face = faces[0]

    quality = None
    if check_quality:
        quality = quality_gate.assess(_nearest(face["face_region"], sdk.quality_faces(image_b64)))
        if not quality["ok"]:
            raise LowQualityError(quality["reasons"][0], {"quality": quality})

    name = (name or "").strip()
    with _connect() as conn:
        target = None
        if person_id is not None:
            if not _person_row(conn, int(person_id)):
                raise NotFoundError("Person not found")
            target = int(person_id)
        elif merge_by_name and name:
            target = _find_person_by_name(conn, name)

    duplicate = None
    if settings["duplicate_check"] and on_duplicate != "new":
        ranked = _rank_persons(face["feature"], 1, exclude_person=target)
        if ranked and ranked[0]["similarity"] >= settings["threshold"]:
            duplicate = ranked[0]
            if on_duplicate == "reject":
                raise DuplicateError(
                    f"This face looks like {duplicate['name']} (ID {duplicate['person_id']}).",
                    {"duplicate": duplicate},
                )
            target = duplicate["person_id"]

    now = _now()
    with _connect() as conn:
        created = target is None
        if created:
            cur = conn.execute(
                "INSERT INTO persons(name, note, created_at) VALUES (?, ?, ?)",
                (name or "Unnamed", note or "", now),
            )
            target = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO faces(person_id, feature_b64, quality, source, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (target, face["feature"], (quality or {}).get("score"), source or "", now),
        )
        face_id = int(cur.lastrowid)
        crop_rel = _write_image(_crop(image_bytes, face["face_region"]), f"{face_id}.jpg")
        full_rel = _write_image(image_bytes, f"{face_id}_full.jpg")
        conn.execute(
            "UPDATE faces SET image_path=?, full_image_path=? WHERE id=?",
            (crop_rel, full_rel, face_id),
        )
        person = dict(_person_row(conn, target))
    _invalidate()
    return {
        "person": person,
        "face_id": face_id,
        "person_created": created,
        "face_region": face["face_region"],
        "quality": quality,
        "duplicate": duplicate,
    }


def import_paths(paths, name_from: str = "file", on_duplicate: str = "reject") -> dict:
    """Server-side batch enrollment. name_from: "file" (stem) or "folder" (parent name)."""
    results = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        name = path.parent.name if name_from == "folder" else path.stem
        item = {"file": str(path), "name": name}
        try:
            out = enroll(
                base64.b64encode(path.read_bytes()).decode("ascii"),
                name=name,
                merge_by_name=True,
                on_duplicate=on_duplicate,
                source=path.name,
            )
            item.update(status="enrolled", person_id=out["person"]["id"], face_id=out["face_id"])
        except GalleryError as exc:
            item.update(status="skipped", code=exc.code, message=str(exc))
        results.append(item)
    enrolled = sum(1 for item in results if item["status"] == "enrolled")
    return {"results": results, "enrolled": enrolled, "skipped": len(results) - enrolled}


def import_directory(directory: str, name_from: str = "file", on_duplicate: str = "reject") -> dict:
    folder = Path(directory)
    if not folder.is_dir():
        raise NotFoundError("Directory not found")
    paths = sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
    return import_paths(paths, name_from=name_from, on_duplicate=on_duplicate)


# ---------------------------------------------------------------- identification


def identify(
    image_b64: str,
    threshold: float | None = None,
    top_k: int = DEFAULT_TOP_K,
    check_liveness: bool | None = None,
    check_quality: bool = False,
) -> dict:
    """1:N search. Each probe face gets a status and its top_k candidate persons.

    status: "identified" | "no_match" | "spoof" (liveness failed: gallery not searched)
    """
    settings = get_settings()
    limit = settings["threshold"] if threshold is None else float(threshold)
    top_k = max(1, min(MAX_TOP_K, int(top_k)))
    want_liveness = settings["liveness_check"] if check_liveness is None else bool(check_liveness)
    use_liveness = want_liveness and sdk.HAS_LIVENESS

    started = time.perf_counter()
    try:
        probe_faces = sdk.features_from_image(image_b64)
    except ValueError:
        raise NoFaceError("No face found in the photo.") from None
    timing = {"extract_ms": (time.perf_counter() - started) * 1000.0}

    quality_list: list[dict] = []
    if check_quality:
        started = time.perf_counter()
        quality_list = sdk.quality_faces(image_b64)
        timing["quality_ms"] = (time.perf_counter() - started) * 1000.0

    liveness_list: list[dict] = []
    if use_liveness:
        started = time.perf_counter()
        liveness_list = sdk.liveness_faces(image_b64)
        timing["liveness_ms"] = (time.perf_counter() - started) * 1000.0

    started = time.perf_counter()
    faces_out = []
    for index, face in enumerate(probe_faces):
        region = face["face_region"]
        quality = (
            quality_gate.assess(_nearest(region, quality_list)) if quality_list else None
        )
        liveness = _liveness_view(_nearest(region, liveness_list, "face_region")) if use_liveness else None
        if liveness and not liveness["live"]:
            status, candidates = "spoof", []
        else:
            candidates = _rank_persons(face["feature"], top_k)
            hit = bool(candidates) and candidates[0]["similarity"] >= limit
            status = "identified" if hit else "no_match"
        faces_out.append(
            {
                "face_index": index,
                "face_region": region,
                "status": status,
                "best": candidates[0] if status == "identified" else None,
                "candidates": candidates,
                "quality": quality,
                "liveness": liveness,
            }
        )
    timing["search_ms"] = (time.perf_counter() - started) * 1000.0

    total = counts()
    return {
        "threshold": limit,
        "top_k": top_k,
        "identified": any(face["status"] == "identified" for face in faces_out),
        "face_count": len(faces_out),
        "faces": faces_out,
        "gallery": total,
        "liveness_checked": use_liveness,
        "timing": {key: round(value, 1) for key, value in timing.items()},
    }


def find_similar(threshold: float | None = None, limit: int = DEFAULT_PAIR_LIMIT) -> dict:
    """Pairs of *different* persons whose faces look alike (possible duplicates)."""
    cutoff = get_threshold() if threshold is None else float(threshold)
    limit = max(1, min(MAX_PAIR_LIMIT, int(limit)))
    rows = _faces_cache()
    best: dict[tuple[int, int], tuple[float, dict, dict]] = {}
    comparisons = 0
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            if a["person_id"] == b["person_id"]:
                continue
            comparisons += 1
            score = sdk.similarity_raw(a["raw"], b["raw"])
            if score < cutoff:
                continue
            key = tuple(sorted((a["person_id"], b["person_id"])))
            if key not in best or score > best[key][0]:
                best[key] = (score, a, b)
    pairs = sorted(best.values(), key=lambda item: item[0], reverse=True)
    return {
        "threshold": cutoff,
        "comparisons": comparisons,
        "pair_count": len(pairs),
        "pairs": [
            {
                "similarity": round(score, 4),
                "a": {"person_id": a["person_id"], "name": a["name"], "face_id": a["face_id"]},
                "b": {"person_id": b["person_id"], "name": b["name"], "face_id": b["face_id"]},
            }
            for score, a, b in pairs[:limit]
        ],
    }
