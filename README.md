# Agnes - AI Supply Chain Assistant

Agnes is an AI-powered procurement intelligence platform built for CPG (Consumer Packaged Goods) supply chain teams. It automates supplier discovery, ingredient analysis, compliance scoring, and supplier ranking across global markets.

## What Agnes Does

- **Ingredient Analysis** — Enter any raw material or ingredient and get instant intelligence: HS codes, supplier data, substitutes, and demand aggregation
- **Barcode Scanning** — Scan or enter a product barcode to decompose it into ingredients and discover suppliers for each one
- **360-Degree Supplier Discovery** — Automatically searches 11+ sources across 5 tiers (Alibaba, IndiaMart, ThomasNet, ImportYeti, FDA, ECHA, and more) to find every available supplier
- **Live HS Code Lookup** — Fetches and cross-verifies Harmonized System tariff codes from the web in real-time for any material, with SQLite caching
- **Supplier Scoring & Ranking** — Scores suppliers on a 100-point scale across 6 dimensions (Price, Quantity, Scalability, Reliability, Data Completeness, Triangulation)
- **Compliance Assessment** — Evaluates suppliers against 8 international standards (ISO 10377, IEC 62321, CPSC, REACH, CE Mark, BRC, Codex Alimentarius, ASTM)
- **Bottleneck Analysis** — Identifies single-source risks and supply chain vulnerabilities
- **Substitution Engine** — Finds alternative ingredients using a 3-signal scoring model (name similarity, BOM co-occurrence, category matching)

---

## Data Sources

### Open Food Facts

