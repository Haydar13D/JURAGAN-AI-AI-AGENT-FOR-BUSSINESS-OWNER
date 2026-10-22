"""
app/services/proposal_generator.py
AI-powered Natural Language to Typed Graph Patch Proposer for Kirata AI Workflow Builder.
Uses DeepSeek / AtmoRouter with strict JSON mode to emit validated patch proposals.
"""

import json
import re
from typing import Dict, List, Any, Optional, Tuple
from app.core.logging import logger
from app.services.deepseek_agent import DeepSeekAgentService
from app.schemas.workflow import (
    WorkflowDefinition,
    WorkflowProposal,
    PatchOperation,
    PatchOpType,
    RiskLevel
)
from app.services.patch_engine import PatchEngine
from app.services.workflow_validator import WorkflowValidator
from app.core.tool_registry import ToolRegistry


class ProposalGenerator:
    """
    Translates user requirements in natural language into a validated WorkflowProposal.
    """

    @classmethod
    async def generate_proposal(
        cls,
        base_workflow: WorkflowDefinition,
        user_prompt: str
    ) -> WorkflowProposal:
        """
        Main entry point: Calls LLM with schema guidance, applies patch, and validates DAG.
        """
        proposed_version = base_workflow.version + 1
        providers = DeepSeekAgentService._get_active_providers()

        llm_response_json = None

        if providers:
            # Query LLM
            llm_response_json = await cls._query_llm_for_patch(base_workflow, user_prompt, providers)

        # Fallback to intelligent rule matcher if LLM unavailable or failed
        if not llm_response_json:
            llm_response_json = cls._rule_based_patch_parser(base_workflow, user_prompt)

        # Parse patch operations
        rationale = llm_response_json.get("rationale", "Penyesuaian konfigurasi alur kerja berdasarkan permintaan pengguna.")
        risk_level_str = llm_response_json.get("risk_level", "LOW")
        risk_level = RiskLevel.MEDIUM if risk_level_str == "MEDIUM" else (RiskLevel.HIGH if risk_level_str == "HIGH" else RiskLevel.LOW)
        requires_clarification = llm_response_json.get("requires_clarification", False)
        clarification_msg = llm_response_json.get("clarification_message")

        raw_ops = llm_response_json.get("patch_operations", [])
        parsed_ops: List[PatchOperation] = []

        for rop in raw_ops:
            try:
                parsed_ops.append(PatchOperation.model_validate(rop))
            except Exception as e:
                logger.warning(f"Failed to parse patch operation {rop}: {e}")

        # If user asks for unsupported connectors or arbitrary things
        if requires_clarification or not parsed_ops:
            return WorkflowProposal(
                base_version=base_workflow.version,
                proposed_version=proposed_version,
                rationale=rationale,
                risk_level=risk_level,
                patch_operations=[],
                diff=None,
                is_valid=False,
                validation_errors=[],
                requires_clarification=True,
                clarification_message=clarification_msg or (
                    "Permintaan Anda belum dapat dipetakan ke operasi alur kerja yang valid. "
                    "Mohon jelaskan secara spesifik nilai margin (misal: '18%'), overhead, atau kriteria review."
                )
            )

        # Apply patch via PatchEngine
        candidate_wf, visual_diff = PatchEngine.apply_patch(
            base_workflow=base_workflow,
            operations=parsed_ops,
            new_version=proposed_version,
            rationale=rationale
        )

        # Validate candidate workflow
        val_result = WorkflowValidator.validate_workflow(candidate_wf, enforce_tool_allowlist=True)

        return WorkflowProposal(
            base_version=base_workflow.version,
            proposed_version=proposed_version,
            rationale=rationale,
            risk_level=risk_level,
            patch_operations=parsed_ops,
            diff=visual_diff,
            is_valid=val_result.is_valid,
            validation_errors=val_result.errors,
            requires_clarification=False,
            clarification_message=None
        )

    @classmethod
    async def _query_llm_for_patch(
        cls,
        base_workflow: WorkflowDefinition,
        user_prompt: str,
        providers: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        system_prompt = f"""Anda adalah 'Kirata AI Workflow Patch Compiler'.
Tugas Anda adalah menerjemahkan instruksi pengguna dalam Bahasa Indonesia menjadi TYPED PATCH OPERATIONS JSON terhadap WorkflowDefinition kanonikal.

ATURAN STRICT (P0 GUARDRAILS):
1. DILARANG menghasilkan kode Python atau kode eksekusi bebas!
2. Hanya gunakan operasi patch terstruktur:
   - UPDATE_NODE_CONFIG: perbarui parameter node (contoh: target_gross_margin, overhead_fee, rounding_increment, flag_low_battery_under).
   - INSERT_NODE: tambah node baru HANYA dari allowlist ToolRegistry yang ada.
   - REMOVE_NODE: hapus node.
   - ADD_EDGE / REMOVE_EDGE: sambungkan atau putuskan edge.
3. KETENTUAN FINANSIAL RETAIL IPHONE:
   - Margin target dinyatakan dalam desimal (contoh: 15% -> 0.15, 18% -> 0.18, 20% -> 0.20).
   - Biaya overhead dinyatakan dalam integer rupiah (contoh: Rp50.000 -> 50000).
   - Jangan pernah tertukar antara Margin vs Markup.
4. JIKA PERMINTAAN TIDAK DIDUKUNG / DI LUAR CAPABILITY:
   - Contoh: scraper marketplace live, integrasi Instagram otomatis, ganti OS.
   - Set "requires_clarification": true dan jelaskan di "clarification_message" bahwa kapabilitas tersebut tidak didukung pada versi MVP ini.
5. FORMAT OUTPUT WAJIB JSON VALID TANPA MARKDOWN:
{{
  "rationale": "Penjelasan ringkas dalam Bahasa Indonesia...",
  "risk_level": "LOW" | "MEDIUM" | "HIGH",
  "patch_operations": [
    {{
      "op": "UPDATE_NODE_CONFIG",
      "node_id": "calculate_pricing",
      "config_patch": {{
        "target_gross_margin": 0.18
      }}
    }}
  ],
  "requires_clarification": false,
  "clarification_message": null
}}"""

        user_content = f"""ALUR KERJA SAAT INI (Versi {base_workflow.version}):
Daftar Node:
{json.dumps([{'id': n.id, 'tool_type': n.tool_type, 'config': n.config} for n in base_workflow.nodes], indent=2)}

INSTRUKSI OWNER:
"{user_prompt}"

Keluarkan TYPED PATCH JSON sekarang:"""

        for prov in providers:
            try:
                client = prov["client"]
                model_name = prov["model"]
                resp = await client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=0.1
                )
                raw_text = resp.choices[0].message.content.strip()
                parsed = cls._extract_json_object(raw_text)
                if parsed:
                    return parsed
            except Exception as e:
                logger.warning(f"Provider {prov['name']} failed in ProposalGenerator: {e}")
                continue
        return None

    @classmethod
    def _extract_json_object(cls, text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
            text = text.strip()
        # Direct parse attempt
        try:
            return json.loads(text)
        except Exception:
            pass

        # Find matching curly braces { ... }
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if match:
            candidate = match.group(1).strip()
            # Try raw_decode to ignore trailing text
            try:
                decoder = json.JSONDecoder()
                obj, _ = decoder.raw_decode(candidate)
                return obj
            except Exception:
                pass
        return None

    @classmethod
    def _rule_based_patch_parser(
        cls,
        base_workflow: WorkflowDefinition,
        user_prompt: str
    ) -> Dict[str, Any]:
        """
        Deterministic parser supporting standard retail pricing requests.
        Ensures high reliability even when offline or in automated test suites.
        """
        s = user_prompt.lower()
        patch_ops = []
        calc_patch = {}
        review_patch = {}
        rationale_parts = []

        # Template-specific parsing
        is_fnb = base_workflow.template_id == "tpl_fnb_sales_to_marketing_v1"

        if is_fnb:
            # F&B specific instructions
            fnb_plan_patch = {}
            fnb_detect_patch = {}

            # 1. Discount percentage (e.g. "diskon 20%", "diskon promo jadi 10%")
            disc_match = re.search(r"diskon\s*(?:menjadi|ke|jadi|=)?\s*(\d+(?:\.\d+)?)\s*%", s)
            if disc_match:
                disc_val = float(disc_match.group(1))
                fnb_plan_patch["default_discount_pct"] = disc_val
                rationale_parts.append(f"mengubah diskon promo menjadi {disc_val}%")

            # 2. Drop threshold (e.g. "penurunan minimal 30%", "drop 35%")
            drop_match = re.search(r"(?:penurunan|drop)\s*(?:minimal|ke|jadi|=)?\s*(\d+(?:\.\d+)?)\s*%", s)
            if drop_match:
                drop_val = float(drop_match.group(1))
                fnb_detect_patch["min_drop_percentage"] = drop_val
                rationale_parts.append(f"menyesuaikan ambang batas penurunan penjualan menjadi {drop_val}%")

            # 3. Duration days (e.g. "durasi 10 hari", "aktif 14 hari")
            dur_match = re.search(r"(?:durasi|aktif|masa berlaku)\s*(?:menjadi|ke|jadi|=)?\s*(\d+)\s*hari", s)
            if dur_match:
                dur_val = int(dur_match.group(1))
                fnb_plan_patch["promo_duration_days"] = dur_val
                rationale_parts.append(f"mengubah durasi promo menjadi {dur_val} hari")

            # Unsupported check for F&B (e.g. live payment settlement, auto-buying)
            if any(unsupported in s for unsupported in ["marketplace", "tokopedia", "shopee", "scraping", "payment gateway"]):
                return {
                    "rationale": "Permintaan integrasi marketplace atau payment gateway di luar kapabilitas F&B template.",
                    "risk_level": "HIGH",
                    "patch_operations": [],
                    "requires_clarification": True,
                    "clarification_message": "Template F&B Sales-to-Marketing berfokus pada deteksi anomali POS, pembuatan promo flyer, dan publikasi Instagram."
                }

            if fnb_plan_patch:
                patch_ops.append({
                    "op": "UPDATE_NODE_CONFIG",
                    "node_id": "plan_campaign",
                    "config_patch": fnb_plan_patch
                })
            if fnb_detect_patch:
                patch_ops.append({
                    "op": "UPDATE_NODE_CONFIG",
                    "node_id": "detect_opportunities",
                    "config_patch": fnb_detect_patch
                })

        else:
            # Retail iPhone specific instructions
            # 1. Margin detection (e.g. "margin 18%", "margin 20%", "naikkan margin jadi 12%")
            margin_match = re.search(r"margin\s*(?:menjadi|ke|jadi|=)?\s*(\d+(?:\.\d+)?)\s*%", s)
            if margin_match:
                margin_pct = float(margin_match.group(1))
                calc_patch["target_gross_margin"] = round(margin_pct / 100.0, 4)
                rationale_parts.append(f"mengubah target gross margin menjadi {margin_pct}%")

            # 2. Overhead fee detection (e.g. "overhead 75rb", "overhead Rp75.000", "biaya packing 50000")
            overhead_match = re.search(r"(?:overhead|packing|biaya)\s*(?:menjadi|ke|jadi|=)?\s*(?:rp\.?)?\s*(\d+)(?:\.000|000|rb)?", s)
            if overhead_match:
                raw_val = int(overhead_match.group(1))
                if raw_val < 1000 and ("rb" in s or "000" in s):
                    raw_val *= 1000
                calc_patch["overhead_fee"] = raw_val
                rationale_parts.append(f"menyesuaikan biaya overhead menjadi Rp{raw_val:,}")

            # 3. Battery health flag detection (e.g. "baterai dibawah 85%", "batre < 75%")
            battery_match = re.search(r"(?:baterai|batre|battery)\s*(?:dibawah|<|kurang dari)?\s*(\d+)\s*%", s)
            if battery_match:
                bat_val = int(battery_match.group(1))
                review_patch["flag_low_battery_under"] = bat_val
                rationale_parts.append(f"menandai unit dengan baterai di bawah {bat_val}% untuk review")

            # 4. Check for unsupported capabilities for Retail iPhone (PRD Acceptance Scenario 3)
            if any(unsupported in s for unsupported in ["marketplace", "tokopedia", "shopee", "scraping", "instagram", "auto post"]):
                return {
                    "rationale": "Permintaan konektor marketplace live / auto-post belum didukung.",
                    "risk_level": "HIGH",
                    "patch_operations": [],
                    "requires_clarification": True,
                    "clarification_message": (
                        "Konektor marketplace live atau publikasi otomatis belum didukung pada versi MVP ini demi kepatuhan "
                        "dan keamanan data. Saat ini sistem berfokus pada pemrosesan daftar vendor CSV dan pencocokan harga pasar terkurasi."
                    )
                }

            if calc_patch:
                patch_ops.append({
                    "op": "UPDATE_NODE_CONFIG",
                    "node_id": "calculate_pricing",
                    "config_patch": calc_patch
                })

            if review_patch:
                patch_ops.append({
                    "op": "UPDATE_NODE_CONFIG",
                    "node_id": "conditional_review_flag",
                    "config_patch": review_patch
                })

        if not patch_ops:
            return {
                "rationale": "Instruksi tidak dapat dipetakan secara jelas.",
                "risk_level": "LOW",
                "patch_operations": [],
                "requires_clarification": True,
                "clarification_message": "Mohon sebutkan angka margin target (misal: 'margin 18%') atau kriteria review yang ingin diubah."
            }

        return {
            "rationale": f"Penyesuaian konfigurasi alur kerja: {', '.join(rationale_parts)}.",
            "risk_level": "LOW",
            "patch_operations": patch_ops,
            "requires_clarification": False,
            "clarification_message": None
        }
