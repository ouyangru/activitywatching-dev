from __future__ import annotations

import email
import imaplib
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from html import unescape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


RECRUITMENT_KEYWORDS = (
    "笔试", "测评", "面试", "校招", "秋招", "招聘", "在线考试", "在线测评",
    "assessment", "interview", "written test", "campus recruitment",
)
TYPE_RULES = (
    ("interview", ("面试", "interview")),
    ("written_test", ("笔试", "在线考试", "written test")),
    ("assessment", ("测评", "assessment")),
)
DATE_PATTERNS = (
    re.compile(r"(?P<year>20\d{2})[年\-/\.](?P<month>\d{1,2})[月\-/\.](?P<day>\d{1,2})日?(?:\s*(?P<hour>\d{1,2})[:：](?P<minute>\d{2}))?"),
    re.compile(r"(?P<month>\d{1,2})月(?P<day>\d{1,2})日(?:\s*(?P<hour>\d{1,2})[:：](?P<minute>\d{2}))?"),
)
DEADLINE_HINTS = ("截止", "之前完成", "前完成", "有效期", "完成测评", "完成笔试", "完成考试")
FIXED_TIME_HINTS = ("考试时间", "笔试时间", "面试时间", "测评时间", "开始时间")
RELATIVE_DAY_RE = re.compile(r"(?P<n>\d{1,2})\s*(?:个)?(?:自然)?(?:天|日)内")
RELATIVE_HOUR_RE = re.compile(r"(?P<n>\d{1,3})\s*(?:个)?小时内")
URL_RE = re.compile(r"https?://[^\s<>\"']+")
HREF_RE = re.compile(r"href\s*=\s*[\"'](?P<url>https?://[^\"']+)[\"']", re.I)
TAG_RE = re.compile(r"<[^>]+>")


