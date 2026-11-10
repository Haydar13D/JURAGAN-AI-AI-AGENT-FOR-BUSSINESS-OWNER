"""
app/services/flyer_generator.py
Flyer & Asset Generator untuk Campaign JuraganAI / Kirata (PRD FR-07)
Arsitektur 2-Layer Professional Marketing Flyer:
1. Layer 1: Clean Visual AI Food/Drink Photography Generator:
   - Google AI Studio (Imagen 3: imagen-3.0-generate-002) via GEMINI_API_KEY
   - Pollinations.ai (Flux Model - Free Zero-Cost Generative AI)
   - Clean Local Photography Fallback
2. Layer 2: Pillow High-Resolution Typography & Marketing Compositor (1080x1080):
   - Dynamic Discount Badges (HEMAT X% / DISKON X%)
   - Dynamic Strikethrough Original Price & Striking Promo Price
   - Dynamic Outlet Branding, Headline Tagline, and Call-to-Action (CTA) Button
   - Unique Filename per Run (Zero Browser Caching Issues)
"""

import os
import io
import re
import time
import uuid
import base64
import urllib.parse
from typing import Optional, Dict, Any, Tuple
import httpx
from PIL import Image, ImageDraw, ImageFont

from app.core.logging import logger
from app.core.config import settings

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "campaigns")
FONTS_DIR = "C:/Windows/Fonts"


def is_beverage_item(name: str, category: str = "") -> bool:
    """
    Deterministically checks if an F&B menu item is a drink/beverage.
    Uses regex word boundaries for short words ('tea', 'teh', 'es', 'jus')
    and prioritizes food exclusions to prevent false positives (e.g., 'steak' containing 'tea').
    """
    cat_lower = (category or "").lower().strip()
    name_lower = (name or "").lower().strip()
    text = f"{name_lower} {cat_lower}"
    
    # Priority food exclusions: If it clearly contains food words, it's NOT a drink
    food_exceptions = [
        "steak", "beef", "daging", "ayam", "chicken", "bebek", "duck",
        "sushi", "sashimi", "nigiri", "maki", "roll", "salmon", "tuna",
        "burger", "sandwich", "pizza", "pasta", "spaghetti", "nasi", "rice",
        "bowl", "bakmi", "mie", "noodle", "ramen", "soup", "soto", "gulai",
        "rendang", "seblak", "pempek", "croissant", "roti", "toast", "pancake", "waffle",
        "snack", "cireng", "batagor", "siomay", "dimsum", "kentang", "fries",
        "cake", "tart", "pastry", "gelato", "es krim", "ice cream"
    ]
    if any(re.search(rf"\b{re.escape(f)}", text) for f in food_exceptions):
        return False

    # If category explicitly says food / makanan / cemilan
    if any(re.search(rf"\b{re.escape(c)}\b", cat_lower) for c in ["makanan", "food", "snack", "cemilan", "dessert", "pastry"]):
        return False

    # If category explicitly says drink / minuman
    if any(re.search(rf"\b{re.escape(c)}\b", cat_lower) for c in ["minuman", "drink", "drinks", "beverage", "beverages", "kopi", "coffee", "non-kopi"]):
        return True

    exact_words = ["tea", "teh", "es", "jus", "milk", "soda", "shake"]
    if any(re.search(rf"\b{re.escape(w)}\b", text) for w in exact_words):
        return True

    drink_substrings = [
        "kopi", "coffee", "latte", "macchiato", "cappuccino", "espresso", "americano",
        "matcha", "juice", "boba", "beverage", "syrup", "sirup", "susu", "frappe",
        "smoothie", "mocktail", "cocktail", "cooler", "non-kopi", "minuman", "wedang",
        "lemonade", "yakult"
    ]
    return any(w in text for w in drink_substrings)


