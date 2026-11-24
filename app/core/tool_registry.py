"""
app/core/tool_registry.py
Tool Registry Contract & Execution Interfaces for Kirata AI Workflow Builder.
Defines strict Pydantic contracts for every tool executable by the workflow runtime.
"""

from abc import ABC, abstractmethod
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Type
from packaging.version import Version
from pydantic import BaseModel, Field, model_validator

WIRE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def to_wire_name(tool_type: str) -> str:
    """Mengubah tool_type internal (boleh bertitik) menjadi nama fungsi aman untuk provider LLM."""
    wire = tool_type.replace(".", "__")
    if not WIRE_NAME_RE.match(wire):
        raise ValueError(
            f"Wire name '{wire}' (dari '{tool_type}') tidak memenuhi kontrak provider ^[a-zA-Z0-9_-]{{1,64}}$."
        )
    return wire


def from_wire_name(wire_name: str) -> str:
    """Mengembalikan wire name dari provider LLM ke tool_type internal."""
    return wire_name.replace("__", ".")


# =====================================================================
# 1. TOOL CONTRACT & CLASSIFICATION
# =====================================================================

class ToolCategory(str, Enum):
    INGESTION = "INGESTION"              # e.g., CSV upload, file parsing
    TRANSFORMATION = "TRANSFORMATION"    # e.g., normalization, cleaning, mapping
    ANALYSIS = "ANALYSIS"                # e.g., market matching, pricing formulas
    GOVERNANCE = "GOVERNANCE"            # e.g., human-in-the-loop action approval
    EXPORT = "EXPORT"                    # e.g., export catalog, notification


class ToolRiskTier(str, Enum):
    """Static risk classification of a tool's side effects.

    Declared in code by the tool author, never decided by an LLM.
    The validator requires an approval gate upstream of every EXTERNAL or FINANCIAL node.
    """
    NONE = "NONE"            # Read-only: ingest, filter, calculate, profile
    INTERNAL = "INTERNAL"    # Writes inside Kirata only: drafts, local reports, staging data
    EXTERNAL = "EXTERNAL"    # Reaches third parties: publishing, messaging, webhooks
    FINANCIAL = "FINANCIAL"  # Moves money or creates binding commitments: POs, invoices, payments


GATED_RISK_TIERS = frozenset({ToolRiskTier.EXTERNAL, ToolRiskTier.FINANCIAL})


class ToolMetadata(BaseModel):
    tool_type: str = Field(..., description="Unique tool identifier, e.g. 'retail.calculate_pricing'")
    version: str = Field(default="1.0.0", description="Semantic version of the tool contract")
    display_name: str = Field(..., description="Human friendly label for UI visual canvas")
    description: str = Field(..., description="Clear explanation of the tool's behavior and constraints")
    category: ToolCategory = Field(..., description="Classification category")
    required_permissions: List[str] = Field(default_factory=list, description="Required permissions to run this tool")
    is_side_effect: bool = Field(default=False, description="Whether this tool mutates external state or systems")
    requires_action_approval: bool = Field(
        default=False,
        description="Whether this tool must pause execution for human action-level approval"
    )
    risk_tier: Optional[ToolRiskTier] = Field(
        default=None,
        description="Static side-effect tier. If omitted, derived from is_side_effect "
                    "(True -> EXTERNAL, False -> NONE) for backward compatibility."
    )
    supported_templates: List[str] = Field(default_factory=list, description="Template IDs that support this tool")

    @model_validator(mode="after")
    def _derive_risk_tier(self) -> "ToolMetadata":
        if self.risk_tier is None:
            self.risk_tier = ToolRiskTier.EXTERNAL if self.is_side_effect else ToolRiskTier.NONE
        return self

    @property
    def requires_upstream_gate(self) -> bool:
        return self.risk_tier in GATED_RISK_TIERS


class ExecutionContext(BaseModel):
    tenant_id: str
    workflow_id: str
    workflow_version: int
    run_id: str
    is_dry_run: bool = False
    actor_id: Optional[str] = None
    extra_meta: Dict[str, Any] = Field(default_factory=dict)


# Generic Empty Models for tools that don't need input or config
class EmptyConfig(BaseModel):
    pass


class EmptyInput(BaseModel):
    pass


# =====================================================================
# 2. BASE TOOL INTERFACE
# =====================================================================

