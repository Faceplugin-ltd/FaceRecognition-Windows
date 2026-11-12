<div align="center">
<img alt="FacePlugin" src="https://raw.githubusercontent.com/Faceplugin-ltd/faceplugin-assets/main/brand/logo.png" width="400"/>
</div>

#### 🌐 Company Site - [Here](https://faceplugin.com)
#### 🤗 Hugging Face - [Here](https://huggingface.co/FacePlugin-Ltd)
#### 🛟 Help Center - [Here](https://doc.faceplugin.com)
#### 🐳 Docker Hub - [Here](https://hub.docker.com/r/faceplugin/face-recognition-liveness-sdk)

# FacePlugin Face Recognition SDK — Windows (Recognition + Liveness)

**Fast edition** — face recognition + passive liveness with TAR **99.4%** @ FAR **1e-4** (TAR **98.5%** @ FAR **1e-6**). For high-accuracy match / PAD at stricter FAR, use **[FaceRecognition-Windows-v2](https://github.com/Faceplugin-ltd/FaceRecognition-Windows-v2)**.

Public repo: [FaceRecognition-Windows](https://github.com/Faceplugin-ltd/FaceRecognition-Windows).

> **Ready in ~10 minutes (after Drive download):** put runtime under `lib\cpu\` → `run.bat` → copy machine code → `curl /api/health`.  
> Jump: [Quick Start](#quick-start) · [Start the API](#start-the-api) · [SDK License](#sdk-license) · [Setup on your own app](#setup-on-your-own-app) · [Try it](#try-it)

## Quick Start

- [ ] Clone [FaceRecognition-Windows](https://github.com/Faceplugin-ltd/FaceRecognition-Windows)
- [ ] Download the CPU runtime into `lib\cpu\` — [Get the runtime](#get-the-runtime)
- [ ] `pip install -r requirements.txt` then `run.bat` — API on **8083**
- [ ] Copy machine code machine code from the terminal (or `GET /api/machinecode`)
- [ ] [Contact us](#contact) to obtain a license key → enter it at the prompt or use `POST /api/activate`
- [ ] Try Postman, curl, or Gradio on **9003** (`run_demo.bat` — local only)

No Docker on Windows. For Docker, use [FaceRecognition-Docker](https://github.com/Faceplugin-ltd/FaceRecognition-Docker) (fast) or [FaceRecognition-Docker-v2](https://github.com/Faceplugin-ltd/FaceRecognition-Docker-v2) (high-accuracy).

## Introduction

FacePlugin **Face Recognition SDK for Windows** combines on-premise **face recognition** and **face liveness** in one App. One wrapper (`FaceRecognitionSDK.dll`), one license, and two packs (`far.fpk` + `fal.fpk`). Your license unlocks Recognition only, Liveness only, or both.

This repository is **standalone**. Download the Windows runtime into this repo and run — **no other FacePlugin repository is required**.

All processing stays on your machine. **No** biometric data is sent to FacePlugin cloud — built for banking, eKYC, and on-premise compliance workflows.

**Windows** product: native x64 runtime, local HTTP API, and a Gradio demo covering Detect, Quality, Match, and Liveness. **CPU-only**.

Test with Postman, curl, or the local Gradio demo (`demo.py`). Docs: [https://doc.faceplugin.com](https://doc.faceplugin.com).

### Which version? (this repo vs v2)

| Edition | Repository | Fit | Speed / accuracy |
| ------- | ---------- | --- | ---------------- |
| **Fast (this repo)** | [FaceRecognition-Windows](https://github.com/Faceplugin-ltd/FaceRecognition-Windows) | Throughput-oriented recognition path | Extract **59 ms**; liveness check **559 ms**; TAR **99.4%** @ FAR 1e-4 |
| **High-accuracy (v2)** | [FaceRecognition-Windows-v2](https://github.com/Faceplugin-ltd/FaceRecognition-Windows-v2) | Strict eKYC / bank-grade match & PAD | Feature **1750 ms**; liveness check **559 ms**; TAR **≥ 99.9%** @ FAR **≤ 0.1%** |

Same REST API shape on both editions. Linux / Docker pairs: [FaceRecognition-Docker](https://github.com/Faceplugin-ltd/FaceRecognition-Docker) (fast) and [FaceRecognition-Docker-v2](https://github.com/Faceplugin-ltd/FaceRecognition-Docker-v2) (high-accuracy).

**Performance snapshot** (measured on **Intel Core i9-13900T**, Linux CPU engines, warm runtime, single face, median / p50; Windows is the same model stack):

| Step | Latency | Accuracy / note |
| ---- | ------- | --------------- |
| Recognition extract | **59 ms** | detect + landmarks + template; TAR **99.4%** @ FAR **1e-4**; TAR **98.5%** @ FAR **1e-6** |
| Liveness check | **559 ms** | `/api/liveness` |

**v2 reference (same host):** feature **1750 ms**; TAR **≥ 99.9%** @ FAR **≤ 0.1%**. Prefer **v2** when match / PAD accuracy is the priority; prefer **this repo** when you want the fast edition.

### Main Functionalities

| Feature | API |
| ------- | --- |
| Face detection (bounding box, landmarks, pose, attributes) | `POST /api/detect` · `sdk.detect` |
| Face quality analysis (ICAO-style checks) | `POST /api/quality` · `sdk.quality` |
| Face template extraction for matching | `POST /api/feature` · `sdk.feature` |
| 1:1 face match (two images) | `POST /api/match` · `sdk.match` |
| Feature vector similarity scoring | `POST /api/similarity` · `sdk.similarity` |
| Face liveness (passive PAD) | `POST /api/liveness` · `sdk.liveness` |
| License capabilities | `GET /api/licenseStatus` · `sdk.get_license_status` |
| Health / machine code / activate | `GET /api/health` · `GET /api/machinecode` · `POST /api/activate` |

### Product List

| Platform | Repository |
|----------|------------|
| Android (Recognition) | [FaceRecognition-Android](https://github.com/Faceplugin-ltd/FaceRecognition-Android) |
| iOS (Recognition) | [FaceRecognition-iOS](https://github.com/Faceplugin-ltd/FaceRecognition-iOS) |
| React Native (Recognition) | [FaceRecognition-React-Native](https://github.com/Faceplugin-ltd/FaceRecognition-React-Native) |
| Flutter (Recognition) | [FaceRecognition-Flutter](https://github.com/Faceplugin-ltd/FaceRecognition-Flutter) |
| Ionic Capacitor (Recognition) | [FaceRecognition-Ionic-Capacitor](https://github.com/Faceplugin-ltd/FaceRecognition-Ionic-Capacitor) |
| Ionic Cordova (Recognition) | [FaceRecognition-Ionic-Cordova](https://github.com/Faceplugin-ltd/FaceRecognition-Ionic-Cordova) |
| **Windows (Recognition + Liveness, fast)** | **[FaceRecognition-Windows](https://github.com/Faceplugin-ltd/FaceRecognition-Windows)** (**this repo**) |
| Windows (Recognition + Liveness, high-accuracy v2) | [FaceRecognition-Windows-v2](https://github.com/Faceplugin-ltd/FaceRecognition-Windows-v2) |
| Linux / Docker (Recognition + Liveness, fast) | [FaceRecognition-Docker](https://github.com/Faceplugin-ltd/FaceRecognition-Docker) |
| Linux / Docker (Recognition + Liveness, high-accuracy v2) | [FaceRecognition-Docker-v2](https://github.com/Faceplugin-ltd/FaceRecognition-Docker-v2) |
| Android (Liveness) | [FaceLivenessDetection-Android](https://github.com/Faceplugin-ltd/FaceLivenessDetection-Android) |
| iOS (Liveness) | [FaceLivenessDetection-iOS](https://github.com/Faceplugin-ltd/FaceLivenessDetection-iOS) |
| Windows (Liveness) | [FaceLivenessDetection-Windows](https://github.com/Faceplugin-ltd/FaceLivenessDetection-Windows) |
| Linux / Docker (Liveness) | [FaceLivenessDetection-Docker](https://github.com/Faceplugin-ltd/FaceLivenessDetection-Docker) |


## Before you start

| Step | What you need |
|------|----------------|
| 1 | Windows 10/11 **x64**, Python 3.10+ |
| 2 | Runtime libraries in `.\lib\cpu\` — see [Get the runtime](#get-the-runtime) |
| 3 | Start **without** a license. Copy machine code from the log or `GET /api/machinecode`, send it to FacePlugin ([contact](#contact)), then activate with your license key |

You do **not** need a license to start the API once. Product endpoints unlock after you activate.

### System requirements

| Item | Minimum | Recommended |
|------|---------|-------------|
| CPU | 2 cores | 4 cores |
| RAM | 4 GB | 8 GB |
| Disk | 4 GB | 8 GB |
| OS | Windows 10 x64 | Windows 11 |
| Python | 3.10+ | 3.12 |

## Start the API

### Get the runtime

`.\lib\cpu\` is empty on GitHub because native binaries and model files are too large. Face Recognition Windows is **CPU-only** — there is no `gpu\` package.

**[FaceRecognition-Windows runtime (Google Drive)](https://drive.google.com/drive/folders/1kwGZ1TYO3VtwSI2xkRH9p0HtnbRfknyU?usp=sharing)**

1. Clone the repo (if you have not already):

```bat
git clone https://github.com/Faceplugin-ltd/FaceRecognition-Windows.git
cd FaceRecognition-Windows
```

2. Open the Google Drive folder above.
3. Download **all files** in that folder (Drive: select all → Download, or download as a zip).
4. Put every file **directly** into `.\lib\cpu\` — not inside a nested subfolder.

```text
FaceRecognition-Windows/
└── lib/
    └── cpu/
        ├── FaceRecognitionSDK.dll
        ├── facerec.dll
        ├── far.fpk
        ├── fal-eng.dll
        ├── fal.fpk
        └── ... (onnxruntime.dll, sense4.dll)
```

Wrong layout: `lib\cpu\SomeFolder\FaceRecognitionSDK.dll` (a nested folder breaks local runs).

```bat
dir lib\cpu\FaceRecognitionSDK.dll
dir lib\cpu\facerec.dll
dir lib\cpu\far.fpk
dir lib\cpu\fal-eng.dll
dir lib\cpu\fal.fpk
```

If those paths exist, you are ready to start. `run.bat` puts `lib\cpu` on `PATH`.

### Run

You can start **without** a license — the server prints your machine code on startup.

```bat
pip install -r requirements.txt
run.bat
```

API: **http://127.0.0.1:8083**

The API starts even if activation fails. Copy the **machine code** (machine code) from the log and send it to FacePlugin.

<p align="center">
 <img src="https://raw.githubusercontent.com/Faceplugin-ltd/faceplugin-assets/main/screenshots/face-recognition/windows/api-start.png" alt="run.bat: copy machine code, paste license key, SDK ready on port 8083" width="900"/>
</p>

## SDK License

Licenses are **offline** and bound to your machine. Offline cryptography is pre-packaged within the SDK—no third-party licensing libraries or external OpenSSL installations are required.

### How to get a license

1. **Start the server** ([above](#start-the-api)). A license is not required for the first start.
2. **Copy the machine code** from the terminal. Copy it from the logs or `GET /api/machinecode`.
3. **Send that machine code** to FacePlugin ([contact](#contact)). We will issue a license key for that code.
4. **Activate** with the license key:

```bat
:: After run.bat, paste the license key on the terminal like the screenshot. You can try 3 times.

:: Or paste the license key into .\license.txt (overwrite the file), then:

curl -s -X POST http://127.0.0.1:8083/api/activate -H "Content-Type: text/plain" --data-binary @license.txt

:: Or stop the process (Ctrl+C), save license.txt, and run run.bat again
:: (run.bat can also prompt you to paste the key interactively).
```

### License capabilities (Recognition + Liveness)

After activation, `GET /api/licenseStatus` reports what the key unlocks. The Gradio demo shows the same summary as **License:** at the top of the page.

| Capability | Meaning |
| --- | --- |
| **Recognition** | Detect, quality, match, feature, similarity |
| **Liveness** | Passive face anti-spoofing (`/api/liveness`) |

Typical labels:

- **Recognition + Liveness** — full product (all tabs)
- **Recognition only** — Detect / Quality / Match; Liveness stays unavailable
- **Liveness only** — Liveness tab; Detect / Quality / Match stay unavailable
- **Not licensed** — machine code only until you activate

```bat
curl -s http://127.0.0.1:8083/api/licenseStatus
```

## Try it

### Health

```bat
curl -s http://127.0.0.1:8083/api/health
```

### Documentation

[https://doc.faceplugin.com](https://doc.faceplugin.com)

### Postman

Import [`postman/FaceRecognition-API.postman_collection.json`](postman/FaceRecognition-API.postman_collection.json).

Default base URL: `http://127.0.0.1:8083`

Routes are `/api/*` (no version segment in paths).

### Demo UI (Gradio) — local only

For a local FacePlugin Face Recognition + Liveness demo in the browser — Detect, Quality, Match, and Liveness (API must already be running on port 8083). The header shows **License:** from `/api/licenseStatus`. Tabs stay visible; unavailable capabilities show a note instead of results.

```bat
pip install -r requirements-demo.txt
run_demo.bat
```

Or (CMD):

```bat
set DEMO_PORT=9003
set API_BASE=http://127.0.0.1:8083
python demo.py
```

PowerShell:

```powershell
$env:DEMO_PORT = "9003"
$env:API_BASE = "http://127.0.0.1:8083"
python demo.py
```

Open **http://127.0.0.1:9003**. Examples when present: `assets/examples/samples/`.

<p align="center">
 <img src="https://raw.githubusercontent.com/Faceplugin-ltd/faceplugin-assets/main/screenshots/face-recognition/desktop/demo-ui-detect.png" alt="FacePlugin Face Recognition Windows demo — Detect tab with landmarks and attributes" width="900"/>
</p>

<p align="center">
 <img src="https://raw.githubusercontent.com/Faceplugin-ltd/faceplugin-assets/main/screenshots/face-recognition/desktop/demo-ui-quality.png" alt="FacePlugin Face Recognition Windows demo — Quality tab with ICAO-style checks" width="900"/>
</p>

<p align="center">
 <img src="https://raw.githubusercontent.com/Faceplugin-ltd/faceplugin-assets/main/screenshots/face-recognition/desktop/demo-ui-match.png" alt="FacePlugin Face Recognition Windows demo — Match tab with 1:1 similarity scores" width="900"/>
</p>

<p align="center">
 <img src="https://raw.githubusercontent.com/Faceplugin-ltd/faceplugin-assets/main/screenshots/face-liveness/desktop/demo-ui.png" alt="FacePlugin Face Recognition SDK Windows demo — Liveness tab with Real/Spoof score" width="900"/>
</p>

Tabs: **Detect**, **Quality**, **Match**, **Liveness**. Each recognition action has a **Result** table and **Raw JSON**. The header **License:** reflects `/api/licenseStatus`. Detect / Quality / Liveness examples are under `assets/examples/samples/` (odd/even faces plus real/fake liveness samples). Match is **Odd vs Even**.

## Setup on your own app

Two ways to call the same engine. Full protocol: [https://doc.faceplugin.com](https://doc.faceplugin.com).

| Path | When to use |
| ---- | ----------- |
| **HTTP** (`app.py` via `run.bat`) | Any language. Keep this API running and `POST` images as JSON. |
| **`sdk.py`** | Python on the **same** Windows machine as `lib\cpu\`. No HTTP hop. |

**HTTP (any language):** start the API, then call `/api/detect`, `/api/quality`, `/api/match`, `/api/feature`, `/api/similarity`, `/api/liveness`. Images are base64. See [Try it](#try-it) and Postman.

**Python in-process:** copy `sdk.py` + `lib\cpu\` into your project (or `import sdk` from this repo). Put `lib\cpu` on `PATH`. Call order: `get_machine_code` → `activate` → `init_sdk` → detect / quality / feature / match / similarity / liveness. Check `get_license_status()` for recognition vs liveness flags. Return code `0` means success.

You do **not** need Gradio (`demo.py` / `run_demo.bat`) in production — it is a local test UI.

## About SDK

Use the Python bindings in [`sdk.py`](sdk.py). Return code `0` means success.

### 1. Initializing the SDK

#### Step One

First, obtain the machine code for activation and request a license based on the machine code.

```python
import sdk

machine_code = sdk.get_machine_code()
print("machineCode:", machine_code)  # machine code
```

#### Step Two

Next, activate the SDK with the path to your license file (`license.txt` containing your license key).

```python
ret = sdk.activate("license.txt")
```

If activation is successful, the return value will be `0`. Otherwise, an error value will be returned.

#### Step Three

After activation, call the initialization function of the SDK.

```python
ret = sdk.init_sdk()
```

If initialization is successful, the return value will be `0`. Otherwise, an error value will be returned.

### 2. APIs

#### Detect

```python
result = sdk.detect(base64_image, crop_image=False)
```

#### Quality

```python
result = sdk.quality(base64_image, crop_image=False)
```

#### Feature

```python
result = sdk.feature(base64_image)
```

#### Match

```python
result = sdk.match(base64_image1, base64_image2, crop_image=False)
```

#### Similarity

```python
result = sdk.similarity(feature1_b64, feature2_b64)
```

#### Liveness

```python
result = sdk.liveness(base64_image)
```

#### License status

```python
status = sdk.get_license_status()
# recognition / liveness flags, label, level 0|1|2
```

HTTP endpoints: `/api/health`, `/api/machinecode`, `/api/licenseStatus`, `/api/backend`, `/api/activate`, `/api/detect`, `/api/quality`, `/api/match`, `/api/feature`, `/api/similarity`, `/api/liveness`.

## Contact

<div align="left">
<a target="_blank" href="mailto:info@faceplugin.com"><img src="https://img.shields.io/badge/email-info@faceplugin.com-blue.svg?logo=gmail" alt="faceplugin.com"></a>&emsp;
<a target="_blank" href="https://t.me/FacePluginSupport"><img src="https://img.shields.io/badge/telegram-@FacePluginSupport-blue.svg?logo=telegram" alt="Telegram @FacePluginSupport"></a>&emsp;
<a target="_blank" href="https://wa.me/+14692784822"><img src="https://img.shields.io/badge/whatsapp-faceplugin-blue.svg?logo=whatsapp" alt="faceplugin.com"></a>
</div>
