"""
Internal Procurement Folder Watcher — Zero LLM
Monitors the /internal_procurement folder for new/changed files.
Supports CSV, Excel (.xlsx), JSON, and PDF formats.
Auto-ingests on file change using watchdog.
"""

import os
import json
import csv
import time
import hashlib
import threading
from pathlib import Path
from typing import Callable, Optional
from datetime import datetime

WATCH_DIR = Path(__file__).parent.parent / "internal_procurement"
WATCH_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".pdf", ".txt"}


# ── File Readers ──────────────────────────────────────────────────────────────

def read_csv_file(path: str) -> list[dict]:
    """Read procurement records from CSV."""
    records = []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(_normalize_procurement_row(dict(row)))
    except UnicodeDecodeError:
        with open(path, "r", encoding="latin-1") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(_normalize_procurement_row(dict(row)))
    except Exception as e:
        records.append({"_error": str(e), "_file": path})
    return records


def read_excel_file(path: str) -> list[dict]:
    """Read procurement records from Excel."""
    try:
        import pandas as pd
        df = pd.read_excel(path, engine="openpyxl")
        records = []
        for _, row in df.iterrows():
            records.append(_normalize_procurement_row(row.to_dict()))
        return records
    except ImportError:
        return [{"_error": "openpyxl not installed. Run: pip install openpyxl", "_file": path}]
    except Exception as e:
        return [{"_error": str(e), "_file": path}]


def read_json_file(path: str) -> list[dict]:
    """Read procurement records from JSON."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [_normalize_procurement_row(r) for r in data]
        elif isinstance(data, dict):
            if "records" in data:
                return [_normalize_procurement_row(r) for r in data["records"]]
            if "components" in data:
                return [_normalize_component_record(c) for c in data["components"]]
            return [_normalize_procurement_row(data)]
    except Exception as e:
        return [{"_error": str(e), "_file": path}]
    return []


def read_txt_file(path: str) -> list[dict]:
    """Read loose text procurement notes — extract what we can."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        return [{"_raw_text": text, "_file": path, "_format": "text"}]
    except Exception as e:
        return [{"_error": str(e), "_file": path}]


def read_pdf_file(path: str) -> list[dict]:
    """Read PDF procurement document."""
    from .pdf_harvester import extract_from_pdf
    result = extract_from_pdf(path)
    return [{"_raw_text": result.get("raw_text", ""), "_extracted": result.get("extracted_fields", {}), "_file": path, "_format": "pdf"}]


def ingest_file(path: str) -> list[dict]:
    """
    Auto-detect format and ingest a procurement file.
    Returns normalized list of records.
    """
    ext = Path(path).suffix.lower()
    if ext == ".csv":
        return read_csv_file(path)
    elif ext in (".xlsx", ".xls"):
        return read_excel_file(path)
    elif ext == ".json":
        return read_json_file(path)
    elif ext == ".pdf":
        return read_pdf_file(path)
    elif ext == ".txt":
        return read_txt_file(path)
    else:
        return [{"_error": f"Unsupported format: {ext}", "_file": path}]


def ingest_all_files(directory: str = None) -> dict:
    """
    Ingest all files in the procurement directory.
    Returns {filename: [records]}.
    """
    watch_dir = Path(directory) if directory else WATCH_DIR
    all_records = {}

    for filepath in watch_dir.rglob("*"):
        if filepath.is_file() and filepath.suffix.lower() in SUPPORTED_EXTENSIONS:
            if filepath.name.startswith((".", "_", "~")):
                continue
            records = ingest_file(str(filepath))
            all_records[filepath.name] = records

    return all_records


# ── Normalization ─────────────────────────────────────────────────────────────

FIELD_ALIASES = {
    # Component identification
    "component": "component_name",
    "component id": "component_id",
    "part": "component_name",
    "part number": "part_number",
    "part no": "part_number",
    "pn": "part_number",
    "item": "component_name",
    "description": "description",
    "mfr": "manufacturer",
    "brand": "manufacturer",
    # Pricing
    "price": "unit_price",
    "unit price": "unit_price",
    "cost": "unit_price",
    "unit cost": "unit_price",
    "total": "total_cost",
    "total cost": "total_cost",
    "total amount": "total_cost",
    # Quantity
    "qty": "quantity",
    "quantity": "quantity",
    "order qty": "quantity",
    # Supplier
    "vendor": "supplier",
    "distributor": "supplier",
    "source": "supplier",
    # Lead time
    "lead time": "lead_time",
    "delivery time": "lead_time",
    "weeks": "lead_time_weeks",
    # Quality
    "quality": "quality_status",
    "status": "delivery_status",
    "notes": "notes",
    "remarks": "notes",
    "comment": "notes",
    # Date
    "date": "order_date",
    "order date": "order_date",
    "po date": "order_date",
    "po": "po_number",
    "po number": "po_number",
    "purchase order": "po_number",
}


