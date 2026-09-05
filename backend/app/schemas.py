from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
    category: Literal["学习", "工作", "娱乐", "空闲", "其他"]
