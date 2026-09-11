from __future__ import annotations

import re
import sqlite3
from typing import Any


# 这组名字对应当前招聘多维表格的真实列名。后端仍会通过
# recruitment_feishu.resolve_field_mapping() 对照现场 schema，因此即使后续改列名，
# 也只需要调整映射而不是重写邮件解析逻辑。
PIPELINE_FIELDS = {
    "company": "投递公司",
    "url": "网申链接",
    "position": "岗位",
    "recruitment_type": "类型",
    "location": "工作地点",
    "status": "投递状态",
    "priority": "优先级",
    "application_date": "投递日期",
    "assessment_date": "测评日期",
    "written_date": "笔试日期",
    "first_interview_date": "一面日期",
    "second_interview_date": "二面日期",
    "third_interview_date": "三面日期",
    "note": "备注",
}

PIPELINE_FIELD_ORDER = tuple(PIPELINE_FIELDS)

RECRUITMENT_METADATA_COLUMNS = {
    "position": "TEXT",
    "recruitment_type": "TEXT",
    "location": "TEXT",
    "priority": "TEXT",
}

POSITION_PATTERNS = (
    re.compile(r"(?:应聘|申请|投递)?(?:岗位|职位|职位名称|应聘职位|申请职位)\s*[:：]\s*([^\n\r，,。；;|｜]{2,80})", re.I),
    re.compile(r"(?:position|role)\s*[:：]\s*([^\n\r,;|]{2,80})", re.I),
)
LOCATION_PATTERNS = (
    re.compile(r"(?:工作地点|工作城市|办公地点|办公城市)\s*[:：]\s*([^\n\r，,。；;|｜]{2,40})", re.I),
    re.compile(r"(?:location)\s*[:：]\s*([^\n\r,;|]{2,40})", re.I),
)
POSITION_HINTS = (
    "工程师", "开发", "研发", "嵌入式", "软件", "硬件", "算法", "后端", "前端",
    "客户端", "测试", "系统", "内核", "驱动", "运维", "产品", "运营", "研究员",
)
NOISE_SEGMENTS = (
    "笔试", "测评", "面试", "通知", "邀请", "校招", "秋招", "春招", "校园招聘",
    "招聘", "offer", "录用", "感谢信",
)


def ensure_recruitment_metadata_columns(connection: sqlite3.Connection) -> None:
    """为已有生产数据库做幂等的小步升级，不重建 recruitment_items。"""
    existing = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(recruitment_items)").fetchall()
    }
    for name, column_type in RECRUITMENT_METADATA_COLUMNS.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE recruitment_items ADD COLUMN {name} {column_type}")


def _clean_candidate(value: str, max_length: int) -> str:
    value = re.sub(r"\s+", " ", value or "").strip(" -—|｜:：,，;；。")
    value = re.split(
        r"(?:工作地点|工作城市|办公地点|面试时间|笔试时间|测评时间|截止时间|时间)\s*[:：]",
        value,
        maxsplit=1,
    )[0].strip()
    return value[:max_length]


def infer_position(subject: str, body: str = "") -> str:
    text = f"{subject}\n{body[:12000]}"
    for pattern in POSITION_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = _clean_candidate(match.group(1), 128)
            if len(candidate) >= 2:
                return candidate

    # 常见招聘主题会写成 “公司-嵌入式软件开发-笔试通知”。只在分段里存在明确岗位词时兜底，
    # 避免把公司名或“笔试通知”误当岗位。
    for part in re.split(r"[-—|｜:：/\\]", subject):
        candidate = _clean_candidate(part, 128)
        lowered = candidate.lower()
        if len(candidate) < 2 or any(noise.lower() in lowered for noise in NOISE_SEGMENTS):
            continue
        if any(hint.lower() in lowered for hint in POSITION_HINTS):
            return candidate
    return ""


def infer_recruitment_type(subject: str, body: str = "") -> str:
    text = f"{subject}\n{body[:12000]}".lower()
    if any(token in text for token in ("实习", "intern", "internship")):
        return "实习"
    if any(token in text for token in ("社招", "社会招聘", "experienced hire", "lateral hire")):
        return "社招"
    if any(token in text for token in ("校招", "校园招聘", "秋招", "春招", "应届", "campus recruitment", "graduate")):
        return "校招"
    return ""


def infer_location(subject: str, body: str = "") -> str:
    text = f"{subject}\n{body[:12000]}"
    for pattern in LOCATION_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = _clean_candidate(match.group(1), 64)
            if len(candidate) >= 2:
                return candidate
    return ""


def extract_pipeline_metadata(subject: str, body: str = "") -> dict[str, str]:
    return {
        "position": infer_position(subject, body),
        "recruitment_type": infer_recruitment_type(subject, body),
        "location": infer_location(subject, body),
        "priority": "",
    }


def stage_date_field(stage: str | None) -> str | None:
    normalized = (stage or "").strip().lower()
    if normalized in {"测评", "assessment"}:
        return PIPELINE_FIELDS["assessment_date"]
    if normalized in {"笔试", "written test"}:
        return PIPELINE_FIELDS["written_date"]
    if normalized in {"一面", "1面", "第一面", "面试", "interview"}:
        return PIPELINE_FIELDS["first_interview_date"]
    if normalized in {"二面", "2面", "第二面", "复试"}:
        return PIPELINE_FIELDS["second_interview_date"]
    if normalized in {"三面", "3面", "第三面"}:
        return PIPELINE_FIELDS["third_interview_date"]
    return None


def build_mail_pipeline_fields(item: dict[str, Any], stage: str, subject: str) -> dict[str, Any]:
    """把一条邮件事项转换成待审核的飞书字段，不在这里执行任何飞书写操作。"""
    fields: dict[str, Any] = {}

    def put(name: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str) and not value.strip():
            return
        fields[name] = value

    put(PIPELINE_FIELDS["company"], item.get("company"))
    put(PIPELINE_FIELDS["url"], item.get("action_url"))
    put(PIPELINE_FIELDS["position"], item.get("position"))
    put(PIPELINE_FIELDS["recruitment_type"], item.get("recruitment_type"))
    put(PIPELINE_FIELDS["location"], item.get("location"))
    put(PIPELINE_FIELDS["priority"], item.get("priority"))
    put(PIPELINE_FIELDS["status"], stage)

    event_at = item.get("start_at") or item.get("deadline_at")
    date_field = stage_date_field(stage)
    if date_field and event_at:
        put(date_field, event_at)

    note_parts = [subject.strip()]
    extraction_note = str(item.get("extraction_note") or "").strip()
    if extraction_note and extraction_note not in note_parts:
        note_parts.append(extraction_note)
    put(PIPELINE_FIELDS["note"], "\n".join(part for part in note_parts if part)[:1000])
    return fields