def _normalize_procurement_row(row: dict) -> dict:
    """Normalize column names to canonical field names."""
    normalized = {}
    for key, value in row.items():
        if not key:
            continue
        clean_key = str(key).strip().lower()
        canonical = FIELD_ALIASES.get(clean_key, clean_key.replace(" ", "_"))
        val = str(value).strip() if value is not None else ""
        if val and val not in ("nan", "None", ""):
            normalized[canonical] = val
    normalized["_ingested_at"] = datetime.utcnow().isoformat()
    return normalized


def _normalize_component_record(comp: dict) -> dict:
    """Normalize a component dict from components_db format."""
    return {
        "component_id": comp.get("id", ""),
        "component_name": comp.get("name", ""),
        "manufacturer": comp.get("manufacturer", ""),
        "type": comp.get("type", ""),
        "unit_price": comp.get("pricing", {}).get("unit_price_usd"),
        "lead_time_weeks": comp.get("availability", {}).get("lead_time_weeks"),
        "stock_qty": comp.get("availability", {}).get("stock_qty"),
        "_format": "component_db",
        "_ingested_at": datetime.utcnow().isoformat()
    }


# ── File Watcher ──────────────────────────────────────────────────────────────

class ProcurementWatcher:
    """
    Watches the internal_procurement folder for file changes.
    Calls callback(filepath, records) when a file is added/modified.
    """

    def __init__(self, watch_dir: str = None, callback: Callable = None):
        self.watch_dir = Path(watch_dir) if watch_dir else WATCH_DIR
        self.callback = callback or self._default_callback
        self._observer = None
        self._file_hashes = {}

    def start(self, block: bool = False):
        """Start watching. block=True waits forever."""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            watcher = self

            class Handler(FileSystemEventHandler):
                def on_created(self, event):
                    if not event.is_directory:
                        watcher._handle_file(event.src_path)
                def on_modified(self, event):
                    if not event.is_directory:
                        watcher._handle_file(event.src_path)

            self._observer = Observer()
            self._observer.schedule(Handler(), str(self.watch_dir), recursive=True)
            self._observer.start()
            print(f"[Watcher] Watching: {self.watch_dir}")

            if block:
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    self.stop()
        except ImportError:
            print("[Watcher] watchdog not installed — using polling mode")
            self._poll_mode(block)

    def stop(self):
        if self._observer:
            self._observer.stop()
            self._observer.join()

    def _handle_file(self, filepath: str):
        ext = Path(filepath).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return
        # Check if file actually changed (avoid double triggers)
        try:
            content_hash = hashlib.md5(Path(filepath).read_bytes()).hexdigest()
            if self._file_hashes.get(filepath) == content_hash:
                return
            self._file_hashes[filepath] = content_hash
        except Exception:
            return

        time.sleep(0.5)  # Wait for file write to complete
        records = ingest_file(filepath)
        self.callback(filepath, records)

    def _poll_mode(self, block: bool):
        """Simple polling fallback if watchdog not available."""
        def poll():
            print("[Watcher] Polling mode (2s interval)")
            while True:
                for filepath in self.watch_dir.rglob("*"):
                    if filepath.is_file() and filepath.suffix.lower() in SUPPORTED_EXTENSIONS:
                        try:
                            content_hash = hashlib.md5(filepath.read_bytes()).hexdigest()
                            fp_str = str(filepath)
                            if self._file_hashes.get(fp_str) != content_hash:
                                self._file_hashes[fp_str] = content_hash
                                records = ingest_file(fp_str)
                                self.callback(fp_str, records)
                        except Exception:
                            pass
                time.sleep(2)

        if block:
            poll()
        else:
            t = threading.Thread(target=poll, daemon=True)
            t.start()

    def _default_callback(self, filepath: str, records: list):
        print(f"[Watcher] New/updated file: {Path(filepath).name} — {len(records)} records ingested")