class RecruitmentPatch(BaseModel):
    company: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=512)
    item_type: str | None = Field(default=None, max_length=64)
    mode: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=64)
    start_at: str | None = None
    end_at: str | None = None
    deadline_at: str | None = None
    deadline_precision: str | None = Field(default=None, max_length=32)
    action_url: str | None = Field(default=None, max_length=2048)


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_recruitment_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS recruitment_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL UNIQUE,
                imap_uid TEXT,
                company TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                item_type TEXT NOT NULL DEFAULT 'other',
                mode TEXT NOT NULL DEFAULT 'uncertain',
                status TEXT NOT NULL DEFAULT 'pending',
                start_at TEXT,
                end_at TEXT,
                deadline_at TEXT,
                deadline_precision TEXT,
                action_url TEXT,
                source_subject TEXT,
                source_sender TEXT,
                source_received_at TEXT,
                extraction_note TEXT,
                calendar_synced INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_recruitment_status_deadline
            ON recruitment_items(status, deadline_at);
            CREATE TABLE IF NOT EXISTS recruitment_mail_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT,
                imap_uid TEXT,
                subject TEXT,
                action TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_recruitment_mail_message
            ON recruitment_mail_log(message_id);
            """
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _message_text(message: Message) -> str:
    chunks: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_maintype() == "multipart":
            continue
        if "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        text = _decode_payload(part)
        if not text:
            continue
        if part.get_content_type() == "text/html":
            links = [unescape(match.group("url")) for match in HREF_RE.finditer(text)]
            text = TAG_RE.sub(" ", unescape(text))
            if links:
                text += "\n" + "\n".join(links)
        chunks.append(text)
    return "\n".join(chunks)


def _guess_company(subject: str, sender: str) -> str:
    bracket = re.search(r"[【\[]([^】\]]{2,24})[】\]]", subject)
    if bracket:
        candidate = bracket.group(1).strip()
        candidate = re.sub(r"(校招|秋招|招聘|笔试|测评|面试|通知|邀请)", "", candidate).strip()
        if len(candidate) >= 2:
            return candidate
    cleaned = re.sub(r"[【\[（(].*?[】\]）)]", " ", subject)
    cleaned = re.sub(r"(校园招聘|校招|秋招|招聘|笔试|测评|面试|通知|邀请|在线考试|在线测评)", " ", cleaned, flags=re.I)
    candidate = re.split(r"[-—|｜:：]", cleaned)[0].strip()
    if 1 < len(candidate) <= 24:
        return candidate
    address = sender.split("<")[-1].strip(" >")
    domain = address.split("@")[-1] if "@" in address else ""
    return domain.split(".")[0] if domain else "未知公司"


def _classify(subject: str, body: str) -> str:
    text = (subject + "\n" + body[:5000]).lower()
    for item_type, keywords in TYPE_RULES:
        if any(keyword.lower() in text for keyword in keywords):
            return item_type
    return "other"


def _parse_date(text: str, base: datetime) -> tuple[datetime | None, str | None]:
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        values = match.groupdict()
        year = int(values.get("year") or base.year)
        month = int(values["month"])
        day = int(values["day"])
        hour = int(values.get("hour") or 0)
        minute = int(values.get("minute") or 0)
        precision = "minute" if values.get("hour") else "day"
        try:
            parsed = datetime(year, month, day, hour, minute, tzinfo=base.tzinfo)
            if not values.get("year") and parsed.date() < base.date() - timedelta(days=180):
                parsed = parsed.replace(year=year + 1)
            return parsed, precision
        except ValueError:
            continue
    return None, None


def _find_context_date(text: str, hints: tuple[str, ...], base: datetime) -> tuple[datetime | None, str | None, str]:
    compact = re.sub(r"\s+", " ", text)
    for hint in hints:
        pos = compact.find(hint)
        if pos < 0:
            continue
        window = compact[max(0, pos - 100):min(len(compact), pos + 220)]
        parsed, precision = _parse_date(window, base)
        if parsed:
            return parsed, precision, window
    return None, None, ""


def _relative_deadline(text: str, base: datetime) -> tuple[datetime | None, str]:
    compact = re.sub(r"\s+", " ", text)
    day_match = RELATIVE_DAY_RE.search(compact)
    if day_match and any(hint in compact[max(0, day_match.start() - 80):day_match.end() + 80] for hint in ("收到", "邮件", "通知", "完成", "有效")):
        days = int(day_match.group("n"))
        return base + timedelta(days=days), f"按邮件接收时间推算：{days} 天内完成"
    hour_match = RELATIVE_HOUR_RE.search(compact)
    if hour_match and any(hint in compact[max(0, hour_match.start() - 80):hour_match.end() + 80] for hint in ("收到", "邮件", "通知", "完成", "有效")):
        hours = int(hour_match.group("n"))
        return base + timedelta(hours=hours), f"按邮件接收时间推算：{hours} 小时内完成"
    return None, ""


def _best_action_url(body: str) -> str | None:
    urls = [url.rstrip(".,);]）") for url in URL_RE.findall(body)]
    if not urls:
        return None
    noisy = ("unsubscribe", "privacy", "help", "support", "tracking", "pixel")
    for url in urls:
        lowered = url.lower()
        if not any(token in lowered for token in noisy):
            return url
    return urls[0]


def extract_recruitment_item(subject: str, sender: str, body: str, received_at: datetime) -> dict[str, Any] | None:
    haystack = (subject + "\n" + body[:12000]).lower()
    if not any(keyword.lower() in haystack for keyword in RECRUITMENT_KEYWORDS):
        return None

    local_tz = ZoneInfo(os.getenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai"))
    base = received_at.astimezone(local_tz)
    item_type = _classify(subject, body)
    deadline, deadline_precision, deadline_context = _find_context_date(body, DEADLINE_HINTS, base)
    fixed_time, fixed_precision, fixed_context = _find_context_date(body, FIXED_TIME_HINTS, base)
    relative_deadline, relative_note = _relative_deadline(body, base)

    mode = "uncertain"
    start_at = None
    end_at = None
    deadline_at = None
    note = "识别为秋招相关邮件，但未找到可靠的时间表达。"

    if fixed_time and fixed_precision == "minute":
        mode = "fixed_time"
        start_at = fixed_time.isoformat()
        note = f"根据时间上下文识别固定时间：{fixed_context[:180]}"
    elif deadline:
        mode = "deadline"
        deadline_at = deadline.date().isoformat() if deadline_precision == "day" else deadline.isoformat()
        note = f"根据截止上下文识别：{deadline_context[:180]}"
    elif relative_deadline:
        mode = "deadline"
        deadline_precision = "derived"
        deadline_at = relative_deadline.isoformat()
        note = relative_note
    elif fixed_time:
        mode = "deadline"
        deadline_at = fixed_time.date().isoformat() if fixed_precision == "day" else fixed_time.isoformat()
        deadline_precision = fixed_precision
        note = f"仅找到日期信息，按待办截止事项展示：{fixed_context[:180]}"

    return {
        "company": _guess_company(subject, sender),
        "title": subject or "秋招事项",
        "item_type": item_type,
        "mode": mode,
        "status": "uncertain" if mode == "uncertain" else "pending",
        "start_at": start_at,
        "end_at": end_at,
        "deadline_at": deadline_at,
        "deadline_precision": deadline_precision,
        "action_url": _best_action_url(body),
        "extraction_note": note,
    }


def _insert_log(connection: sqlite3.Connection, message_id: str, uid: str, subject: str, action: str, detail: str = "") -> None:
    connection.execute(
        "INSERT INTO recruitment_mail_log(message_id, imap_uid, subject, action, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (message_id, uid, subject, action, detail, _now_iso()),
    )


def scan_qq_mail(db_path: Path, limit: int | None = None) -> dict[str, Any]:
    host = os.getenv("QQ_IMAP_HOST", "imap.qq.com")
    port = int(os.getenv("QQ_IMAP_PORT", "993"))
    user = os.getenv("QQ_EMAIL", "").strip()
    auth_code = os.getenv("QQ_EMAIL_AUTH_CODE", "").strip()
    mailbox = os.getenv("QQ_IMAP_MAILBOX", "INBOX")
    scan_limit = limit or int(os.getenv("RECRUITMENT_SCAN_LIMIT", "120"))
    if not user or not auth_code:
        raise RuntimeError("QQ_EMAIL / QQ_EMAIL_AUTH_CODE 尚未配置")

    init_recruitment_db(db_path)
    imported = 0
    skipped = 0
    uncertain = 0
    ignored = 0

    client = imaplib.IMAP4_SSL(host, port)
    try:
        client.login(user, auth_code)
        status, _ = client.select(mailbox, readonly=True)
        if status != "OK":
            raise RuntimeError(f"无法打开邮箱目录 {mailbox}")
        status, data = client.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("读取 QQ 邮箱失败")
        uids = data[0].split()[-max(1, scan_limit):]
        uids.reverse()

        with _connect(db_path) as connection:
            for raw_uid in uids:
                uid = raw_uid.decode("ascii", errors="ignore")
                known_uid = connection.execute(
                    "SELECT 1 FROM recruitment_items WHERE imap_uid=? UNION SELECT 1 FROM recruitment_mail_log WHERE imap_uid=? LIMIT 1",
                    (uid, uid),
                ).fetchone()
                if known_uid:
                    skipped += 1
                    continue

                status, message_data = client.uid("fetch", raw_uid, "(RFC822)")
                if status != "OK" or not message_data or not message_data[0]:
                    continue
                message = email.message_from_bytes(message_data[0][1])
                subject = _decode(message.get("Subject"))
                sender = _decode(message.get("From"))
                message_id = (message.get("Message-ID") or f"qq-uid-{uid}").strip()
                known_message = connection.execute(
                    "SELECT 1 FROM recruitment_items WHERE message_id=? UNION SELECT 1 FROM recruitment_mail_log WHERE message_id=? LIMIT 1",
                    (message_id, message_id),
                ).fetchone()
                if known_message:
                    skipped += 1
                    continue

                try:
                    received_at = email.utils.parsedate_to_datetime(message.get("Date")) if message.get("Date") else datetime.now(timezone.utc)
                    if received_at.tzinfo is None:
                        received_at = received_at.replace(tzinfo=timezone.utc)
                except Exception:
                    received_at = datetime.now(timezone.utc)

                body = _message_text(message)
                item = extract_recruitment_item(subject, sender, body, received_at)
                if not item:
                    _insert_log(connection, message_id, uid, subject, "ignored", "未命中秋招关键词")
                    ignored += 1
                    continue

                now = _now_iso()
                connection.execute(
                    """
                    INSERT INTO recruitment_items(
                        message_id, imap_uid, company, title, item_type, mode, status,
                        start_at, end_at, deadline_at, deadline_precision, action_url,
                        source_subject, source_sender, source_received_at, extraction_note,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id, uid, item["company"], item["title"], item["item_type"], item["mode"], item["status"],
                        item["start_at"], item["end_at"], item["deadline_at"], item["deadline_precision"], item["action_url"],
                        subject, sender, received_at.isoformat(), item["extraction_note"], now, now,
                    ),
                )
                _insert_log(connection, message_id, uid, subject, "created", item["extraction_note"])
                imported += 1
                if item["status"] == "uncertain":
                    uncertain += 1
            connection.commit()
    finally:
        try:
            client.logout()
        except Exception:
            pass

    return {"imported": imported, "skipped": skipped, "ignored": ignored, "uncertain": uncertain}


