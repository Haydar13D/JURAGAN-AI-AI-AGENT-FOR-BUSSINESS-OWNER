"""
app/services/inventory_pack/__init__.py
Inventory Tool Pack (restock & supplier PO drafts).
"""

from app.services.inventory_pack.tools import register_inventory_tools

# Auto-register certified tools when package is loaded
register_inventory_tools()

__all__ = ["register_inventory_tools"]