# ── Sample data generator ─────────────────────────────────────────────────────

def create_sample_files():
    """Create sample procurement files in the internal_procurement folder."""
    # Sample CSV
    sample_csv = WATCH_DIR / "procurement_q1_2026.csv"
    if not sample_csv.exists():
        with open(sample_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["PO Number", "Date", "Component", "Part Number", "Manufacturer",
                             "Quantity", "Unit Price", "Total Cost", "Supplier", "Lead Time", "Status", "Notes"])
            rows = [
                ["PO-2026-001", "2026-01-10", "M5 Hex Bolt 30mm SS", "DIN912-M5x30-A2",
                 "Wurth", 5000, 0.12, 600.0, "Wurth Group", "3 weeks", "Delivered", "Stainless A2"],
                ["PO-2026-002", "2026-01-15", "M5 Socket Head Cap 20mm", "DIN912-M5x20-88",
                 "Bossard", 10000, 0.08, 800.0, "Bossard AG", "4 weeks", "Pending", "Grade 8.8 Black Oxide"],
                ["PO-2026-003", "2026-02-01", "M6 Hex Bolt 40mm", "ISO4017-M6x40-88",
                 "Acument", 3000, 0.15, 450.0, "Stanley Engineered Fastening", "5 weeks", "Delivered", ""],
                ["PO-2026-004", "2026-02-14", "M5 Hex Nut", "DIN934-M5-A2",
                 "Fastenal", 10000, 0.04, 400.0, "Fastenal", "2 weeks", "Delivered", "A2 Stainless"],
                ["PO-2026-005", "2026-03-01", "M5 Washer DIN125", "DIN125-M5-A2",
                 "RS Components", 20000, 0.02, 400.0, "RS Components", "1 week", "Delivered", ""],
                ["PO-2026-006", "2026-03-10", "M5 Bolt 25mm Grade 10.9", "ISO4762-M5x25-109",
                 "Textron", 8000, 0.09, 720.0, "Textron Fastening", "6 weeks", "Quality Hold",
                 "Batch 2026B had surface defects — 2% rejection"],
            ]
            writer.writerows(rows)
        print(f"Created: {sample_csv}")

    # Sample JSON
    sample_json = WATCH_DIR / "approved_suppliers.json"
    if not sample_json.exists():
        data = {
            "updated": "2026-04-01",
            "approved_suppliers": [
                {
                    "supplier_id": "SUP-001",
                    "name": "Wurth Group",
                    "website": "https://www.wurth.com",
                    "region": "EU/Global",
                    "categories": ["fasteners", "screws", "bolts", "nuts"],
                    "certifications": ["ISO 9001", "ISO 14001", "IATF 16949"],
                    "lead_time_typical_weeks": 3,
                    "min_order": 100,
                    "quality_rating": 4.8,
                    "notes": "Preferred supplier for automotive fasteners"
                },
                {
                    "supplier_id": "SUP-002",
                    "name": "Bossard AG",
                    "website": "https://www.bossard.com",
                    "region": "EU",
                    "categories": ["fasteners", "bolts", "screws", "nuts", "washers"],
                    "certifications": ["ISO 9001", "AS9100"],
                    "lead_time_typical_weeks": 4,
                    "min_order": 500,
                    "quality_rating": 4.7,
                    "notes": "Swiss-quality fastener specialist"
                },
                {
                    "supplier_id": "SUP-003",
                    "name": "Fastenal",
                    "website": "https://www.fastenal.com",
                    "region": "US",
                    "categories": ["fasteners", "tools", "safety", "industrial supplies"],
                    "certifications": ["ISO 9001"],
                    "lead_time_typical_weeks": 2,
                    "min_order": 25,
                    "quality_rating": 4.5,
                    "notes": "Good for quick US delivery"
                },
            ]
        }
        with open(sample_json, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Created: {sample_json}")

    return [str(sample_csv), str(sample_json)]


if __name__ == "__main__":
    print("Creating sample internal procurement files...")
    files = create_sample_files()
    print("\nIngesting all files...")
    all_records = ingest_all_files()
    for filename, records in all_records.items():
        print(f"  {filename}: {len(records)} records")
    print("\nStarting watcher (Ctrl+C to stop)...")
    watcher = ProcurementWatcher()
    watcher.start(block=True)
