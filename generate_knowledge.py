import csv
import os
from src.procurement.cpg_db import CpgDatabase
from src.procurement.supplier_db import SupplierDatabase

def generate():
    print("Connecting to databases...")
    cpg = CpgDatabase("db.sqlite")
    sup = SupplierDatabase("data/suppliers.db")

    # 1. Get all ingredients
    ingredients = cpg._ingredient_index()
    
    # 2. Get all suppliers
    suppliers = sup.get_all_suppliers()

    output_file = "data/agnes_knowledge_base.txt"
    os.makedirs("data", exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("AGNES PROCUREMENT KNOWLEDGE BASE\n")
        f.write("================================\n\n")

        f.write("--- INGREDIENT LIST ---\n")
        f.write("These are the primary raw materials available in the Spherecast network:\n")
        for ing in ingredients:
            f.write(f"- {ing['ingredient_name']}\n")
        
        f.write("\n--- SUPPLIER DIRECTORY ---\n")
        f.write("Details on qualified suppliers, their pricing, and performance tiers:\n\n")
        
        for s in suppliers:
            f.write(f"SUPPLIER: {s.get('supplier_name')}\n")
            f.write(f"  Product: {s.get('product')}\n")
            f.write(f"  Country: {s.get('country')}\n")
            f.write(f"  Price: {s.get('price_per_unit')} {s.get('currency')} per unit\n")
            f.write(f"  MOQ: {s.get('moq')} {s.get('moq_unit')}\n")
            f.write(f"  Lead Time: {s.get('lead_time_days')} days\n")
            f.write(f"  Tier: {s.get('tier_output')}\n")
            f.write(f"  Final Score: {s.get('final_score')}/100\n")
            f.write(f"  Certifications: {s.get('cert_other') or 'Standard'}\n")
            if s.get('red_flags'):
                f.write(f"  RED FLAGS: {s.get('red_flags')}\n")
            f.write("-" * 30 + "\n")

    print(f"Success! Knowledge base generated at: {output_file}")
    print("Next step: Upload this file to your ElevenLabs Agent's 'Knowledge Base' tab.")

if __name__ == "__main__":
    generate()
