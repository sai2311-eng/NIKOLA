---
supplier_name: "{{supplier_name}}"
product: "{{product}}"
product_category: "{{product_category}}"
country: "{{country}}"

# ── PRICING ──────────────────────────────────────────────
price_per_unit: 
currency: "INR"
moq: 
moq_unit: "units"
price_for_moq: 
bulk_price_tier_1_qty: 
bulk_price_tier_1_price: 
bulk_price_tier_2_qty: 
bulk_price_tier_2_price: 

# ── SUPPLY CAPACITY ───────────────────────────────────────
monthly_capacity: 
lead_time_days: 
sample_available: false
sample_cost: 

# ── SCORING INPUTS (0–10) ─────────────────────────────────
scalability_score: 
reliability_score: 
price_score: 
quantity_score: 

# ── SCALABILITY DETAILS ───────────────────────────────────
# 10 = can 3x in 6 months | 5 = moderate | 1 = fixed capacity | 0 = unknown
scalability_evidence: ""
# e.g. "Ships to Walmart, Samsung - seen on ImportYeti"

# ── RELIABILITY DETAILS ───────────────────────────────────
# 10 = ISO + audited | 7 = third-party cert | 4 = self-declared | 0 = none
reliability_evidence: ""

# ── CERTIFICATIONS ────────────────────────────────────────
cert_iso: false
cert_haccp: false
cert_reach: false
cert_ce_mark: false
cert_cpsc: false
cert_astm: false
cert_brc: false
cert_fssai: false
cert_bis: false
cert_other: ""
cert_verified_via: ""
# e.g. "IAF CertSearch - verified 2024-03"
cert_expiry: ""

# ── COMPLIANCE FLAGS ──────────────────────────────────────
recall_history: false
recall_details: ""
eu_safety_gate_flagged: false
cpsc_recall_flagged: false
self_declared_only: false
# Self-declared with no third-party audit = RED FLAG

# ── PACKAGING & LABEL DATA ───────────────────────────────
label_image_url: ""
packaging_text_source: ""
ingredients_available: false
barcode_gtin: ""
barcode_verified_gs1: false
pack_sizes_available: ""
# e.g. "250ml, 500ml, 1L"

# ── TRADE / SHIPMENT INTELLIGENCE ────────────────────────
importyeti_verified: false
importyeti_url: ""
ships_to_countries: ""
shipment_frequency: ""
# e.g. "Monthly - consistent 12 months"
known_buyers: ""
# e.g. "Walmart, Target (seen on Panjiva)"
hs_code: ""
customs_data_source: ""
# e.g. "Zauba, ImportYeti, Panjiva"

# ── SOURCE TRACKING ───────────────────────────────────────
source_tier: 
# 1=Regulatory | 2=Brand/first-party | 3=B2B marketplace | 4=Trade/customs | 5=Aggregator
source_name: ""
# e.g. "Alibaba", "ImportYeti", "FDA", "Open Food Facts"
source_url: ""
source_type: ""
# "regulatory" | "brand" | "b2b" | "trade" | "crowdsource"
date_scraped: "{{date}}"
scraped_by: ""

# ── TRIANGULATION STATUS ──────────────────────────────────
triangulation_regulatory: false
triangulation_firstparty: false
triangulation_trade: false
triangulation_complete: false
# All 3 must be true before Tier 1 or Tier 2 output

# ── CONTACT INFO ──────────────────────────────────────────
contact_name: ""
contact_email: ""
contact_phone: ""
contact_whatsapp: ""
website: ""
response_speed: ""
# "<12 hrs" | "12-24 hrs" | "24-48 hrs" | ">48 hrs" | "No response"

# ── DATA COMPLETENESS (auto-fill) ────────────────────────
# Count filled fields: price, moq, scalability_score, reliability_score
# 4/4 = 10 | 3/4 = 7.5 | 2/4 = 5 | 1/4 = 2.5 | 0/4 = 0
data_completeness_score: 
imputed_fields: ""
# List which fields used market average e.g. "price_per_unit, scalability_score"

# ── FINAL SCORES (calculated after export) ────────────────
final_score: 
tier_output: ""
# "Tier 1 - Primary" | "Tier 2 - Backup" | "Tier 3 - Conditional" | "Tier 4 - Reject"
action: ""

# ── NOTES ─────────────────────────────────────────────────
red_flags: ""
positive_signals: ""
follow_up_required: ""
notes: ""
---

# {{supplier_name}} — {{product}}

## Quick summary
> Write 2–3 lines about this supplier after reviewing all data.

## Evidence log
| Parameter | Value | Source | Verified? |
|-----------|-------|--------|-----------|
| Price | | | |
| MOQ | | | |
| Monthly capacity | | | |
| Key certifications | | | |
| Shipment history | | | |
| Known buyers | | | |

## Red flags checklist
- [ ] Self-declared certs only (no third-party verification)
- [ ] No export/shipment history found
- [ ] Expired certifications
- [ ] Listed on EU Safety Gate / CPSC recalls
- [ ] Cannot provide material safety data sheet
- [ ] No direct website or legal entity info
- [ ] MOQ/lead time commercially unrealistic

## Triangulation status
- [ ] Tier 1 regulatory source confirmed
- [ ] Tier 2 first-party/brand source confirmed  
- [ ] Tier 4 trade/customs source confirmed

## Raw clipped content
<!-- Paste anything you clipped from the web here -->