def _get_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    """Safely loads TrueType fonts from system or falls back gracefully."""
    candidates = [
        os.path.join(FONTS_DIR, font_name),
        os.path.join(FONTS_DIR, "bahnschrift.ttf"),
        os.path.join(FONTS_DIR, "arialbd.ttf"),
        os.path.join(FONTS_DIR, "segoeuib.ttf"),
        os.path.join(FONTS_DIR, "arial.ttf"),
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


class FlyerGenerator:

    @classmethod
    def build_image_prompt(cls, product_name: str, category: str = "") -> str:
        prod_lower = (product_name or "").lower()
        is_drink = is_beverage_item(product_name, category)
        if is_drink:
            glass_type = "aesthetic tall clear glassware with ice cubes" if any(w in prod_lower for w in ["es", "ice", "jus", "juice", "tea", "teh", "boba", "soda", "cold", "dingin"]) else "ceramic artisan coffee cup with latte art"
            return (
                f"A commercial close-up studio beverage photography of refreshing authentic {product_name}, "
                f"served in an {glass_type} on a cozy wooden cafe table, "
                f"gentle condensation droplets, soft warm ambient lighting, "
                f"bokeh cafe background, photorealistic 8k, appetizing presentation, strictly no text, no watermark, clean composition"
            )
        else:
            if any(w in prod_lower for w in ["sushi", "sashimi", "nigiri", "maki", "roll", "salmon", "tuna"]):
                presentation = "artfully arranged fresh gourmet sushi on a dark slate or wooden sushi geta board with wasabi and pickled ginger"
            elif any(w in prod_lower for w in ["burger", "sandwich"]):
                presentation = "gourmet burger with melted cheese and fresh crisp lettuce on a rustic wooden board"
            elif any(w in prod_lower for w in ["pizza", "pasta", "spaghetti"]):
                presentation = "steaming hot Italian culinary style on an artisan ceramic dish"
            elif any(w in prod_lower for w in ["nasi", "rice", "bowl"]):
                presentation = "served in an artisan ceramic bowl with colorful appetizing garnishes"
            elif any(w in prod_lower for w in ["ayam", "chicken", "bebek", "duck"]):
                presentation = "crispy golden roasted or fried on an artisan plate with dipping sauce"
            elif any(w in prod_lower for w in ["steak", "daging", "beef"]):
                presentation = "seared succulent steak with aromatic herbs on a cast iron skillet or wooden platter"
            elif any(w in prod_lower for w in ["dessert", "cake", "kue", "croissant", "roti"]):
                presentation = "delicate bakery pastry on an elegant dessert plate with powdered sugar dusting"
            else:
                presentation = "beautifully plated on an artisan tableware, appetizing culinary gourmet presentation"

            return (
                f"A commercial close-up studio food photography of delicious authentic {product_name}, "
                f"{presentation}, warm dramatic ambient lighting, rich food textures, depth of field, "
                f"bokeh background, photorealistic 8k, strictly no text, no watermark, clean composition"
            )

    @classmethod
    async def generate_background_gemini_flash_image(
        cls,
        product_name: str,
        category: str,
        api_key: str,
        model: str = "gemini-2.5-flash-image"
    ) -> Optional[Image.Image]:
        """
        Calls Google AI Studio Gemini Flash Image generateContent REST API.
        """
        if not api_key:
            return None

        prompt = cls.build_image_prompt(product_name, category)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{
                "role": "user",
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"]
            }
        }
        try:
            logger.info(f"Mengirim request ke Google AI Studio ({model}) untuk '{product_name}'...")
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for part in parts:
                            if "inlineData" in part and "data" in part["inlineData"]:
                                raw_bytes = base64.b64decode(part["inlineData"]["data"])
                                img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
                                logger.info(f"Google {model} berhasil membangkitkan foto produk!")
                                return img
                elif resp.status_code == 429:
                    logger.warning("Google AI Studio mengembalikan Quota Limit 0 pada akun free tier. Menggunakan fallback AI Flux!")
                else:
                    logger.warning(f"Google AI Studio ({model}) status {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"Error memanggil Google AI Studio API: {e}")
        return None

    @classmethod
    async def generate_background_gemini_imagen(
        cls,
        product_name: str,
        category: str,
        api_key: str,
        model: str = "imagen-3.0-generate-002"
    ) -> Optional[Image.Image]:
        """
        Calls Google AI Studio Imagen 3 REST API to generate photorealistic food background.
        """
        if not api_key:
            return None

        prompt = cls.build_image_prompt(product_name, category)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predict?key={api_key}"
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": "1:1"
            }
        }
        try:
            logger.info(f"Mengirim request ke Google AI Studio Imagen 3 untuk '{product_name}'...")
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
                if resp.status_code == 200:
                    data = resp.json()
                    predictions = data.get("predictions", [])
                    if predictions and "bytesBase64Encoded" in predictions[0]:
                        raw_bytes = base64.b64decode(predictions[0]["bytesBase64Encoded"])
                        img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
                        logger.info("Google Imagen 3 berhasil menghasilkan gambar makanan 1080x1080!")
                        return img
                else:
                    logger.warning(f"Google Imagen 3 status {resp.status_code}: {resp.text[:250]}")
        except Exception as e:
            logger.warning(f"Error memanggil Google Imagen 3 API: {e}")
        return None

    @classmethod
    async def generate_background_pollinations(
        cls,
        product_name: str,
        category: str,
        seed: int
    ) -> Optional[Image.Image]:
        """
        Calls Pollinations.ai (Flux) as zero-cost generative AI image engine.
        """
        clean_prompt = cls.build_image_prompt(product_name, category)
        encoded_prompt = urllib.parse.quote(clean_prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1080&model=flux&nologo=true&seed={seed}"
        try:
            logger.info(f"Mengambil gambar AI visual dari Pollinations Flux (seed={seed}) untuk '{product_name}'...")
            async with httpx.AsyncClient(timeout=35.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.content) > 5000:
                    img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                    return img
        except Exception as e:
            logger.warning(f"Pollinations Flux error ({e}), beralih ke fallback lokal.")
        return None

    @classmethod
    def get_clean_fallback_background(cls, product_name: str, category: str = "") -> Image.Image:
        """
        Returns clean category-aware fallback base image if external APIs are unreachable.
        Strictly separates Drink vs Food to avoid showing food for drinks (e.g. Matcha, Kopi).
        """
        prod_lower = (product_name or "").lower()
        cat_lower = (category or "").lower()
        is_drink = any(w in prod_lower or w in cat_lower for w in [
            "kopi", "coffee", "latte", "macchiato", "cappuccino", "espresso", "americano",
            "matcha", "tea", "teh", "drink", "jus", "juice", "es ", "es_", "boba",
            "beverage", "soda", "syrup", "sirup", "milk", "susu", "frappe", "smoothie",
            "mocktail", "cocktail", "cooler", "non-kopi", "minuman"
        ])

        if is_drink:
            base_kopi = os.path.join(STATIC_DIR, "fnb_kopi_promo_flyer.jpg")
            if os.path.exists(base_kopi):
                try:
                    return Image.open(base_kopi).convert("RGBA")
                except Exception:
                    pass
            # Elegant dark emerald/coffee gradient for drinks
            img = Image.new("RGBA", (1080, 1080), (18, 38, 28, 255) if "matcha" in prod_lower else (32, 22, 16, 255))
            draw = ImageDraw.Draw(img)
            draw.ellipse([(200, 200), (880, 880)], fill=(28, 55, 40, 255) if "matcha" in prod_lower else (48, 32, 22, 255))
            return img
        else:
            if any(w in prod_lower for w in ["sushi", "sashimi", "nigiri"]):
                base_sushi = os.path.join(STATIC_DIR, "fnb_sushi_promo_flyer.jpg")
                if os.path.exists(base_sushi):
                    try:
                        return Image.open(base_sushi).convert("RGBA")
                    except Exception:
                        pass
            base_food = os.path.join(STATIC_DIR, "fnb_food_promo_flyer.jpg")
            if os.path.exists(base_food):
                try:
                    return Image.open(base_food).convert("RGBA")
                except Exception:
                    pass
            # Warm dark amber background for food (burger, rice, etc.)
            img = Image.new("RGBA", (1080, 1080), (36, 20, 14, 255))
            draw = ImageDraw.Draw(img)
            draw.ellipse([(200, 200), (880, 880)], fill=(55, 30, 20, 255))
            return img

    @classmethod
    def composite_marketing_flyer(
        cls,
        bg_image: Image.Image,
        outlet_name: str,
        product_name: str,
        category: str,
        original_price: float,
        discount_pct: Optional[float],
        promo_price: float,
        tagline: str,
        cta_text: str,
        duration_days: int = 7,
        campaign_id: Any = 1,
        engine_label: str = "Google Imagen 3"
    ) -> str:
        """
        Composites professional marketing typography, dynamic discount badges,
        strikethrough original price, promo price, and CTA onto the visual background.
        Saves a unique 1080x1080 JPEG to prevent browser caching.
        """
        os.makedirs(STATIC_DIR, exist_ok=True)
        bg = bg_image.convert("RGBA").resize((1080, 1080))

        # 1. Overlay smooth dark vignettes for high-contrast typography
        overlay = Image.new("RGBA", (1080, 1080), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)

        # Smooth Top gradient (0 - 240px)
        for y in range(240):
            a = int(220 * ((240 - y) / 240) ** 1.3)
            odraw.line([(0, y), (1080, y)], fill=(12, 10, 8, a))

        # Smooth Bottom gradient (580 - 1080px)
        for y in range(580, 1080):
            a = int(245 * ((y - 580) / 500) ** 1.1)
            odraw.line([(0, y), (1080, y)], fill=(8, 6, 5, a))

        final = Image.alpha_composite(bg, overlay).convert("RGB")
        draw = ImageDraw.Draw(final)

        # 2. Typography fonts
        f_outlet = _get_font("segoeui.ttf", 24)
        f_tagline = _get_font("arialbd.ttf", 40)
        f_badge = _get_font("arialbd.ttf", 32)
        f_sub = _get_font("segoeui.ttf", 22)
        f_prod = _get_font("bahnschrift.ttf", 52)
        f_price_strike = _get_font("arial.ttf", 32)
        f_price_big = _get_font("bahnschrift.ttf", 66)
        f_cta = _get_font("arialbd.ttf", 30)

        # 3. Header Section (Outlet Name & Divider)
        out_name = (outlet_name or "KEDAI KOPI SENJA").upper()
        draw.text((540, 48), out_name, font=f_outlet, fill=(220, 220, 220), anchor="mm")
        draw.line([(440, 66), (640, 66)], fill=(245, 158, 11), width=2)

        # 4. Tagline Headline
        tag_text = (tagline or "SPESIAL MAKAN SIANG").upper()
        draw.text((540, 115), tag_text, font=f_tagline, fill=(245, 158, 11), anchor="mm")

        # 5. Discount Badge (Top Right Pill)
        has_discount = discount_pct is not None and discount_pct > 0
        if has_discount:
            disc_label = f"HEMAT {int(discount_pct)}%"
            draw.rounded_rectangle([(760, 42), (1030, 114)], radius=36, fill=(225, 29, 72))
            draw.text((895, 78), disc_label, font=f_badge, fill=(255, 255, 255), anchor="mm")

        # 6. Product Info Section
        cat_text = f"MENU REKOMENDASI • {category.upper()}"
        draw.text((540, 700), cat_text, font=f_sub, fill=(160, 174, 192), anchor="mm")

        # Wrap long product names if necessary
        p_name = product_name or "Menu Spesial"
        if len(p_name) > 30:
            words = p_name.split()
            mid = len(words) // 2
            line1 = " ".join(words[:mid])
            line2 = " ".join(words[mid:])
            draw.text((540, 745), line1, font=f_prod, fill=(255, 255, 255), anchor="mm")
            draw.text((540, 795), line2, font=f_prod, fill=(255, 255, 255), anchor="mm")
            pricing_y = 860
        else:
            draw.text((540, 760), p_name, font=f_prod, fill=(255, 255, 255), anchor="mm")
            pricing_y = 835

        # 7. Pricing Section
        if has_discount and original_price > promo_price:
            orig_text = f"Rp {original_price:,.0f}"
            promo_text = f"Rp {promo_price:,.0f}"
            draw.text((430, pricing_y), orig_text, font=f_price_strike, fill=(156, 163, 175), anchor="rm")
            bbox = draw.textbbox((430, pricing_y), orig_text, font=f_price_strike, anchor="rm")
            draw.line([(bbox[0], pricing_y), (bbox[2], pricing_y)], fill=(239, 68, 68), width=3)
            draw.text((470, pricing_y - 2), promo_text, font=f_price_big, fill=(251, 191, 36), anchor="lm")
        else:
            norm_text = f"Rp {promo_price:,.0f}"
            draw.text((540, pricing_y), norm_text, font=f_price_big, fill=(251, 191, 36), anchor="mm")

        # 8. CTA Button
        cta = cta_text or "Pesan Sekarang via Kasir / GoFood"
        btn_y = pricing_y + 80
        draw.rounded_rectangle([(240, btn_y), (840, btn_y + 76)], radius=38, fill=(245, 158, 11))
        draw.text((540, btn_y + 38), f"👉 {cta}", font=f_cta, fill=(15, 15, 15), anchor="mm")

        # 9. Validity & Guardrail Subtext
        draw.text((540, btn_y + 105), f"⏰ Berlaku {duration_days} Hari ke Depan • Kuota Terbatas", font=f_sub, fill=(156, 163, 175), anchor="mm")

        # 10. Save unique output file
        unique_id = uuid.uuid4().hex[:8]
        filename = f"flyer_mkt_{campaign_id}_{unique_id}.jpg"
        filepath = os.path.join(STATIC_DIR, filename)
        final.save(filepath, format="JPEG", quality=95)
        logger.info(f"Flyer komposit berhasil disimpan: {filepath}")

        return f"/static/campaigns/{filename}"

    @classmethod
    async def generate_dynamic_flyer(
        cls,
        outlet_name: str,
        product_name: str,
        category: str,
        original_price: float,
        discount_pct: Optional[float] = None,
        tagline: str = "PROMO SPESIAL",
        cta_text: str = "Klaim Promo di Kasir",
        duration_days: int = 7,
        campaign_id: Any = 1,
        style: str = "photography"
    ) -> Tuple[str, str]:
        """
        Orchestrates Layer 1 (AI visual generation) and Layer 2 (Pillow Compositor).
        Returns: (flyer_url, engine_label)
        """
        os.makedirs(STATIC_DIR, exist_ok=True)
        promo_price = round(original_price * (1 - (discount_pct / 100.0))) if discount_pct else original_price
        seed = int(time.time() * 1000) % 100000

        # Check if vector SVG is specifically requested
        if style in ["vector_svg", "svg"]:
            svg_url = cls.generate_flyer_svg(
                outlet_name=outlet_name,
                product_name=product_name,
                category=category,
                original_price=original_price,
                discount_pct=discount_pct,
                tagline=tagline,
                cta_text=cta_text,
                campaign_id=campaign_id
            )
            return svg_url, "Vector SVG Minimalist"

        bg_img: Optional[Image.Image] = None
        engine_label = "Studio Photography"

        # 1. Try Google AI Studio (Flash Image / Imagen 3) if API Key is set
        gemini_key = getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY")
        if gemini_key:
            bg_img = await cls.generate_background_gemini_flash_image(
                product_name=product_name,
                category=category,
                api_key=gemini_key
            )
            if not bg_img:
                model = getattr(settings, "GEMINI_IMAGE_MODEL", "imagen-3.0-generate-002")
                bg_img = await cls.generate_background_gemini_imagen(
                    product_name=product_name,
                    category=category,
                    api_key=gemini_key,
                    model=model
                )
            if bg_img:
                engine_label = "Google Gemini (AI Studio)"

        # 2. Try Pollinations Flux if style is AI or Gemini failed / quota exceeded
        if not bg_img and (style in ["ai_flux", "pollinations", "ai"] or not gemini_key or gemini_key):
            bg_img = await cls.generate_background_pollinations(
                product_name=product_name,
                category=category,
                seed=seed
            )
            if bg_img:
                engine_label = "AI Flux Model (Pollinations)"
            if bg_img:
                engine_label = "AI Flux Model (Pollinations)"

        # 3. Clean photography fallback
        if not bg_img:
            bg_img = cls.get_clean_fallback_background(product_name, category)
            engine_label = "Studio Commercial Photography"

        # Layer 2: Composite typography, dynamic prices, and badges
        flyer_url = cls.composite_marketing_flyer(
            bg_image=bg_img,
            outlet_name=outlet_name,
            product_name=product_name,
            category=category,
            original_price=original_price,
            discount_pct=discount_pct,
            promo_price=promo_price,
            tagline=tagline,
            cta_text=cta_text,
            duration_days=duration_days,
            campaign_id=campaign_id,
            engine_label=engine_label
        )

        return flyer_url, engine_label

    @staticmethod
    def generate_flyer_svg(
        outlet_name: str,
        product_name: str,
        category: str,
        original_price: float,
        discount_pct: Optional[float] = None,
        tagline: str = "PROMO SPESIAL HARI INI",
        cta_text: str = "Order Sekarang via Kasir / GoFood",
        campaign_id: Any = 1
    ) -> str:
        """
        Fallback vector SVG generator (PRD FR-07).
        """
        os.makedirs(STATIC_DIR, exist_ok=True)
        filename = f"flyer_campaign_{campaign_id}_{uuid.uuid4().hex[:6]}.svg"
        filepath = os.path.join(STATIC_DIR, filename)

        promo_price = original_price
        has_discount = discount_pct is not None and discount_pct > 0
        if has_discount:
            promo_price = round(original_price * (1 - (discount_pct / 100.0)))

        badge_markup = ""
        price_markup = ""
        if has_discount:
            badge_markup = f"""
            <rect x="730" y="80" width="270" height="70" rx="35" fill="#EF4444" />
            <text x="865" y="125" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="32" font-weight="800" fill="#FFFFFF" text-anchor="middle">DISKON {int(discount_pct)}%</text>
            """
            price_markup = f"""
            <text x="540" y="740" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="28" font-weight="600" fill="#9CA3AF" text-anchor="middle" text-decoration="line-through">Rp {original_price:,.0f}</text>
            <text x="540" y="810" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="58" font-weight="900" fill="#F59E0B" text-anchor="middle">Rp {promo_price:,.0f}</text>
            """
        else:
            price_markup = f"""
            <text x="540" y="780" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="58" font-weight="900" fill="#F59E0B" text-anchor="middle">Rp {original_price:,.0f}</text>
            """

        svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080" width="1080" height="1080">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#18181B" />
      <stop offset="50%" stop-color="#27272A" />
      <stop offset="100%" stop-color="#09090B" />
    </linearGradient>
    <linearGradient id="cardGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#2D1F17" stop-opacity="0.95" />
      <stop offset="100%" stop-color="#1A130F" stop-opacity="0.98" />
    </linearGradient>
    <linearGradient id="goldGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#F59E0B" />
      <stop offset="100%" stop-color="#D97706" />
    </linearGradient>
    <radialGradient id="glow" cx="50%" cy="40%" r="50%">
      <stop offset="0%" stop-color="#D97706" stop-opacity="0.35" />
      <stop offset="100%" stop-color="#000000" stop-opacity="0" />
    </radialGradient>
    <filter id="shadow" x="-10%" y="-10%" width="120%" height="120%">
      <feDropShadow dx="0" dy="16" stdDeviation="24" flood-color="#000000" flood-opacity="0.6" />
    </filter>
  </defs>

  <rect width="1080" height="1080" fill="url(#bgGrad)" />
  <circle cx="540" cy="460" r="480" fill="url(#glow)" />
  <rect x="50" y="50" width="980" height="980" rx="32" fill="none" stroke="#3F3F46" stroke-width="2" stroke-dasharray="12 8" opacity="0.6" />

  <text x="540" y="130" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="28" font-weight="700" fill="#E4E4E7" letter-spacing="4" text-anchor="middle">{(outlet_name or 'OUTLET').upper()}</text>
  <rect x="440" y="150" width="200" height="4" rx="2" fill="url(#goldGrad)" />
  {badge_markup}

  <rect x="340" y="210" width="400" height="50" rx="25" fill="#3F3F46" opacity="0.8" />
  <text x="540" y="244" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="22" font-weight="800" fill="#FBBF24" letter-spacing="2" text-anchor="middle">{(tagline or 'PROMO').upper()}</text>

  <g filter="url(#shadow)">
    <rect x="140" y="290" width="800" height="560" rx="28" fill="url(#cardGrad)" stroke="#78350F" stroke-width="2" />
  </g>

  <text x="540" y="615" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="22" font-weight="600" fill="#D1D5DB" letter-spacing="3" text-anchor="middle">{category.upper()}</text>
  <text x="540" y="675" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="52" font-weight="900" fill="#FFFFFF" text-anchor="middle">{product_name}</text>

  {price_markup}

  <rect x="240" y="885" width="600" height="80" rx="40" fill="url(#goldGrad)" filter="url(#shadow)" />
  <text x="540" y="935" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="26" font-weight="800" fill="#000000" text-anchor="middle">{cta_text}</text>

  <text x="540" y="1015" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="16" font-weight="500" fill="#71717A" text-anchor="middle">⚡ JuraganAI Simulation Asset • 100% Deterministic Guardrails</text>
</svg>
"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(svg_content)

        return f"/static/campaigns/{filename}"
