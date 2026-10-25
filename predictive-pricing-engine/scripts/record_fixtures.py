"""Record raw context.dev scrape responses as JSON fixtures.

Each run makes LIVE calls that cost credits (2 per scrape), so it is deliberately
sequential (the plan allows one request at a time) and appends every attempt to
benchmarks/fixtures/contextdev/_call_log.jsonl so the number of live calls is auditable.

Usage:  python scripts/record_fixtures.py [name ...]     (no names = record all missing)
The API key is read from CONTEXT_DEV_API_KEY, or from the env file named by
CONTEXT_DEV_ENV_FILE. It is never written to a fixture.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmarks" / "fixtures" / "contextdev"
ENDPOINT = "https://api.context.dev/v1/web/scrape"

TARGETS: dict[str, dict] = {
    "amazon_airpods_pro2": {"url": "https://www.amazon.com/dp/B0CHWRXH8B", "formats": {"product": True}},
    "amazon_corsair_ddr5": {"url": "https://www.amazon.com/dp/B0C3RYHZJQ", "formats": {"product": True}},
    "amazon_dead_spec_asin": {"url": "https://www.amazon.com/dp/B082DH1FWM", "formats": {"product": True}},
    "bestbuy_airpods_pro2": {"url": "https://www.bestbuy.com/site/6447382.p?skuId=6447382", "formats": {"product": True}},
    "bestbuy_spec_sku_6448611": {"url": "https://www.bestbuy.com/site/corsair-vengeance-32gb/6448611.p", "formats": {"product": True}},
    "pcpartpicker_7800x3d": {
        "url": "https://pcpartpicker.com/product/3hyH99/amd-ryzen-7-7800x3d-42-ghz-8-core-processor-100-100000910wof",
        "formats": {"product": True, "markdown": True},
    },
    "bestbuy_home_not_a_product": {"url": "https://www.bestbuy.com/", "formats": {"product": True}},
    "newegg_7800x3d": {"url": "https://www.newegg.com/p/N82E16819113793", "formats": {"product": True}},
    "camel_price_history_page": {"url": "https://camelcamelcamel.com/product/B0CHWRXH8B", "formats": {"markdown": True}},
}


def load_key() -> str:
    key = os.environ.get("CONTEXT_DEV_API_KEY")
    env_file = os.environ.get("CONTEXT_DEV_ENV_FILE")
    if not key and env_file and Path(env_file).exists():
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("CONTEXT_DEV_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise SystemExit("CONTEXT_DEV_API_KEY is not set (and CONTEXT_DEV_ENV_FILE did not provide it)")
    return key


def record(name: str, body: dict, key: str) -> None:
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    status, payload, headers = None, None, {}
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            status, headers = resp.status, dict(resp.headers)
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        status, headers = err.code, dict(err.headers)
        raw = err.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"raw": raw[:2000]}
    except Exception as err:  # network failure / timeout
        payload = {"client_error": repr(err)}
    elapsed = round(time.time() - started, 2)

    # Screenshots / raw html are not needed for tests; keep fixtures small.
    if isinstance(payload, dict):
        md = payload.get("markdown")
        if isinstance(md, dict) and isinstance(md.get("data"), str) and len(md["data"]) > 60000:
            md["data"] = md["data"][:60000] + "\n[fixture truncated]"
    fixture = {
        "_recorded": {
            "request": body,
            "http_status": status,
            "elapsed_seconds": elapsed,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "rate_limit_headers": {k: v for k, v in headers.items() if k.lower().startswith(("x-ratelimit", "retry-after"))},
        },
        "response": payload,
    }
    (OUT / f"{name}.json").write_text(json.dumps(fixture, indent=1), encoding="utf-8")
    with (OUT / "_call_log.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"name": name, "url": body["url"], "http_status": status, "elapsed_seconds": elapsed}) + "\n")
    print(f"{name}: HTTP {status} in {elapsed}s", flush=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    key = load_key()
    names = sys.argv[1:] or [n for n in TARGETS if not (OUT / f"{n}.json").exists()]
    for name in names:
        record(name, TARGETS[name], key)


if __name__ == "__main__":
    main()
