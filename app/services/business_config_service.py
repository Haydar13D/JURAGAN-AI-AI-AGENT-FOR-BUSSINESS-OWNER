"""
app/services/business_config_service.py
Phase 2: Business Configuration Service (Level 1 Parameter Customization).
Enforces:
1. Business Outcome First, Workflow Complexity Second
2. Strict Allowlisted Patch Operations (operation: replace, path: /config/...)
3. Deterministic Validation with Pydantic Models (FBNCampaignConfig & RetailPricingConfig)
4. Human Configuration Approval Lifecycle (PROPOSED -> VALIDATED -> PENDING_APPROVAL -> APPROVED -> ACTIVATED / REJECTED)
5. Immutable Versioning & Zero DAG Topology Drift
"""

import re
import uuid
import copy
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple, Set
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.schemas.workflow import (
    WorkflowDefinition,
    WorkflowStatus,
    ConfigPatchOperation,
    BusinessProposal,
    ProposalStatus,
    FBNCampaignConfig,
    RetailPricingConfig,
    AssistantIntent,
    AssistantMessageRequest,
    AssistantMessageResponse,
)
from app.services.workflow_repository import WorkflowRepository


class SecurityViolationError(Exception):
    """Raised when an unauthorized or potentially dangerous modification is attempted."""
    pass


