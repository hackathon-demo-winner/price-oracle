"""Download REAL monthly CPI-U sub-indexes from the U.S. Bureau of Labor Statistics.

Source: BLS Public Data API v1 (no key, public domain). v1 allows 10 years and 25 series
per query and 25 queries/day, so this makes three queries. Output: data/bls_cpi/<series>.csv
with columns date,value (date = first day of the reference month), plus series.json.

These are category-level price indexes (U.S. city average, not seasonally adjusted). They are
real, but they are NOT product-level prices: an index averages away individual promotions.
"""
from __future__ import annotations

import csv
import json
import time
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "bls_cpi"
API = "https://api.bls.gov/publicAPI/v1/timeseries/data/"

# model category -> (BLS CPI item code, BLS item name)
SERIES: dict[str, tuple[str, str]] = {
    "computer_hardware": ("SEEE01", "Computers, peripherals, and smart home assistants"),
    "software": ("SEEE02", "Computer software and accessories"),
    "phones_other_electronics": ("SEEE04", "Telephone hardware, calculators, and other consumer information items"),
    "televisions": ("SERA01", "Televisions"),
    "audio": ("SERA05", "Audio equipment"),
    "appliances": ("SEHK", "Appliances"),
    "major_appliances": ("SEHK01", "Major appliances"),
    "small_appliances": ("SEHK02", "Other appliances"),
    "furniture": ("SEHJ", "Furniture and bedding"),
    "home_goods": ("SEHL", "Other household equipment and furnishings"),
    "tools": ("SEHM", "Tools, hardware, outdoor equipment and supplies"),
    "personal_care": ("SEGB", "Personal care products"),
    "hair_dental_shaving": ("SEGB01", "Hair, dental, shaving, and miscellaneous personal care products"),
    "beauty": ("SEGB02", "Cosmetics, perfume, bath, nail preparations and implements"),
    "toys": ("SERE01", "Toys"),
    "sporting_goods": ("SERC", "Sporting goods"),
    "photo": ("SERD01", "Photographic equipment and supplies"),
    "apparel": ("SAA", "Apparel"),
    "footwear": ("SEAE", "Footwear"),
}


def series_id(item_code: str) -> str:
    return f"CUUR0000{item_code}"  # CPI-U, not seasonally adjusted, U.S. city average


def query(ids: list[str], start: int, end: int) -> dict:
    body = json.dumps({"seriesid": ids, "startyear": str(start), "endyear": str(end)}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise SystemExit(f"BLS query failed: {payload.get('status')} {payload.get('message')}")
    return payload


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ids = [series_id(code) for code, _ in SERIES.values()]
    points: dict[str, dict[str, float]] = {i: {} for i in ids}
    for start, end in ((1997, 2006), (2007, 2016), (2017, 2026)):
        payload = query(ids, start, end)
        for series in payload["Results"]["series"]:
            for row in series["data"]:
                period = row["period"]
                if not period.startswith("M") or period == "M13":
                    continue
                try:
                    value = float(row["value"])
                except ValueError:
                    continue  # BLS prints "-" for months it did not collect
                points[series["seriesID"]][f"{row['year']}-{period[1:]}-01"] = value
        time.sleep(1)

    manifest = {}
    for category, (code, name) in SERIES.items():
        sid = series_id(code)
        rows = sorted(points[sid].items())
        if not rows:
            print(f"no data returned for {sid} ({name})")
            continue
        with (OUT / f"{sid}.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["date", "value"])
            writer.writerows(rows)
        manifest[category] = {"series_id": sid, "bls_item": name, "months": len(rows), "first": rows[0][0], "last": rows[-1][0]}
        print(f"{category:26s} {sid} {len(rows):4d} months {rows[0][0]} .. {rows[-1][0]}")
    (OUT / "series.json").write_text(
        json.dumps({"source": "U.S. Bureau of Labor Statistics, CPI-U, U.S. city average, NSA (api.bls.gov v1)",
                    "fetched_at": time.strftime("%Y-%m-%d", time.gmtime()), "series": manifest}, indent=1),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
