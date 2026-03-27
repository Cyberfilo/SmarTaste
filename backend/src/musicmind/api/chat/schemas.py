"""Pydantic request/response models for Claude chat integration endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SendMessageRequest(BaseModel):
    """Request body for sending a message in a chat conversation."""

    conversation_id: str | None = Field(
        default=None,
        description="Existing conversation ID to continue, or None to start new",
    )
    message: str = Field(
        min_length=1,
        description="User message text to send to Claude",
    )


class MessageItem(BaseModel):
    """A single message in a conversation history."""

    role: str = Field(description="Message role: user, assistant, or tool")
    content: str = Field(description="Message text content")
    tool_use: dict | None = Field(
        default=None,
        description="Tool use block if Claude invoked a tool (name, id, input)",
    )
    tool_result: dict | None = Field(
        default=None,
        description="Tool result block with output from tool execution",
    )


class ConversationResponse(BaseModel):
    """Full conversation detail with all messages."""

    id: str = Field(description="Conversation unique identifier")
    title: str = Field(description="Conversation title (auto-generated from first message)")
    messages: list[MessageItem] = Field(
        default_factory=list,
        description="Ordered list of messages in the conversation",
    )
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last update timestamp")


class ConversationListItem(BaseModel):
    """Summary of a conversation for listing purposes."""

    id: str = Field(description="Conversation unique identifier")
    title: str = Field(description="Conversation title")
    message_count: int = Field(description="Total number of messages in conversation")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last update timestamp")


class ConversationListResponse(BaseModel):
    """List of user's conversations."""

    conversations: list[ConversationListItem] = Field(
        default_factory=list,
        description="List of conversation summaries, newest first",
    )
