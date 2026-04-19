"""
Build a SQLite index from the Open Food Facts CSV for fast barcode lookups.

Usage:  python data/openfoodfacts/build_off_index.py

Input:  data/openfoodfacts/en.openfoodfacts.org.products.csv.gz  (4.4M rows)
Output: data/openfoodfacts/off_index.db  (~400-600 MB SQLite)
"""

import csv
import gzip
import sqlite3
import sys
import time
from pathlib import Path

csv.field_size_limit(10 * 1024 * 1024)

_DIR = Path(__file__).resolve().parent
_CSV_GZ = _DIR / "en.openfoodfacts.org.products.csv.gz"
_DB = _DIR / "off_index.db"

# Columns we extract (by name)
_KEEP_COLS = [
    "code",
    "product_name",
    "brands",
    "categories",
    "ingredients_text",
    "image_url",
    "quantity",
    "countries",
    "stores",
    "origins",
    "labels",
    "nutriscore_grade",
    "nova_group",
]


def build():
    if not _CSV_GZ.exists():
        print(f"ERROR: {_CSV_GZ} not found")
        sys.exit(1)

    # Remove old DB
    if _DB.exists():
        _DB.unlink()

    conn = sqlite3.connect(str(_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")

    conn.execute("""
        CREATE TABLE products (
            code TEXT PRIMARY KEY,
            product_name TEXT,
            brands TEXT,
            categories TEXT,
            ingredients_text TEXT,
            image_url TEXT,
            quantity TEXT,
            countries TEXT,
            stores TEXT,
            origins TEXT,
            labels TEXT,
            nutriscore_grade TEXT,
            nova_group TEXT
        )
    """)

    print(f"Reading {_CSV_GZ} ...")
    t0 = time.time()
    inserted = 0
    skipped = 0

    with gzip.open(str(_CSV_GZ), "rt", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader)

        # Map column names to indices
        col_idx = {}
        for col in _KEEP_COLS:
            if col in header:
                col_idx[col] = header.index(col)
            else:
                print(f"  WARNING: column '{col}' not found in CSV")
                col_idx[col] = None

        batch = []
        for row_num, row in enumerate(reader, start=1):
            try:
                code = row[col_idx["code"]].strip() if col_idx["code"] is not None and col_idx["code"] < len(row) else ""
                if not code or not code.isdigit():
                    skipped += 1
                    continue

                vals = []
                for col in _KEEP_COLS:
                    idx = col_idx[col]
                    if idx is not None and idx < len(row):
                        vals.append(row[idx].strip()[:2000])  # cap field length
                    else:
                        vals.append("")

                batch.append(tuple(vals))

                if len(batch) >= 50000:
                    conn.executemany(
                        f"INSERT OR IGNORE INTO products ({', '.join(_KEEP_COLS)}) VALUES ({', '.join('?' * len(_KEEP_COLS))})",
                        batch,
                    )
                    conn.commit()
                    inserted += len(batch)
                    batch.clear()
                    elapsed = time.time() - t0
                    rate = inserted / elapsed
                    print(f"  {inserted:,} inserted ({rate:,.0f}/s) ...", flush=True)

            except Exception:
                skipped += 1
                continue

        # Final batch
        if batch:
            conn.executemany(
                f"INSERT OR IGNORE INTO products ({', '.join(_KEEP_COLS)}) VALUES ({', '.join('?' * len(_KEEP_COLS))})",
                batch,
            )
            conn.commit()
            inserted += len(batch)

    print(f"\nDone: {inserted:,} products indexed, {skipped:,} skipped")
    print(f"Time: {time.time() - t0:.1f}s")
    print(f"DB size: {_DB.stat().st_size / 1024 / 1024:.1f} MB")

    conn.close()
    print(f"Output: {_DB}")


if __name__ == "__main__":
    build()

