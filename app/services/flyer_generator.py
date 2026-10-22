"""
app/services/flyer_generator.py
Flyer & Asset Generator untuk Campaign JuraganAI (PRD FR-07)
Mendukung 3 Opsi Engine:
1. 'svg' (Default Free & Fast): Template SVG deterministik 1080x1080, nol biaya API.
2. 'pollinations' (Free AI Image): Model Flux / SDXL via Pollinations.ai, visual sinematik tanpa biaya API.
3. 'dalle3' (Premium Paid AI): OpenAI DALL-E 3 (membutuhkan OPENAI_API_KEY).
"""

import os
import urllib.parse
from typing import Optional, Dict, Any
import httpx
from app.core.logging import logger
from app.core.config import settings

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "campaigns")

class FlyerGenerator:

    @staticmethod
    def generate_flyer_svg(
        outlet_name: str,
        product_name: str,
        category: str,
        original_price: float,
        discount_pct: Optional[float] = None,
        tagline: str = "PROMO SPESIAL HARI INI",
        cta_text: str = "Order Sekarang via Kasir / GoFood",
        campaign_id: int = 1
    ) -> str:
        """
        Menghasilkan file SVG flyer beresolusi 1080x1080 (Square Instagram Format).
        Mengembalikan path relatif URL untuk diakses via frontend.
        """
        os.makedirs(STATIC_DIR, exist_ok=True)
        filename = f"flyer_campaign_{campaign_id}.svg"
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

  <!-- Background Base -->
  <rect width="1080" height="1080" fill="url(#bgGrad)" />
  <circle cx="540" cy="460" r="480" fill="url(#glow)" />

  <!-- Outer Frame -->
  <rect x="50" y="50" width="980" height="980" rx="32" fill="none" stroke="#3F3F46" stroke-width="2" stroke-dasharray="12 8" opacity="0.6" />

  <!-- Header Section -->
  <text x="540" y="130" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="28" font-weight="700" fill="#E4E4E7" letter-spacing="4" text-anchor="middle">{outlet_name.upper()}</text>
  <rect x="440" y="150" width="200" height="4" rx="2" fill="url(#goldGrad)" />

  {badge_markup}

  <!-- Tagline Badge -->
  <rect x="360" y="210" width="360" height="50" rx="25" fill="#3F3F46" opacity="0.8" />
  <text x="540" y="244" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="22" font-weight="800" fill="#FBBF24" letter-spacing="2" text-anchor="middle">{tagline.upper()}</text>

  <!-- Central Product Card Graphic -->
  <g filter="url(#shadow)">
    <rect x="140" y="290" width="800" height="560" rx="28" fill="url(#cardGrad)" stroke="#78350F" stroke-width="2" />
  </g>

  <!-- Graphic Icon Illustration in Center -->
  <circle cx="540" cy="450" r="110" fill="#78350F" opacity="0.4" />
  <circle cx="540" cy="450" r="85" fill="#451A03" opacity="0.8" stroke="#F59E0B" stroke-width="2" />
  
  <!-- Coffee Cup Icon -->
  <path d="M490 410 h100 v60 a50 50 0 0 1 -100 0 z M590 425 h15 a15 15 0 0 1 0 30 h-15" fill="none" stroke="#FBBF24" stroke-width="7" stroke-linecap="round" stroke-linejoin="round" />
  <path d="M480 500 h120" stroke="#FBBF24" stroke-width="6" stroke-linecap="round" />
  <!-- Steam lines -->
  <path d="M515 390 q-10 -15 0 -30" stroke="#F59E0B" stroke-width="4" stroke-linecap="round" fill="none" />
  <path d="M540 385 q-10 -15 0 -30" stroke="#F59E0B" stroke-width="4" stroke-linecap="round" fill="none" />
  <path d="M565 390 q-10 -15 0 -30" stroke="#F59E0B" stroke-width="4" stroke-linecap="round" fill="none" />

  <!-- Product Name & Category -->
  <text x="540" y="615" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="22" font-weight="600" fill="#D1D5DB" letter-spacing="3" text-anchor="middle">{category.upper()}</text>
  <text x="540" y="675" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="52" font-weight="900" fill="#FFFFFF" text-anchor="middle">{product_name}</text>

  <!-- Price Section -->
  {price_markup}

  <!-- Footer CTA Button -->
  <rect x="240" y="885" width="600" height="80" rx="40" fill="url(#goldGrad)" filter="url(#shadow)" />
  <text x="540" y="935" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="26" font-weight="800" fill="#000000" text-anchor="middle">{cta_text}</text>

  <!-- Simulation Watermark as requested by PRD Sec. 11 -->
  <text x="540" y="1015" font-family="'Plus Jakarta Sans', Arial, sans-serif" font-size="16" font-weight="500" fill="#71717A" text-anchor="middle">⚡ JuraganAI Simulation Asset • 100% Deterministic Guardrails</text>
