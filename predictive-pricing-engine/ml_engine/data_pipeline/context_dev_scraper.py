"""context.dev structured product extractor.

POST https://api.context.dev/v1/web/scrape  {"url": ..., "formats": {"product": true}}
The key is read from the CONTEXT_DEV_API_KEY environment variable and never hardcoded.

Facts about the live service this client is built around (measured 2026-10-11):
  * The plan allows ONE request at a time (X-RateLimit-Mode: concurrency, limit 1); a second
    concurrent call gets HTTP 429 with Retry-After. Calls are therefore serialized locally.
  * An uncached scrape takes roughly 7-47 s; a cached repeat is under 1 s. 2 credits each.
  * HTTP is 200 even for dead pages: check product.success / error_code and isProductPage.

A scrape that fails, is not a product page, or has no price raises ScrapeError. Nothing in
this module ever substitutes a price.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse

import aiohttp

from .feature_engineering import retailer_from_url

ENDPOINT = "https://api.context.dev/v1/web/scrape"
HTTP_TIMEOUT_S = 60.0          # a scrape that is already running is allowed to finish and warm the cache
RESULT_CACHE_TTL_S = 10 * 60
MAX_429_RETRIES = 3

# (status, headers, json payload)
Transport = Callable[[dict, dict, float], Awaitable[tuple[int, dict, dict]]]


class ScrapeError(Exception):
    """A product could not be read. `code` is stable and machine-readable."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


@dataclass
class ProductSnapshot:
    url: str
    retailer: str
    product_id: Optional[str]
    name: str
    price: float
    regular_price: Optional[float]
    currency: Optional[str]
    availability: Optional[str]
    category: Optional[str]
    brand: Optional[str]
    sku: Optional[str]
    in_stock: Optional[bool]
    is_first_party_seller: Optional[bool] = None   # context.dev does not expose the seller
    marketplace: dict = field(default_factory=dict)  # retailer-specific extras
    cache_status: Optional[str] = None

    def to_json(self) -> dict:
        return asdict(self)


