from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Settings(StrictModel):
    stalled_days: int = Field(21, ge=1, le=3650)
    inactive_days: int = Field(90, ge=1, le=3650)
    old_months: int = Field(5, ge=1, le=120)
    dead_years: int = Field(2, ge=1, le=20)
    frequent_messages: int = Field(20, ge=4, le=1000)
    history_limit: int = Field(100, ge=20, le=500)
    scan_interval_hours: int = Field(0, ge=0, le=720)
    llm_after_scan: bool = False
    llm_model: str = Field("gpt-6-astra", min_length=1, max_length=120)
    llm_max_chats: int = Field(20, ge=1, le=50)
    redact_contacts: bool = True


class Annotation(StrictModel):
    important: bool | None = None
    dating: bool | None = None
    stage: Literal["new", "talking", "meeting_planned", "met", "relationship", "closed"] = "new"
    last_progress_at: datetime | None = None
    risk_review: Literal["auto", "trusted", "scam"] = "auto"
    notes: str = Field("", max_length=10000)
    reply_draft: str = Field("", max_length=10000)

    @field_validator("last_progress_at")
    @classmethod
    def progress_date(cls, value):
        if value is not None:
            value = value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
            if value > datetime.now(timezone.utc):
                raise ValueError("Дата прогресса не может быть в будущем")
        return value


class Message(StrictModel):
    id: int
    date: datetime
    outgoing: bool
    text: str = ""
    urls: list[str] = Field(default_factory=list)
    forwarded: bool = False
    media: str | None = None

    @field_validator("date")
    @classmethod
    def utc_date(cls, value):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)


class Chat(StrictModel):
    id: str
    title: str
    username: str | None = None
    kind: Literal["user", "group", "channel", "bot"] = "user"
    pinned: bool = False
    archived: bool = False
    unread_count: int = 0
    telegram_scam: bool = False
    telegram_fake: bool = False
    deleted: bool = False
    last_message_id: int | None = None
    messages: list[Message] = Field(default_factory=list)
    history_status: Literal["ok", "limited", "unavailable"] = "ok"
    history_limit: int = 100
    history_fetched_at: datetime | None = None
    demo: bool = False


class LLMChatResult(StrictModel):
    chat_id: str = Field(min_length=1, max_length=80)
    importance: Literal["important", "normal", "uncertain"]
    dating: Literal["yes", "no", "uncertain"]
    development: Literal["progress", "stalled", "uncertain"]
    scam_risk: Literal["low", "medium", "high", "unknown"]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=2500)
    evidence_message_ids: list[int] = Field(max_length=20)
    suggested_action: str = Field(max_length=1000)


class LLMBatchResult(StrictModel):
    results: list[LLMChatResult] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [row.chat_id for row in self.results]
        if len(ids) != len(set(ids)):
            raise ValueError("Повторяющиеся chat_id в ответе")
        return self


class BatchRequest(StrictModel):
    chat_ids: list[str] = Field(min_length=1, max_length=50)
    redact_contacts: bool = True

    @field_validator("chat_ids")
    @classmethod
    def unique_ids(cls, value):
        return list(dict.fromkeys(value))


class ImportRequest(StrictModel):
    batch_id: str
    result: LLMBatchResult


class AuthApp(StrictModel):
    api_id: int = Field(gt=0)
    api_hash: str = Field(pattern=r"^[a-fA-F0-9]{32}$")


class AuthStart(AuthApp):
    phone: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")


class ScanRequest(StrictModel):
    full: bool = False


class AuthCode(StrictModel):
    code: str = Field(min_length=1, max_length=128)


class AuthPassword(StrictModel):
    password: str = Field(min_length=1, max_length=256)


class LLMKey(StrictModel):
    api_key: str = Field(min_length=10, max_length=500)