</svg>
"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(svg_content)

        return f"/static/campaigns/{filename}"

    @classmethod
    async def generate_flyer_ai(
        cls,
        outlet_name: str,
        product_name: str,
        category: str,
        original_price: float,
        discount_pct: Optional[float] = None,
        tagline: str = "PROMO SPESIAL",
        campaign_id: int = 1,
        engine: str = "pollinations"
    ) -> str:
        """
        Menghasilkan flyer visual dengan AI Image Generation Model.
        Opsi Engine:
        - 'pollinations': Free Flux Model via Pollinations.ai (Tanpa API Key / Gratis).
        - 'dalle3': OpenAI DALL-E 3 (Perlu OPENAI_API_KEY).
        - 'svg': Fallback ke SVG template bawaan jika gagal/offline.
        """
        os.makedirs(STATIC_DIR, exist_ok=True)

        # 1. Bangun prompt sinematik F&B
        discount_str = f"with a special {int(discount_pct)}% discount badge" if discount_pct else "special promo"
        ai_prompt = (
            f"Commercial professional advertising food photography of delicious {product_name} ({category}), "
            f"served in a modern cozy cafe setting at '{outlet_name}', {tagline}, {discount_str}, "
            f"warm golden ambient lighting, elegant coffee shop background, rich espresso textures, "
            f"appetizing presentation, high resolution, 8k, award winning commercial ad layout, photorealistic"
        )

        filename_ai = f"flyer_ai_{campaign_id}_{engine}.jpg"
        filepath_ai = os.path.join(STATIC_DIR, filename_ai)

        # ENGINE 1: POLLINATIONS (FREE FLUX)
        if engine == "pollinations":
            try:
                logger.info(f"Membangkitkan gambar AI flyer via Pollinations.ai (Free Flux) untuk '{product_name}'...")
                encoded_prompt = urllib.parse.quote(ai_prompt)
                pollinations_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1080&model=flux&nologo=true&seed=42"

                async with httpx.AsyncClient(timeout=35.0) as client:
                    resp = await client.get(pollinations_url)
                    if resp.status_code == 200 and len(resp.content) > 5000:
                        with open(filepath_ai, "wb") as f:
                            f.write(resp.content)
                        logger.info(f"Flyer AI Pollinations berhasil disimpan di: {filepath_ai}")
                        return f"/static/campaigns/{filename_ai}"
                    else:
                        logger.warning(f"Pollinations mengembalikan status {resp.status_code}, beralih ke fallback SVG.")
            except Exception as e:
                logger.warning(f"Gagal generate image via Pollinations ({e}), menggunakan fallback SVG template.")

        # ENGINE 2: OPENAI DALL-E 3 (PREMIUM PAID)
        elif engine == "dalle3":
            try:
                openai_key = os.getenv("OPENAI_API_KEY")
                if not openai_key:
                    logger.warning("OPENAI_API_KEY tidak ditemukan, fallback ke Pollinations/SVG.")
                else:
                    from openai import AsyncOpenAI
                    logger.info(f"Membangkitkan gambar AI flyer via OpenAI DALL-E 3 untuk '{product_name}'...")
                    client = AsyncOpenAI(api_key=openai_key)
                    result = await client.images.generate(
                        model="dall-e-3",
                        prompt=ai_prompt,
                        size="1024x1024",
                        quality="standard",
                        n=1
                    )
                    image_url = result.data[0].url
                    # Download image bytes
                    async with httpx.AsyncClient(timeout=30.0) as http_client:
                        resp = await http_client.get(image_url)
                        if resp.status_code == 200:
                            with open(filepath_ai, "wb") as f:
                                f.write(resp.content)
                            return f"/static/campaigns/{filename_ai}"
            except Exception as e:
                logger.warning(f"Gagal generate image via DALL-E 3 ({e}), menggunakan fallback SVG.")

        # FALLBACK: Template SVG Deterministik
        return cls.generate_flyer_svg(
            outlet_name=outlet_name,
            product_name=product_name,
            category=category,
            original_price=original_price,
            discount_pct=discount_pct,
            tagline=tagline,
            campaign_id=campaign_id
        )
