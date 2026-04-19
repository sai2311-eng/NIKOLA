# Supplier Scraping SOP — Standard Operating Procedure
*Hand this to your team. Follow this exactly for every supplier.*

---

## SETUP (one time only)

### Step 1 — Install Obsidian
Download from obsidian.md → install on your laptop.
Create a new vault called: `SupplierIntelligence`
Inside the vault, create a folder called: `Suppliers`

### Step 2 — Install plugins (Settings → Community Plugins)
- Dataview
- Templater
- DB Folder

### Step 3 — Install Obsidian Web Clipper
Go to obsidian.md/clipper → install Chrome/Firefox extension
In the extension settings, set:
- Default folder: `Suppliers`
- Default template: `SUPPLIER_TEMPLATE`

### Step 4 — Copy files into vault
Place these files in your vault root:
- `SUPPLIER_TEMPLATE.md`
- `_QUERIES.md`
- `clipper-config.json`

---

## DAILY SCRAPING WORKFLOW (for each supplier)

### Rule: Always go Tier 1 → 2 → 3 → 4 → 5

---

### TIER 1 SOURCES — Do this FIRST for every product

**A. Check GS1 (product identity)**
1. Go to gs1.org/services/verified-by-gs1
2. Enter product barcode / GTIN
3. Clip the result. Record: brand owner name, GTIN, product name

**B. Check FDA / FSSAI / ECHA (compliance)**
1. Search supplier or product name in fda.gov, fssai.gov.in, echa.europa.eu
2. If found on recall list → mark `recall_history: true`, write details in `red_flags`
3. If clean → mark `triangulation_regulatory: true`

**C. Check IAF CertSearch (ISO verification)**
1. Go to iaf.nu/certsearch
2. Enter supplier name
3. If ISO cert found → record cert number, body, expiry → set `cert_iso: true`
4. If NOT found but supplier claims ISO → set `self_declared_only: true` ← RED FLAG

---

### TIER 2 SOURCES — First-party product data

**A. Brand / manufacturer website**
1. Search: `[product name] manufacturer site:manufacturer.com`
2. Clip product page. Record: pack sizes, ingredients, label images, contact info
3. Set `triangulation_firstparty: true`

**B. Open Food Facts (for food products)**
1. Go to world.openfoodfacts.org
2. Search by product name or barcode
3. Clip. Record: ingredients, label image URL, packaging text
4. Note: this is community data — cross-check with brand site

---

### TIER 3 SOURCES — Supplier discovery + pricing

**A. Alibaba**
1. Search product name → filter: Verified Supplier + Trade Assurance
2. Open each result. Clip. Record:
   - Price per unit (at different MOQ tiers)
   - MOQ
   - Lead time
   - Monthly capacity
   - Certifications listed
   - Response time (check their badge)
3. ⚠️ Never trust self-declared certs here — verify in IAF CertSearch

**B. ThomasNet (for USA suppliers)**
1. Go to thomasnet.com → search product
2. Clip top verified results. Record: company size, certifications, capacity

**C. IndiaMart (for India suppliers)**
1. Go to indiamart.com → search product
2. Look for TrustSEAL verified badge
3. Record same fields as Alibaba above

**D. Europages (for EU suppliers)**
1. Go to europages.com → search product
2. Filter: verified companies only
3. Record: company, country, certifications, product range

---

### TIER 4 SOURCES — Operational realism check (MANDATORY)

**This is the most important check. Do not skip.**

**A. ImportYeti (free)**
1. Go to importyeti.com
2. Search supplier company name
3. If found:
   - Record: shipment frequency, destination countries, known buyers
   - Set `importyeti_verified: true`
   - If ships to major brands (Walmart, Costco, Samsung) → strong positive signal
   - Set `triangulation_trade: true`
4. If NOT found → high risk. Mark `follow_up_required: "No shipment history found"`

**B. Zauba (for India)**
1. Go to zauba.com
2. Search company name
3. Record: import/export frequency, volumes, HS code

**C. UN Comtrade (macro check)**
1. Go to comtrade.un.org
2. Search by HS code to see which countries are top producers
3. Use this to shortlist countries to focus sourcing in

---

### TIER 5 SOURCES — Enrichment only

Use only to cross-check prices and product data:
- Google Shopping → retail price benchmarking
- Keepa / CamelCamelCamel → Amazon price history
- Statista / IndexMundi → commodity/raw material prices

---

## AFTER SCRAPING — FILL THE TEMPLATE

Open the supplier note in Obsidian. Fill every field you have data for.
For blank fields, write the field name in `imputed_fields` — these will use market averages.

**Calculate data_completeness_score:**
- 4 fields filled (price + qty + scalability + reliability) = 10
- 3 fields filled = 7.5
- 2 fields filled = 5
- 1 field filled = 2.5
- 0 fields filled = 0

**Check triangulation:**
- Set `triangulation_complete: true` ONLY if all three are true:
  - `triangulation_regulatory: true`
  - `triangulation_firstparty: true`
  - `triangulation_trade: true`

---

## QUALITY RULES — Never break these

1. One note per supplier (not one per source — merge all sources into one supplier note)
2. Always record the `source_url` so data can be re-verified
3. Never mark a cert verified unless you personally checked IAF CertSearch or the cert body
4. Never set `triangulation_complete: true` unless all 3 checks are done
5. If a supplier fails any red flag check → set `tier_output: "Tier 4 - Reject"` immediately, do not continue

---

## EXPORT TO SCORING MATRIX

Weekly:
1. Open `_QUERIES.md` in Obsidian
2. Run Query 12 (CSV Export Query)
3. Click "..." → Export to CSV
4. Upload CSV to scoring matrix tool
5. Review tier outputs
6. Promote Tier 2 suppliers with complete triangulation to Tier 1

---

## CONTACT: If you find a supplier who passes everything

Flag them in Obsidian with tag `#ready-to-negotiate`
Record in `action` field: "Initiate RFQ — [date]"
Pass contact info to procurement team within 24 hours.