Agnes uses the [Open Food Facts](https://world.openfoodfacts.org/) open database as a primary data source for product and ingredient intelligence.

- **Dataset**: [en.openfoodfacts.org.products.csv.gz](https://static.openfoodfacts.org/data/en.openfoodfacts.org.products.csv.gz)
- **Size**: ~12 GB uncompressed (~1.2 GB compressed)
- **Coverage**: ~4.4 million products worldwide
- **Local Index**: Agnes builds a local SQLite index (`off_index.db`) for instant barcode-to-product lookups without network calls
- **Fields Used**: product_name, brands, ingredients_text, categories, quantity, countries, nutriscore_grade

---

## Methodology

### 1. Barcode Lookup Pipeline

Agnes resolves barcodes through a **4-tier fallback chain**, trying each source in order until a match is found:

| Priority | Source | Type | Latency |
|----------|--------|------|---------|
| 1 | Open Food Facts (local SQLite) | Offline | Instant |
| 2 | Open Food Facts API | Online (free, no key) | ~200ms |
| 3 | barcodelookup.com | Web scrape | ~1-2s |
| 4 | upcfoodsearch.com | Web scrape | ~1-2s |

**GTIN Validation**:
- GS1 checksum validation for EAN-8, UPC-A (12-digit), EAN-13, and GTIN-14 formats
- Left zero-fill padding to 14-digit standard format
- Priority scoring: valid checksum (+1 pt) + preferred length 12/13 digits (+1 pt)

**Image Processing** (for barcode scanning from photos):
- PIL decoding with EXIF orientation correction (RGB and grayscale)
- OpenCV fallback with candidate region extraction (full frame + center crops + horizontal bands)
- Preprocessing pipeline: CLAHE, GaussianBlur, medianBlur, Otsu thresholding, adaptive thresholding, Sobel edge detection
- Image upscaling (2x/3x for small regions) and rotation trials (0°, 90°, 180°, 270°)

**HS Code Inference** (after product lookup):
1. Live web lookup with cross-verification (primary)
2. Seed map fallback (300+ hardcoded ingredient-to-HS mappings)
3. Category-based fallback (broad product type mapping)

---

### 2. Email Ingestion

Agnes connects to Gmail via OAuth2 to automatically ingest procurement-related emails into the analysis pipeline.

**Pipeline**:
1. **Authentication** — Gmail API OAuth2 flow with token refresh
2. **Sync** — `sync_mailbox()` fetches new messages since last sync
3. **Extraction** — Parses email bodies and attachments for procurement data (supplier quotes, RFQs, order confirmations)
4. **Storage** — All ingested emails stored in local SQLite database with metadata (sender, date, subject, extracted fields)
5. **Deduplication** — Message ID tracking prevents re-processing

---

### 3. Digital Source Ingestion

Agnes ingests structured and unstructured data from multiple digital channels:

#### PDF Harvesting (Zero-LLM)
- Discovers spec sheets and datasheets via DuckDuckGo `filetype:pdf` searches
- Downloads with content-type and magic byte validation (`%PDF` header check)
- Extracts raw text and tables using **pdfplumber** (no language model involved)
- Parses 40+ regex patterns to extract structured fields:
  - Part identification (part number, manufacturer)
  - Mechanical specs (thread designation, diameter, pitch, length, head type, material, grade, tensile strength)
  - Electronic specs (capacitance, resistance, voltage rating)
  - Compliance markers (RoHS, REACH, CE, ISO, DIN, ASTM, AEC-Q)
  - Physical properties (surface finish, temperature range, weight)
- Max 20 tables extracted per PDF

#### Multi-Engine Web Search
Agnes queries up to 4 search engines and combines results:

| Engine | Method | API Key Required |
|--------|--------|-----------------|
| DuckDuckGo | duckduckgo-search library | No |
| Bing | HTML scraping via BeautifulSoup | No |
| Google | HTML scraping via BeautifulSoup | No |
| Google Shopping | Price comparison scraping | No |

**Result ranking**: Known industrial/supplier domains (McMaster, Grainger, Digikey, Mouser, SKF) are scored higher (quality 5). B2B marketplaces (Alibaba, IndiaMart) score 3-4. Retail (Amazon) scores 2. Non-supplier sites (Wikipedia, YouTube, Reddit) are excluded. Max 3 results per domain to prevent single-source domination.

#### Internal File Watcher
Monitors local directories for new procurement files and auto-ingests them:

| Format | Parser |
|--------|--------|
| `.csv` | DictReader (UTF-8 with Latin-1 fallback) |
| `.xlsx` / `.xls` | pandas + openpyxl |
| `.json` | Records or components array |
| `.pdf` | pdf_harvester |
| `.txt` | Raw text parsing |

- Real-time detection via Watchdog (falls back to 2-second polling)
- MD5 hash-based change detection to avoid duplicate processing
- Column name normalization to canonical fields via alias mapping

#### Query Expansion
Before searching, Agnes expands queries with domain-specific intelligence:

- **Fastener detection**: M-designation mapping (M1-M12 with coarse/fine pitches), ISO/DIN standard codes, material variants (A2/A4/316 stainless, grades 8.8-12.9)
- **Electronic component detection**: Capacitance normalization (pF/nF/µF), voltage ratings, dielectric types (X7R, C0G), package codes (0201-1812)
- **Bearing detection**: Bearing number extraction, manufacturer cross-reference (SKF, NSK, Schaeffler, Timken), seal types, ISO/DIN/JIS standards
- Outputs: primary search terms, variant synonyms, standard designations, material variants, ready-to-use search queries

---

### 4. Supplier Discovery — The 5-Tier Source Matrix

Agnes searches **11 sources across 5 tiers** simultaneously to build a complete supplier picture for any ingredient or material:

| Tier | Type | Sources | What's Extracted |
|------|------|---------|-----------------|
| **T1** | Regulatory | FDA, ECHA (REACH) | Supplier name, compliance status |
| **T2** | Brand / First-party | Manufacturer websites, Open Food Facts | Supplier name, product data |
| **T3** | B2B Marketplace | Alibaba, IndiaMart, ThomasNet, Europages, Made-in-China, GlobalSources | Price, MOQ, supplier name, country, certifications |
| **T4** | Trade / Customs | ImportYeti | Supplier name, shipment frequency, known buyers |
| **T5** | Aggregator | General web search | Supplier name, price |

**Why 5 tiers?** No single source tells the whole story. A supplier listed on Alibaba (T3) could be a trading company. But if that same supplier also appears in FDA registrations (T1) and ImportYeti shipment records (T4), we know they're a real manufacturer with an active export history. This is the foundation of **triangulation**.

**Triangulation Logic**:
- A supplier is marked `triangulation_complete = true` when found across all three verification axes:
  - **Regulatory** (T1) — officially registered
  - **First-party** (T2) — has a real brand presence
  - **Trade** (T4) — has actual shipping/customs records
- Partial triangulation = found in 2 of 3 axes

**Deduplication**: Supplier names are normalized (lowercase, whitespace stripped) and tracked in a set. Names ≤ 2 characters are discarded.

---

### 5. Supplier Scoring — 100-Point Scale

Every discovered supplier is scored across **6 dimensions** totalling 100 points:

| Dimension | Weight | What It Measures |
|-----------|--------|-----------------|
| **Price** | 20 pts | Price competitiveness vs. market |
| **Quantity** | 15 pts | MOQ flexibility and capacity fit |
| **Scalability** | 20 pts | Production scaling capability |
| **Reliability** | 25 pts | Track record + certification quality |
| **Data Completeness** | 10 pts | How much verifiable data exists |
| **Triangulation** | 10 pts | Cross-verified across source tiers |

**Scoring Formula**:
```
Price Score      = (raw / 10) × 20
Quantity Score   = (raw / 10) × 15
Scalability      = (raw / 10) × 20
Reliability      = (raw / 10) × 20 + cert_bonus (max 5 pts)
Data Completeness = (raw / 10) × 10
Triangulation    = triangulation_complete ? 10 : (tri_count / 3) × 10

Final Score = max(0, min(100, sum_of_above - penalties))
```

**Certification Bonuses** (added to reliability, normalized to max 5 pts):

| Certification | Bonus |
|--------------|-------|
| ISO 9001 | +2.0 |
| REACH | +2.0 |
| HACCP | +1.5 |
| CE Mark | +1.5 |
| CPSC | +1.5 |
| ASTM | +1.0 |
| BRC | +1.0 |
| FSSAI | +0.5 |
| BIS | +0.5 |
| Verified (any) | +1.0 |

**Red Flag Penalties** (deducted from final score):

| Flag | Penalty |
|------|---------|
| Recall history | -15 pts |
| EU Safety Gate flagged | -10 pts |
| CPSC recall flagged | -10 pts |
| Expired certification | -8 pts |
| Self-declared certs only | -5 pts |

**Tier Assignment**:

| Tier | Criteria | Action |
|------|----------|--------|
| **Tier 1 — Primary** | Score ≥ 70 AND triangulation complete | Initiate RFQ |
| **Tier 2 — Backup** | Score ≥ 50 AND partial triangulation | Complete triangulation / Strong candidate |
| **Tier 3 — Conditional** | Score ≥ 30 | Needs more data |
| **Tier 4 — Reject** | Score < 30 OR critical red flags | Not recommended |

---

### 6. Compliance Scoring — 80-Point Scale

Suppliers are evaluated against **8 international standards** across two categories:

#### Legal Standards (max 30 pts)

| Standard | Points | Scope |
|----------|--------|-------|
| REACH (EU) | 10 | Chemical restrictions — mandatory for EU market |
| CPSC (USA) | 10 | Consumer product safety — mandatory for US market |
| CE Mark (EU) | 10 | EU conformity marking — mandatory for applicable products |

#### Quality Standards (max 50 pts)

| Standard | Points | Scope |
|----------|--------|-------|
| Codex Alimentarius (FAO/WHO) | 10 | Food safety and quality standards |
| HACCP | 10 | Food safety management systems |
| ASTM | 10 | Voluntary consensus standards |
| ISO 10377 / ISO 9001 | 10 | Consumer safety and quality management |
| IEC 62321 | 10 | Electrical/electronic product safety |

#### Evidence Levels

Each standard is scored based on the strength of evidence:

| Evidence Level | Points | Example |
|---------------|--------|---------|
| Third-party certified | 10 | SGS, TÜV, Bureau Veritas, Intertek audit |
| Certificate provided (unverified) | 7 | Supplier uploaded certificate, not yet verified |
| Self-declared | 4 | Supplier claims compliance, no documentation |
| Expired certification | 2 | Certificate exists but past expiry date |
| No evidence | 0 | No compliance information available |

#### Risk Classification

| Risk Level | Score Threshold |
|-----------|----------------|
| Low | ≥ 60 / 80 (75%) |
| Medium | ≥ 40 / 80 (50%) |
| High | ≥ 20 / 80 (25%) |
| Critical | < 20 / 80 |

**Red Flag Triggers**: Self-declaration without third-party certificate, expired certifications, REACH claim without MSDS, certificate name mismatch, zero legal compliance, 3+ self-declared-only standards.

---

### 7. HS Code Lookup — Live Cross-Verification

Agnes fetches the correct Harmonized System tariff code for any material in real-time:

**Pipeline**:
1. **Check SQLite cache** — instant lookup for previously resolved codes
2. **Run 3 search strategies in parallel**:
   - `"{material}" HS code`
   - `"{material}" harmonized tariff schedule classification`
   - `"{material}" HTS code import export tariff heading`
3. **Query DuckDuckGo + Bing** — extract candidates from search results
4. **Pattern matching** — multi-format regex extraction:
   - Dotted: `HS 2923.20.00`, `HTS: 2923.20`
   - Non-dotted: `HS Code 29232010`, `HSN 29232000`
   - Bare codes with context window
5. **Normalize** — convert all formats to standard dotted notation (e.g., `29232010` → `2923.20.10`)
6. **Cross-verify** — group by 4-digit heading, pick the code confirmed by 2+ sources
7. **Cache result** — store in SQLite with source attribution and confidence score

**Validation**: Chapter range check (01-99), year pattern filtering, format normalization.

**Confidence**: `min(confirming_source_count / total_queries, 1.0)`. Verified = 2+ sources agree.

---

### 8. Substitution Engine — 3-Signal Scoring

Agnes finds alternative ingredients using a composite scoring model:

```
Score = (0.30 × Name Similarity) + (0.40 × BOM Co-occurrence) + (0.30 × Category Match)
```

| Signal | Weight | Method |
|--------|--------|--------|
| **Name Similarity** | 30% | Jaccard distance on word tokens (50%) + SequenceMatcher ratio (50%) |
| **BOM Co-occurrence** | 40% | How often two ingredients appear together in the same Bill of Materials |
| **Category Match** | 30% | Whether ingredients share a functional category (16+ categories: emulsifier, protein, sweetener, etc.) |

**Variant Filtering**: Salt/chemical-form modifiers (oxide, citrate, stearate) are stripped to prevent false matches. Same base ingredient with different salts (e.g., magnesium oxide vs. magnesium citrate) are excluded. Different source variants (e.g., soy lecithin vs. sunflower lecithin) are kept as valid substitutes.

**Web Fallback**: When no local substitutes are found, Agnes searches DuckDuckGo for patterns like "substitute with X", "X is alternative to Y". Web-discovered substitutes get a lower confidence score (0.35).

**Minimum threshold**: 0.20 similarity score (configurable).

---

## Architecture

```
app_v3.py                      # Streamlit UI (6 tabs)
src/
  agnes/                       # Core AI agent
    actions.py                 # Analysis orchestration
    agent.py                   # Claude-powered agent
    tools.py                   # Agent tool definitions
    pipeline.py                # Multi-step analysis pipeline
    candidates.py              # Candidate generation
    scoring.py                 # Scoring engine
    constraints.py             # Constraint evaluation
    context.py                 # Context management
    consolidation.py           # Result consolidation
    review.py                  # Review workflow
    evidence_collector.py      # Evidence gathering
    elevenlabs_prompt.py       # Voice integration
  procurement/                 # Procurement intelligence
    barcode_lookup.py          # Barcode/GTIN lookup (Open Food Facts + UPC DB)
    hs_lookup.py               # Live HS code lookup with web verification
    supplier_discovery.py      # 360-degree web supplier search
    supplier_scorer.py         # 100-point supplier scoring
    supplier_db.py             # SQLite supplier database
    compliance.py              # 8-standard compliance engine
    supply_intelligence.py     # Core supply chain analysis
    substitution_engine.py     # Substitute ingredient finder
    ranking.py                 # Supplier ranking logic
    cpg_db.py                  # CPG product database
    internal_checker.py        # Internal procurement checker
    product_identifier.py      # Product identification
    consolidated_sourcing.py   # Consolidated sourcing
    evidence.py                # Evidence models
data_collection/               # Web scraping & data ingestion
  search_engine.py             # Multi-engine web search (DuckDuckGo + Bing)
  pdf_harvester.py             # PDF/datasheet harvester
  query_expander.py            # Search query expansion
  internal_watcher.py          # Internal data monitoring
  marketplace_scrapers/        # B2B marketplace scrapers
    alibaba_scraper.py
    indiamart_scraper.py
    thomasnet_scraper.py
data/
  procurement_records.csv      # Sample procurement data
internal_procurement/
  approved_suppliers.json      # Approved supplier list
  procurement_q1_2026.csv      # Q1 2026 procurement records
```

## Tabs

| Tab | Purpose |
|-----|---------|
| **Supply Intelligence** | Main analysis interface — ingredient lookup, barcode scanning, bottleneck detection |
| **Internal Procurement** | Internal procurement records and approved supplier management |
| **Order Procurement** | Order tracking and procurement workflow |
| **Risk & Compliance** | Supplier compliance scoring against 8 international standards |
| **Supplier Database** | Central hub for all discovered suppliers with triangulation tracking |
| **Final Ranking** | Scored and ranked supplier list with tier assignments |

## Setup

### Prerequisites

- Python 3.10+
- Anthropic API key (for Claude-powered analysis)

### Installation

```bash
# Clone the repository
git clone https://github.com/sai2311-eng/NIKOLA.git
cd NIKOLA

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### Running

```bash
streamlit run app_v3.py --server.port 8502
```

The app will be available at `http://localhost:8502`.

## Key Dependencies

- **Streamlit** — Web UI framework
- **Anthropic** — Claude AI for intelligent analysis
- **BeautifulSoup4** — Web scraping
- **duckduckgo-search** — Web search (no API key needed)
- **SQLite** — Local database for suppliers, HS codes, and product data
- **EasyOCR** — Optical character recognition for documents
- **Pillow** — Image processing for barcode scanning
- **pdfplumber** — PDF text and table extraction
- **OpenCV** — Barcode image preprocessing
- **Watchdog** — File system monitoring

## License

This project is proprietary. All rights reserved.
