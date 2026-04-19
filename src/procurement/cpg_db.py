"""
CPG (Consumer Packaged Goods) Database Access Layer.
Wraps db.sqlite — the Spherecast/hackathon dataset of supplement
companies, products, BOMs, and suppliers.
"""

import re
import sqlite3
from pathlib import Path
from functools import lru_cache

_DEFAULT_DB = Path(__file__).resolve().parents[2] / "db.sqlite"

# Regex to strip RM-C{n}- prefix and -{8-hex} suffix from SKUs
_SKU_RE = re.compile(r"^RM-C\d+-(.+)-[a-f0-9]{6,10}$")


def _canon(sku: str) -> str:
    """Extract human-readable ingredient name from SKU."""
    m = _SKU_RE.match(sku)
    return m.group(1).replace("-", " ") if m else sku


class CpgDatabase:
    """Read-only helper around db.sqlite."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB
        if not self.db_path.exists():
            raise FileNotFoundError(f"CPG database not found: {self.db_path}")
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    # ------------------------------------------------------------------
    # Low-level queries
    # ------------------------------------------------------------------

    def _q(self, sql: str, params: tuple = ()) -> list[dict]:
        # Reconnect if called from a different thread than the one that created _conn
        try:
            cur = self._conn.execute(sql, params)
        except sqlite3.ProgrammingError:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._ingredient_index.cache_clear()
            self.bom_ingredient_sets.cache_clear()
            self.ingredient_to_boms.cache_clear()
            cur = self._conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Companies
    # ------------------------------------------------------------------

    def get_companies(self) -> list[dict]:
        return self._q("SELECT Id, Name FROM Company ORDER BY Name")

    def get_company(self, company_id: int) -> dict | None:
        rows = self._q("SELECT Id, Name FROM Company WHERE Id = ?", (company_id,))
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    def get_raw_materials(self) -> list[dict]:
        rows = self._q(
            "SELECT Id, SKU, CompanyId, Type FROM Product WHERE Type = 'raw-material' ORDER BY SKU"
        )
        for r in rows:
            r["ingredient_name"] = _canon(r["SKU"])
        return rows

    def get_finished_goods(self) -> list[dict]:
        return self._q(
            """SELECT p.Id, p.SKU, p.CompanyId, p.Type, c.Name AS company_name
               FROM Product p JOIN Company c ON c.Id = p.CompanyId
               WHERE p.Type = 'finished-good'
               ORDER BY c.Name, p.SKU"""
        )

    # ------------------------------------------------------------------
    # BOM
    # ------------------------------------------------------------------

    def get_bom(self, finished_good_id: int) -> list[dict]:
        """Return ingredients for a finished good."""
        rows = self._q(
            """SELECT bc.BOMId, p.Id AS product_id, p.SKU, p.Type
               FROM BOM b
               JOIN BOM_Component bc ON bc.BOMId = b.Id
               JOIN Product p ON p.Id = bc.ConsumedProductId
               WHERE b.ProducedProductId = ?
               ORDER BY p.SKU""",
            (finished_good_id,),
        )
        for r in rows:
            r["ingredient_name"] = _canon(r["SKU"])
        return rows

    def get_bom_by_sku(self, sku: str) -> list[dict]:
        rows = self._q("SELECT Id FROM Product WHERE SKU = ?", (sku,))
        if not rows:
            return []
        return self.get_bom(rows[0]["Id"])

    # ------------------------------------------------------------------
    # Suppliers
    # ------------------------------------------------------------------

    def get_suppliers(self) -> list[dict]:
        return self._q("SELECT Id, Name FROM Supplier ORDER BY Name")

    def get_suppliers_for_product(self, product_id: int) -> list[dict]:
        return self._q(
            """SELECT s.Id, s.Name
               FROM Supplier_Product sp
               JOIN Supplier s ON s.Id = sp.SupplierId
               WHERE sp.ProductId = ?
               ORDER BY s.Name""",
            (product_id,),
        )

    def get_supplier_catalog(self) -> dict[str, list[str]]:
        """supplier_name -> [ingredient_name, ...]"""
        rows = self._q(
            """SELECT s.Name AS supplier, p.SKU
               FROM Supplier_Product sp
               JOIN Supplier s ON s.Id = sp.SupplierId
               JOIN Product p ON p.Id = sp.ProductId
               ORDER BY s.Name"""
        )
        cat: dict[str, list[str]] = {}
        for r in rows:
            cat.setdefault(r["supplier"], []).append(_canon(r["SKU"]))
        return cat

    # ------------------------------------------------------------------
    # Demand / usage
    # ------------------------------------------------------------------

    def get_demand_map(self) -> dict[str, list[dict]]:
        """ingredient_name -> [{company, bom_id, finished_good_sku}]"""
        rows = self._q(
            """SELECT rm.SKU AS rm_sku, fg.SKU AS fg_sku,
                      c.Name AS company, b.Id AS bom_id
               FROM BOM_Component bc
               JOIN BOM b ON b.Id = bc.BOMId
               JOIN Product fg ON fg.Id = b.ProducedProductId
               JOIN Product rm ON rm.Id = bc.ConsumedProductId
               JOIN Company c ON c.Id = fg.CompanyId
               ORDER BY rm.SKU"""
        )
        dm: dict[str, list[dict]] = {}
        for r in rows:
            name = _canon(r["rm_sku"])
            dm.setdefault(name, []).append(
                {"company": r["company"], "bom_id": r["bom_id"], "finished_good": r["fg_sku"]}
            )
        return dm

    # ------------------------------------------------------------------
    # Search / autocomplete
    # ------------------------------------------------------------------

    @lru_cache(maxsize=1)
    def _ingredient_index(self) -> list[dict]:
        """Build a searchable index of unique ingredient canonical names."""
        rows = self.get_raw_materials()
        seen: dict[str, dict] = {}
        for r in rows:
            name = r["ingredient_name"]
            if name not in seen:
                seen[name] = {
                    "ingredient_name": name,
                    "example_sku": r["SKU"],
                    "product_ids": [],
                }
            seen[name]["product_ids"].append(r["Id"])
        return list(seen.values())

    def search_ingredients(self, query: str, limit: int = 20) -> list[dict]:
        """Fuzzy prefix + contains search on canonical ingredient names."""
        q = query.lower().strip()
        if not q:
            return []

        idx = self._ingredient_index()
        starts = []
        contains = []
        for item in idx:
            name = item["ingredient_name"]
            if name.startswith(q):
                starts.append(item)
            elif q in name:
                contains.append(item)

        results = starts[:limit] + contains[: limit - len(starts)]
        return results[:limit]

    # ------------------------------------------------------------------
    # BOM similarity helpers (used by SubstitutionEngine)
    # ------------------------------------------------------------------

    @lru_cache(maxsize=1)
    def bom_ingredient_sets(self) -> dict[int, set[str]]:
        """bom_id -> {canonical ingredient names}"""
        rows = self._q(
            """SELECT bc.BOMId, p.SKU
               FROM BOM_Component bc
               JOIN Product p ON p.Id = bc.ConsumedProductId"""
        )
        boms: dict[int, set[str]] = {}
        for r in rows:
            boms.setdefault(r["BOMId"], set()).add(_canon(r["SKU"]))
        return boms

    @lru_cache(maxsize=1)
    def ingredient_to_boms(self) -> dict[str, set[int]]:
        """canonical ingredient name -> {bom_ids using it}"""
        bom_sets = self.bom_ingredient_sets()
        inv: dict[str, set[int]] = {}
        for bom_id, ingredients in bom_sets.items():
            for ing in ingredients:
                inv.setdefault(ing, set()).add(bom_id)
        return inv

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        return {
            "companies": self._q("SELECT COUNT(*) AS n FROM Company")[0]["n"],
            "finished_goods": self._q(
                "SELECT COUNT(*) AS n FROM Product WHERE Type='finished-good'"
            )[0]["n"],
            "raw_materials": self._q(
                "SELECT COUNT(*) AS n FROM Product WHERE Type='raw-material'"
            )[0]["n"],
            "boms": self._q("SELECT COUNT(*) AS n FROM BOM")[0]["n"],
            "suppliers": self._q("SELECT COUNT(*) AS n FROM Supplier")[0]["n"],
            "supplier_links": self._q("SELECT COUNT(*) AS n FROM Supplier_Product")[0]["n"],
        }

    def close(self):
        self._conn.close()
