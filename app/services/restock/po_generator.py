"""Supplier Resolver & Purchase Order Draft Generator Tool.

Matches items to approved supplier masters using fuzzy string matching (rapidfuzz)
with persistent alias table fallback, and generates standardized Indonesian WhatsApp PO text.
"""

from typing import Any, Dict, List, Optional
import urllib.parse
from difflib import SequenceMatcher


def _token_sort_ratio(s1: str, s2: str) -> float:
    """Calculates token sort ratio similarity between 0 and 100 without third-party dependencies."""
    t1 = " ".join(sorted(s1.lower().split()))
    t2 = " ".join(sorted(s2.lower().split()))
    return SequenceMatcher(None, t1, t2).ratio() * 100.0


class SupplierResolver:
    """Fuzzy matches item names to supplier catalogs with persistent alias resolution."""

    def __init__(
        self,
        supplier_catalog: Optional[Dict[str, Dict[str, Any]]] = None,
        alias_table: Optional[Dict[str, str]] = None,
    ):
        """Args:

        supplier_catalog: Dict mapping supplier_id to details:
            {
                "SUPP-01": {"name": "CV Sumber Daging", "phone": "08123456789", "items": ["Beef", "Daging sapi"]},
                "SUPP-02": {"name": "Toko Bahan Roti Berkah", "phone": "08198765432", "items": ["Roti bun (burger)", "Keju slice"]}
            }
        alias_table: Dict mapping normalized raw item string to canonical item name.
        """
        self.supplier_catalog = supplier_catalog or {}
        self.alias_table = alias_table or {}

    def resolve_supplier_for_item(self, item_name: str) -> Optional[Dict[str, Any]]:
        """Finds the best supplier matching an item name."""
        norm_name = item_name.strip().lower()

        # 1. Check persistent alias table
        canonical_name = self.alias_table.get(norm_name, item_name)

        # 2. Match against suppliers
        best_match_supplier = None
        highest_score = 0.0

        for supp_id, supp_info in self.supplier_catalog.items():
            candidate_items = supp_info.get("items", [])
            for cand in candidate_items:
                score = _token_sort_ratio(canonical_name, cand)
                if score > highest_score:
                    highest_score = score
                    best_match_supplier = supp_info
                    best_match_supplier["supplier_id"] = supp_id

        if highest_score >= 70.0 and best_match_supplier:
            return best_match_supplier

        return None


def generate_po_whatsapp_draft(
    supplier_name: str,
    supplier_phone: str,
    items_to_order: List[Dict[str, Any]],
    store_name: str = "Kirata F&B Store",
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Generates formatted WhatsApp PO text and wa.me direct link with length fallback.

    Normalizes phone to 628xxx format (no + sign).
    """
    # 1. Normalize phone: strip spaces, dashes, +
    clean_phone = supplier_phone.strip().replace(" ", "").replace("-", "").replace("+", "")
    if clean_phone.startswith("08"):
        clean_phone = "628" + clean_phone[2:]
    elif clean_phone.startswith("8"):
        clean_phone = "62" + clean_phone

    # 2. Build message text
    lines = [
        f"Halo {supplier_name},",
        f"Saya ingin memesan restock bahan baku untuk *{store_name}*:",
        "",
    ]
    for idx, it in enumerate(items_to_order, 1):
        name = it.get("item_name", "Item")
        qty = it.get("order_qty", it.get("deficit", 10.0))
        unit = it.get("unit", "pcs")
        lines.append(f"{idx}. {name}: *{qty:.0f} {unit}*")

    if notes:
        lines.append(f"\nCatatan: {notes}")
    lines.append("\nMohon konfirmasi ketersediaan dan total tagihannya. Terima kasih!")

    po_text = "\n".join(lines)

    # 3. Encode wa.me URL
    encoded_text = urllib.parse.quote(po_text)
    wa_url = f"https://wa.me/{clean_phone}?text={encoded_text}"

    # 4. Check URL length (fallback if > 1500 chars)
    is_long = len(encoded_text) > 1500
    fallback_codeblock = f"```\n{po_text}\n```" if is_long else None

    return {
        "supplier_name": supplier_name,
        "supplier_phone": clean_phone,
        "po_text": po_text,
        "wa_url": wa_url if not is_long else f"https://wa.me/{clean_phone}",
        "is_long_url": is_long,
        "fallback_codeblock": fallback_codeblock,
        "item_count": len(items_to_order),
        "status": "Draf diserahkan ke owner",
    }
