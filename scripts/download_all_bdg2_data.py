"""Download all Building Data Genome 2 (BDG2) meters, weather, and metadata files.

Downloads the complete multi-meter, multi-building dataset (~500MB - 1GB total):
- Raw meters: electricity, solar, gas, water, chilled water, hot water, steam, irrigation
- Cleaned meters: electricity_cleaned, solar_cleaned, gas_cleaned, water_cleaned, chilledwater_cleaned, etc.
- Weather data: weather.csv for all sites
- Metadata: metadata.csv
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEST_DIR = ROOT / ".agents" / "real_data"
DEST_DIR.mkdir(parents=True, exist_ok=True)

COMMIT = "9b97ccbe90096aff42ed4fd6493bf7ae692d7118"
BASE_URL = f"https://media.githubusercontent.com/media/buds-lab/building-data-genome-project-2/{COMMIT}"

FILES_TO_DOWNLOAD = [
    # Metadata
    "data/metadata/metadata.csv",
    # Raw Meters
    "data/meters/raw/electricity.csv",
    "data/meters/raw/solar.csv",
    "data/meters/raw/gas.csv",
    "data/meters/raw/water.csv",
    "data/meters/raw/chilledwater.csv",
    "data/meters/raw/hotwater.csv",
    "data/meters/raw/steam.csv",
    "data/meters/raw/irrigation.csv",
    # Cleaned Meters
    "data/meters/cleaned/electricity_cleaned.csv",
    "data/meters/cleaned/solar_cleaned.csv",
    "data/meters/cleaned/gas_cleaned.csv",
    "data/meters/cleaned/water_cleaned.csv",
    "data/meters/cleaned/chilledwater_cleaned.csv",
    "data/meters/cleaned/hotwater_cleaned.csv",
    "data/meters/cleaned/steam_cleaned.csv",
    "data/meters/cleaned/irrigation_cleaned.csv",
    # Weather
    "data/weather/weather.csv",
]


def download_file(remote_path: str, client: httpx.Client) -> tuple[Path, int]:
    filename = Path(remote_path).name
    target_path = DEST_DIR / filename
    url = f"{BASE_URL}/{remote_path}"

    if target_path.exists() and target_path.stat().st_size > 1000:
        size = target_path.stat().st_size
        print(f"  [EXISTS] {filename} ({size / (1024*1024):.2f} MB)")
        return target_path, size

    print(f"  [DOWNLOADING] {filename} from {url}...")
    temp_target = target_path.with_suffix(".download")
    t0 = time.time()
    downloaded_bytes = 0

    with client.stream("GET", url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        total_size = int(response.headers.get("content-length", 0))
        with temp_target.open("wb") as f:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):  # 1MB chunks
                f.write(chunk)
                downloaded_bytes += len(chunk)
                if total_size > 0:
                    pct = (downloaded_bytes / total_size) * 100
                    print(f"\r    -> {downloaded_bytes / (1024*1024):.1f} / {total_size / (1024*1024):.1f} MB ({pct:.1f}%)", end="", flush=True)

    temp_target.replace(target_path)
    t1 = time.time()
    duration = max(0.01, t1 - t0)
    speed_mb = (downloaded_bytes / (1024 * 1024)) / duration
    print(f"\n  [COMPLETED] {filename}: {downloaded_bytes / (1024*1024):.2f} MB in {duration:.1f}s ({speed_mb:.2f} MB/s)")
    return target_path, downloaded_bytes


def main():
    print("=" * 70)
    print("BDG2 COMPLETE DATASET DOWNLOADER")
    print(f"Target Directory: {DEST_DIR}")
    print("=" * 70)

    total_bytes = 0
    start_time = time.time()

    with httpx.Client() as client:
        for rel_path in FILES_TO_DOWNLOAD:
            try:
                _, b = download_file(rel_path, client)
                total_bytes += b
            except Exception as e:
                print(f"  [WARNING] Could not download {rel_path}: {e}")

    total_mb = total_bytes / (1024 * 1024)
    total_time = time.time() - start_time
    print("=" * 70)
    print(f"DOWNLOAD COMPLETE: {total_mb:.2f} MB downloaded/verified in {total_time:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
