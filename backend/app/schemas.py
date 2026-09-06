from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ClipboardEvent(BaseModel):
    kind: Literal["text", "image", "files", "other"]
    length_bucket: Literal["empty", "1-32", "33-256", "257-2048", "2049+"]
    occurred_at: datetime


class WindowContext(BaseModel):
    process: str = Field(max_length=260)
    window_title: str = Field(default="", max_length=2048)


class Interaction(BaseModel):
    key_count: int = Field(default=0, ge=0)
    mouse_click_count: int = Field(default=0, ge=0)
    scroll_count: int = Field(default=0, ge=0)
    idle_ms: int = Field(default=0, ge=0)
    clipboard_copy_count: int = Field(default=0, ge=0)
    clipboard_paste_count: int = Field(default=0, ge=0)
    clipboard_events: list[ClipboardEvent] = Field(default_factory=list, max_length=100)


class FeatureWindow(BaseModel):
    platform: Literal["windows", "android"] = "windows"
    device_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    start_time: datetime
    duration_ms: int = Field(gt=0, le=300_000)
    context: WindowContext
    interaction: Interaction

    @field_validator("start_time")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("start_time must include a timezone or Z suffix")
        return value


class BatchRequest(BaseModel):
    events: list[FeatureWindow] = Field(min_length=1, max_length=1000)


class SegmentCorrection(BaseModel):
    category: Literal["学习", "工作", "娱乐", "空闲", "其他", "睡眠", "运动", "出游", "用餐", "通勤", "休息", "家务"] | None = None
    purpose: str | None = Field(default=None, min_length=1, max_length=32)
    remember: bool = False
    memory_note: str | None = Field(default=None, max_length=280)

    @model_validator(mode="after")
    def require_at_least_one(self) -> "SegmentCorrection":
        if self.category is None and self.purpose is None:
            raise ValueError("at least one of category or purpose is required")
        return self


class HeartbeatRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    platform: Literal["windows", "android"] = "windows"
    collector_version: str = Field(default="", max_length=64)


class AgentMemoryRequest(BaseModel):
    """手动添加长期记忆（如"我最近在赶毕业设计 mini-nccl"）。"""

    kind: Literal["app_fact", "project_fact", "correction", "pattern"] = "project_fact"
    scope: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=280)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class OfflineActivityRequest(BaseModel):
    start_time: datetime
    end_time: datetime
    category: Literal["睡眠", "运动", "出游", "用餐", "通勤", "休息", "家务"]
    note: str = Field(default="", max_length=280)
    remember: bool = False

    @model_validator(mode="after")
    def validate_interval(self) -> "OfflineActivityRequest":
        from datetime import datetime, timezone
        for value in (self.start_time, self.end_time):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("times must include a timezone")
        seconds = (self.end_time - self.start_time).total_seconds()
        if not 0 < seconds <= 48 * 3600:
            raise ValueError("interval must be positive and at most 48 hours")
        if self.end_time > datetime.now(timezone.utc):
            raise ValueError("cannot annotate future activity")
        return self
