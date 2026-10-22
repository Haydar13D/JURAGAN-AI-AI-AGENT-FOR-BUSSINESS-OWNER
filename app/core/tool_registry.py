"""
app/core/tool_registry.py
Tool Registry Contract & Execution Interfaces for Kirata AI Workflow Builder.
Defines strict Pydantic contracts for every tool executable by the workflow runtime.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional, Type
from pydantic import BaseModel, Field


# =====================================================================
# 1. TOOL CONTRACT & CLASSIFICATION
# =====================================================================

class ToolCategory(str, Enum):
    INGESTION = "INGESTION"              # e.g., CSV upload, file parsing
    TRANSFORMATION = "TRANSFORMATION"    # e.g., normalization, cleaning, mapping
    ANALYSIS = "ANALYSIS"                # e.g., market matching, pricing formulas
    GOVERNANCE = "GOVERNANCE"            # e.g., human-in-the-loop action approval
    EXPORT = "EXPORT"                    # e.g., export catalog, notification


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
    supported_templates: List[str] = Field(default_factory=list, description="Template IDs that support this tool")


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

    def validate_config(self, raw_config: Dict[str, Any]) -> BaseModel:
        """Validates raw dict config against the tool's config_schema."""
        return self.config_schema.model_validate(raw_config)

    def validate_inputs(self, raw_inputs: Dict[str, Any]) -> BaseModel:
        """Validates raw dict inputs against the tool's input_schema."""
        return self.input_schema.model_validate(raw_inputs)


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
        # Default to latest semantic version registered
        latest_ver = sorted(versions.keys(), reverse=True)[0]
        return versions[latest_ver]

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
