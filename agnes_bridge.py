from fastapi import FastAPI, Query
from pydantic import BaseModel
from src.procurement.cpg_db import CpgDatabase
from src.procurement.supplier_db import SupplierDatabase
import uvicorn

app = FastAPI(title="Agnes Database Bridge")

# Initialize databases
cpg_db = CpgDatabase("db.sqlite")
sup_db = SupplierDatabase("data/suppliers.db")

@app.get("/search_ingredients")
async def search_ingredients(q: str = Query(..., description="The ingredient name to search for")):
    """Search for ingredients and their associated products in db.sqlite."""
    results = cpg_db.search_ingredients(q, limit=5)
    return {"results": results}

@app.get("/get_suppliers")
async def get_suppliers(q: str = Query(..., description="The ingredient or supplier name")):
    """Search for supplier details, pricing, and rankings in suppliers.db."""
    results = sup_db.search_suppliers(q)
    # Return top 5 most relevant
    return {"suppliers": results[:5]}

@app.get("/get_stats")
async def get_stats():
    """Get high-level procurement analytics."""
    return sup_db.get_stats()

if __name__ == "__main__":
    print("🚀 Agnes Bridge starting on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
