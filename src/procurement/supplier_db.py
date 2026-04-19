"""
SQLite-based supplier database mapped to Obsidian SUPPLIER_TEMPLATE.md fields.

Provides CRUD operations, CSV/Obsidian import-export, search, scoring,
and analytics for the procurement pipeline.
"""

import csv
import io
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

# (column_name, sqlite_type)
# BOOLEAN fields are stored as INTEGER 0/1
_COLUMNS: list[tuple[str, str]] = [
    # identity
    ("supplier_name", "TEXT"),
    ("product", "TEXT"),
    ("product_category", "TEXT"),
    ("country", "TEXT"),
    # pricing
    ("price_per_unit", "REAL"),
    ("currency", "TEXT"),
    ("moq", "REAL"),
    ("moq_unit", "TEXT"),
    ("price_for_moq", "REAL"),
    ("bulk_price_tier_1_qty", "REAL"),
    ("bulk_price_tier_1_price", "REAL"),
    ("bulk_price_tier_2_qty", "REAL"),
    ("bulk_price_tier_2_price", "REAL"),
    # supply capacity
    ("monthly_capacity", "REAL"),
    ("lead_time_days", "REAL"),
    ("sample_available", "INTEGER"),
    ("sample_cost", "REAL"),
    # scoring inputs
    ("scalability_score", "REAL"),
    ("reliability_score", "REAL"),
    ("price_score", "REAL"),
    ("quantity_score", "REAL"),
    # evidence
    ("scalability_evidence", "TEXT"),
    ("reliability_evidence", "TEXT"),
    # certifications
    ("cert_iso", "INTEGER"),
    ("cert_haccp", "INTEGER"),
    ("cert_reach", "INTEGER"),
    ("cert_ce_mark", "INTEGER"),
    ("cert_cpsc", "INTEGER"),
    ("cert_astm", "INTEGER"),
    ("cert_brc", "INTEGER"),
    ("cert_fssai", "INTEGER"),
    ("cert_bis", "INTEGER"),
    ("cert_other", "TEXT"),
    ("cert_verified_via", "TEXT"),
    ("cert_expiry", "TEXT"),
    # compliance flags
    ("recall_history", "INTEGER"),
    ("recall_details", "TEXT"),
    ("eu_safety_gate_flagged", "INTEGER"),
    ("cpsc_recall_flagged", "INTEGER"),
    ("self_declared_only", "INTEGER"),
    # packaging & label data
    ("label_image_url", "TEXT"),
    ("packaging_text_source", "TEXT"),
    ("ingredients_available", "INTEGER"),
    ("barcode_gtin", "TEXT"),
    ("barcode_verified_gs1", "INTEGER"),
    ("pack_sizes_available", "TEXT"),
    # trade / shipment intelligence
    ("importyeti_verified", "INTEGER"),
    ("importyeti_url", "TEXT"),
    ("ships_to_countries", "TEXT"),
    ("shipment_frequency", "TEXT"),
    ("known_buyers", "TEXT"),
    ("hs_code", "TEXT"),
    ("customs_data_source", "TEXT"),
    # source tracking
    ("source_tier", "INTEGER"),
    ("source_name", "TEXT"),
    ("source_url", "TEXT"),
    ("source_type", "TEXT"),
    ("date_scraped", "TEXT"),
    ("scraped_by", "TEXT"),
    # triangulation status
    ("triangulation_regulatory", "INTEGER"),
    ("triangulation_firstparty", "INTEGER"),
    ("triangulation_trade", "INTEGER"),
    ("triangulation_complete", "INTEGER"),
    # contact info
    ("contact_name", "TEXT"),
    ("contact_email", "TEXT"),
    ("contact_phone", "TEXT"),
    ("contact_whatsapp", "TEXT"),
    ("website", "TEXT"),
    ("response_speed", "TEXT"),
    # data completeness
    ("data_completeness_score", "REAL"),
    ("imputed_fields", "TEXT"),
    # final scores
    ("final_score", "REAL"),
    ("tier_output", "TEXT"),
    ("action", "TEXT"),
    # notes
    ("red_flags", "TEXT"),
    ("positive_signals", "TEXT"),
    ("follow_up_required", "TEXT"),
    ("notes", "TEXT"),
    # timestamps (auto-managed)
    ("created_at", "TEXT"),
    ("updated_at", "TEXT"),
]

_COLUMN_NAMES: list[str] = [c[0] for c in _COLUMNS]

_BOOLEAN_COLUMNS: set[str] = {
    name for name, typ in _COLUMNS if typ == "INTEGER" and name != "source_tier"
}

