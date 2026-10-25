"""Firebase Functions (2nd gen, Python 3.12) entry point.

    POST forecastPrice
    {"currentPrice": 249.69, "regularPrice": null, "category": null, "retailer": "bestbuy",
     "title": "Apple - AirPods Pro 2", "productId": "6447382"}

The function does not scrape: the caller supplies the price it already read. It runs the
ONNX quantile model and returns the forecast (contract in README.md, "Deployed function").
The product model is trained on SYNTHETIC data; the response says so in forecast.model.trainedOn.
"""
from __future__ import annotations

import json

from firebase_functions import https_fn, options

from ml_engine.serving import handle_forecast


@https_fn.on_request(region="us-central1", memory=options.MemoryOption.MB_512, timeout_sec=30, max_instances=10)
def forecastPrice(req: https_fn.Request) -> https_fn.Response:
    body = req.get_json(silent=True) if req.method == "POST" else None
    status, payload = handle_forecast(req.method, body)
    headers = {"Content-Type": "application/json", "Cache-Control": "no-store"}
    if status == 405:
        headers["Allow"] = "POST"
    return https_fn.Response(json.dumps(payload), status=status, headers=headers)
