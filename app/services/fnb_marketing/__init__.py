"""
app/services/fnb_marketing/__init__.py
F&B Sales-to-Marketing Vertical Package (FR-16).
"""

from app.services.fnb_marketing.tools import register_fnb_tools

# Auto-register certified tools when package is loaded
register_fnb_tools()

__all__ = ["register_fnb_tools"]
