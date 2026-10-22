"""
app/services/campaign_planner.py
Agent Campaign Planner & Copywriting Engine (PRD JuraganAI FR-06 & FR-07)
- Membaca bukti evidence dari Opportunity Detector
- Memanggil LLM (AtmoRouter / DeepSeek) dengan instruksi F&B Growth Marketer
- Mengeluarkan draf strategi, copywriting Instagram, dan batasan diskon aman
- Merender flyer banner grafis melalui FlyerGenerator
- Menyimpan campaign dengan status WAITING_APPROVAL dan audit timeline
"""

import json
import re
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.logging import logger
from app.models.campaign import Opportunity, Campaign, AgentEvent
from app.models.outlet import Outlet
from app.services.deepseek_agent import DeepSeekAgentService
from app.services.flyer_generator import FlyerGenerator

CAMPAIGN_PLANNER_SYSTEM_PROMPT = """Kamu adalah Senior F&B Growth Marketer & Content Specialist untuk JuraganAI.
Tugasmu adalah merumuskan proposal kampanye promosi Instagram yang tajam, actionable, dan menguntungkan berdasarkan bukti data (evidence) penjualan nyata.

ATURAN BISNIS & GUARDRAILS KETAT (WAJIB DIPATUHI):
1. JANGAN PERNAH mengarang data penjualan di luar evidence yang diberikan.
2. ATURAN DISKON:
   - Jika Margin Produk >= 50%: Boleh usulkan diskon 10% s/d 20% (JANGAN melebihi 20%).
   - Jika Margin Produk 30% - 49%: Usulkan diskon maksimal 10% atau promo bundling (tanpa diskon besar).
   - Jika Margin Produk < 30%: DILARANG memberikan diskon! Gunakan strategi penonjolan rasa/kualitas (exposure).
3. FORMAT COPYWRITING INSTAGRAM:
   - Hook menarik di baris pertama.
   - Cerita singkat atau alasan kenapa menu ini wajib dicoba.
   - Penawaran promo jelas (termasuk jam berlaku, misal: 'Spesial jam ngantuk 14:00 - 17:00').
   - Call to Action (CTA) ramah mengajak datang ke outlet atau pesan via online.
   - 4-6 hashtag relevan (#KopiJakarta #PromoKopi dsb).
4. OUTPUT WAJIB BERUPA VALID JSON MURNI TANPA MARKDOWN BACKTICKS (```json ... ```):
{
  "objective": "Target terukur kampanye (maksimal 1 kalimat)",
  "brief": "Ringkasan strategi promosi dan target jam/audiens (2-3 kalimat)",
  "tagline": "Tagline singkat untuk flyer (3-5 kata, contoh: SPESIAL AFTERNOON TREAT)",
  "caption": "Copywriting lengkap Instagram siap publish beserta emoji & hashtags",
  "discount_pct": 15.0,
  "cta_text": "Teks tombol CTA pada flyer (contoh: Klaim Promo di Kasir)"
}"""