class BaseTool(ABC):
    """
    Abstract Base Class for all Kirata Tools.
    Every tool must declare strict Pydantic schemas for its Config, Inputs, and Outputs.
    """
    metadata: ToolMetadata
    config_schema: Type[BaseModel] = EmptyConfig
    input_schema: Type[BaseModel] = EmptyInput
    output_schema: Type[BaseModel] = BaseModel

    @abstractmethod
    async def execute(
        self,
        config: BaseModel,
        inputs: BaseModel,
        context: ExecutionContext
    ) -> BaseModel:
        """
        Deterministic execution logic of the tool.
        Must return an instance of self.output_schema.
        """
        pass

    def apply_business_config(
        self, raw_config: Dict[str, Any], business_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Merges workflow-level business_config into this tool's node config.

        Tools can override this method to translate domain-level business rules
        into tool-specific config parameters without coupling the runner to templates.
        """
        return dict(raw_config)

    def validate_config(self, raw_config: Dict[str, Any]) -> BaseModel:
        """Validates raw dict config against the tool's config_schema."""
        return self.config_schema.model_validate(raw_config)

    def validate_inputs(self, raw_inputs: Dict[str, Any]) -> BaseModel:
        """Validates raw dict inputs against the tool's input_schema."""
        return self.input_schema.model_validate(raw_inputs)

    def to_openai_tool(self) -> Dict[str, Any]:
        """Menghasilkan schema function-calling OpenAI dari input_schema Pydantic."""
        return {
            "type": "function",
            "function": {
                "name": to_wire_name(self.metadata.tool_type),
                "description": self.metadata.description,
                "parameters": self.input_schema.model_json_schema(),
            },
        }


# =====================================================================
# 3. TOOL REGISTRY SINGLETON
# =====================================================================

class ToolRegistry:
    """
    Central in-memory registry of all allowed and certified tools.
    The validator uses this to reject any non-allowlisted tool types or invalid configs.
    """
    _registry: Dict[str, Dict[str, BaseTool]] = {}  # {tool_type: {version: ToolInstance}}

    @classmethod
    def register(cls, tool_instance: BaseTool) -> None:
        ttype = tool_instance.metadata.tool_type
        tver = tool_instance.metadata.version
        if tool_instance.metadata.risk_tier in GATED_RISK_TIERS:
            if "idempotency_key" not in tool_instance.input_schema.model_fields:
                raise ValueError(
                    f"Tool '{ttype}' ber-risk_tier {tool_instance.metadata.risk_tier.value} "
                    "wajib memiliki field 'idempotency_key' di input_schema (I19)."
                )
        wire_name = to_wire_name(ttype)
        for existing_ttype in cls._registry:
            if existing_ttype != ttype and to_wire_name(existing_ttype) == wire_name:
                raise ValueError(
                    f"Tabrakan wire name '{wire_name}' antara '{ttype}' dan '{existing_ttype}'."
                )
        if ttype not in cls._registry:
            cls._registry[ttype] = {}
        cls._registry[ttype][tver] = tool_instance

    @classmethod
    def get(cls, tool_type: str, version: Optional[str] = None) -> Optional[BaseTool]:
        versions = cls._registry.get(tool_type)
        if not versions:
            return None
        if version:
            return versions.get(version)
        latest_ver = max(versions.keys(), key=Version)
        return versions[latest_ver]

    @classmethod
    def get_all_latest(cls, category: Optional[ToolCategory] = None) -> List[BaseTool]:
        """Mengembalikan versi terbaru untuk setiap tool_type terdaftar."""
        tools: List[BaseTool] = []
        for ttype in cls._registry:
            tool = cls.get(ttype)
            if tool and (category is None or tool.metadata.category == category):
                tools.append(tool)
        return tools

    @classmethod
    def is_allowlisted(cls, tool_type: str) -> bool:
        return tool_type in cls._registry

    @classmethod
    def list_tools(cls, category: Optional[ToolCategory] = None) -> List[ToolMetadata]:
        result = []
        for ttype, versions in cls._registry.items():
            for v, tool in versions.items():
                if category is None or tool.metadata.category == category:
                    result.append(tool.metadata)
        return result

    @classmethod
    def clear(cls) -> None:
        """Used in test suites."""
        cls._registry.clear()
