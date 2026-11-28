from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class AgentChatRequest(BaseModel):
    outlet_id: int = Field(1, description="ID Outlet")
    message: str = Field(..., min_length=1, description="Pesan pertanyaan dari pengguna")
    conversation_id: Optional[str] = Field(None, description="ID sesi percakapan untuk konteks multi-turn")
    agent_id: str = Field("general", description="ID profil agent spesialis (general, sales_analyst, marketing_studio, ops_automation)")

class ToolExecutionRecord(BaseModel):
    tool_call_id: Optional[str] = None
    tool_name: str
    arguments: Dict[str, Any]
    output: Any

class AgentChatResponse(BaseModel):
    reply: str
    run_id: Optional[str] = None
    status: str = "DONE"
    action_id: Optional[str] = None
    pending_approval: Optional[Dict[str, Any]] = None
    tools_executed: List[str] = []
    tool_details: List[ToolExecutionRecord] = []
    conversation_id: Optional[str] = None
    agent_id: Optional[str] = None
    provider_used: Optional[str] = None
    model_used: Optional[str] = None

class ConversationMessageItem(BaseModel):
    role: str
    content: str
    tool_calls: Optional[str] = None
    created_at: str

class ConversationHistoryResponse(BaseModel):
    conversation_id: str
    outlet_id: int
    total_messages: int
    messages: List[ConversationMessageItem]

class ConversationResetResponse(BaseModel):
    status: str = "success"
    message: str
    conversation_id: str


class ApprovalDecisionRequest(BaseModel):
    reviewed_by: str = Field("owner", min_length=1, description="Identitas reviewer yang menyetujui/menolak aksi")


class AgentStepSchema(BaseModel):
    step: int
    tool_name: str
    arguments_json: str
    output_digest: Optional[str] = None
    error: Optional[str] = None
    duration_ms: int = 0


class AgentRunTraceResponse(BaseModel):
    run_id: str
    conversation_id: Optional[str] = None
    outlet_id: int
    status: str
    provider: Optional[str] = None
    model: Optional[str] = None
    steps_taken: int
    tool_calls_count: int
    latency_ms: int
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    est_cost_idr: Optional[float] = None
    trigger: str
    created_at: str
    steps: List[AgentStepSchema] = []
