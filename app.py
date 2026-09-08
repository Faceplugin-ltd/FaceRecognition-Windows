#!/usr/bin/env python3
"""Face Recognition HTTP API — Flask + sdk.py."""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Flask, Response, request
from werkzeug.exceptions import HTTPException

import http_body
import license_ux
import sdk

ROOT = Path(__file__).resolve().parent
app = Flask(__name__)


def envelope(success: bool, code: int, message: str, data=None, status: int = 200):
    return Response(
        json.dumps(
            {
                "success": success,
                "code": code,
                "message": message,
                "request_id": None,
                "data": data,
            }
        ),
        status=status,
        mimetype="application/json",
    )


def sdk_json(result):
    raw = result if isinstance(result, (bytes, bytearray)) else str(result).encode()
    return Response(raw, mimetype="application/json")


@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/api/<path:_unused>", methods=["OPTIONS"])
def options(_unused):
    return Response(status=204)


@app.get("/api/health")
def health():
    return envelope(True, 0, "OK", {"status": "ok"})


@app.get("/api/machinecode")
def machinecode():
    return envelope(True, 0, "OK", {"machinecode": sdk.get_machine_code()})


@app.get("/api/licenseStatus")
def license_status():
    return envelope(True, 0, "OK", sdk.get_license_status())


@app.get("/api/backend")
def backend():
    name = sdk.backend()
    return envelope(
        True,
        0,
        "OK",
        {
            "product": "FaceRecognition",
            "sdk_version": "1.0.0",
            "backend": name or "cpu",
            "lib_dir": f"lib/{name}",
        },
    )


@app.post("/api/activate")
def activate():
    raw = http_body.activate_license_bytes()
    if not raw:
        return envelope(False, -1, "Empty license", None)
    path = license_ux.save_license(ROOT, raw)
    res = sdk.activate(str(path))
    mc = sdk.get_machine_code()
    if res != 0:
        return envelope(
            False, -1, "Invalid license", {"activated": False, "machinecode": mc}
        )
    sdk.init_sdk()
    return envelope(
        True, 0, "Successfully activated", {"activated": True, "machinecode": mc}
    )


@app.post("/api/detect")
def detect():
    data = http_body.parse_body()
    crop = http_body.crop_flag(data)
    return sdk_json(sdk.detect(data["image"], crop))


@app.post("/api/quality")
def quality():
    data = http_body.parse_body()
    crop = http_body.crop_flag(data)
    return sdk_json(sdk.quality(data["image"], crop))


@app.post("/api/match")
def match():
    data = http_body.parse_body()
    crop = http_body.crop_flag(data)
    return sdk_json(sdk.match(data["image1"], data["image2"], crop))


@app.post("/api/feature")
def feature():
    data = http_body.parse_body()
    return sdk_json(sdk.feature(data["image"]))


@app.post("/api/similarity")
def similarity():
    data = http_body.parse_body()
    f1 = data.get("feature1") or data.get("template1")
    f2 = data.get("feature2") or data.get("template2")
    if not f1 or not f2:
        return envelope(
            False,
            -1,
            "feature1/feature2 required (template1/template2 aliases ok)",
            None,
        )
    return sdk_json(sdk.similarity(f1, f2))


@app.errorhandler(HTTPException)
def http_error(ex: HTTPException):
    if ex.code == 404:
        return envelope(False, -10, "Not found", None, 404)
    return envelope(False, -19, ex.description or str(ex), None, ex.code or 500)


@app.errorhandler(Exception)
def fail(ex: Exception):
    if isinstance(ex, HTTPException):
        return http_error(ex)
    return envelope(False, -19, str(ex), None, 500)


def main() -> None:
    license_ux.bootstrap(
        ROOT,
        get_machine_code=sdk.get_machine_code,
        activate=sdk.activate,
        init_sdk=sdk.init_sdk,
    )
    port = int(os.environ.get("PORT", os.environ.get("FACESDK_PORT", "8083")))
    host = os.environ.get("FACESDK_BIND_HOST", "0.0.0.0")
    print("API listening on http://{0}:{1}".format(host, port))
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
