"""One LIVE end-to-end prediction (real context.dev scrape = 2 credits, real Gemini call).

    python scripts/live_smoke.py <product-url>

The key comes from CONTEXT_DEV_API_KEY, or from the env file named by CONTEXT_DEV_ENV_FILE.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_key_from_env_file() -> None:
    env_file = os.environ.get("CONTEXT_DEV_ENV_FILE")
    if os.environ.get("CONTEXT_DEV_API_KEY") or not env_file or not Path(env_file).exists():
        return
    for line in Path(env_file).read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("CONTEXT_DEV_API_KEY="):
            os.environ["CONTEXT_DEV_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")


async def main(url: str) -> None:
    from ml_engine.service import PricingService
    service = PricingService()
    try:
        result = await service.predict(url)
    finally:
        await service.aclose()
    result.pop("features", None)
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    load_key_from_env_file()
    asyncio.run(main(sys.argv[1]))
