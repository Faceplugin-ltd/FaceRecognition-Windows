#!/usr/bin/env python3
"""Face Recognition HTTP API: 1:1 verification and 1:N identification.

Every route lives under /api/v1/… and is also served at the old /api/… path.
Interactive API docs: http://127.0.0.1:8092/docs

Configuration (environment variables):
  PORT / FACESDK_PORT     Listen port (default 8092).
  FACESDK_BIND_HOST       Listen address (default 127.0.0.1 = this machine only).
  FACESDK_CORS_ORIGINS    Comma-separated browser origins allowed to call the API
                          ("*" for any). Default: none (server-to-server only).
  FACESDK_MAX_UPLOAD_MB   Largest request body in MB (default 20).
  FACESDK_IMPORT_ROOT     Folder that POST /api/v1/persons/import may read. Default: disabled.
  FACESDK_THREADS         Worker threads (default 8).
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path

from flask import Flask, Response, g, request, send_file
from werkzeug.exceptions import HTTPException

import gallery
import http_body
import license_ux
import sdk

ROOT = Path(__file__).resolve().parent
PRODUCT = "FaceRecognition"
API_VERSION = "1.0.0"

CORS_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.environ.get("FACESDK_CORS_ORIGINS", "").split(",")
    if origin.strip()
}
MAX_UPLOAD_MB = float(os.environ.get("FACESDK_MAX_UPLOAD_MB", "20"))
_import_root = os.environ.get("FACESDK_IMPORT_ROOT", "").strip()
IMPORT_ROOT = Path(_import_root).resolve() if _import_root else None

log = logging.getLogger("face_recognition")
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(MAX_UPLOAD_MB * 1024 * 1024)


def route(path: str, methods: list[str]):
    """Register a view at /api/v1<path> and the legacy /api<path>."""

    def wrap(view):
        app.add_url_rule(f"/api/v1{path}", view.__name__, view, methods=methods)
        app.add_url_rule(f"/api{path}", f"legacy_{view.__name__}", view, methods=methods)
        return view

    return wrap


class ApiError(Exception):
    """An error whose message is safe to return to the caller."""

    def __init__(self, code: int, message: str, status: int = 400, data=None):
        super().__init__(message)
        self.code, self.message, self.status, self.data = code, message, status, data


def envelope(success: bool, code: int, message: str, data=None, status: int = 200):
    return Response(
        json.dumps({"success": success, "code": code, "message": message, "data": data}),
        status=status,
        mimetype="application/json",
    )


def ok(data=None, message: str = "OK"):
    return envelope(True, 0, message, data)


def sdk_json(result):
    raw = result if isinstance(result, (bytes, bytearray)) else str(result).encode()
    return Response(raw, mimetype="application/json")


# ---------------------------------------------------------------- CORS


@app.before_request
def note_started():
    g.started = time.perf_counter()


@app.after_request
def cors(resp):
    started = getattr(g, "started", None)
    if started is not None:
        resp.headers["X-Process-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    origin = (request.headers.get("Origin") or "").rstrip("/")
    if origin and ("*" in CORS_ORIGINS or origin in CORS_ORIGINS):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/api/<path:_unused>", methods=["OPTIONS"])
def options(_unused):
    return Response(status=204)


# ---------------------------------------------------------------- input helpers


def _body() -> dict:
    if request.method in ("GET", "DELETE"):
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}
    return http_body.parse_body()


def _param(body: dict, name: str, default=None):
    value = body.get(name)
    if value in (None, ""):
        value = request.args.get(name)
    return default if value in (None, "") else value


def _number(body: dict, name: str, default=None, *, kind=float):
    value = _param(body, name)
    if value is None:
        return default
    try:
        number = kind(value)
    except (TypeError, ValueError, OverflowError):
        raise ApiError(-1, f"{name} must be a number") from None
    if not math.isfinite(number):
        raise ApiError(-1, f"{name} must be a number")
    return number


def _flag(body: dict, name: str, default=None):
    value = _param(body, name)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _image(body: dict) -> str:
    image = body.get("image")
    if not image:
        raise ApiError(-1, "image is required (base64 in JSON, or a file in form-data)")
    return image


def _import_path(raw: str) -> Path:
    if IMPORT_ROOT is None:
        raise ApiError(-4, "Import is disabled. Set FACESDK_IMPORT_ROOT on the server.", 403)
    path = (IMPORT_ROOT / raw).resolve()
    if path != IMPORT_ROOT and not path.is_relative_to(IMPORT_ROOT):
        raise ApiError(-4, "Path is outside the allowed import folder", 403)
    return path


# ---------------------------------------------------------------- system


@route("/health", ["GET"])
def health():
    return ok({"status": "ok", "product": PRODUCT, "api_version": API_VERSION})


@route("/machinecode", ["GET"])
def machinecode():
    return ok({"machinecode": sdk.get_machine_code()})


@route("/licenseStatus", ["GET"])
def license_status():
    return ok(sdk.get_license_status())


@route("/backend", ["GET"])
def backend():
    name = sdk.backend()
    return ok({
        "product": PRODUCT,
        "sdk_version": "1.0.0",
        "api_version": API_VERSION,
        "backend": name or "cpu",
        "lib_dir": f"lib/{name}",
        "liveness": bool(sdk.HAS_LIVENESS),
    })


@route("/activate", ["POST"])
def activate():
    raw = http_body.activate_license_bytes()
    if not raw:
        raise ApiError(-1, "Empty license")
    path = license_ux.save_license(ROOT, raw)
    res = sdk.activate(str(path))
    mc = sdk.get_machine_code()
    if res != 0:
        return envelope(False, -1, "Invalid license", {"activated": False, "machinecode": mc})
    sdk.init_sdk()
    return ok({"activated": True, "machinecode": mc}, "Activated")


# ---------------------------------------------------------------- 1:1 and analysis
# These return the SDK's own JSON unchanged (no envelope), as before.


@route("/detect", ["POST"])
def detect():
    data = http_body.parse_body()
    return sdk_json(sdk.detect(_image(data), http_body.crop_flag(data)))


@route("/quality", ["POST"])
def quality():
    data = http_body.parse_body()
    return sdk_json(sdk.quality(_image(data), http_body.crop_flag(data)))


@route("/match", ["POST"])
def match():
    data = http_body.parse_body()
    if not data.get("image1") or not data.get("image2"):
        raise ApiError(-1, "image1 and image2 are required")
    return sdk_json(sdk.match(data["image1"], data["image2"], http_body.crop_flag(data)))


@route("/feature", ["POST"])
def feature():
    return sdk_json(sdk.feature(_image(http_body.parse_body())))


@route("/similarity", ["POST"])
def similarity():
    data = http_body.parse_body()
    f1 = data.get("feature1") or data.get("template1")
    f2 = data.get("feature2") or data.get("template2")
    if not f1 or not f2:
        raise ApiError(-1, "feature1 and feature2 are required (template1/template2 also accepted)")
    return sdk_json(sdk.similarity(f1, f2))


# ---------------------------------------------------------------- settings


@route("/settings", ["GET"])
def settings_get():
    return ok(gallery.get_settings())


@route("/settings", ["POST", "PATCH"])
def settings_set():
    body = _body()
    values = {
        "threshold": _number(body, "threshold"),
        "duplicate_check": _flag(body, "duplicate_check"),
        "liveness_check": _flag(body, "liveness_check"),
    }
    return ok(gallery.set_settings(values))


# ---------------------------------------------------------------- gallery


@route("/persons", ["GET"])
def persons_list():
    body = _body()
    return ok(gallery.list_persons(
        page=_number(body, "page", 1, kind=int),
        page_size=_number(body, "page_size", gallery.DEFAULT_PAGE_SIZE, kind=int),
        query=str(_param(body, "q", "") or ""),
        thumbs=_flag(body, "thumbs", False),
    ))


@route("/persons", ["POST"])
def persons_enroll():
    body = _body()
    result = gallery.enroll(
        _image(body),
        name=str(_param(body, "name", "") or ""),
        note=str(_param(body, "note", "") or ""),
        person_id=_number(body, "person_id", None, kind=int),
        merge_by_name=_flag(body, "merge_by_name", False),
        on_duplicate=str(_param(body, "on_duplicate", "reject")),
        check_quality=_flag(body, "check_quality", False),
        source=str(_param(body, "source", "") or ""),
    )
    return ok(result, "Enrolled")


@route("/persons", ["DELETE"])
def persons_clear():
    if not _flag(_body(), "confirm", False):
        raise ApiError(-1, "Deleting the whole gallery needs confirm=true")
    return ok(gallery.clear_all(), "Deleted")


@route("/persons/<int:person_id>", ["GET"])
def person_get(person_id: int):
    return ok(gallery.get_person(person_id, thumbs=_flag(_body(), "thumbs", False)))


@route("/persons/<int:person_id>", ["PATCH", "PUT"])
def person_update(person_id: int):
    body = _body()
    name, note = body.get("name"), body.get("note")
    return ok(gallery.update_person(
        person_id,
        name=None if name is None else str(name),
        note=None if note is None else str(note),
    ), "Updated")


@route("/persons/<int:person_id>", ["DELETE"])
def person_delete(person_id: int):
    return ok(gallery.delete_person(person_id), "Deleted")


@route("/persons/import", ["POST"])
def persons_import():
    body = _body()
    name_from = str(_param(body, "name_from", "file"))
    if name_from not in ("file", "folder"):
        raise ApiError(-1, "name_from must be file or folder")
    on_duplicate = str(_param(body, "on_duplicate", "reject"))
    paths = body.get("paths") or []
    if paths:
        resolved = [_import_path(str(p)) for p in paths]
        return ok(gallery.import_paths(resolved, name_from, on_duplicate), "Imported")
    directory = body.get("directory") or ""
    if not directory:
        raise ApiError(-1, "directory or paths is required")
    return ok(gallery.import_directory(str(_import_path(directory)), name_from, on_duplicate),
              "Imported")


@route("/faces/<int:face_id>", ["DELETE"])
def face_delete(face_id: int):
    return ok(gallery.delete_face(face_id), "Deleted")


@route("/faces/<int:face_id>/image", ["GET"])
def face_image(face_id: int):
    kind = "full" if request.args.get("kind") == "full" else "crop"
    path = gallery.image_file(face_id, kind)
    if path is None:
        raise ApiError(-10, "Image not found", 404)
    return send_file(path, mimetype="image/jpeg", max_age=3600)


@route("/identify", ["POST"])
def identify():
    body = _body()
    return ok(gallery.identify(
        _image(body),
        threshold=_number(body, "threshold"),
        top_k=_number(body, "top_k", gallery.DEFAULT_TOP_K, kind=int),
        check_liveness=_flag(body, "liveness", None),
        check_quality=_flag(body, "quality", False),
    ))


@route("/similar", ["POST"])
def similar():
    body = _body()
    return ok(gallery.find_similar(
        _number(body, "threshold"),
        _number(body, "limit", gallery.DEFAULT_PAIR_LIMIT, kind=int),
    ))


# ---------------------------------------------------------------- docs

_DOCS_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>FacePlugin Face Recognition API</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css"/>
</head><body><div id="ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({url: "/api/v1/openapi.json", dom_id: "#ui", deepLinking: true});</script>
<noscript>Open /api/v1/openapi.json for the API description.</noscript>
</body></html>"""


