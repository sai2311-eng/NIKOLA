# Supplier Intelligence — Dataview Query Hub
*Paste each query block into any Obsidian note. Requires Dataview plugin.*

---

## 1. MASTER SUPPLIER TABLE (all suppliers, all fields)

```dataview
TABLE
  supplier_name AS "Supplier",
  product AS "Product",
  country AS "Country",
  price_per_unit AS "Price/unit",
  moq AS "MOQ",
  monthly_capacity AS "Monthly cap.",
  scalability_score AS "Scale (0-10)",
  reliability_score AS "Rely (0-10)",
  data_completeness_score AS "Data %",
  final_score AS "Score /100",
  tier_output AS "Tier",
  triangulation_complete AS "Triangulated?"
FROM "Suppliers"
SORT final_score DESC
```

---

## 2. TIER 1 SUPPLIERS ONLY (best, negotiate now)

```dataview
TABLE
  supplier_name AS "Supplier",
  product AS "Product",
  price_per_unit AS "Price",
  moq AS "MOQ",
  final_score AS "Score",
  action AS "Action",
  contact_email AS "Email",
  contact_phone AS "Phone"
FROM "Suppliers"
WHERE tier_output = "Tier 1 - Primary"
SORT final_score DESC
```

---

## 3. RED FLAG SUPPLIERS (auto-flagged for rejection)

```dataview
TABLE
  supplier_name AS "Supplier",
  red_flags AS "Red flags",
  self_declared_only AS "Self-declared?",
  recall_history AS "Recall?",
  eu_safety_gate_flagged AS "EU flagged?",
  cpsc_recall_flagged AS "CPSC flagged?",
  importyeti_verified AS "Shipment data?"
FROM "Suppliers"
WHERE self_declared_only = true
  OR recall_history = true
  OR eu_safety_gate_flagged = true
  OR cpsc_recall_flagged = true
SORT supplier_name ASC
```

---

## 4. MISSING DATA — NEEDS FOLLOW-UP (Strategy 1 imputation targets)

```dataview
TABLE
  supplier_name AS "Supplier",
  imputed_fields AS "Imputed fields",
  data_completeness_score AS "Data score",
  follow_up_required AS "Follow up action",
  contact_email AS "Email"
FROM "Suppliers"
WHERE data_completeness_score < 10
SORT data_completeness_score ASC
```

---

## 5. TRIANGULATION INCOMPLETE (cannot be Tier 1 yet)

```dataview
TABLE
  supplier_name AS "Supplier",
  triangulation_regulatory AS "Reg. confirmed",
  triangulation_firstparty AS "Brand confirmed",
  triangulation_trade AS "Trade confirmed",
  triangulation_complete AS "Complete?",
  tier_output AS "Current tier"
FROM "Suppliers"
WHERE triangulation_complete = false
SORT supplier_name ASC
```

---

## 6. CERTIFICATION VERIFICATION STATUS

```dataview
TABLE
  supplier_name AS "Supplier",
  cert_iso AS "ISO",
  cert_haccp AS "HACCP",
  cert_reach AS "REACH",
  cert_ce_mark AS "CE",
  cert_cpsc AS "CPSC",
  cert_verified_via AS "Verified via",
  cert_expiry AS "Expiry",
  self_declared_only AS "Self-declared only?"
FROM "Suppliers"
SORT supplier_name ASC
```

---

## 7. SHIPMENT INTELLIGENCE TABLE

```dataview
TABLE
  supplier_name AS "Supplier",
  importyeti_verified AS "ImportYeti?",
  ships_to_countries AS "Ships to",
  shipment_frequency AS "Frequency",
  known_buyers AS "Known buyers",
  hs_code AS "HS code",
  customs_data_source AS "Data source"
FROM "Suppliers"
SORT importyeti_verified DESC
```

---

## 8. PRICE COMPARISON TABLE

```dataview
TABLE
  supplier_name AS "Supplier",
  country AS "Country",
  price_per_unit AS "Unit price",
  currency AS "Currency",
  moq AS "MOQ",
  price_for_moq AS "Total at MOQ",
  lead_time_days AS "Lead time",
  sample_available AS "Sample?"
FROM "Suppliers"
SORT price_per_unit ASC
```

---

## 9. SUPPLIER RESPONSE SPEED RANKING

```dataview
TABLE
  supplier_name AS "Supplier",
  response_speed AS "Response",
  contact_name AS "Contact",
  contact_email AS "Email",
  contact_whatsapp AS "WhatsApp",
  follow_up_required AS "Follow up"
FROM "Suppliers"
SORT response_speed ASC
```

---

## 10. SOURCE AUDIT TRAIL (where did each data point come from?)

```dataview
TABLE
  supplier_name AS "Supplier",
  source_tier AS "Tier",
  source_name AS "Source",
  source_type AS "Type",
  source_url AS "URL",
  date_scraped AS "Scraped",
  scraped_by AS "By"
FROM "Suppliers"
SORT source_tier ASC, supplier_name ASC
```

---

## 11. DAILY SUMMARY DASHBOARD

```dataview
TABLE WITHOUT ID
  length(rows) AS "Total suppliers",
  length(filter(rows, (r) => r.tier_output = "Tier 1 - Primary")) AS "Tier 1",
  length(filter(rows, (r) => r.tier_output = "Tier 2 - Backup")) AS "Tier 2",
  length(filter(rows, (r) => r.tier_output = "Tier 3 - Conditional")) AS "Tier 3",
  length(filter(rows, (r) => r.tier_output = "Tier 4 - Reject")) AS "Tier 4",
  length(filter(rows, (r) => r.triangulation_complete = true)) AS "Triangulated",
  length(filter(rows, (r) => r.data_completeness_score = 10)) AS "Full data"
FROM "Suppliers"
GROUP BY true
```

---

## 12. CSV EXPORT QUERY (copy output → paste into scoring tool)

```dataview
TABLE
  supplier_name,
  price_per_unit,
  moq,
  monthly_capacity,
  scalability_score,
  reliability_score,
  data_completeness_score,
  cert_iso,
  cert_haccp,
  cert_reach,
  cert_ce_mark,
  importyeti_verified,
  triangulation_complete,
  response_speed,
  imputed_fields,
  tier_output,
  country,
  source_name,
  date_scraped
FROM "Suppliers"
SORT final_score DESC
```
*After running: click the "..." menu on the table → "Export to CSV" → feed into scoring matrix tool*

---

## HOW TO USE THIS FILE

1. Save this file as `_QUERIES.md` inside your Obsidian vault
2. Save `SUPPLIER_TEMPLATE.md` inside a folder called `Suppliers/`
3. Every new supplier note goes in `Suppliers/` folder
4. All queries above use `FROM "Suppliers"` — they auto-update as you add notes
5. Run Query 12 weekly to export to CSV → paste into the scoring matrix
6. Run Query 3 daily to catch any new red flags
7. Run Query 5 to see which suppliers still need triangulation before promoting to Tier 1

## OBSIDIAN PLUGINS NEEDED

| Plugin | Purpose | Install from |
|--------|---------|-------------|
| Dataview | All queries above | Community plugins |
| Templater | Auto-fill date, name on new note | Community plugins |
| Web Clipper | Browser extension for scraping | obsidian.md/clipper |
| DB Folder | Visual table view of supplier notes | Community plugins |
| CSV Export | Export Dataview tables to CSV | Community plugins |
