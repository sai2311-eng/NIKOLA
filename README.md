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
obsidian_scraper/              # Obsidian Web Clipper integration
  clipper-config.json          # Clipper configuration
  SUPPLIER_TEMPLATE.md         # Supplier note template
  SCRAPING_SOP.md              # Scraping SOP
  _QUERIES.md                  # Dataview queries
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

## Supplier Scoring (100 points)

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Price | 20 | Price competitiveness |
| Quantity | 15 | MOQ flexibility and capacity |
| Scalability | 20 | Production scaling capability |
| Reliability | 25 | Track record + certification bonus |
| Data Completeness | 10 | How much data is available |
| Triangulation | 10 | Cross-verified across regulatory, first-party, and trade sources |

### Tier Assignment

- **Tier 1 - Primary**: Score >= 70 AND fully triangulated
- **Tier 2 - Backup**: Score >= 50 AND partial triangulation
- **Tier 3 - Conditional**: Score >= 30 OR incomplete data
- **Tier 4 - Reject**: Score < 30 OR critical red flags

## Source Tiers for Supplier Discovery

| Tier | Type | Sources |
|------|------|---------|
| T1 | Regulatory | FDA, ECHA (REACH), CPSC, GS1 |
| T2 | Brand/First-party | Manufacturer websites, Open Food Facts |
| T3 | B2B Marketplace | Alibaba, IndiaMart, ThomasNet, Europages, Made-in-China, GlobalSources |
| T4 | Trade/Customs | ImportYeti, Zauba, Panjiva |
| T5 | Aggregator | Google Shopping, general web |

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

## License

This project is proprietary. All rights reserved.
