"""
tests/test_content_grounding.py
T22 / I20 — Menguji validator grounding angka, produk, panjang caption, dan emoji:
(a) Angka karangan ("diskon 50%" saat payload 15%) -> ditolak ContentGroundingError
(b) Produk asing yang tidak ada di payload -> ditolak ContentGroundingError
(d) Caption > 2200 karakter -> ditolak ContentGroundingError
"""

import pytest

from app.services.content_studio import (
    ContentGroundingError,
    ContentVariantDraft,
    validate_content_variant,
)

BASE_PAYLOAD = {
    "campaign_id": 101,
    "product_name": "Kopi Susu Gula Aren",
    "discount_pct": 15,
    "promo_price_idr": 25000,
    "duration_days": 3,
}


def test_fabricated_number_rejected_by_grounding_validator():
    variant = ContentVariantDraft(
        variant_index=1,
        style="hard_sell",
        caption="Promo gila diskon 50% untuk Kopi Susu Gula Aren hari ini!",
        hashtags=["#kopisusu", "#promo", "#ngopi"],
        cta="Pesan sekarang!",
    )
    with pytest.raises(ContentGroundingError, match="50"):
        validate_content_variant(variant, BASE_PAYLOAD)


def test_unknown_product_rejected_by_grounding_validator():
    variant = ContentVariantDraft(
        variant_index=2,
        style="soft_relatable",
        caption="Lagi pengen Croissant Cokelat? Nikmati promo 15% Kopi Susu Gula Aren.",
        hashtags=["#kopisusu", "#ngopipagi", "#promo"],
        cta="Yuk mampir!",
    )
    with pytest.raises(ContentGroundingError, match="Croissant Cokelat"):
        validate_content_variant(
            variant,
            BASE_PAYLOAD,
            known_catalog_products=["Kopi Susu Gula Aren", "Croissant Cokelat", "Matcha Latte"],
        )


def test_caption_over_2200_chars_and_too_many_emojis_rejected():
    too_long = ContentVariantDraft(
        variant_index=1,
        style="hard_sell",
        caption="Kopi Susu Gula Aren " + ("a" * 2200),
        hashtags=["#kopi", "#promo", "#umkm"],
        cta="Beli sekarang",
    )
    with pytest.raises(ContentGroundingError, match="2200"):
        validate_content_variant(too_long, BASE_PAYLOAD)

    too_many_emojis = ContentVariantDraft(
        variant_index=1,
        style="humor",
        caption="Ngopi dulu 🔥😍🎉🚀 biar melek!",
        hashtags=["#kopi", "#promo", "#umkm"],
        cta="Gas!",
    )
    with pytest.raises(ContentGroundingError, match="emoji"):
        validate_content_variant(too_many_emojis, BASE_PAYLOAD)


def test_valid_formatted_indonesian_numbers_pass_grounding():
    variant = ContentVariantDraft(
        variant_index=1,
        style="hard_sell",
        caption="Kopi Susu Gula Aren cuma Rp25.000 dengan diskon 15% selama 3 hari! ☕",
        hashtags=["#kopisusu", "#promo", "#diskon"],
        cta="Order sekarang!",
    )
    validate_content_variant(
        variant,
        BASE_PAYLOAD,
        known_catalog_products=["Kopi Susu Gula Aren", "Matcha Latte"],
    )
