"""
Internal Availability Checker — Stage 3 of the Procurement Pipeline.

Checks internal procurement records before triggering the 11-layer
external supply intelligence gathering.

Outcome states:
  in_stock     — material available; fulfil from internal inventory
  partial      — some stock or quality hold; partial external sourcing needed
  out_of_stock — no current stock; full external sourcing required
  no_records   — material never purchased; go external
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Optional

INTERNAL_DIR = Path(__file__).parent.parent.parent / "internal_procurement"

# Maps our canonical field names to the many aliases found in real CSVs
_FIELD_ALIASES: dict[str, list[str]] = {
    "component":  ["component", "material", "part", "item", "product",
                   "description", "part_name", "material_name"],
    "part_number":["part_number", "part number", "pn", "p/n", "sku",
                   "material_id", "part no", "item no", "item_no"],
    "quantity":   ["quantity", "qty", "units", "amount", "order_qty",
                   "ordered_qty", "quantity_ordered"],
    "unit_price": ["unit_price", "unit price", "price", "cost", "rate",
                   "unit_cost", "price_per_unit"],
    "supplier":   ["supplier", "vendor", "manufacturer", "supplier_name",
                   "vendor_name"],
    "status":     ["status", "delivery_status", "order_status",
                   "fulfillment_status", "po_status"],
    "date":       ["date", "order_date", "po_date", "purchase_date",
                   "created_date"],
    "lead_time":  ["lead_time", "lead time", "lead_time_weeks",
                   "lead_time_days", "delivery_days", "delivery_weeks"],
    "po_number":  ["po_number", "po number", "po#", "purchase_order",
                   "order_number"],
}

_IN_STOCK_INDICATORS = {"delivered", "complete", "received", "in stock",
                        "available", "fulfilled", "closed"}
_HOLD_INDICATORS     = {"hold", "quality", "reject", "inspection", "pending qa"}


def _to_float(val: str) -> Optional[float]:
    if val is None:
        return None
    try:
        cleaned = re.sub(r"[^\d.]", "", str(val))
        return float(cleaned) if cleaned else None
    except Exception:
        return None


class InternalChecker:
    """
    Queries internal procurement records (CSV/Excel/JSON) to determine
    whether a material is already available in-house.

    In CPG mode (cpg_db provided), also queries the SQLite database for
    supplier relationships and BOM usage data.
    """

    def __init__(self, internal_dir: Path = None, cpg_db=None):
        self.internal_dir = Path(internal_dir) if internal_dir else INTERNAL_DIR
        self._records: Optional[list[dict]] = None
        self._suppliers: dict[str, dict] = {}
        self._cpg_db = cpg_db

    # ── data loading ──────────────────────────────────────────────────────────

    def _load(self):
        if self._records is not None:
            return
        self._records = []
        if not self.internal_dir.exists():
            return

        # Procurement CSVs
        for path in self.internal_dir.glob("*.csv"):
            self._ingest_csv(path)

        # Try Excel files if pandas available
        for path in self.internal_dir.glob("*.xlsx"):
            self._ingest_excel(path)

        # Approved suppliers JSON
        for name in ("approved_suppliers.json", "suppliers.json"):
            sup_path = self.internal_dir / name
            if sup_path.exists():
                with open(sup_path, encoding="utf-8") as f:
                    raw = json.load(f)
                items = raw if isinstance(raw, list) else raw.get("suppliers", [])
                for s in items:
                    sid = s.get("supplier_id") or s.get("id") or s.get("name", "")
                    self._suppliers[sid] = s

    def _ingest_csv(self, path: Path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rec = self._normalise(row)
                    if rec:
                        self._records.append(rec)
        except Exception:
            pass

    def _ingest_excel(self, path: Path):
        try:
            import pandas as pd
            df = pd.read_excel(path)
            for _, row in df.iterrows():
                rec = self._normalise(dict(row))
                if rec:
                    self._records.append(rec)
        except Exception:
            pass

    def _normalise(self, row: dict) -> Optional[dict]:
        row_lc = {str(k).lower().strip(): str(v).strip() for k, v in row.items()
                  if v is not None and str(v).strip() not in ("", "nan", "None")}
        out: dict = {}
        for field, aliases in _FIELD_ALIASES.items():
            for alias in aliases:
                if alias in row_lc:
                    out[field] = row_lc[alias]
                    break
        return out if out.get("component") else None

    # ── matching ──────────────────────────────────────────────────────────────

    def _matches(self, record: dict, name: str, material_id: Optional[str]) -> bool:
        nl = name.lower()
        comp = record.get("component", "").lower()
        pn   = record.get("part_number", "").lower()

        if material_id and material_id.lower() in pn:
            return True
        if nl in comp or comp in nl:
            return True
        # Word-level: at least 2 key words in common
        words = [w for w in nl.split() if len(w) > 2]
        if words and sum(1 for w in words if w in comp) >= min(2, len(words)):
            return True
        return False

    # ── public API ────────────────────────────────────────────────────────────

    def check(self, material_name: str, material_id: str = None) -> dict:
        """
        Check internal availability for a material.

        Returns
        -------
        dict with keys:
          status          : "in_stock" | "partial" | "out_of_stock" | "no_records"
          message         : human-readable summary
          records         : last 5 matching procurement records
          last_supplier   : name of most recent supplier
          last_price_usd  : most recent unit price
          avg_price_usd   : average unit price across all records
          total_ordered   : cumulative quantity ordered
          record_count    : number of matching records found
          quality_hold    : True if any recent record has a quality hold
          approved_suppliers : relevant pre-approved suppliers
        """
        self._load()

        matched = [
            r for r in self._records
            if self._matches(r, material_name, material_id)
        ]

        if not matched:
            result = {
                "status": "no_records",
                "message": f"No internal procurement history for '{material_name}'",
                "records": [],
                "last_supplier": None,
                "last_price_usd": None,
                "avg_price_usd": None,
                "total_ordered": 0,
                "record_count": 0,
                "quality_hold": False,
                "approved_suppliers": self._relevant_suppliers(material_name),
            }
            self._enrich_cpg(result, material_name)
            return result

        # Aggregate prices
        prices = [p for p in (_to_float(r.get("unit_price")) for r in matched)
                  if p is not None and p > 0]

        # Aggregate quantities
        total_qty = sum(
            q for q in (_to_float(r.get("quantity")) for r in matched)
            if q is not None
        )

        # Determine status from last 3 records
        recent_statuses = [r.get("status", "").lower() for r in matched[-3:]]
        quality_hold = any(
            any(ind in s for ind in _HOLD_INDICATORS)
            for s in recent_statuses
        )
        any_delivered = any(
            any(ind in s for ind in _IN_STOCK_INDICATORS)
            for s in recent_statuses
        )

        if quality_hold:
            status = "partial"
        elif any_delivered:
            status = "in_stock"
        elif matched:
            status = "out_of_stock"
        else:
            status = "no_records"

        last = matched[-1]
        result = {
            "status": status,
            "message": f"Found {len(matched)} internal record(s) for '{material_name}'",
            "records": matched[-5:],
            "last_supplier": last.get("supplier"),
            "last_po_number": last.get("po_number"),
            "last_price_usd": prices[-1] if prices else None,
            "avg_price_usd": round(sum(prices) / len(prices), 4) if prices else None,
            "total_ordered": int(total_qty),
            "record_count": len(matched),
            "quality_hold": quality_hold,
            "approved_suppliers": self._relevant_suppliers(material_name),
        }
        # Enrich with CPG data if available
        self._enrich_cpg(result, material_name)
        return result

    def _enrich_cpg(self, result: dict, material_name: str):
        """Add CPG-specific data from SQLite if available."""
        if not self._cpg_db:
            return
        hits = self._cpg_db.search_ingredients(material_name, limit=1)
        if not hits:
            return

        hit = hits[0]
        product_ids = hit.get("product_ids", [])

        # Get all suppliers offering this ingredient
        cpg_suppliers = []
        seen_sup = set()
        for pid in product_ids[:10]:
            for s in self._cpg_db.get_suppliers_for_product(pid):
                if s["Name"] not in seen_sup:
                    cpg_suppliers.append({"name": s["Name"], "source": "cpg_database"})
                    seen_sup.add(s["Name"])

        # Get BOM usage
        demand = self._cpg_db.get_demand_map().get(hit["ingredient_name"], [])
        bom_usage = [
            {"company": d["company"], "finished_good": d["finished_good"]}
            for d in demand[:20]
        ]

        result["cpg_suppliers"] = cpg_suppliers
        result["cpg_bom_usage"] = bom_usage
        result["cpg_ingredient_name"] = hit["ingredient_name"]
        result["cpg_variant_count"] = len(product_ids)

        # Upgrade status if we have CPG supplier data
        if cpg_suppliers and result["status"] == "no_records":
            result["status"] = "in_stock"
            result["message"] = (
                f"Found {len(cpg_suppliers)} supplier(s) for '{hit['ingredient_name']}' "
                f"in CPG database, used in {len(bom_usage)} product(s)"
            )

    def _relevant_suppliers(self, material_name: str) -> list[dict]:
        """Return pre-approved suppliers relevant to this material."""
        if not self._suppliers:
            return []
        nl = material_name.lower()
        results = []
        for s in self._suppliers.values():
            cats = " ".join(str(v) for v in s.get("categories", [])).lower()
            name = s.get("name", "").lower()
            notes = s.get("notes", "").lower()
            if any(word in cats or word in name or word in notes
                   for word in nl.split()[:3]):
                results.append({
                    "name": s.get("name"),
                    "region": s.get("region"),
                    "quality_rating": s.get("quality_rating"),
                    "lead_time": s.get("lead_time"),
                    "certifications": s.get("certifications", []),
                    "website": s.get("website", ""),
                })
        return results[:5]
