# SAI - CPG Procurement Intelligence Platform

**Open-source procurement intelligence system for CPG (Consumer Packaged Goods) ingredient sourcing, substitution analysis, barcode scanning, and supplier consolidation.**

---

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Architecture](#architecture)
4. [Agnes AI Assistant](#agnes-ai-assistant)
5. [Agnes 7-Step Pipeline](#agnes-7-step-pipeline)
6. [Pipeline Stages](#pipeline-stages)
7. [CPG Database](#cpg-database)
8. [Barcode Scanner](#barcode-scanner)
9. [Substitution Engine](#substitution-engine)
10. [Consolidated Sourcing](#consolidated-sourcing)
11. [Evidence Trail](#evidence-trail)
12. [Compliance Engine](#compliance-engine)
13. [Supply Intelligence Layers](#supply-intelligence-layers)
14. [Databases](#databases)
15. [Project Structure](#project-structure)
16. [Configuration](#configuration)
17. [API Reference](#api-reference)
18. [CLI Usage](#cli-usage)

---

## Overview

SAI is a multi-stage procurement intelligence platform for CPG/supplement ingredient sourcing, built for the TUM.ai x Spherecast hackathon challenge. It features **Agnes**, an AI Supply Chain Assistant that helps CPG brands find suppliers, rank them, identify substitutes during bottlenecks, and aggregate demand across companies sharing the same raw materials.

The system runs on a CPG database (61 companies, 876 raw materials, 149 BOMs, 40 suppliers) and includes:
- **Agnes AI Assistant** — conversational interface with voice input, barcode scanning, and supply chain intelligence
- **Agnes 7-Step Pipeline** — deep substitution analysis with evidence-based scoring and compliance gates
- **8-Stage Procurement Pipeline** — traditional procurement flow with web-based supplier sourcing
- **Barcode Scanner** — backed by a local database of 4.45 million products from Open Food Facts
- **Variant-Filtered Substitution Engine** — ensures different forms of the same ingredient (e.g., magnesium citrate vs magnesium oxide) are NOT suggested as substitutes
- **Web Fallback** — when no local substitutes exist, searches the web for alternatives

---

## Quick Start

### Prerequisites

- Python 3.10+
- pip

### Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Or run the setup script
python setup.py
```

### Run the Web UI

```bash
streamlit run app_v3.py
```

The UI launches at `http://localhost:8505` with two tabs:
- **Procurement Intelligence** - Search and analyze CPG ingredients
- **Scan Barcode / Add Photo** - Scan barcodes to identify products and extract ingredients

### Run the CLI Pipeline

```bash
# USA compliance (default)
python procurement_pipeline.py "whey protein isolate"

# EU compliance
python procurement_pipeline.py "magnesium stearate" --compliance eu
```

### Sync Gmail into Agnes

Agnes can sync Gmail messages into a local SQLite mailbox store for downstream analysis.

1. Enable the Gmail API in Google Cloud Console.
2. Create a desktop OAuth client.
3. Save the downloaded client JSON as `credentials.json`, or set `GOOGLE_CLIENT_SECRET_FILE`.
4. Install dependencies with `pip install -r requirements.txt`.
5. Run a sync:

```bash
python gmail_sync.py sync --query "label:inbox"
```

The first run opens a browser for Google OAuth consent. Agnes stores the OAuth token in `data/gmail/gmail_token.json` and messages in `data/gmail/agnes_gmail.db`.

Search the stored mailbox:

```bash
python gmail_sync.py search "invoice"
```

---

## Architecture

```
User Input (text / barcode / photo)
    |
    v
+---------------------------------------------------------------+
|                   CPG PROCUREMENT PIPELINE                    |
|                                                               |
|  Stage 1: Product Identification (CPG ingredient matching)    |
|  Stage 2: CPG Database Lookup (db.sqlite)                     |
|  Stage 3: Internal Check (stock / BOM / suppliers)            |
|      |                                                        |
|      +-- in_stock -----> Continue to substitution analysis    |
|      +-- no_stock -----> Full external sourcing               |
|      |                                                        |
|  Stage 4: Supply Intelligence (Alibaba + IndiaMART)           |
|  Stage 5: Ranking Engine (Quality/Compliance/Price/LeadTime)  |
|  Stage 6: Assessment Dashboard                                |
|  Stage 7: Substitution Analysis (3-signal engine)             |
|  Stage 8: Consolidated Sourcing (supplier aggregation)        |
+---------------------------------------------------------------+
    |
    v
  Results + Evidence Trail
```

---

## Agnes AI Assistant

**Module:** `src/agnes/actions.py`

Agnes is a conversational AI Supply Chain Assistant with three core actions:

### 1. Ingredient Analysis (`analyze_ingredient`)

Search for any ingredient to get:
- **Suppliers** — ranked by composite score (quality 35%, compliance 25%, price 25%, lead time 15%)
- **Substitutes** — functionally equivalent ingredients (variant-filtered, with web fallback)
- **Demand Aggregation** — which companies use this ingredient and in how many products

```python
from src.procurement.cpg_db import CpgDatabase
from src.agnes.actions import analyze_ingredient

db = CpgDatabase("db.sqlite")
result = analyze_ingredient("magnesium stearate", db)
# result["suppliers"]     -> ranked list with composite scores
# result["substitutes"]   -> filtered list (no magnesium oxide, citrate, etc.)
# result["demand_aggregation"]["companies"] -> ["GNC", "Nature Made", ...]
```

### 2. Barcode Analysis (`analyze_barcode`)

Scan a product barcode to:
- Identify the product via Open Food Facts / web lookup
- Extract all ingredients from the label
- For each ingredient: find suppliers + substitutes from the CPG database

```python
from src.agnes.actions import analyze_barcode

result = analyze_barcode("3017620422003", db)
# result["product"]       -> {"product_name": "Nutella", ...}
# result["ingredients"]   -> [{name, suppliers, substitutes}, ...]
```

### 3. Bottleneck Analysis (`analyze_bottleneck`)

When a supply disruption occurs:
- **Risk Assessment** — single-source risk, cross-company impact, product breadth
- **Risk Levels** — `critical` (0 suppliers), `high` (1 supplier), `medium` (2 or high impact), `low`
- **Expanded Substitutes** — up to 20 alternatives with web fallback
- **Actionable Recommendations** — diversification, qualification, joint sourcing

```python
from src.agnes.actions import analyze_bottleneck

result = analyze_bottleneck("soy lecithin", db)
# result["risk_assessment"]["risk_level"]  -> "medium"
# result["risk_assessment"]["risk_factors"] -> ["Limited supplier pool", ...]
# result["recommendations"] -> ["Diversify supplier base...", ...]
```

---

## Agnes 7-Step Pipeline

**Module:** `src/agnes/pipeline.py`

Deep analysis pipeline for ingredient substitution intelligence. Each step builds on the previous:

| Step | Module | Purpose |
|------|--------|---------|
| 1. Intake & Context | `context.py` | Assembles 8 context blocks from db.sqlite (demand, supply, compliance, sensitivity) |
| 2. Candidate Generation | `candidates.py` | Generates up to 30 candidates via 5 expansion signals |
| 3. Constraint Inference | `constraints.py` | Infers hard/soft constraints from structural patterns |
| 4. Evidence Collection | `evidence_collector.py` | Gathers evidence records with 5 trust tiers (T1=1.0 to T5=0.3) |
| 5. Feasibility Scoring | `scoring.py` | 4 hard gates + 4 scoring dimensions (0-100 each) |
| 6. Consolidation | `consolidation.py` | 5 scenario types, 3 recommendation frames (cost/risk/balanced) |
| 7. Human Review | `review.py` | 5 decision modes (auto_approve to blocked), gap analysis |

### Hard Gates (Step 5)

All gates must pass before a candidate is scored:
1. **Compliance Floor** — must meet target market regulatory requirements
2. **Functional Floor** — minimum functional compatibility threshold
3. **Supply Availability** — at least one verified supplier pathway
4. **Safety Gate** — no flagged safety concerns

### Decision Modes (Step 7)

| Mode | Criteria |
|------|----------|
| `auto_approve` | Confidence >= 0.85, no critical gaps |
| `review_recommended` | Confidence 0.65-0.85, minor/major gaps |
| `expert_required` | Confidence < 0.65, or critical gaps present |
| `blocked` | Hard gate failures, cannot proceed |
| `insufficient_data` | Too many gaps, evidence quality < 0.3 |

```python
from src.procurement.cpg_db import CpgDatabase
from src.agnes.pipeline import AgnesPipeline

db = CpgDatabase("db.sqlite")
pipeline = AgnesPipeline(db)
result = pipeline.run("soy lecithin", target_market="usa")
# result.candidates     -> 30 candidates
# result.scorecards     -> scored with 4 dimensions
# result.review_package -> decision mode + gap analysis
```

---

## Pipeline Stages

### Stage 1 - Product Identification

**Module:** `src/procurement/product_identifier.py`

- Matches user query against the CPG database (876 raw materials) using fuzzy search
- Returns: `material_id`, `ingredient_name`, `hsn_code`, `confidence_score`, `cpg_suppliers`
- Provides autocomplete for the search bar from CPG ingredient names

### Stage 2 - CPG Database Lookup

- Confirms the ingredient exists in `db.sqlite`
- Reports variant count and supplier availability

### Stage 3 - Internal Check

**Module:** `src/procurement/internal_checker.py`

- Checks internal procurement records for existing stock and past purchases
- Enriches with CPG supplier data, BOM usage counts, and variant information from `db.sqlite`
- Outcome states: `in_stock`, `partial`, `out_of_stock`, `no_records`
- Even when in-stock, continues to substitution/consolidation analysis

### Stage 4 - Supply Intelligence Gathering

**Module:** `src/procurement/supply_intelligence.py`

- Gathers supplier data from Alibaba (Layer 9) and IndiaMART (Layer 10)
- Two modes: `offline` (web scraping only) and `api` (scraping + Claude LLM post-processing)

### Stage 5 - Ranking Engine

**Module:** `src/procurement/ranking.py`

- Scores each supplier on 4 signals (0-100 each), produces weighted composite score
- Signals: Quality, Compliance (region-aware), Price, Lead Time
- Verdicts: `excellent` (>=78), `good` (>=62), `possible` (>=46), `limited` (>=30), `poor` (<30)

### Stage 6 - Assessment Dashboard

- Aggregates all pipeline results into a structured output
- Geographic distribution, layer coverage, verdict breakdown, top recommendations

### Stage 7 - Substitution Analysis

**Module:** `src/procurement/substitution_engine.py`

- See [Substitution Engine](#substitution-engine) section

### Stage 8 - Consolidated Sourcing

**Module:** `src/procurement/consolidated_sourcing.py`

- See [Consolidated Sourcing](#consolidated-sourcing) section

---

## CPG Database

### Schema (`db.sqlite`)

| Table | Rows | Description |
|-------|------|-------------|
| `Company` | 61 | Supplement/CPG companies |
| `Product` (finished-good) | 149 | Finished products (supplements, foods) |
| `Product` (raw-material) | 876 | Raw material ingredients |
| `BOM` | 149 | Bill of Materials linking finished goods to raw materials |
| `BOM_Component` | -- | Junction table (BOM -> consumed products) |
| `Supplier` | 40 | Ingredient suppliers |
| `Supplier_Product` | 1,633 | Links suppliers to the products they provide |

### Access Layer

**Module:** `src/procurement/cpg_db.py` (`CpgDatabase` class)

Key methods:
- `get_companies()` - List all 61 companies
- `get_raw_materials()` - List all 876 ingredients with canonical names
- `get_finished_goods()` - List all 149 products with company names
- `get_bom(finished_good_id)` - Get ingredients for a product
- `get_suppliers_for_product(product_id)` - Find suppliers for an ingredient
- `get_supplier_catalog()` - Full supplier -> ingredient mapping
- `get_demand_map()` - Ingredient -> which companies/BOMs use it
- `search_ingredients(query)` - Fuzzy prefix + contains search
- `bom_ingredient_sets()` - BOM ID -> set of ingredient names (cached)
- `ingredient_to_boms()` - Ingredient -> set of BOM IDs (cached)

SKU format: `RM-C{company_id}-{ingredient-name}-{hex_hash}` (decoded by `_canon()`)

---

## Barcode Scanner

**Module:** `src/procurement/barcode_lookup.py`

The barcode tab provides three input methods:
1. **Scan with Camera** - Uses `st.camera_input()` for live camera capture with vibration feedback
2. **Enter barcode number** - Manual EAN/UPC entry
3. **Upload barcode photo** - Upload an image containing a barcode

### Barcode Decoding Pipeline

```
Image bytes
    |
    +--> pyzbar (direct barcode decode)
    |       |
    |       +--> (fail) --> OpenCV preprocessing (grayscale, threshold, blur)
    |                           +--> pyzbar retry
    |
    +--> (fail) --> Tesseract OCR (--psm 6 digits mode)
    |
    v
Barcode number (8-14 digits)
```

### Lookup Chain (tries in order)

| Priority | Source | Method | Coverage |
|----------|--------|--------|----------|
| 1 | Open Food Facts (local) | SQLite index (`off_index.db`) | 4.45M products |
| 2 | Open Food Facts API | REST API (`world.openfoodfacts.org`) | 3M+ products |
| 3 | Barcode Lookup | HTML scraping (`barcodelookup.com`) | Broad coverage |
| 4 | UPC Food Search | HTML scraping (`upcfoodsearch.com`) | US products |

### Output Data

Each barcode lookup returns:
- Product name, brand
- GTIN-14 (padded from barcode)
- HS / tariff code (inferred from 50+ product category mappings)
- Ingredients list (parsed from text)
- Categories, quantity/size, Nutri-Score, countries
- Product image URL

---

## Substitution Engine

**Module:** `src/procurement/substitution_engine.py`

Identifies functionally equivalent CPG raw materials using three weighted signals:

| Signal | Weight | Method |
|--------|--------|--------|
| **Name Similarity** | 30% | Jaccard on word tokens + `SequenceMatcher` ratio |
| **BOM Co-occurrence** | 40% | Jaccard similarity on ingredient sets of BOMs that use each material |
| **Functional Category** | 30% | Exact match within 22 curated functional categories |

### Variant Filtering

Different forms/salts of the same base ingredient are **excluded** from substitute results. This prevents suggesting "magnesium oxide" as a substitute for "magnesium citrate" — they are variants, not substitutes.

The filter works by:
1. **Substring check** — if the query is contained in the candidate name or vice versa (e.g., "magnesium" in "magnesium citrate")
2. **Base token comparison** — strip chemical/salt modifiers (oxide, citrate, stearate, glycinate, etc.) and compare remaining tokens. If the base is identical, it's a variant.

Stripped modifiers include: `oxide`, `citrate`, `stearate`, `glycinate`, `lactate`, `carbonate`, `phosphate`, `gluconate`, `sulfate`, `chloride`, `acetate`, `succinate`, `ascorbate`, `palmitate`, `fumarate`, `bisglycinate`, `chelate`, `hydrochloride`, and 10+ more.

### Web Fallback

When no local substitutes are found in the database, the engine automatically searches the web via DuckDuckGo for alternative ingredients. Web results are returned with a lower confidence score (0.35) and tagged with `source: "web_search"` in their evidence dict.

### Functional Categories (22 total)

`emulsifier`, `protein`, `sweetener`, `flow_agent`, `capsule_shell`, `binder_filler`, `coating`, `vitamin_a`, `vitamin_b`, `vitamin_c`, `vitamin_d`, `vitamin_e`, `vitamin_k`, `omega_fatty_acid`, `calcium_source`, `iron_source`, `zinc_source`, `magnesium_source`, `preservative`, `flavoring`, `thickener_gum`, `colouring`

---

## Consolidated Sourcing

**Module:** `src/procurement/consolidated_sourcing.py`

Aggregates demand across all 61 companies for the same or substitutable ingredients and recommends supplier consolidation.

1. **Build Demand Matrix** - Groups ingredients into substitution clusters
2. **Aggregate Demand** - Counts how many BOMs/companies use each ingredient group
3. **Score Suppliers** - Ranks suppliers by coverage of variant groups
4. **Recommend Consolidation** - Identifies consolidation opportunities

---

## Evidence Trail

**Module:** `src/procurement/evidence.py`

Every pipeline result carries an evidence trail tracking reasoning across all stages.

```python
@dataclass
class EvidenceNode:
    stage: str          # "identification", "internal_check", "substitution", etc.
    claim: str          # Human-readable assertion
    source: str         # Where the evidence came from
    confidence: float   # 0.0 - 1.0
    data_ref: dict      # Pointers to underlying data
```

---

## Compliance Engine

**Module:** `src/procurement/ranking.py`

Dual-region compliance scoring with user toggle in the sidebar. **Default region: USA.**

### Ranking Signals

| Signal | Weight | Scoring Method |
|--------|--------|----------------|
| **Quality** | 35% | Quality rating (0-5 scale -> 60 pts) + certification keywords (5 pts each, max 35) |
| **Compliance** | 25% | Region-specific keyword matching (USA or EU) + region bonus |
| **Price** | 25% | Ratio to reference price, or absolute tier scoring |
| **Lead Time** | 15% | Days-based tier scoring (3d=100, 7d=92, 14d=82, etc.) |

### Verdicts

| Verdict | Composite Score |
|---------|----------------|
| `excellent` | >= 78 |
| `good` | >= 62 |
| `possible` | >= 46 |
| `limited` | >= 30 |
| `poor` | < 30 |

### USA Compliance (FDA/GRAS/USP) — Default

27 keywords: `fda`, `fda registered`, `21 cfr`, `gras`, `usp`, `nsf`, `gmp`, `cgmp`, `dshea`, `prop 65`, `usda organic`, `non-gmo`, `kosher`, `halal`, `informed sport`, etc.

### EU Compliance (REACH/RoHS)

18 keywords: `ce`, `reach`, `rohs`, `en 10204`, `din`, `tuv`, `iso 9001`, `iso 14001`, `iatf 16949`, `as9100`, etc.

---

## Supply Intelligence Layers

| Layer | Name | Source |
|-------|------|--------|
| 9 | Alibaba | Dedicated scraper (`AlibabaScraper`) |
| 10 | IndiaMART | Dedicated scraper (`IndiamartScraper`) |

Additional layers (1-8, 11) are available but default to layers 9+10 for CPG ingredient sourcing.

---

## Databases

### 1. CPG Database (`db.sqlite`)
- 61 companies, 876 raw materials, 149 BOMs, 40 suppliers
- Access layer: `src/procurement/cpg_db.py`

### 2. Open Food Facts Local Index (`data/openfoodfacts/off_index.db`)
- 4,450,617 products indexed from Open Food Facts CSV
- Instant barcode lookups via SQLite PRIMARY KEY
- Build script: `data/openfoodfacts/build_off_index.py`

### 3. Internal Procurement (`internal_procurement/`)
- `approved_suppliers.json` - Pre-approved supplier list
- `procurement_q1_2026.csv` - Historical purchase orders

---

## Project Structure

```
SAI/
|-- app_v3.py                          # Streamlit UI (main entry point)
|-- procurement_pipeline.py            # 8-stage pipeline orchestrator
|-- requirements.txt                   # Python dependencies
|-- setup.py                           # One-command setup script
|-- db.sqlite                          # CPG database (Spherecast dataset)
|-- .env.example                       # Environment variable template
|
|-- src/
|   |-- procurement/
|   |   |-- __init__.py                # Public exports
|   |   |-- product_identifier.py      # Stage 1: CPG ingredient matching
|   |   |-- internal_checker.py        # Stage 3: Internal stock check
|   |   |-- supply_intelligence.py     # Stage 4: Supplier gathering
|   |   |-- ranking.py                 # Stage 5: Scoring + compliance
|   |   |-- cpg_db.py                  # CPG SQLite access layer
|   |   |-- substitution_engine.py     # Stage 7: Ingredient substitution
|   |   |-- consolidated_sourcing.py   # Stage 8: Supplier consolidation
|   |   |-- evidence.py                # Evidence trail tracking
|   |   |-- barcode_lookup.py          # Barcode decoding + lookup
|   |
|   |-- agnes/                         # Agnes AI Supply Chain Assistant
|       |-- __init__.py                # Public exports
|       |-- actions.py                 # 3 high-level actions (ingredient/barcode/bottleneck)
|       |-- agent.py                   # Claude-powered conversational agent
|       |-- tools.py                   # 8 agent tool definitions
|       |-- pipeline.py                # 7-step pipeline orchestrator
|       |-- context.py                 # Step 1: Intake & Context Assembly
|       |-- candidates.py              # Step 2: Candidate Generation (5 signals)
|       |-- constraints.py             # Step 3: Constraint Inference
|       |-- evidence_collector.py      # Step 4: Evidence Collection (5 trust tiers)
|       |-- scoring.py                 # Step 5: Feasibility Scoring (4 gates + 4 dims)
|       |-- consolidation.py           # Step 6: Consolidation & Recommendation
|       |-- review.py                  # Step 7: Human Review & Decision Modes
|
|-- data/
|   |-- openfoodfacts/
|   |   |-- off_index.db              # 4.4M product SQLite index
|   |   |-- build_off_index.py        # Index builder script
|   |   |-- en.openfoodfacts.org.products.csv.gz  # Source data (1.2 GB)
|   |-- procurement_records.csv       # Sample procurement data
|
|-- data_collection/
|   |-- marketplace_scrapers/         # B2B marketplace scrapers
|   |   |-- alibaba_scraper.py        # Alibaba.com
|   |   |-- indiamart_scraper.py      # IndiaMART
|   |   |-- thomasnet_scraper.py      # ThomasNet
|   |-- search_engine.py              # DuckDuckGo search wrapper
|   |-- query_expander.py             # Query expansion
|   |-- pdf_harvester.py              # PDF datasheet harvester
|   |-- internal_watcher.py           # File watcher for internal docs
|
|-- internal_procurement/
    |-- approved_suppliers.json        # Approved supplier list
    |-- procurement_q1_2026.csv       # Q1 2026 purchase orders
```

---

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | For API mode | Claude API key for LLM-assisted extraction |

### Sidebar Controls (UI)

| Control | Options | Default |
|---------|---------|---------|
| Compliance Region | USA (FDA/GRAS/USP), EU (REACH/RoHS) | USA |
| Pipeline Mode | offline, api | offline |
| Quality Weight | 0-100 | 35 |
| Compliance Weight | 0-100 | 25 |
| Price Weight | 0-100 | 25 |
| Lead Time Weight | 0-100 | 15 |

---

## API Reference

### Agnes Actions (Recommended)

```python
from src.procurement.cpg_db import CpgDatabase
from src.agnes.actions import analyze_ingredient, analyze_barcode, analyze_bottleneck

db = CpgDatabase("db.sqlite")

# Ingredient analysis — suppliers + substitutes + demand
result = analyze_ingredient("magnesium stearate", db)

# Barcode scan — product + per-ingredient analysis
result = analyze_barcode("3017620422003", db)

# Bottleneck — risk assessment + expanded substitutes + recommendations
result = analyze_bottleneck("soy lecithin", db)
```

### Agnes 7-Step Pipeline

```python
from src.procurement.cpg_db import CpgDatabase
from src.agnes.pipeline import AgnesPipeline

db = CpgDatabase("db.sqlite")
pipeline = AgnesPipeline(db)
result = pipeline.run("soy lecithin", target_market="usa")
```

### Procurement Pipeline

```python
from procurement_pipeline import ProcurementPipeline

pipeline = ProcurementPipeline(mode="offline", compliance_region="usa")
result = pipeline.run("whey protein isolate")

# Autocomplete
suggestions = pipeline.autocomplete("magnesium", limit=10)
```

### Barcode Lookup

```python
from src.procurement.barcode_lookup import lookup_barcode

result = lookup_barcode("3017620422003")  # Nutella
print(result["product_name"])       # "Nutella"
print(result["ingredients_list"])   # ["Sucre", "huile de palme", ...]
```

### Substitution Engine

```python
from src.procurement import CpgDatabase, SubstitutionEngine

db = CpgDatabase("db.sqlite")
engine = SubstitutionEngine(db)
# Variant-filtered: magnesium oxide won't appear for magnesium citrate
subs = engine.find_substitutes("soy lecithin", max_results=10)
```

### Supplier Ranking

```python
from src.procurement.ranking import ProcurementRanker

ranker = ProcurementRanker()
ranked = ranker.rank(
    suppliers,                    # list of supplier dicts
    compliance_region="usa",      # "usa" (default) or "eu"
    weights={                     # optional custom weights
        "quality": 0.35,
        "compliance": 0.25,
        "price": 0.25,
        "lead_time": 0.15,
    },
)
# Each supplier gets: scores, composite_score, verdict, rank
```

---

## CLI Usage

```bash
# Basic query
python procurement_pipeline.py "whey protein isolate"

# With EU compliance
python procurement_pipeline.py "magnesium stearate" --compliance eu

# API mode (requires ANTHROPIC_API_KEY)
python procurement_pipeline.py "soy lecithin" --mode api

# Save results to JSON
python procurement_pipeline.py "vitamin d3" --output results.json
```

### CLI Flags

| Flag | Description |
|------|-------------|
| `--compliance {usa,eu}` | Set compliance region (default: usa) |
| `--mode {offline,api}` | Pipeline mode (default: offline) |
| `--db PATH` | Path to CPG SQLite database |
| `--industry TEXT` | Target industry context |
| `--output PATH` | Save JSON result to file |
| `--top N` | Show top N ranked suppliers (default: 20) |

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `streamlit` | Web UI framework |
| `anthropic` | Claude API client (API mode) |
| `requests` | HTTP requests for web scraping |
| `beautifulsoup4` | HTML parsing for scraping |
| `pyzbar` + `Pillow` | Barcode decoding from images |
| `opencv-python` | Image preprocessing for barcode detection |
| `pytesseract` | OCR fallback for barcode extraction |
| `pandas` | Data processing |
| `ddgs` | DuckDuckGo search API |

---

## License

Open-source project.
