"""
app/schemas/workflow.py
Canonical Workflow Definition & Typed Patch Schemas for Kirata AI Workflow Builder.
Single source of truth for both React Flow visualization and LangGraph / runtime execution.
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field, model_validator


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
# 1B. BUSINESS CONFIGURATION SCHEMAS (LEVEL 1 CUSTOMIZATION)
# =====================================================================

class FBNCampaignConfig(BaseModel):
    max_discount_pct: float = Field(
        default=20.0,
        ge=0.0,
        le=50.0,
        description="Batas diskon maksimal untuk promosi (0 - 50%)"
    )
    minimum_gross_margin_pct: float = Field(
        default=25.0,
        ge=0.0,
        le=100.0,
        description="Batas margin kotor minimum yang harus dipertahankan setelah diskon"
    )
    sales_drop_threshold_pct: float = Field(
        default=25.0,
        ge=0.0,
        le=100.0,
        description="Ambang batas penurunan kuantiti penjualan untuk memicu deteksi anomali"
    )
    campaign_duration_days: int = Field(
        default=7,
        ge=1,
        le=30,
        description="Durasi masa berlaku kampanye promosi dalam hitungan hari (1 - 30)"
    )
    flyer_tagline: str = Field(
        default="SPESIAL AFTERNOON TREAT",
        description="Tagline headline promosi yang tercetak pada flyer"
    )
    flyer_cta_text: str = Field(
        default="Klaim Promo di Kasir",
        description="Teks tombol ajakan aksi (Call to Action) pada flyer"
    )
    target_audience: str = Field(
        default="Pekerja remote & pelanggan setia jam santai",
        description="Target audiens kampanye promosi"
    )
    focus_product_name: Optional[str] = Field(
        default=None,
        description="Nama menu makanan/minuman spesifik yang difokuskan untuk promosi"
    )
    flyer_style: str = Field(
        default="photography",
        description="Gaya visual flyer: 'photography' (studio foto komersial), 'ai_flux' (generasi AI Flux), atau 'vector_svg' (vektor grafis)"
    )

    @model_validator(mode="after")
    def validate_cross_field_margin_discount(self) -> "FBNCampaignConfig":
        if self.max_discount_pct >= 50.0 and self.minimum_gross_margin_pct > 60.0:
            raise ValueError(
                "Kombinasi parameter tidak aman: diskon maksimal 50% akan membuat sebagian besar menu "
                "gagal memenuhi target margin kotor minimum > 60%."
            )
        return self


class RetailPricingConfig(BaseModel):
    pricing_method: Literal["markup", "gross_margin"] = Field(
        default="markup",
        description="Metode kalkulasi harga: 'markup' (cost * (1 + rate)) atau 'gross_margin' (cost / (1 - rate))"
    )
    target_profit_pct: float = Field(
        default=18.0,
        ge=0.0,
        le=90.0,
        description="Target persentase keuntungan (markup rate atau margin rate)"
    )
    overhead_cost_idr: int = Field(
        default=75000,
        ge=0,
        description="Biaya overhead tetap per unit dalam rupiah"
    )
    market_deviation_threshold_pct: float = Field(
        default=25.0,
        ge=0.0,
        le=100.0,
        description="Batas deviasi maksimal dari harga pasar acuan sebelum diberi flag review"
    )
    rounding_increment_idr: int = Field(
        default=50000,
        gt=0,
        description="Kelipatan pembulatan harga jual rupiah (harus > 0)"
    )


class ConfigPatchOperation(BaseModel):
    operation: Literal["replace"] = "replace"
    path: str = Field(..., description="Allowlisted JSON path, e.g. '/config/max_discount_pct'")
    value: Any = Field(..., description="Target value to replace")


class BusinessProposal(BaseModel):
    proposal_id: str = Field(..., description="Unique proposal identifier, e.g. 'prop_abc123'")
    intent: str = "MODIFY_WORKFLOW"
    workflow_id: str
    base_version: int
    changes: List[ConfigPatchOperation] = Field(default_factory=list)
    explanation: str
    diff_summary: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    is_valid: bool = True
    validation_errors: List[str] = Field(default_factory=list)
    status: Literal["PROPOSED", "VALIDATED", "PENDING_APPROVAL", "APPROVED", "REJECTED"] = "PENDING_APPROVAL"
    created_at: str
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None


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
    business_config: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Typed business configuration parameters")
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


# =====================================================================
# 5. PHASE 2: BUSINESS CONFIGURATION & PROPOSAL SCHEMAS
# =====================================================================

class FBNCampaignConfig(BaseModel):
    """
    Typed business configuration for F&B Sales-to-Marketing Workflow.
    Guarantees strict parameter bounds and business sanity.
    """
    max_discount_pct: float = Field(
        default=20.0,
        ge=0.0,
        le=50.0,
        description="Batas diskon maksimum untuk promo (0% - 50%)"
    )
    minimum_gross_margin_pct: float = Field(
        default=25.0,
        ge=0.0,
        le=100.0,
        description="Batas margin kotor minimum agar promo tetap profitabel (0% - 100%)"
    )
    sales_drop_threshold_pct: float = Field(
        default=20.0,
        ge=0.0,
        le=100.0,
        description="Ambang batas penurunan penjualan (%) untuk memicu kampanye"
    )
    campaign_duration_days: int = Field(
        default=7,
        ge=1,
        le=30,
        description="Durasi berjalannya promo dalam hari (1 - 30 hari)"
    )
    flyer_tagline: str = Field(
        default="SPESIAL AFTERNOON TREAT",
        description="Tagline headline promosi yang tercetak pada flyer"
    )
    flyer_cta_text: str = Field(
        default="Klaim Promo di Kasir",
        description="Teks tombol ajakan aksi (Call to Action) pada flyer"
    )
    target_audience: str = Field(
        default="Pekerja remote & pelanggan setia jam santai",
        description="Target audiens kampanye promosi"
    )
    focus_product_name: Optional[str] = Field(
        default=None,
        description="Nama menu makanan/minuman spesifik yang difokuskan untuk promosi"
    )
    flyer_style: str = Field(
        default="photography",
        description="Gaya visual flyer: 'photography' (studio foto komersial), 'ai_flux' (generasi AI Flux), atau 'vector_svg' (vektor grafis)"
    )

    @model_validator(mode="after")
    def validate_cross_field_sanity(self) -> "FBNCampaignConfig":
        if self.max_discount_pct + self.minimum_gross_margin_pct > 100.0:
            raise ValueError(
                f"Kombinasi diskon maksimum ({self.max_discount_pct}%) dan target margin minimum "
                f"({self.minimum_gross_margin_pct}%) melebihi 100% dan berisiko merugikan bisnis."
            )
        return self


class RetailPricingConfig(BaseModel):
    """
    Typed business configuration for Retail Dynamic Pricing Workflow.
    """
    pricing_method: Literal["markup", "gross_margin"] = Field(
        default="gross_margin",
        description="Metode kalkulasi harga: markup dari modal vs gross margin"
    )
    target_profit_pct: float = Field(
        default=15.0,
        ge=0.0,
        le=100.0,
        description="Target keuntungan (%) sesuai metode yang dipilih"
    )
    overhead_cost_idr: int = Field(
        default=50000,
        ge=0,
        description="Biaya operasional/overhead per unit (Rupiah)"
    )
    market_deviation_threshold_pct: float = Field(
        default=15.0,
        ge=0.0,
        le=100.0,
        description="Ambang deviasi harga terhadap pasar sebelum ditandai review"
    )
    rounding_increment_idr: int = Field(
        default=50000,
        ge=1000,
        description="Kelipatan pembulatan harga jual (Rupiah)"
    )


class ConfigPatchOperation(BaseModel):
    """
    Strict allowlist patch operation for business configuration.
    Only 'replace' operations on '/config/...' paths are permitted.
    """
    op: Literal["replace"] = Field(default="replace", description="Only 'replace' is supported")
    path: str = Field(..., description="JSON pointer path starting with '/config/'")
    value: Any = Field(..., description="Target value to replace")


class ProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    ACTIVATED = "ACTIVATED"
    REJECTED = "REJECTED"


class BusinessProposal(BaseModel):
    """
    Structured Business Configuration Proposal (Level 1 Parameter Customization).
    Tracks full lifecycle from natural language prompt to human approval and activation.
    """
    proposal_id: str = Field(..., description="Unique proposal ID, e.g. 'prop_fnb_89a1c2'")
    intent: str = Field(..., description="Detected business intent, e.g. 'MODIFY_PROMO_DISCOUNT'")
    workflow_id: str = Field(..., description="Target workflow ID")
    base_version: int = Field(..., description="Active version of workflow when proposal was created")
    proposed_version: int = Field(..., description="Next version number upon approval (base_version + 1)")
    changes: List[ConfigPatchOperation] = Field(default_factory=list, description="Validated config patch operations")
    explanation: str = Field(..., description="Human-friendly explanation in Indonesian")
    diff_summary: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Before/after summary: {field_name: {'before': old_val, 'after': new_val}}"
    )
    is_valid: bool = Field(default=False, description="Whether patch passed schema & business validation")
    validation_errors: List[str] = Field(default_factory=list, description="Validation error messages if invalid")
    status: ProposalStatus = Field(default=ProposalStatus.PROPOSED, description="Current lifecycle state")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    candidate_config: Optional[Dict[str, Any]] = None


class AssistantIntent(str, Enum):
    RUN_WORKFLOW = "RUN_WORKFLOW"
    MODIFY_WORKFLOW = "MODIFY_WORKFLOW"
    EXPLAIN_RESULT = "EXPLAIN_RESULT"
    VIEW_WORKFLOW = "VIEW_WORKFLOW"
    UNKNOWN = "UNKNOWN"


class AssistantMessageRequest(BaseModel):
    message: str = Field(..., description="User message in natural language")
    workflow_id: Optional[str] = Field(None, description="Optional target workflow context")
    tenant_id: Optional[str] = Field(default="tenant_default", description="Tenant ID")


class AssistantMessageResponse(BaseModel):
    intent: AssistantIntent
    reply: str
    workflow_id: Optional[str] = None
    proposal: Optional[BusinessProposal] = None
    action_required: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