class BusinessConfigService:
    """
    Manages controlled Level 1 parameter customizations for Kirata AI Business Workflows.
    """

    ALLOWED_FNB_PATHS: Set[str] = {
        "/config/max_discount_pct",
        "/config/minimum_gross_margin_pct",
        "/config/sales_drop_threshold_pct",
        "/config/campaign_duration_days",
        "/config/flyer_tagline",
        "/config/flyer_cta_text",
        "/config/target_audience",
        "/config/focus_product_name",
        "/config/flyer_style",
    }

    ALLOWED_RETAIL_PATHS: Set[str] = {
        "/config/pricing_method",
        "/config/target_profit_pct",
        "/config/overhead_cost_idr",
        "/config/market_deviation_threshold_pct",
        "/config/rounding_increment_idr",
    }

    # Malicious injection and DAG tampering keywords
    SECURITY_BLACKLIST_PATTERNS = [
        r"__import__",
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"\bos\.",
        r"\bsubprocess\b",
        r"\bsystem\s*\(",
        r"drop\s+table",
        r"<script",
        r"\bdelete\s+from",
        r"\balter\s+table",
    ]

    # In-memory proposal registry for fast retrieval and state tracking
    _PROPOSALS: Dict[str, BusinessProposal] = {}

    @classmethod
    def get_allowed_paths_for_template(cls, template_id: str) -> Set[str]:
        if template_id == "tpl_fnb_sales_to_marketing_v1":
            return cls.ALLOWED_FNB_PATHS
        elif template_id == "tpl_used_iphone_pricing_v1":
            return cls.ALLOWED_RETAIL_PATHS
        return cls.ALLOWED_FNB_PATHS.union(cls.ALLOWED_RETAIL_PATHS)

    @classmethod
    def get_default_config_for_template(cls, template_id: str) -> Dict[str, Any]:
        if template_id == "tpl_fnb_sales_to_marketing_v1":
            return FBNCampaignConfig().model_dump()
        elif template_id == "tpl_used_iphone_pricing_v1":
            return RetailPricingConfig().model_dump()
        return {}

    @classmethod
    def check_security(cls, text_or_val: Any) -> None:
        """Inspects input for code injection, script tags, or shell execution attempts."""
        if not isinstance(text_or_val, str):
            return
        lower = text_or_val.lower()
        for pat in cls.SECURITY_BLACKLIST_PATTERNS:
            if re.search(pat, lower):
                raise SecurityViolationError(
                    f"Instruksi atau nilai mengandung pola tidak diizinkan ({pat}). "
                    "Kirata AI melarang eksekusi kode bebas atau manipulasi sistem."
                )

    @classmethod
    def validate_and_apply_patch(
        cls,
        workflow: WorkflowDefinition,
        changes: List[ConfigPatchOperation],
        base_version: int,
    ) -> Tuple[bool, List[str], Dict[str, Any], Dict[str, Dict[str, Any]]]:
        """
        Validates operations against allowlisted paths, checks base_version match,
        applies changes to candidate config, and validates with schema.
        Returns: (is_valid, validation_errors, candidate_config, diff_summary)
        """
        errors: List[str] = []
        diff_summary: Dict[str, Dict[str, Any]] = {}

        # 1. Stale version check (P0 guardrail)
        if base_version != workflow.version:
            errors.append(
                f"Stale version mismatch: Proposal dibuat berdasarkan versi {base_version}, "
                f"tetapi versi aktif alur kerja saat ini adalah {workflow.version}. "
                "Silakan muat ulang konfigurasi terbaru."
            )
            return False, errors, workflow.business_config or {}, diff_summary

        allowed_paths = cls.get_allowed_paths_for_template(workflow.template_id)
        current_config = dict(workflow.business_config or {})
        if not current_config:
            current_config = cls.get_default_config_for_template(workflow.template_id)

        candidate_config = dict(current_config)

        # 2. Validate individual patch operations
        for op in changes:
            # Operation must be strictly 'replace'
            if op.op != "replace":
                errors.append(f"Operasi '{op.op}' ditolak. Hanya operasi 'replace' yang diizinkan.")
                continue

            # Path allowlist check
            if op.path not in allowed_paths:
                errors.append(
                    f"Jalur parameter '{op.path}' tidak diizinkan. "
                    f"Parameter yang dapat dikustomisasi: {sorted(list(allowed_paths))}."
                )
                continue

            # Security check on value
            try:
                cls.check_security(op.value)
            except SecurityViolationError as sec_err:
                errors.append(str(sec_err))
                continue

            # Extract parameter key: /config/max_discount_pct -> max_discount_pct
            param_key = op.path.replace("/config/", "")
            old_val = current_config.get(param_key)
            new_val = op.value

            # Attempt type conversion if needed
            if isinstance(old_val, (int, float)) and not isinstance(new_val, (int, float)):
                try:
                    if isinstance(old_val, int) and not isinstance(old_val, bool):
                        new_val = int(new_val)
                    else:
                        new_val = float(new_val)
                except (ValueError, TypeError):
                    errors.append(f"Nilai untuk '{param_key}' harus berupa angka, diterima: {op.value}")
                    continue

            candidate_config[param_key] = new_val
            diff_summary[param_key] = {"before": old_val, "after": new_val}

        # 3. Deterministic schema validation with Pydantic model
        if not errors:
            try:
                if workflow.template_id == "tpl_fnb_sales_to_marketing_v1":
                    validated_model = FBNCampaignConfig.model_validate(candidate_config)
                    candidate_config = validated_model.model_dump()
                elif workflow.template_id == "tpl_used_iphone_pricing_v1":
                    validated_model = RetailPricingConfig.model_validate(candidate_config)
                    candidate_config = validated_model.model_dump()
            except Exception as exc:
                # Capture validation errors (range violation, cross-field violation, etc.)
                errors.append(f"Validasi bisnis gagal: {str(exc)}")

        is_valid = len(errors) == 0
        return is_valid, errors, candidate_config, diff_summary

    @classmethod
    def generate_fnb_proposal_from_prompt(
        cls,
        workflow: WorkflowDefinition,
        user_prompt: str,
    ) -> BusinessProposal:
        """
        Translates Indonesian user intent into a validated F&B BusinessProposal.
        """
        proposal_id = f"prop_fnb_{uuid.uuid4().hex[:6]}"
        created_at = datetime.now().isoformat()
        base_version = workflow.version
        proposed_version = base_version + 1

        # Check for security violations in user prompt
        try:
            cls.check_security(user_prompt)
        except SecurityViolationError as sec_err:
            return BusinessProposal(
                proposal_id=proposal_id,
                intent="SECURITY_VIOLATION",
                workflow_id=workflow.id,
                base_version=base_version,
                proposed_version=proposed_version,
                changes=[],
                explanation=f"Permintaan ditolak demi keamanan sistem: {str(sec_err)}",
                diff_summary={},
                is_valid=False,
                validation_errors=[str(sec_err)],
                status=ProposalStatus.REJECTED,
                created_at=created_at,
            )

        changes: List[ConfigPatchOperation] = []
        explanation_parts: List[str] = []
        prompt_lower = user_prompt.lower()

        # 1. Detect max_discount_pct change
        # e.g.: "ubah maksimal diskon menjadi 15%", "diskon 10%", "set diskon maks 25%"
        disc_match = re.search(r"(?:diskon|potongan|discount)(?:\s+maksimal|\s+maks|\s+max)?\s*(?:menjadi|ke|sebesar|jadi|\=)?\s*(\d+(?:\.\d+)?)\s*%?", prompt_lower)
        if disc_match:
            val = float(disc_match.group(1))
            changes.append(ConfigPatchOperation(op="replace", path="/config/max_discount_pct", value=val))
            explanation_parts.append(f"Maksimal diskon promosi disesuaikan menjadi {val}%")

        # 2. Detect minimum_gross_margin_pct change
        # e.g.: "minimal margin 30%", "margin kotor minimal 25%", "jaga margin 35%"
        margin_match = re.search(r"(?:minimal\s+margin|margin\s+kotor|target\s+margin|margin\s+minimal|margin)(?:\s+kotor)?\s*(?:menjadi|ke|sebesar|jadi|\=)?\s*(\d+(?:\.\d+)?)\s*%?", prompt_lower)
        if margin_match and not (disc_match and margin_match.group(1) == disc_match.group(1)):
            val = float(margin_match.group(1))
            changes.append(ConfigPatchOperation(op="replace", path="/config/minimum_gross_margin_pct", value=val))
            explanation_parts.append(f"Batas aman margin kotor minimum ditetapkan ke {val}%")

        # 3. Detect sales_drop_threshold_pct change
        # e.g.: "turunkan batas drop ke 20%", "penjualan turun 30%", "ambang drop 15%"
        drop_match = re.search(r"(?:drop|turun|penurunan)(?:\s+penjualan)?(?:\s+sebesar|\s+ke|\s+menjadi|\s+minimal)?\s*(\d+(?:\.\d+)?)\s*%?", prompt_lower)
        if drop_match:
            val = float(drop_match.group(1))
            # avoid collision if it matched the discount number
            if not any(c.path == "/config/max_discount_pct" and c.value == val for c in changes):
                changes.append(ConfigPatchOperation(op="replace", path="/config/sales_drop_threshold_pct", value=val))
                explanation_parts.append(f"Ambang batas deteksi penurunan penjualan diubah ke {val}%")

        # 4. Detect campaign_duration_days change
        # e.g.: "durasi promo 5 hari", "berlaku 10 hari", "selama 14 hari"
        dur_match = re.search(r"(?:durasi|selama|berlaku|promo)\s*(?:menjadi|ke|sebesar|jadi|\=)?\s*(\d+)\s*hari", prompt_lower)
        if dur_match:
            val = int(dur_match.group(1))
            changes.append(ConfigPatchOperation(op="replace", path="/config/campaign_duration_days", value=val))
            explanation_parts.append(f"Durasi masa berlaku promo diatur menjadi {val} hari")

        # 5. Detect flyer_tagline change
        # e.g.: "tagline flyer jadi 'Sensasi Segar Kopi Sore'", "judul flyer Promo Seru", "ubah headline flyer jadi Semangat Pagi"
        tag_match = re.search(r"(?:tagline|headline|judul|tema)(?:\s+flyer)?\s*(?:menjadi|ke|jadi|\=)?\s*(?:['\"]([^'\"]+)['\"]|([^,;\n]+?)(?=\s+dan\b|\s+tombol\b|$))", user_prompt, re.I)
        if tag_match:
            val = (tag_match.group(1) or tag_match.group(2) or "").strip().strip("'\"")
            if val and len(val) >= 3:
                changes.append(ConfigPatchOperation(op="replace", path="/config/flyer_tagline", value=val))
                explanation_parts.append(f"Tagline / headline flyer diubah menjadi '{val}'")

        # 6. Detect flyer_cta_text change
        # e.g.: "tombol cta jadi 'Pesan via GoFood'", "ubah cta jadi Klaim Promo di Kasir", "teks tombol Pesan Sekarang"
        cta_match = re.search(r"(?:tombol(?:\s+cta)?|cta|call\s+to\s+action|ajakan)\s*(?:menjadi|ke|jadi|\=)?\s*(?:['\"]([^'\"]+)['\"]|([^,;\n]+?)(?=\s+dan\b|\s+tagline\b|$))", user_prompt, re.I)
        if cta_match:
            val = (cta_match.group(1) or cta_match.group(2) or "").strip().strip("'\"")
            if val and len(val) >= 3:
                changes.append(ConfigPatchOperation(op="replace", path="/config/flyer_cta_text", value=val))
                explanation_parts.append(f"Tombol ajakan aksi (CTA) flyer diubah menjadi '{val}'")

        # 7. Detect target_audience change
        # e.g.: "target audiens jadi Mahasiswa & Pekerja Kantor", "sasaran pelanggan pencinta kopi"
        aud_match = re.search(r"(?:target\s+audiens|audiens|sasaran\s+pelanggan)\s*(?:menjadi|ke|jadi|\=)?\s*(?:['\"]([^'\"]+)['\"]|([^,;\n]+?)(?=\s+dan\b|$))", user_prompt, re.I)
        if aud_match:
            val = (aud_match.group(1) or aud_match.group(2) or "").strip().strip("'\"")
            if val and len(val) >= 3:
                changes.append(ConfigPatchOperation(op="replace", path="/config/target_audience", value=val))
                explanation_parts.append(f"Target audiens kampanye disesuaikan menjadi '{val}'")

        # 8. Detect focus_product_name change
        # Matches: "bukan matcha tapi cheese burger", "ganti gambar matcha jadi burger", "ubah ke cheese burger",
        # "revisi desain jadi cheese burger", "fokus ke Cheese Burger", "buatkan flyer burger", etc.
        product_found: Optional[str] = None

        # 8a. "bukan X tapi Y" or "bukan X melainkan Y"
        bukan_match = re.search(r"bukan\s+[^,;\n]+?\s+(?:tapi|melainkan)\s+(?:ke\s+|menu\s+|produk\s+|gambar\s+)?(['\"][^'\"]+['\"]|[a-zA-Z0-9\s]+?)(?=\s+dengan\b|\s+dan\b|\s+diskon\b|$)", user_prompt, re.I)
        if bukan_match:
            product_found = (bukan_match.group(1) or "").strip().strip("'\"")

        # 8b. "ganti/ubah/revisi ... jadi/ke <target>"
        if not product_found:
            ganti_match = re.search(r"(?:ganti|ubah|revisi|tukar|jadikan)\s+(?:gambar|foto|desain|menu|produk|flyer)?\s*(?:[a-zA-Z0-9\s]+?\s+)?(?:menjadi|ke|jadi|\=)\s*(?:menu\s+|produk\s+|gambar\s+)?(['\"][^'\"]+['\"]|[a-zA-Z0-9\s]+?)(?=\s+dengan\b|\s+dan\b|\s+diskon\b|\s+sebesar\b|\s+durasi\b|$)", user_prompt, re.I)
            if ganti_match:
                candidate = (ganti_match.group(1) or "").strip().strip("'\"")
                if candidate.lower() not in ["ai", "ai flux", "flux", "vektor", "svg", "foto", "fotografi", "studio"]:
                    product_found = candidate

        # 8c. "fokus/promosikan ... <target>"
        if not product_found:
            fokus_match = re.search(r"(?:fokus(?:kan)?(?:\s+promo|\s+promosi)?|promosikan|fokus\s+produk|menu\s+fokus|target\s+produk|fokus\s+menu)\s*(?:ke|pada|menu|produk|untuk)?\s*(?:['\"][^'\"]+['\"]|([a-zA-Z0-9\s]+?)(?=\s+dengan\b|\s+dan\b|\s+diskon\b|\s+sebesar\b|\s+durasi\b|$))", user_prompt, re.I)
            if fokus_match:
                product_found = (fokus_match.group(1) or "").strip().strip("'\"")

        # 8d. "buatkan/bikin/generate/ganti/ubah/mau flyer/gambar/menu <target>" (open-vocabulary without needing 'jadi'/'ke')
        if not product_found:
            buat_match = re.search(
                r"(?:buatkan|buat|bikin|generate|tampilkan|ganti|ubah|revisi|mau|ingin)\s+"
                r"(?:flyer|promo|promosi|desain|poster|gambar|foto|menu|produk)(?:nya)?\s+"
                r"(?:untuk\s+|menu\s+|produk\s+|gambar\s+|ke\s+|jadi\s+)?(['\"][^'\"]+['\"]|[a-zA-Z0-9\s]+?)"
                r"(?=\s+dengan\b|\s+dan\b|\s+diskon\b|\s+sebesar\b|\s+durasi\b|$)",
                user_prompt,
                re.I
            )
            if buat_match:
                candidate = (buat_match.group(1) or "").strip().strip("'\"")
                if candidate.lower() not in ["baru", "lain", "alternatif", "ai", "ai flux", "flux", "vektor", "svg", "foto", "fotografi", "studio", "flyer"]:
                    product_found = candidate

        # 8e. Contextual keyword match if user mentions common/known menu items
        if not product_found and any(w in prompt_lower for w in ["desain", "gambar", "flyer", "menu", "revisi", "salah", "kok", "malah", "kenapa", "ganti", "ubah", "harusnya", "mau", "ingin"]):
            for item in ["sushi", "sashimi", "cheese burger", "burger", "rice bowl", "cireng", "kopi susu", "americano", "cappuccino", "matcha latte", "ramen", "dimsum", "pizza", "pasta", "steak"]:
                if item in prompt_lower:
                    product_found = item.title()
                    break

        if product_found:
            cleaned_prod = re.sub(r"^(?:menu|produk|gambar|makanan|minuman|untuk|ke|jadi)\s+", "", product_found, flags=re.I).strip()
            if cleaned_prod and len(cleaned_prod) >= 3 and not any(k in cleaned_prod.lower() for k in ["diskon", "margin", "durasi", "tagline", "cta"]):
                changes.append(ConfigPatchOperation(op="replace", path="/config/focus_product_name", value=cleaned_prod.title()))
                explanation_parts.append(f"Fokus produk promosi diarahkan khusus ke menu '{cleaned_prod.title()}'")

        # 9. Detect flyer_style change / alternate design request
        # e.g.: "ganti desain jadi AI", "ubah gaya flyer ke vektor", "generate desain lain", "buat desain baru dengan AI Flux"
        style_match = re.search(r"(?:gaya|model|mode|desain|tampilan|tipe|style|gambar)(?:\s+flyer|\s+visual|\s+desain)?\s*(?:menjadi|ke|jadi|\=)?\s*(ai(?:\s+flux)?|flux|vektor|svg|foto(?:grafi)?|studio)", prompt_lower)
        general_design_request = bool(re.search(r"(?:generate|buat|bikin|ubah|ganti|revisi)\s+(?:desain|gambar|flyer|visual)", prompt_lower))

        if style_match:
            raw_s = style_match.group(1).lower()
            if any(k in raw_s for k in ["ai", "flux"]):
                target_style = "ai_flux"
                desc = "Generasi AI Sinematik (Flux)"
            elif any(k in raw_s for k in ["vektor", "svg"]):
                target_style = "vector_svg"
                desc = "Grafis Vektor Minimalis (SVG)"
            else:
                target_style = "photography"
                desc = "Studio Komersial Fotografi"
            changes.append(ConfigPatchOperation(op="replace", path="/config/flyer_style", value=target_style))
            explanation_parts.append(f"Gaya visual flyer promosi diubah menjadi '{desc}'")
        elif general_design_request and not any(c.path.startswith("/config/flyer_") for c in changes):
            curr_style = (workflow.business_config or {}).get("flyer_style", "photography")
            target_style = "ai_flux" if curr_style != "ai_flux" else "photography"
            desc = "Generasi AI Sinematik (Flux)" if target_style == "ai_flux" else "Studio Komersial Fotografi"
            changes.append(ConfigPatchOperation(op="replace", path="/config/flyer_style", value=target_style))
            explanation_parts.append(f"Membuat variasi gaya desain baru menggunakan '{desc}'")

        # If no recognized business parameter
        if not changes:
            proposal = BusinessProposal(
                proposal_id=proposal_id,
                intent="UNCLEAR_PARAMETER_MODIFICATION",
                workflow_id=workflow.id,
                base_version=base_version,
                proposed_version=proposed_version,
                changes=[],
                explanation=(
                    "Kirata belum dapat mengidentifikasi parameter bisnis yang ingin diubah. "
                    "Anda dapat menyesuaikan by prompt: 'fokus promo' (contoh: 'fokuskan promo ke Rice Bowl Ayam Sambal Matah'), "
                    "'gaya desain flyer' (contoh: 'ganti desain jadi AI' atau 'pakai gaya vektor'), "
                    "'maksimal diskon' (contoh: 15%), 'margin kotor minimum' (contoh: 25%), "
                    "'durasi kampanye' (contoh: 7 hari), 'tagline flyer' (contoh: 'Sensasi Kopi Sore'), atau 'tombol CTA' (contoh: 'Pesan via GoFood')."
                ),
                diff_summary={},
                is_valid=False,
                validation_errors=["Tidak ada parameter bisnis yang dikenali dalam instruksi."],
                status=ProposalStatus.PROPOSED,
                created_at=created_at,
            )
            cls._PROPOSALS[proposal_id] = proposal
            return proposal

        # Validate patch and business constraints
        is_valid, validation_errors, candidate_config, diff_summary = cls.validate_and_apply_patch(
            workflow=workflow,
            changes=changes,
            base_version=base_version
        )

        explanation = (
            "Usulan Perubahan Konfigurasi Bisnis F&B:\n- " + "\n- ".join(explanation_parts) +
            "\n\nPerubahan ini hanya memperbarui parameter operasional alur kerja tanpa mengubah integritas DAG."
            if is_valid else
            f"Usulan perubahan tidak dapat disetujui secara otomatis karena melanggar aturan bisnis: {', '.join(validation_errors)}"
        )

        status = ProposalStatus.VALIDATED if is_valid else ProposalStatus.PROPOSED

        proposal = BusinessProposal(
            proposal_id=proposal_id,
            intent="MODIFY_FNB_CAMPAIGN_CONFIG",
            workflow_id=workflow.id,
            base_version=base_version,
            proposed_version=proposed_version,
            changes=changes,
            explanation=explanation,
            diff_summary=diff_summary,
            is_valid=is_valid,
            validation_errors=validation_errors,
            status=status,
            created_at=created_at,
            candidate_config=candidate_config if is_valid else None
        )

        cls._PROPOSALS[proposal_id] = proposal
        return proposal

    @classmethod
    def generate_retail_proposal_from_prompt(
        cls,
        workflow: WorkflowDefinition,
        user_prompt: str,
    ) -> BusinessProposal:
        """
        Translates Indonesian user intent into a validated Retail Pricing BusinessProposal.
        """
        proposal_id = f"prop_retail_{uuid.uuid4().hex[:6]}"
        created_at = datetime.now().isoformat()
        base_version = workflow.version
        proposed_version = base_version + 1

        try:
            cls.check_security(user_prompt)
        except SecurityViolationError as sec_err:
            return BusinessProposal(
                proposal_id=proposal_id,
                intent="SECURITY_VIOLATION",
                workflow_id=workflow.id,
                base_version=base_version,
                proposed_version=proposed_version,
                changes=[],
                explanation=f"Permintaan ditolak demi keamanan sistem: {str(sec_err)}",
                diff_summary={},
                is_valid=False,
                validation_errors=[str(sec_err)],
                status=ProposalStatus.REJECTED,
                created_at=created_at,
            )

        changes: List[ConfigPatchOperation] = []
        explanation_parts: List[str] = []
        prompt_lower = user_prompt.lower()

        # 1. Pricing method: markup vs gross_margin
        if "markup" in prompt_lower:
            changes.append(ConfigPatchOperation(op="replace", path="/config/pricing_method", value="markup"))
            explanation_parts.append("Metode kalkulasi harga diubah menjadi Markup dari modal")
        elif "gross margin" in prompt_lower or "margin kotor" in prompt_lower:
            changes.append(ConfigPatchOperation(op="replace", path="/config/pricing_method", value="gross_margin"))
            explanation_parts.append("Metode kalkulasi harga diubah menjadi Gross Margin")

        # 2. Target profit / margin percentage
        profit_match = re.search(r"(?:target\s+profit|target\s+keuntungan|profit|target\s+margin|margin|markup)(?:\s+sebesar|\s+ke|\s+menjadi|\s*=\s*)?\s*(\d+(?:\.\d+)?)\s*%?", prompt_lower)
        if profit_match:
            val = float(profit_match.group(1))
            changes.append(ConfigPatchOperation(op="replace", path="/config/target_profit_pct", value=val))
            explanation_parts.append(f"Target keuntungan disesuaikan menjadi {val}%")

        # 3. Overhead fee in IDR
        overhead_match = re.search(r"(?:overhead(?:\s+fee)?|biaya\s+overhead|biaya\s+operasional|biaya\s+tambahan)(?:\s+sebesar|\s+ke|\s+menjadi|\s*=\s*)?\s*(?:rp\.?|idr)?\s*(\d[\d\.\,]+)", prompt_lower)
        if overhead_match:
            raw_num = overhead_match.group(1).replace(".", "").replace(",", "")
            if raw_num.isdigit():
                val = int(raw_num)
                changes.append(ConfigPatchOperation(op="replace", path="/config/overhead_cost_idr", value=val))
                explanation_parts.append(f"Biaya overhead per unit diatur menjadi Rp{val:,}")

        # 4. Market deviation threshold %
        dev_match = re.search(r"(?:deviasi|selisih\s+pasar|ambang\s+pasar)(?:\s+sebesar|\s+ke|\s+menjadi|\s*=\s*)?\s*(\d+(?:\.\d+)?)\s*%?", prompt_lower)
        if dev_match:
            val = float(dev_match.group(1))
            changes.append(ConfigPatchOperation(op="replace", path="/config/market_deviation_threshold_pct", value=val))
            explanation_parts.append(f"Batas deviasi terhadap harga pasar diatur ke {val}%")

        # 5. Rounding increment IDR
        round_match = re.search(r"(?:pembulatan|kelipatan|rounding)(?:\s+harga|\s+ke\s+atas)?(?:\s+sebesar|\s+ke|\s+menjadi|\s*=\s*)?\s*(?:rp\.?|idr)?\s*(\d[\d\.\,]+)", prompt_lower)
        if round_match:
            raw_num = round_match.group(1).replace(".", "").replace(",", "")
            if raw_num.isdigit():
                val = int(raw_num)
                changes.append(ConfigPatchOperation(op="replace", path="/config/rounding_increment_idr", value=val))
                explanation_parts.append(f"Kelipatan pembulatan harga diatur ke Rp{val:,}")

        if not changes:
            proposal = BusinessProposal(
                proposal_id=proposal_id,
                intent="UNCLEAR_PARAMETER_MODIFICATION",
                workflow_id=workflow.id,
                base_version=base_version,
                proposed_version=proposed_version,
                changes=[],
                explanation=(
                    "Kirata belum dapat mengidentifikasi parameter harga ritel yang ingin diubah. "
                    "Anda dapat menyesuaikan: 'target profit' (contoh: 18%), 'metode pricing' ('markup' atau 'gross margin'), "
                    "'biaya overhead' (contoh: Rp60.000), atau 'kelipatan pembulatan' (contoh: Rp50.000)."
                ),
                diff_summary={},
                is_valid=False,
                validation_errors=["Tidak ada parameter harga ritel yang dikenali."],
                status=ProposalStatus.PROPOSED,
                created_at=created_at,
            )
            cls._PROPOSALS[proposal_id] = proposal
            return proposal

        is_valid, validation_errors, candidate_config, diff_summary = cls.validate_and_apply_patch(
            workflow=workflow,
            changes=changes,
            base_version=base_version
        )

        explanation = (
            "Usulan Perubahan Konfigurasi Harga Retail:\n- " + "\n- ".join(explanation_parts) +
            "\n\nPerubahan ini hanya memperbarui parameter kalkulasi harga tanpa mengubah DAG alur kerja."
            if is_valid else
            f"Usulan perubahan tidak valid menurut aturan bisnis: {', '.join(validation_errors)}"
        )

        status = ProposalStatus.VALIDATED if is_valid else ProposalStatus.PROPOSED

        proposal = BusinessProposal(
            proposal_id=proposal_id,
            intent="MODIFY_RETAIL_PRICING_CONFIG",
            workflow_id=workflow.id,
            base_version=base_version,
            proposed_version=proposed_version,
            changes=changes,
            explanation=explanation,
            diff_summary=diff_summary,
            is_valid=is_valid,
            validation_errors=validation_errors,
            status=status,
            created_at=created_at,
            candidate_config=candidate_config if is_valid else None
        )

        cls._PROPOSALS[proposal_id] = proposal
        return proposal

    @classmethod
    def generate_proposal_for_workflow(
        cls,
        workflow: WorkflowDefinition,
        user_prompt: str,
    ) -> BusinessProposal:
        """Dispatches prompt to the appropriate template-specific proposal compiler."""
        if workflow.template_id == "tpl_fnb_sales_to_marketing_v1":
            return cls.generate_fnb_proposal_from_prompt(workflow, user_prompt)
        elif workflow.template_id == "tpl_used_iphone_pricing_v1":
            return cls.generate_retail_proposal_from_prompt(workflow, user_prompt)
        return cls.generate_fnb_proposal_from_prompt(workflow, user_prompt)

    @classmethod
    def get_proposal(cls, proposal_id: str) -> Optional[BusinessProposal]:
        return cls._PROPOSALS.get(proposal_id)

    @classmethod
    async def approve_proposal(
        cls,
        session: AsyncSession,
        workflow_id: str,
        proposal_id: str,
        reviewer_id: str = "business_owner"
    ) -> Tuple[WorkflowDefinition, BusinessProposal]:
        """
        Approves a validated proposal, generates new immutable version V+1,
        and marks it ACTIVE while preserving historical versions.
        """
        proposal = cls.get_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal '{proposal_id}' tidak ditemukan.")

        if not proposal.is_valid:
            raise ValueError(f"Proposal '{proposal_id}' tidak valid dan tidak dapat disetujui: {proposal.validation_errors}")

        if proposal.status in (ProposalStatus.APPROVED, ProposalStatus.ACTIVATED):
            raise ValueError(f"Proposal '{proposal_id}' sudah disetujui sebelumnya.")

        if proposal.status == ProposalStatus.REJECTED:
            raise ValueError(f"Proposal '{proposal_id}' telah ditolak dan tidak dapat diaktifkan.")

        # Load active workflow from database
        active_wf = await WorkflowRepository.get_active_workflow(session, workflow_id)
        if not active_wf:
            # Fallback to specific base_version
            active_wf = await WorkflowRepository.get_workflow_version(session, workflow_id, proposal.base_version)

        if not active_wf:
            raise ValueError(f"Alur kerja aktif dengan ID '{workflow_id}' tidak ditemukan.")

        # Stale version check: Active version must match proposal's base_version
        if active_wf.version != proposal.base_version:
            proposal.status = ProposalStatus.REJECTED
            proposal.validation_errors.append(
                f"Gagal aktivasi: Alur kerja telah diperbarui ke versi {active_wf.version}. "
                f"Proposal ini dibuat atas versi {proposal.base_version}."
            )
            raise ValueError(
                f"Konflik versi alur kerja: Versi aktif saat ini adalah v{active_wf.version}, "
                f"tetapi proposal berbasis v{proposal.base_version}. Buat proposal baru."
            )

        # Create new immutable version V+1
        new_version_no = active_wf.version + 1
        new_wf = copy.deepcopy(active_wf)
        new_wf.version = new_version_no
        new_wf.status = WorkflowStatus.ACTIVE
        new_wf.business_config = proposal.candidate_config
        new_wf.activated_at = datetime.now().isoformat()

        # Save to repository as new active version
        await WorkflowRepository.save_workflow(session, new_wf, is_active=True)

        # Update proposal state
        proposal.status = ProposalStatus.ACTIVATED
        proposal.reviewed_at = datetime.now().isoformat()
        proposal.reviewed_by = reviewer_id

        logger.info(
            f"Proposal '{proposal_id}' berhasil diaktivasi untuk alur kerja '{workflow_id}'. "
            f"Versi alur kerja meningkat dari v{active_wf.version} -> v{new_version_no}."
        )

        return new_wf, proposal

    @classmethod
    def reject_proposal(
        cls,
        proposal_id: str,
        reviewer_id: str = "business_owner",
        reason: Optional[str] = None
    ) -> BusinessProposal:
        """
        Rejects an open proposal. The active workflow version remains unchanged.
        """
        proposal = cls.get_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal '{proposal_id}' tidak ditemukan.")

        proposal.status = ProposalStatus.REJECTED
        proposal.reviewed_at = datetime.now().isoformat()
        proposal.reviewed_by = reviewer_id
        if reason:
            proposal.explanation += f"\n\n[Ditolak oleh {reviewer_id}]: {reason}"

        return proposal

    @classmethod
    def classify_intent(cls, message: str) -> AssistantIntent:
        """Classifies user intent into one of 5 supported intents."""
        msg = message.lower()

        # MODIFY_WORKFLOW
        if any(k in msg for k in [
            "ubah", "ganti", "sesuaikan", "set ", "turunkan", "naikkan", "edit", "update", "perbarui",
            "fokus", "fokuskan", "promosikan", "prioritaskan", "jadikan", "generate", "buatkan", "buat",
            "bikin", "variasi", "revisi", "desain", "gambar", "flyer", "jangan", "bukan"
        ]):
            return AssistantIntent.MODIFY_WORKFLOW

        # RUN_WORKFLOW
        if any(k in msg for k in ["jalankan", "eksekusi", "run", "mulai proses", "lakukan analisis"]):
            return AssistantIntent.RUN_WORKFLOW

        # EXPLAIN_RESULT
        if any(k in msg for k in ["jelaskan", "mengapa", "kenapa", "uraikan", "analisis hasil", "penjelasan"]):
            return AssistantIntent.EXPLAIN_RESULT

        # VIEW_WORKFLOW
        if any(k in msg for k in ["lihat", "tampilkan", "cek", "status", "konfigurasi", "versi", "detail"]):
            return AssistantIntent.VIEW_WORKFLOW

        return AssistantIntent.UNKNOWN

    @classmethod
    async def handle_assistant_message(
        cls,
        session: AsyncSession,
        request: AssistantMessageRequest,
    ) -> AssistantMessageResponse:
        """
        Processes a conversational message from business owner.
        """
        intent = cls.classify_intent(request.message)

        # Target workflow lookup
        workflow_id = request.workflow_id
        target_wf: Optional[WorkflowDefinition] = None

        if workflow_id:
            target_wf = await WorkflowRepository.get_active_workflow(session, workflow_id)
            if not target_wf:
                versions = await WorkflowRepository.get_all_versions_dict(session, workflow_id)
                if versions:
                    target_wf = versions[max(versions.keys())]

        if not target_wf:
            # Query first workflow for tenant
            wfs = await WorkflowRepository.list_workflows(session, tenant_id=request.tenant_id)
            if wfs:
                workflow_id = wfs[0]["id"]
                target_wf = await WorkflowRepository.get_active_workflow(session, workflow_id)
                if not target_wf:
                    versions = await WorkflowRepository.get_all_versions_dict(session, workflow_id)
                    if versions:
                        target_wf = versions[max(versions.keys())]

        if not target_wf:
            return AssistantMessageResponse(
                intent=intent,
                reply="Tidak ditemukan alur kerja. Silakan pilih atau inisiasi alur kerja dari Galeri Template terlebih dahulu.",
                workflow_id=None
            )

        if intent == AssistantIntent.MODIFY_WORKFLOW:
            proposal = cls.generate_proposal_for_workflow(target_wf, request.message)
            if proposal.is_valid:
                diff_lines = [f"- {k}: {v['before']} ➔ {v['after']}" for k, v in proposal.diff_summary.items()]
                reply = (
                    f"Saya telah menyiapkan proposal penyesuaian konfigurasi untuk alur kerja '{target_wf.name}' (v{target_wf.version}):\n\n"
                    f"{proposal.explanation}\n\n"
                    f"Ringkasan Parameter Baru:\n" + "\n".join(diff_lines) +
                    f"\n\nVersi target setelah persetujuan: v{proposal.proposed_version}. "
                    "Apakah Anda menyetujui usulan perubahan ini?"
                )
                return AssistantMessageResponse(
                    intent=intent,
                    reply=reply,
                    workflow_id=target_wf.id,
                    proposal=proposal,
                    action_required="APPROVE_PROPOSAL",
                    metadata={"proposal_id": proposal.proposal_id}
                )
            else:
                return AssistantMessageResponse(
                    intent=intent,
                    reply=f"Usulan perubahan tidak dapat diajukan:\n{proposal.explanation}",
                    workflow_id=target_wf.id,
                    proposal=proposal
                )

        elif intent == AssistantIntent.VIEW_WORKFLOW:
            cfg = target_wf.business_config or cls.get_default_config_for_template(target_wf.template_id)
            cfg_lines = [f"- {k}: {v}" for k, v in cfg.items()]
            reply = (
                f"Informasi Alur Kerja Aktif:\n"
                f"- Nama: {target_wf.name}\n"
                f"- ID: {target_wf.id}\n"
                f"- Versi Aktif: v{target_wf.version}\n"
                f"- Status: {target_wf.status}\n\n"
                f"Konfigurasi Parameter Bisnis:\n" + "\n".join(cfg_lines)
            )
            return AssistantMessageResponse(
                intent=intent,
                reply=reply,
                workflow_id=target_wf.id,
                metadata={"version": target_wf.version, "config": cfg}
            )

        elif intent == AssistantIntent.RUN_WORKFLOW:
            reply = (
                f"Alur kerja '{target_wf.name}' (v{target_wf.version}) siap dijalankan. "
                f"Anda dapat mengeksekusi Dry-Run untuk simulasi aman atau Live Run melalui dashboard eksekusi."
            )
            return AssistantMessageResponse(
                intent=intent,
                reply=reply,
                workflow_id=target_wf.id,
                action_required="EXECUTE_WORKFLOW"
            )

        elif intent == AssistantIntent.EXPLAIN_RESULT:
            reply = (
                f"Alur kerja '{target_wf.name}' memproses data penjualan secara deterministik. "
                "Setiap produk yang mengalami anomali penurunan penjualan dianalisis terhadap batas margin kotor sebelum flyer dan kampanye promosi dirumuskan."
            )
            return AssistantMessageResponse(
                intent=intent,
                reply=reply,
                workflow_id=target_wf.id
            )

        else:
            return AssistantMessageResponse(
                intent=AssistantIntent.UNKNOWN,
                reply=(
                    "Halo! Saya Kirata AI Business Assistant. Saya dapat membantu Anda mengelola alur kerja bisnis:\n"
                    "1. Menyesuaikan aturan bisnis (contoh: 'Ubah maksimal diskon menjadi 15%')\n"
                    "2. Menampilkan status konfigurasi (contoh: 'Lihat konfigurasi workflow')\n"
                    "3. Menjalankan simulasi atau eksekusi operasional (contoh: 'Jalankan workflow F&B')"
                ),
                workflow_id=target_wf.id
            )
