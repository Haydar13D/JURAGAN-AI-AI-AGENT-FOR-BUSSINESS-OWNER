"""
app/schemas/workflow.py
Canonical Workflow Definition & Typed Patch Schemas for Kirata AI Workflow Builder.
Single source of truth for both React Flow visualization and LangGraph / runtime execution.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# =====================================================================
# 1. ENUMS & CONSTANTS
# =====================================================================

class WorkflowStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    DEPRECATED = "DEPRECATED"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class PatchOpType(str, Enum):
    UPDATE_NODE_CONFIG = "UPDATE_NODE_CONFIG"
    INSERT_NODE = "INSERT_NODE"
    REMOVE_NODE = "REMOVE_NODE"
    ADD_EDGE = "ADD_EDGE"
    REMOVE_EDGE = "REMOVE_EDGE"
    UPDATE_APPROVAL_POLICY = "UPDATE_APPROVAL_POLICY"
    UPDATE_METADATA = "UPDATE_METADATA"


class RunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


class ActionDecisionType(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REVISE = "REVISE"


# =====================================================================
# 2. CANONICAL WORKFLOW DEFINITION (NODES, EDGES, POLICY)
# =====================================================================

class NodePosition(BaseModel):
    x: float = 0.0
    y: float = 0.0


class WorkflowNode(BaseModel):
    id: str = Field(..., description="Unique node ID within the workflow DAG, e.g. 'calculate_pricing'")
    tool_type: str = Field(..., description="Allowlisted tool identifier in ToolRegistry, e.g. 'retail.calculate_pricing'")
    tool_version: str = Field(default="1.0.0", description="Semantic version of the tool contract")
    display_name: Optional[str] = Field(None, description="Human readable label for visual canvas")
    config: Dict[str, Any] = Field(default_factory=dict, description="Configurable parameters matching the tool's config schema")
    input_bindings: Dict[str, str] = Field(
        default_factory=dict,
        description="Data mapping from upstream outputs, e.g. {'items': 'validate_attributes.output'}"
    )
    output_schema: str = Field(..., description="Name of the Pydantic schema model produced by this node")
    position: Optional[NodePosition] = Field(default_factory=NodePosition, description="Canvas layout coordinate")


class EdgeCondition(BaseModel):
    expression: Optional[str] = Field(None, description="Logical predicate condition for branching, e.g. 'item.has_exceptions == true'")
    label: Optional[str] = Field(None, description="Display label on the edge canvas")


class WorkflowEdge(BaseModel):
    id: Optional[str] = Field(None, description="Unique edge identifier, auto-generated if omitted (e.g. 'e_source_target')")
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    condition: Optional[EdgeCondition] = Field(None, description="Optional branching condition")

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            self.id = f"e_{self.source}_{self.target}"


class ApprovalPolicy(BaseModel):
    require_config_activation_approval: bool = Field(
        default=True,
        description="Whether changes to the workflow graph require explicit activation approval before execution"
    )
    require_action_approval: bool = Field(
        default=True,
        description="Whether consequential business actions (e.g. export catalog, update live prices) require human approval during run"
    )
    auto_approve_if_dry_run_clean: bool = Field(
        default=False,
        description="Strict safety guardrail: must remain false in production MVP"
    )


class WorkflowDefinition(BaseModel):
    id: str = Field(..., description="Workflow ID, e.g. 'wf_retail_pricing_001'")
    tenant_id: str = Field(..., description="Tenant ID for strict multi-tenant boundary")
    template_id: str = Field(..., description="Origin template ID, e.g. 'tpl_used_iphone_pricing_v1'")
    version: int = Field(default=1, description="Immutable integer version number")
    status: WorkflowStatus = Field(default=WorkflowStatus.DRAFT, description="Current workflow state")
    name: str = Field(..., description="Descriptive workflow name")
    description: Optional[str] = Field(None, description="Detailed explanation of what this workflow accomplishes")
    schema_version: str = Field(default="2.0", description="Canonical schema specification version")
    nodes: List[WorkflowNode] = Field(default_factory=list, description="DAG nodes in the workflow")
    edges: List[WorkflowEdge] = Field(default_factory=list, description="Connections between DAG nodes")
    permissions: List[str] = Field(default_factory=list, description="Required system permissions for execution")
    approval_policy: ApprovalPolicy = Field(default_factory=ApprovalPolicy, description="Governance approval settings")
    created_at: Optional[str] = None
    activated_at: Optional[str] = None


# =====================================================================
# 3. TYPED PATCH & PROPOSAL SCHEMAS (AI OUTPUT)
# =====================================================================

class PatchOperation(BaseModel):
    op: PatchOpType = Field(..., description="Type of atomic graph modification")
    node_id: Optional[str] = Field(None, description="Target node ID for node operations")
    node_data: Optional[WorkflowNode] = Field(None, description="Full node specification when inserting a node")
    config_patch: Optional[Dict[str, Any]] = Field(None, description="Key-value overrides for UPDATE_NODE_CONFIG")
    edge: Optional[WorkflowEdge] = Field(None, description="Edge to add or remove")
    edge_id: Optional[str] = Field(None, description="Edge ID to remove")
    policy_patch: Optional[Dict[str, Any]] = Field(None, description="Updates to ApprovalPolicy")


class NodeDiffStatus(str, Enum):
    UNCHANGED = "UNCHANGED"
    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    REMOVED = "REMOVED"


class NodeVisualDiff(BaseModel):
    node_id: str
    status: NodeDiffStatus
    tool_type: str
    config_changes: Optional[Dict[str, Dict[str, Any]]] = Field(
        default=None,
        description="Dict of {field_name: {'before': val1, 'after': val2}}"
    )
    explanation: Optional[str] = None


class GraphDiff(BaseModel):
    nodes: List[NodeVisualDiff] = Field(default_factory=list)
    added_edges: List[str] = Field(default_factory=list)
    removed_edges: List[str] = Field(default_factory=list)
    summary: str = Field(..., description="Human-readable summary of the diff")


class WorkflowProposal(BaseModel):
    base_version: int = Field(..., description="Base workflow version being patched")
    proposed_version: int = Field(..., description="Target version number after patch")
    rationale: str = Field(..., description="Clear explanation in Indonesian of what was changed and why")
    risk_level: RiskLevel = Field(default=RiskLevel.LOW, description="Assessment of business/operational risk")
    patch_operations: List[PatchOperation] = Field(default_factory=list, description="List of atomic graph operations")
    diff: Optional[GraphDiff] = Field(None, description="Computed visual diff before/after")
    is_valid: bool = Field(default=False, description="Whether patch passed server-side DAG and schema validation")
    validation_errors: List[str] = Field(default_factory=list, description="Actionable errors if validation failed")
    requires_clarification: bool = Field(default=False, description="True if prompt was ambiguous")
    clarification_message: Optional[str] = Field(None, description="Polite question to the owner")


# =====================================================================
# 4. DRY-RUN & EXECUTION RUNTIME SCHEMAS
# =====================================================================

class NodeRunEvent(BaseModel):
    node_id: str
    tool_type: str
    status: RunStatus
    started_at: str
    completed_at: Optional[str] = None
    input_snapshot: Optional[Dict[str, Any]] = None
    output_snapshot: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    execution_time_ms: Optional[float] = None


class DryRunResult(BaseModel):
    workflow_id: str
    version: int
    is_clean: bool = Field(..., description="True if dry run completed without exceptions or violations")
    node_traces: List[NodeRunEvent] = Field(default_factory=list)
    items_processed: int = 0
    items_flagged_for_review: int = 0
    estimated_api_cost_usd: float = 0.0
    summary: str = ""


class ActionApprovalRequest(BaseModel):
    action_id: str
    run_id: str
    node_id: str
    action_type: str = "CATALOG_PUBLISH_OR_EXPORT"
    preview_data: Dict[str, Any] = Field(..., description="Exact payload preview, e.g. rows of calculated iPhone prices")
    created_at: str
    expires_at: Optional[str] = None


class ActionDecisionRequest(BaseModel):
    decision: ActionDecisionType
    decision_reason: Optional[str] = None
    reviewer_user_id: str