_ID_PATTERNS: dict[str, tuple[str, ...]] = {
    "amazon": (r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?#]|$)",),
    "bestbuy": (r"[?&]skuId=(\d{7,8})", r"/(\d{7,8})\.p(?:[/?#]|$)", r"/sku/(\d{7,8})"),
    "pcpartpicker": (r"/product/([A-Za-z0-9]{4,8})(?:[/?#]|$)",),
    "newegg": (r"/p/([A-Z0-9-]{8,20})(?:[/?#]|$)", r"[?&]Item=([A-Z0-9-]{8,20})"),
    "bhphoto": (r"/c/product/(\d+)-",),
}


def product_id_from_url(url: str, retailer: Optional[str] = None) -> Optional[str]:
    retailer = retailer or retailer_from_url(url)
    for pattern in _ID_PATTERNS.get(retailer, ()):
        match = re.search(pattern, url, re.I)
        if match:
            return match.group(1).upper() if retailer in ("amazon", "newegg") else match.group(1)
    return None


def validate_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc or "." not in parsed.netloc:
        raise ScrapeError("invalid_url", "the product URL must be an http(s) URL")
    return parsed.geturl()


_PCPP_ROW = re.compile(r"^\|\s*\[([^\]]+)\]\([^)]*\)\s*\|\s*\$([0-9,]+\.\d{2})\s*\|(.*)$", re.M)


def parse_pcpartpicker_prices(markdown: str) -> dict:
    """Lowest current vendor and price spread from PCPartPicker's merchant table."""
    section = (markdown or "").split("## Prices", 1)
    if len(section) < 2:
        return {}
    table = section[1].split("\n## ", 1)[0]
    offers = []
    for match in _PCPP_ROW.finditer(table):
        in_stock = "in stock" in match.group(3).lower()
        offers.append({"merchant": match.group(1), "base_price": float(match.group(2).replace(",", "")), "in_stock": in_stock})
    available = [o for o in offers if o["in_stock"]] or offers
    if not available:
        return {}
    lowest = min(available, key=lambda o: o["base_price"])
    highest = max(available, key=lambda o: o["base_price"])
    return {"lowest_vendor": lowest["merchant"], "lowest_price": lowest["base_price"],
            "highest_price": highest["base_price"],
            "price_spread": round(highest["base_price"] - lowest["base_price"], 2),
            "vendors": len(available)}


def _positive(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 and number == number and number != float("inf") else None


def parse_product_response(payload: dict, url: str) -> ProductSnapshot:
    """Turn a raw scrape response into a ProductSnapshot, or raise ScrapeError."""
    if not isinstance(payload, dict):
        raise ScrapeError("upstream_error", "context.dev returned an unreadable response", retryable=True)
    block = payload.get("product")
    if not isinstance(block, dict) or block.get("success") is None:
        raise ScrapeError("upstream_error", "context.dev returned no product output", retryable=True)
    if block.get("success") is False:
        code = str(block.get("error_code") or "").upper()
        message = block.get("message") or "the page could not be read"
        if code == "NOT_FOUND":
            raise ScrapeError("not_found", f"the product page does not exist ({message})")
        raise ScrapeError("scrape_failed", f"context.dev could not read the page: {code or 'UNKNOWN'} {message}", retryable=True)
    data = block.get("data") or {}
    product = data.get("product")
    if data.get("isProductPage") is False or not isinstance(product, dict):
        raise ScrapeError("not_a_product_page", "the URL is not a product page")

    availability = product.get("availability")
    in_stock = None if availability is None else availability in ("in_stock", "limited_stock", "preorder", "backorder")
    price = _positive(product.get("price"))
    if price is None:
        if in_stock is False:
            raise ScrapeError("out_of_stock", "the product is out of stock and shows no price")
        raise ScrapeError("no_price", "the product page shows no price")

    retailer = retailer_from_url(url)
    regular = _positive(product.get("regularPrice"))
    if regular is not None and regular < price:
        regular = None  # a "regular" price below the selling price is not a list price
    marketplace: dict = {}
    if retailer == "pcpartpicker":
        markdown = (payload.get("markdown") or {}).get("data")
        marketplace = parse_pcpartpicker_prices(markdown) if isinstance(markdown, str) else {}
    return ProductSnapshot(
        url=url, retailer=retailer, product_id=product_id_from_url(url, retailer),
        name=str(product.get("name") or "").strip(), price=price, regular_price=regular,
        currency=product.get("currency"), availability=availability, category=product.get("category"),
        brand=product.get("brand"), sku=product.get("sku"), in_stock=in_stock, marketplace=marketplace,
        cache_status=(payload.get("cache_metadata") or {}).get("status"),
    )


async def _aiohttp_transport(body: dict, headers: dict, timeout_s: float) -> tuple[int, dict, dict]:
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(ENDPOINT, json=body, headers=headers) as resp:
            try:
                payload = await resp.json(content_type=None)
            except ValueError:
                payload = {}
            return resp.status, dict(resp.headers), payload


class ContextDevScraper:
    def __init__(self, api_key: Optional[str] = None, transport: Optional[Transport] = None,
                 max_concurrency: int = 1, http_timeout_s: float = HTTP_TIMEOUT_S,
                 cache_ttl_s: float = RESULT_CACHE_TTL_S):
        self._api_key = api_key if api_key is not None else os.environ.get("CONTEXT_DEV_API_KEY", "")
        self._transport = transport or _aiohttp_transport
        self._slot = asyncio.Semaphore(max_concurrency)
        self._http_timeout_s = http_timeout_s
        self._cache_ttl_s = cache_ttl_s
        self._cache: dict[tuple[str, str], tuple[float, dict]] = {}
        self.live_calls = 0

    def cached(self, url: str, kind: str = "product") -> Optional[dict]:
        hit = self._cache.get((kind, url))
        if hit and time.monotonic() - hit[0] < self._cache_ttl_s:
            return hit[1]
        return None

    async def _request(self, url: str, formats: dict, kind: str, on_start: Optional[Callable[[], None]] = None) -> dict:
        hit = self.cached(url, kind)
        if hit is not None:
            return hit
        if not self._api_key:
            raise ScrapeError("missing_api_key", "CONTEXT_DEV_API_KEY is not set")
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        async with self._slot:
            hit = self.cached(url, kind)
            if hit is not None:
                return hit
            if on_start:
                on_start()
            for attempt in range(MAX_429_RETRIES + 1):
                self.live_calls += 1
                try:
                    status, resp_headers, payload = await self._transport({"url": url, "formats": formats}, headers, self._http_timeout_s)
                except asyncio.TimeoutError:
                    raise ScrapeError("upstream_timeout", "context.dev did not answer in time", retryable=True)
                except aiohttp.ClientError as err:
                    raise ScrapeError("network_error", f"could not reach context.dev ({type(err).__name__})", retryable=True)
                if status == 429 and attempt < MAX_429_RETRIES:
                    try:
                        wait = float(resp_headers.get("Retry-After", 2))
                    except (TypeError, ValueError):
                        wait = 2.0
                    await asyncio.sleep(min(max(wait, 0.5), 10.0))
                    continue
                if status == 429:
                    raise ScrapeError("rate_limited", "context.dev is busy (one request at a time on this plan)", retryable=True)
                if status in (401, 403):
                    raise ScrapeError("unauthorized", "context.dev rejected the API key")
                if status == 402:
                    raise ScrapeError("out_of_credits", "the context.dev account has no credits left")
                if status >= 400:
                    raise ScrapeError("upstream_error", f"context.dev answered HTTP {status}", retryable=status >= 500)
                self._cache[(kind, url)] = (time.monotonic(), payload)
                return payload
        raise ScrapeError("rate_limited", "context.dev is busy", retryable=True)

    async def scrape_product(self, url: str, on_start: Optional[Callable[[], None]] = None) -> ProductSnapshot:
        """`on_start` is called when the HTTP request is actually sent (after any queueing)."""
        url = validate_url(url)
        formats = {"product": True}
        if retailer_from_url(url) == "pcpartpicker":
            formats["markdown"] = True  # same credit cost; needed for the vendor table
        return parse_product_response(await self._request(url, formats, "product", on_start), url)

    async def scrape_markdown(self, url: str) -> Optional[str]:
        payload = await self._request(validate_url(url), {"markdown": True}, "markdown")
        block = payload.get("markdown") if isinstance(payload, dict) else None
        return block.get("data") if isinstance(block, dict) and block.get("success") else None
