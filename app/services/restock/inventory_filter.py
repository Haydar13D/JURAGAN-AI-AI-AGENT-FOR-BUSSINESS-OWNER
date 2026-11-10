"""Inventory Filter & Safety Stock Evaluator Tool.

Filters items that fall below safety stock thresholds,
flagging items that urgently require reordering.
"""

from typing import Any, Dict, List, Optional


def filter_low_stock_items(
    items: List[Dict[str, Any]],
    default_safety_stock: float = 100.0,
    safety_stock_overrides: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Filters items whose current_stock is <= safety_stock threshold.

    Args:
        items: List of dicts containing 'item_id', 'item_name', 'current_stock', 'unit'.
        default_safety_stock: Fallback threshold if not specified per item.
        safety_stock_overrides: Dict mapping item_id or item_name to specific safety thresholds.

    Returns:
        Dict containing:
            - 'low_stock_items': List of items that need restocking.
            - 'healthy_items': List of items with sufficient stock.
            - 'total_items': Count of all evaluated items.
            - 'needs_reorder_count': Count of low stock items.
    """
    safety_stock_overrides = safety_stock_overrides or {}
    low_stock = []
    healthy = []

    for item in items:
        item_id = str(item.get("item_id", ""))
        item_name = str(item.get("item_name", ""))
        curr_stock = float(item.get("current_stock", 0.0))

        # Check threshold
        threshold = safety_stock_overrides.get(
            item_id, safety_stock_overrides.get(item_name, default_safety_stock)
        )

        item_enriched = dict(item)
        item_enriched["safety_stock"] = threshold
        item_enriched["deficit"] = max(0.0, threshold - curr_stock)

        if curr_stock <= threshold:
            item_enriched["reorder_needed"] = True
            low_stock.append(item_enriched)
        else:
            item_enriched["reorder_needed"] = False
            healthy.append(item_enriched)

    return {
        "low_stock_items": low_stock,
        "healthy_items": healthy,
        "total_items": len(items),
        "needs_reorder_count": len(low_stock),
    }