_NUMERIC_COLUMNS: set[str] = {
    name
    for name, typ in _COLUMNS
    if typ == "REAL"
} | {"source_tier"}

# The four key fields used for data-completeness scoring
_COMPLETENESS_KEYS = ("price_per_unit", "moq", "scalability_score", "reliability_score")


class SupplierDatabase:
    """SQLite-backed supplier store aligned with the Obsidian template."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path("D:/SAI/data/suppliers.db"))
        self._db_path = db_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_table()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_table(self) -> None:
        col_defs = ",\n    ".join(
            f"{name} {typ}" for name, typ in _COLUMNS
        )
        ddl = f"""
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {col_defs}
        )
        """
        self._conn.execute(ddl)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _coerce(key: str, value):
        """Coerce a value to the appropriate Python type for its column."""
        if value is None or value == "" or value == "null":
            return None
        if key in _BOOLEAN_COLUMNS:
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, str):
                return 1 if value.strip().lower() in ("true", "1", "yes") else 0
            return int(bool(value))
        if key in _NUMERIC_COLUMNS:
            try:
                return float(value)
            except (ValueError, TypeError):
                return None
        return str(value) if value is not None else None

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        # Convert boolean integer columns back to bool for convenience
        for col in _BOOLEAN_COLUMNS:
            if col in d and d[col] is not None:
                d[col] = bool(d[col])
        return d

    def _filter_keys(self, data: dict) -> dict:
        """Keep only keys that map to real columns and coerce values."""
        return {
            k: self._coerce(k, v)
            for k, v in data.items()
            if k in _COLUMN_NAMES
        }

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_supplier(self, data: dict) -> int:
        """Insert a supplier row and return its row id."""
        clean = self._filter_keys(data)
        now = self._now_iso()
        clean.setdefault("created_at", now)
        clean["updated_at"] = now

        # Auto-calculate data completeness if not provided
        if "data_completeness_score" not in data or data.get("data_completeness_score") is None:
            clean["data_completeness_score"] = self.calculate_data_completeness(clean)

        cols = list(clean.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_str = ", ".join(cols)
        values = [clean[c] for c in cols]

        cur = self._conn.execute(
            f"INSERT INTO suppliers ({col_str}) VALUES ({placeholders})", values
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update_supplier(self, supplier_id: int, data: dict) -> None:
        """Update one or more fields for a given supplier."""
        clean = self._filter_keys(data)
        clean["updated_at"] = self._now_iso()

        # Recalculate completeness if any key field changed
        if any(k in clean for k in _COMPLETENESS_KEYS):
            existing = self.get_supplier(supplier_id)
            if existing:
                merged = {**existing, **clean}
                clean["data_completeness_score"] = self.calculate_data_completeness(merged)

        set_clause = ", ".join(f"{k} = ?" for k in clean)
        values = list(clean.values()) + [supplier_id]
        self._conn.execute(
            f"UPDATE suppliers SET {set_clause} WHERE id = ?", values
        )
        self._conn.commit()

    def get_supplier(self, supplier_id: int) -> Optional[dict]:
        """Return a single supplier as a dict, or None."""
        cur = self._conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,))
        row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def get_all_suppliers(self) -> list[dict]:
        """Return every supplier."""
        cur = self._conn.execute("SELECT * FROM suppliers ORDER BY id")
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def search_suppliers(self, query: str) -> list[dict]:
        """Search suppliers by name, product, or country (case-insensitive LIKE)."""
        pattern = f"%{query}%"
        cur = self._conn.execute(
            """
            SELECT * FROM suppliers
            WHERE supplier_name LIKE ? COLLATE NOCASE
               OR product LIKE ? COLLATE NOCASE
               OR country LIKE ? COLLATE NOCASE
            ORDER BY supplier_name
            """,
            (pattern, pattern, pattern),
        )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def delete_supplier(self, supplier_id: int) -> None:
        """Delete a supplier by id."""
        self._conn.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))
        self._conn.commit()

    def clear_discovered(self) -> int:
        """Delete all web-discovered suppliers (keeps manual/imported entries).
        Returns count deleted."""
        cur = self._conn.execute(
            "DELETE FROM suppliers WHERE scraped_by = 'Agnes Auto-Discovery'"
        )
        self._conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # Import / Export
    # ------------------------------------------------------------------

    def import_from_csv(self, csv_path: str) -> int:
        """Import suppliers from an Obsidian Dataview CSV export. Returns count imported."""
        count = 0
        with open(csv_path, "r", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                # Strip whitespace from keys (Dataview sometimes pads them)
                cleaned = {k.strip(): v.strip() if isinstance(v, str) else v for k, v in row.items()}
                self.add_supplier(cleaned)
                count += 1
        return count

    def import_from_obsidian_md(self, md_content: str) -> dict:
        """Parse a single Obsidian note's YAML frontmatter and return the parsed dict.

        Does NOT insert into the database -- the caller can pass the result
        to ``add_supplier`` if desired.
        """
        parts = md_content.split("---")
        if len(parts) < 3:
            raise ValueError("No valid YAML frontmatter found between --- markers")

        raw_yaml = parts[1]
        # Strip comment lines that confuse the YAML parser
        lines = []
        for line in raw_yaml.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            lines.append(line)
        cleaned_yaml = "\n".join(lines)

        parsed: dict = yaml.safe_load(cleaned_yaml) or {}

        # Coerce types to match our schema
        result = {}
        for key, value in parsed.items():
            if key in _COLUMN_NAMES:
                result[key] = self._coerce(key, value)
            else:
                result[key] = value

        # Remove template placeholders like {{supplier_name}}
        for k, v in list(result.items()):
            if isinstance(v, str) and v.startswith("{{") and v.endswith("}}"):
                result[k] = None

        return result

    def export_to_csv(self, output_path: str) -> str:
        """Export all suppliers to CSV. Returns the output path."""
        suppliers = self.get_all_suppliers()
        if not suppliers:
            # Write header-only file
            with open(output_path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["id"] + _COLUMN_NAMES)
            return output_path

        fieldnames = list(suppliers[0].keys())
        with open(output_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for s in suppliers:
                writer.writerow(s)
        return output_path

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return summary statistics across all suppliers."""
        total = self._conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]

        # By tier
        tier_rows = self._conn.execute(
            "SELECT tier_output, COUNT(*) as cnt FROM suppliers GROUP BY tier_output"
        ).fetchall()
        by_tier = {(r["tier_output"] or "Unclassified"): r["cnt"] for r in tier_rows}

        # By country
        country_rows = self._conn.execute(
            "SELECT country, COUNT(*) as cnt FROM suppliers WHERE country IS NOT NULL GROUP BY country ORDER BY cnt DESC"
        ).fetchall()
        by_country = {r["country"]: r["cnt"] for r in country_rows}

        # Triangulated
        triangulated = self._conn.execute(
            "SELECT COUNT(*) FROM suppliers WHERE triangulation_complete = 1"
        ).fetchone()[0]

        # Avg data completeness
        avg_row = self._conn.execute(
            "SELECT AVG(data_completeness_score) FROM suppliers WHERE data_completeness_score IS NOT NULL"
        ).fetchone()
        avg_completeness = round(avg_row[0], 2) if avg_row[0] is not None else 0.0

        return {
            "total_suppliers": total,
            "by_tier": by_tier,
            "by_country": by_country,
            "triangulated_count": triangulated,
            "avg_data_completeness": avg_completeness,
        }

    def get_red_flag_suppliers(self) -> list[dict]:
        """Return suppliers that have any red flags set."""
        cur = self._conn.execute(
            """
            SELECT * FROM suppliers
            WHERE (red_flags IS NOT NULL AND red_flags != '')
               OR recall_history = 1
               OR eu_safety_gate_flagged = 1
               OR cpsc_recall_flagged = 1
               OR self_declared_only = 1
            ORDER BY supplier_name
            """
        )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def get_by_tier(self, tier: str) -> list[dict]:
        """Filter suppliers by tier_output (case-insensitive LIKE)."""
        cur = self._conn.execute(
            "SELECT * FROM suppliers WHERE tier_output LIKE ? COLLATE NOCASE ORDER BY supplier_name",
            (f"%{tier}%",),
        )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def get_untriangulated(self) -> list[dict]:
        """Return suppliers where triangulation_complete is false or null."""
        cur = self._conn.execute(
            "SELECT * FROM suppliers WHERE triangulation_complete IS NULL OR triangulation_complete = 0 ORDER BY supplier_name"
        )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_data_completeness(data: dict) -> float:
        """Compute data_completeness_score from the four key fields.

        Scoring: 4/4 = 10, 3/4 = 7.5, 2/4 = 5, 1/4 = 2.5, 0/4 = 0.
        """
        filled = sum(
            1
            for key in _COMPLETENESS_KEYS
            if data.get(key) is not None and data.get(key) != "" and data.get(key) != 0
        )
        return filled * 2.5

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
