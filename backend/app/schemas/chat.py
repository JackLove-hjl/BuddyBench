"""对话(SSE)API 模型。"""
import uuid
from typing import Literal

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    """上传的附件(图片或文本文件)。"""

    url: str
    filename: str = ""
    kind: str = "file"  # image | file
    content: str = ""  # 文本文件内容(前端读取后随请求提交)


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID
    model: str
    message: str = Field(min_length=1, max_length=20000)
    attachments: list[Attachment] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    """单个待审批动作的决策(对应 LangGraph HITL 的 DecisionType 子集)。

    - approve / reject:危险操作与计划评审的批准 / 驳回(reject 可附带说明);
    - respond:ask_user 的作答 —— 不执行工具,而是把 message 作为 `status=success`
      的合成 ToolMessage 回给模型。
    """

    type: Literal["approve", "reject", "respond"]
    message: str | None = None  # reject 的说明 / respond 的答案正文


class ApprovalRequest(BaseModel):
    """人工审批:批准/拒绝后恢复被挂起的执行。"""

    conversation_id: uuid.UUID
    model: str
    decisions: list[ApprovalDecision] = Field(default_factory=list)


class ModelsResponse(BaseModel):
    models: list[dict]
