"""
app/services/agent_catalog.py
Katalog profil AI Agent spesialis per tugas (memfilter allowed_tools dari satu ToolRegistry).
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from app.core.tool_registry import BaseTool, ToolRegistry


@dataclass(frozen=True)
class AgentProfile:
    agent_id: str
    name: str
    description: str
    role_instruction: str
    allowed_tool_types: Optional[Sequence[str]]  # None = semua tool terdaftar (backward-compatible)


AGENT_PROFILES: Dict[str, AgentProfile] = {
    "general": AgentProfile(
        agent_id="general",
        name="JuraganAI Lengkap",
        description="Asisten umum yang dapat mengakses analitik kasir, pembuatan kampanye, memori bisnis, dan alur otomatis.",
        role_instruction="Anda beroperasi dalam mode asisten bisnis lengkap.",
        allowed_tool_types=None,
    ),
    "sales_analyst": AgentProfile(
        agent_id="sales_analyst",
        name="Analis Penjualan Kasir",
        description="Fokus membaca omzet, menu terlaris, menu turun, jam ramai kasir, metode bayar, dan tren penjualan.",
        role_instruction=(
            "PERAN KHUSUS: Anda adalah Analis Penjualan Kasir. Fokus pada angka aktual dari database POS "
            "(omzet, transaksi, menu terlaris/lambat, jam ramai, perbandingan periode) dan simpan preferensi bisnis jika diminta."
        ),
        allowed_tool_types=(
            "analytics.get_sales_overview",
            "analytics.get_top_products",
            "analytics.get_slow_moving_products",
            "analytics.get_peak_hours",
            "analytics.compare_sales_period",
            "analytics.get_payment_method_stats",
            "analytics.get_category_contribution",
            "analytics.get_day_of_week_analysis",
            "memory.remember",
        ),
    ),
    "marketing_studio": AgentProfile(
        agent_id="marketing_studio",
        name="Spesialis Promo & Konten IG",
        description="Mendeteksi menu yang perlu dipromosikan, membuat draf kampanye + 3 varian caption IG + rekomendasi jam tayang, dan publish setelah approval.",
        role_instruction=(
            "PERAN KHUSUS: Anda adalah Spesialis Promo & Konten Instagram. "
            "Gunakan tool analitik untuk melihat menu lambat/jam ramai, gunakan `campaign.create_draft` untuk menyimpan "
            "draf kampanye & 3 varian konten nyata ke database, dan hanya panggil `instagram.publish` untuk `content_id` yang sudah APPROVED."
        ),
        allowed_tool_types=(
            "analytics.get_slow_moving_products",
            "analytics.get_top_products",
            "analytics.get_peak_hours",
            "campaign.create_draft",
            "instagram.publish",
            "memory.remember",
        ),
    ),
    "ops_automation": AgentProfile(
        agent_id="ops_automation",
        name="Spesialis Otomatisasi & WA",
        description="Menjalankan alur kerja otomatis (workflow DAG) dan menyiapkan pengiriman pesan WhatsApp dengan persetujuan pemilik.",
        role_instruction=(
            "PERAN KHUSUS: Anda adalah Spesialis Otomatisasi & Operasional. "
            "Gunakan `workflow.run` untuk menjalankan alur kerja yang terdaftar dan `whatsapp.send_message` "
            "untuk mengirim pesan operasional (selalu melalui persetujuan pemilik)."
        ),
        allowed_tool_types=(
            "workflow.run",
            "whatsapp.send_message",
            "memory.remember",
        ),
    ),
}


def get_agent_profile(agent_id: Optional[str]) -> AgentProfile:
    key = (agent_id or "general").strip().lower()
    if key not in AGENT_PROFILES:
        valid = ", ".join(sorted(AGENT_PROFILES.keys()))
        raise ValueError(f"Agent '{agent_id}' tidak ditemukan. Pilihan yang tersedia: {valid}.")
    return AGENT_PROFILES[key]


def resolve_tools_for_profile(profile: AgentProfile) -> List[BaseTool]:
    """Mengambil subset tool dari satu ToolRegistry sesuai profil agent."""
    all_latest = ToolRegistry.get_all_latest()
    if profile.allowed_tool_types is None:
        return all_latest
    allow_set = set(profile.allowed_tool_types)
    return [t for t in all_latest if t.metadata.tool_type in allow_set]


def list_agent_profiles_summary() -> List[Dict[str, object]]:
    """Ringkasan profil agent beserta daftar tool aktifnya untuk ditampilkan di UI."""
    summaries: List[Dict[str, object]] = []
    for profile in AGENT_PROFILES.values():
        tools = resolve_tools_for_profile(profile)
        summaries.append({
            "agent_id": profile.agent_id,
            "name": profile.name,
            "description": profile.description,
            "tool_count": len(tools),
            "tools": [
                {
                    "tool_type": t.metadata.tool_type,
                    "display_name": t.metadata.display_name,
                    "requires_approval": bool(t.metadata.requires_upstream_gate),
                }
                for t in tools
            ],
        })
    return summaries
