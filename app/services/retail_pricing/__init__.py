"""
app/services/retail_pricing/__init__.py
Retail Used-iPhone Vertical Package.
"""

from app.services.retail_pricing.tools import register_retail_tools

# Auto-register certified tools when package is loaded
register_retail_tools()

__all__ = ["register_retail_tools"]
