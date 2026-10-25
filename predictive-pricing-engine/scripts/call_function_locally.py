"""Call the deployable function in-process, the way the Functions runtime does, and time it.

    venv/Scripts/python scripts/call_function_locally.py            # three examples + 400 + 405
    venv/Scripts/python scripts/call_function_locally.py --offline  # skip the live Gemini lookup

Run it with the slim deploy virtualenv (requirements.txt only) to prove the function needs
nothing else. No context.dev call is made.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

T0 = time.perf_counter()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
from ml_engine import serving  # noqa: E402

T_IMPORT = time.perf_counter()
serving.load()
T_LOAD = time.perf_counter()

from werkzeug.test import EnvironBuilder  # noqa: E402
from werkzeug.wrappers import Request  # noqa: E402

EXAMPLES = [
    {"currentPrice": 249.69, "regularPrice": None, "category": None, "retailer": "bestbuy",
     "title": "Apple - AirPods Pro 2, Wireless Active Noise Cancelling Earbuds - White", "productId": "6447382"},
    {"currentPrice": 609.99, "regularPrice": None, "category": "Computer Memory", "retailer": "amazon",
     "title": "CORSAIR Vengeance DDR5 RAM 64GB (2x32GB) 6000MHz CL30", "productId": "B0C3RYHZJQ"},
    {"currentPrice": 14.99, "regularPrice": None, "category": "Beauty", "retailer": "amazon",
     "title": "Maybelline Lash Sensational Mascara", "productId": None},
]


def call(method: str, body=None):
    kwargs = {"method": method, "path": "/"}
    if body is not None:
        kwargs["json"] = body
    t0 = time.perf_counter()
    response = main.forecastPrice(Request(EnvironBuilder(**kwargs).get_environ()))
    return response.status_code, response.get_data(as_text=True), (time.perf_counter() - t0) * 1000


if __name__ == "__main__":
    if "--offline" in sys.argv:
        serving._macro = serving.MacroCache(fetch=lambda timeout_s: (_ for _ in ()).throw(ConnectionError("offline")))
    print(f"# cold start (this machine): imports {1000 * (T_IMPORT - T0):.0f} ms + model load {1000 * (T_LOAD - T_IMPORT):.0f} ms")
    for body in EXAMPLES:
        status, text, ms = call("POST", body)
        print(f"\n# POST {json.dumps(body)}\n# -> HTTP {status} in {ms:.1f} ms\n{text}")
    for label, method, body in (("missing currentPrice", "POST", {"retailer": "amazon"}), ("GET", "GET", None)):
        status, text, ms = call(method, body)
        print(f"\n# {label} -> HTTP {status} in {ms:.1f} ms\n{text}")
    timings = [call("POST", EXAMPLES[0])[2] for _ in range(300)]
    timings.sort()
    print(f"\n# warm: median {timings[150]:.2f} ms, p95 {timings[285]:.2f} ms, max {timings[-1]:.2f} ms over 300 requests")