class CampaignPlannerService:

    @classmethod
    async def plan_campaign_for_opportunity(
        cls,
        session: AsyncSession,
        opportunity_id: int
    ) -> Optional[Campaign]:
        """
        Merumuskan Campaign dari Opportunity yang sudah terdeteksi.
        """
        # 1. Ambil Opportunity
        stmt = select(Opportunity).where(Opportunity.id == opportunity_id)
        res = await session.execute(stmt)
        opp = res.scalar_one_or_none()
        if not opp:
            logger.error(f"Opportunity ID {opportunity_id} tidak ditemukan.")
            return None

        # Ambil Outlet info
        outlet_stmt = select(Outlet).where(Outlet.id == opp.outlet_id)
        outlet_res = await session.execute(outlet_stmt)
        outlet = outlet_res.scalar_one_or_none()
        outlet_name = outlet.name if outlet else "Coffee Shop"

        evidence = json.loads(opp.evidence_json) if isinstance(opp.evidence_json, str) else opp.evidence_json

        # 2. Susun prompt untuk LLM
        prompt_content = f"""Berdasarkan temuan Opportunity berikut:
- Outlet: {outlet_name}
- Menu: {opp.product_name}
- Sinyal: {opp.signal}
- Harga Jual: Rp{evidence.get('price', 0):,.0f}
- HPP / Cost: Rp{evidence.get('cost', 0):,.0f}
- Margin: {evidence.get('margin_pct', 0)}%
- Sisa Stok: {evidence.get('stock_available', 0)}
- Bukti Data: {evidence.get('explanation', '')}

Tolong buatkan proposal strategi promosi Instagram sesuai dengan aturan diskon dan format JSON yang ditentukan."""

        messages = [
            {"role": "system", "content": CAMPAIGN_PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt_content}
        ]

        # 3. Panggil LLM dengan cascading fallback
        providers = DeepSeekAgentService._get_active_providers()
        llm_response_text = ""
        used_provider_name = ""

        for prov in providers:
            try:
                logger.info(f"Mengirim permintaan Campaign Planning ke {prov['name']} ({prov['model']})...")
                completion = await prov["client"].chat.completions.create(
                    model=prov["model"],
                    messages=messages,
                    temperature=0.4,
                    max_tokens=1000
                )
                llm_response_text = completion.choices[0].message.content or ""
                used_provider_name = prov["name"]
                if llm_response_text.strip():
                    break
            except Exception as e:
                logger.warning(f"Gagal memanggil provider {prov['name']}: {e}. Mencoba fallback berikutnya...")

        # Bersihkan response jika ada markdown fences atau teks pengantar
        json_match = re.search(r"\{.*\}", llm_response_text, re.DOTALL)
        clean_json_str = json_match.group(0).strip() if json_match else llm_response_text.strip()

        # Parse JSON atau fallback aman
        try:
            plan_data = json.loads(clean_json_str)
        except Exception as e:
            logger.error(f"Gagal parse JSON LLM ({e}), menggunakan fallback terstruktur.")
            discount_val = 15.0 if evidence.get('margin_pct', 0) >= 50 else 0.0
            plan_data = {
                "objective": f"Memulihkan penjualan {opp.product_name} dan memaksimalkan margin profit.",
                "brief": f"Menggelar promosi terbatas untuk menggenjot kembali penjualan {opp.product_name} pada jam santai sore.",
                "tagline": "AFTERNOON COFFEE TREAT",
                "caption": f"✨ Butuh mood booster sore ini? Nikmati manis lembutnya {opp.product_name} di {outlet_name}! Dibuat dari biji kopi pilihan dan saus karamel premium. Yuk mampir atau pesan sekarang sebelum kehabisan! ☕\n\n#KopiSore #{opp.product_name.replace(' ', '')} #{outlet_name.replace(' ', '')}",
                "discount_pct": discount_val,
                "cta_text": "Tunjukkan Postingan Ini di Kasir"
            }

        # 4. Generate Visual Flyer Asset (SVG atau AI Image)
        import os
        original_price = float(evidence.get('price', 30000))
        discount_pct = float(plan_data.get("discount_pct", 0.0) or 0.0)
        category_name = str(evidence.get("category", "Menu"))

        engine_choice = os.getenv("FLYER_IMAGE_ENGINE", "svg").lower()
        if engine_choice in ["pollinations", "dalle3"]:
            flyer_url = await FlyerGenerator.generate_flyer_ai(
                outlet_name=outlet_name,
                product_name=opp.product_name,
                category=category_name,
                original_price=original_price,
                discount_pct=discount_pct if discount_pct > 0 else None,
                tagline=str(plan_data.get("tagline", "PROMO SPESIAL")),
                campaign_id=opp.id,
                engine=engine_choice
            )
        else:
            flyer_url = FlyerGenerator.generate_flyer_svg(
                outlet_name=outlet_name,
                product_name=opp.product_name,
                category=category_name,
                original_price=original_price,
                discount_pct=discount_pct if discount_pct > 0 else None,
                tagline=str(plan_data.get("tagline", "PROMO SPESIAL")),
                cta_text=str(plan_data.get("cta_text", "Pesan Sekarang")),
                campaign_id=opp.id
            )

        # 5. Simpan Campaign ke Database
        campaign = Campaign(
            outlet_id=opp.outlet_id,
            opportunity_id=opp.id,
            product_name=opp.product_name,
            objective=str(plan_data.get("objective", "")),
            brief=str(plan_data.get("brief", "")),
            caption=str(plan_data.get("caption", "")),
            media_url=flyer_url,
            discount_pct=discount_pct if discount_pct > 0 else None,
            version=1,
            status="WAITING_APPROVAL"
        )
        session.add(campaign)
        await session.flush()

        # Update status Opportunity
        opp.status = "CAMPAIGN_CREATED"
        session.add(opp)

        # 6. Catat Timeline Event (Audit Trail)
        event1 = AgentEvent(
            outlet_id=opp.outlet_id,
            campaign_id=campaign.id,
            state="DETECTED",
            event_type="OPPORTUNITY_DETECTED",
            payload=json.dumps({"signal": opp.signal, "score": opp.score, "evidence": evidence}, ensure_ascii=False)
        )
        event2 = AgentEvent(
            outlet_id=opp.outlet_id,
            campaign_id=campaign.id,
            state="WAITING_APPROVAL",
            event_type="CAMPAIGN_PROPOSED",
            payload=json.dumps({"provider": used_provider_name, "version": 1, "discount_pct": discount_pct}, ensure_ascii=False)
        )
        session.add(event1)
        session.add(event2)

        await session.commit()
        await session.refresh(campaign)
        logger.info(f"Campaign Proposal ID={campaign.id} berhasil dibuat dengan status WAITING_APPROVAL.")
        return campaign
