"""
One-command setup script.
Run: python setup.py
"""
import subprocess
import sys
import os
from pathlib import Path


def run(cmd: str, desc: str):
    print(f"\n  {desc}...")
    result = subprocess.run(cmd, shell=True, capture_output=False)
    if result.returncode != 0:
        print(f"  Warning: '{cmd}' exited with code {result.returncode}")
    else:
        print(f"  Done")
    return result.returncode == 0


def main():
    print("=" * 60)
    print("  SAI — CPG Procurement Intelligence Setup")
    print("=" * 60)

    root = Path(__file__).parent

    # 1. Install dependencies
    run(f"{sys.executable} -m pip install -r requirements.txt --quiet", "Installing dependencies")

    # 2. Check CPG database exists
    db_path = root / "db.sqlite"
    if db_path.exists():
        print(f"\n  CPG database found: {db_path}")
    else:
        print(f"\n  WARNING: db.sqlite not found — place it in {root}")

    # 3. Check Open Food Facts index
    off_db = root / "data" / "openfoodfacts" / "off_index.db"
    if off_db.exists():
        print(f"  Open Food Facts index found: {off_db}")
    else:
        off_gz = root / "data" / "openfoodfacts" / "en.openfoodfacts.org.products.csv.gz"
        if off_gz.exists():
            print(f"  Building Open Food Facts index from {off_gz}...")
            run(f"{sys.executable} data/openfoodfacts/build_off_index.py",
                "Building barcode lookup index (4.4M products)")
        else:
            print("  Open Food Facts data not found — barcode scanner will use online API only")

    print("\n" + "=" * 60)
    print("  Setup complete!")
    print("=" * 60)
    print()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  Note: ANTHROPIC_API_KEY not set (only needed for API mode).")
        print("  Set it: set ANTHROPIC_API_KEY=your_key_here")
        print()

    print("  Start the UI:     streamlit run app_v3.py")
    print("  Test pipeline:    python procurement_pipeline.py \"whey protein isolate\"")
    print()


if __name__ == "__main__":
    main()
