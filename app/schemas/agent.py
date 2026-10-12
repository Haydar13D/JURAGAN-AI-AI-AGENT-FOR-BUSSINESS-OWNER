from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class AgentChatRequest(BaseModel):
    outlet_id: int = Field(1, description="ID Outlet")
    message: str = Field(..., min_length=1, description="Pesan pertanyaan dari pengguna")
    conversation_id: Optional[str] = Field(None, description="ID sesi percakapan untuk konteks multi-turn")

class ToolExecutionRecord(BaseModel):
    tool_name: str
    arguments: Dict[str, Any]
    output: Any

class AgentChatResponse(BaseModel):
    reply: str
    tools_executed: List[str] = []
    tool_details: List[ToolExecutionRecord] = []
    conversation_id: Optional[str] = None

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