@app.get("/api/v1/openapi.json")
def openapi():
    spec = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    spec["servers"] = [{"url": request.host_url.rstrip("/")}]
    return Response(json.dumps(spec), mimetype="application/json")


@app.get("/docs")
def docs():
    return Response(_DOCS_HTML, mimetype="text/html")


# ---------------------------------------------------------------- errors


@app.errorhandler(ApiError)
def api_error(ex: ApiError):
    return envelope(False, ex.code, ex.message, ex.data, ex.status)


@app.errorhandler(gallery.GalleryError)
def gallery_error(ex: gallery.GalleryError):
    status = {-10: 404, -7: 409, -8: 422}.get(ex.code, 400)
    return envelope(False, ex.code, str(ex), ex.data or None, status)


@app.errorhandler(HTTPException)
def http_error(ex: HTTPException):
    if ex.code == 404:
        return envelope(False, -10, "Not found", None, 404)
    if ex.code == 413:
        return envelope(False, -5, f"Request is larger than {MAX_UPLOAD_MB:g} MB", None, 413)
    return envelope(False, -19, ex.description or str(ex), None, ex.code or 500)


@app.errorhandler(Exception)
def fail(ex: Exception):
    if isinstance(ex, HTTPException):
        return http_error(ex)
    log.exception("Unhandled error on %s %s", request.method, request.path)
    return envelope(False, -19, "Internal server error", None, 500)


# ---------------------------------------------------------------- startup


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.environ.get("PORT", os.environ.get("FACESDK_PORT", "8092")))
    host = os.environ.get("FACESDK_BIND_HOST", "127.0.0.1")
    license_ux.bootstrap(
        ROOT,
        get_machine_code=sdk.get_machine_code,
        activate=sdk.activate,
        init_sdk=sdk.init_sdk,
    )
    print(f"Face Recognition API {API_VERSION} on http://{host}:{port}  (docs: /docs)")
    print("Folder import:", IMPORT_ROOT or "disabled")
    try:
        from waitress import serve
    except ImportError:
        print("WARNING: waitress is not installed; using Flask's development server.")
        app.run(host=host, port=port, threaded=True)
        return
    serve(app, host=host, port=port, threads=int(os.environ.get("FACESDK_THREADS", "8")))


if __name__ == "__main__":
    main()