def _serialize(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def build_recruitment_router(db_path: Path, require_auth: Any) -> APIRouter:
    init_recruitment_db(db_path)
    router = APIRouter(prefix="/api/v1/recruitment", dependencies=[Depends(require_auth)])

    @router.get("/items")
    def list_items(status: str | None = None, limit: int = 200) -> dict[str, Any]:
        limit = min(max(limit, 1), 500)
        sql = "SELECT * FROM recruitment_items"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY CASE WHEN status='uncertain' THEN 0 WHEN status='pending' THEN 1 ELSE 2 END, COALESCE(deadline_at, start_at, '9999') ASC, id DESC LIMIT ?"
        params.append(limit)
        with _connect(db_path) as connection:
            rows = connection.execute(sql, params).fetchall()
        return {"items": [_serialize(row) for row in rows]}

    @router.get("/summary")
    def summary() -> dict[str, Any]:
        tz = ZoneInfo(os.getenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai"))
        today = datetime.now(tz).date()
        three_days = today + timedelta(days=3)
        with _connect(db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM recruitment_items WHERE status IN ('pending','uncertain') ORDER BY COALESCE(deadline_at,start_at,'9999') ASC"
            ).fetchall()
        today_count = 0
        three_day_count = 0
        uncertain_count = 0
        next_item = None
        for row in rows:
            item = dict(row)
            if item["status"] == "uncertain":
                uncertain_count += 1
            raw = item.get("deadline_at") or item.get("start_at")
            if raw:
                try:
                    day = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(tz).date() if "T" in raw else datetime.fromisoformat(raw).date()
                    if day == today:
                        today_count += 1
                    if today <= day <= three_days:
                        three_day_count += 1
                except ValueError:
                    pass
            if next_item is None and item["status"] == "pending":
                next_item = item
        return {"today": today_count, "three_days": three_day_count, "uncertain": uncertain_count, "next_item": next_item}

    @router.get("/mail-log")
    def mail_log(limit: int = 100) -> dict[str, Any]:
        limit = min(max(limit, 1), 300)
        with _connect(db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM recruitment_mail_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return {"logs": [_serialize(row) for row in rows]}

    @router.post("/scan")
    def scan_mail() -> dict[str, Any]:
        try:
            return {"ok": True, **scan_qq_mail(db_path)}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.patch("/items/{item_id}")
    def patch_item(item_id: int, patch: RecruitmentPatch) -> dict[str, Any]:
        values = patch.model_dump(exclude_unset=True)
        if not values:
            raise HTTPException(status_code=400, detail="没有需要更新的字段")
        assignments = [f"{key} = ?" for key in values]
        params: list[Any] = list(values.values())
        assignments.append("updated_at = ?")
        params.extend([_now_iso(), item_id])
        with _connect(db_path) as connection:
            cursor = connection.execute(
                f"UPDATE recruitment_items SET {', '.join(assignments)} WHERE id = ?", params
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="事项不存在")
            row = connection.execute("SELECT * FROM recruitment_items WHERE id = ?", (item_id,)).fetchone()
            connection.commit()
        return {"item": _serialize(row)}

    @router.post("/items/{item_id}/complete")
    def complete_item(item_id: int) -> dict[str, Any]:
        with _connect(db_path) as connection:
            cursor = connection.execute(
                "UPDATE recruitment_items SET status='done', updated_at=? WHERE id=?", (_now_iso(), item_id)
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="事项不存在")
            connection.commit()
        return {"ok": True}

    return router
