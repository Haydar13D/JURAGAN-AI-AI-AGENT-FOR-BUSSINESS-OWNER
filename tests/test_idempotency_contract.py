"""
tests/test_idempotency_contract.py
T19 / I19 — Contract test idempotency_key pada ToolRegistry:
- Tool EXTERNAL atau FINANCIAL tanpa field idempotency_key di input_schema ditolak saat register().
- Seluruh tool EXTERNAL/FINANCIAL terdaftar di ToolRegistry wajib memiliki idempotency_key.
"""

from pydantic import BaseModel
import pytest

from app.core.tool_registry import (
    BaseTool,
    ExecutionContext,
    GATED_RISK_TIERS,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolRiskTier,
)

import app.core.governance_tools  # noqa: F401
import app.services.deepseek_agent  # noqa: F401
import app.services.fnb_marketing.tools  # noqa: F401
import app.services.inventory_pack.tools  # noqa: F401
import app.services.retail_pricing.tools  # noqa: F401
from app.services.whatsapp_tool import register_whatsapp_tool
from app.services.workflow_tool import register_workflow_tool

register_workflow_tool()
register_whatsapp_tool()


class _NoIdemInput(BaseModel):
    target: str


class _DummyOut(BaseModel):
    ok: bool = True


def _make_unsafe_tool(tool_type: str, tier: ToolRiskTier) -> BaseTool:
    class _UnsafeTool(BaseTool):
        metadata = ToolMetadata(
            tool_type=tool_type,
            version="1.0.0",
            display_name=tool_type,
            description="Unsafe tool without idempotency_key",
            category=ToolCategory.EXPORT,
            risk_tier=tier,
            is_side_effect=True,
        )
        input_schema = _NoIdemInput
        output_schema = _DummyOut

        async def execute(self, config: BaseModel, inputs: _NoIdemInput, context: ExecutionContext) -> BaseModel:
            return _DummyOut()

    return _UnsafeTool()


@pytest.mark.parametrize("tier", [ToolRiskTier.EXTERNAL, ToolRiskTier.FINANCIAL])
def test_register_gated_tool_without_idempotency_key_raises(tier: ToolRiskTier):
    saved = {k: dict(v) for k, v in ToolRegistry._registry.items()}
    try:
        with pytest.raises(ValueError, match="idempotency_key"):
            ToolRegistry.register(_make_unsafe_tool(f"test.no_idem_{tier.value.lower()}", tier))
    finally:
        ToolRegistry._registry.clear()
        ToolRegistry._registry.update(saved)


def test_all_registered_gated_tools_have_idempotency_key():
    gated_tools = [t for t in ToolRegistry.get_all_latest() if t.metadata.risk_tier in GATED_RISK_TIERS]
    assert len(gated_tools) >= 3  # fnb.publish_instagram, retail.export_catalog, whatsapp.send_message
    for tool in gated_tools:
        assert "idempotency_key" in tool.input_schema.model_fields, (
            f"Tool '{tool.metadata.tool_type}' ({tool.metadata.risk_tier.value}) wajib punya idempotency_key"
        )
