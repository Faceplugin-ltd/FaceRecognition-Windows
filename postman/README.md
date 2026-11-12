Import `FaceRecognition-API.postman_collection.json`.

baseUrl: `http://127.0.0.1:8083`

Flow: Health → Machine code → Activate (FP1.) → License status → Detect / Quality / Match / Liveness

`GET /api/licenseStatus` reports recognition vs liveness from the key level (0 / 1 / 2).
