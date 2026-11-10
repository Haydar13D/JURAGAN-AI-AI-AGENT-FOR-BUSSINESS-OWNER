"""
app/core/governance_tools.py
Domain-agnostic human-in-the-loop governance gate tools for the Kirata engine.
"""

from typing import Any, Optional
from pydantic import BaseModel, ConfigDict

from app.core.tool_registry import (
    BaseTool,
    ExecutionContext,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolRiskTier,
)


class OwnerActionApprovalConfig(BaseModel):
    action_type: str = "ACTION_APPROVAL"
    require_preview: bool = True


class OwnerActionApprovalInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    catalog: Optional[Any] = None
    campaign_payload: Optional[Any] = None
    payload: Optional[Any] = None


class OwnerActionApprovalTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="governance.action_approval",
        version="1.0.0",
        display_name="Owner Action Approval",
        description=(
            "Human-in-the-loop safety gate. Pauses execution for explicit owner approval "
            "before external side effects or financial transactions."
        ),
        category=ToolCategory.GOVERNANCE,
        is_side_effect=False,
        requires_action_approval=True,
        risk_tier=ToolRiskTier.NONE,
    )
    config_schema = OwnerActionApprovalConfig
    input_schema = OwnerActionApprovalInput
    output_schema = BaseModel

    async def execute(
        self,
        config: OwnerActionApprovalConfig,
        inputs: OwnerActionApprovalInput,
        context: ExecutionContext,
    ) -> Any:
        payload = inputs.catalog or inputs.campaign_payload or inputs.payload
        if payload is None and inputs.model_extra:
            # Accept any single bound upstream payload generically
            payload = next(iter(inputs.model_extra.values()), None)

        if payload is None:
            raise ValueError("No payload provided to owner_action_approval")

        if context.is_dry_run:
            return payload

        approved = context.extra_meta.get("action_approved", False)
        if not approved:
            raise PermissionError(
                f"ACTION_APPROVAL_REQUIRED: Human approval required before {config.action_type}."
            )

        return payload


def register_governance_tools() -> None:
    ToolRegistry.register(OwnerActionApprovalTool())

