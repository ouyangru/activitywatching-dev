from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
EDITABLE_FIELD_TYPES = {1, 2, 3, 4, 5, 7}
FIELD_ALIASES = {
    "company": ("公司", "公司名称", "企业", "企业名称"),
    "stage": ("招聘进度", "当前阶段", "进度", "招聘阶段", "流程"),
    "latest": ("最新动态", "最近进展", "最新进展", "进展", "备注"),
    "next": ("下一节点", "下一步", "下一事项", "下次安排", "下一时间"),
}
STAGE_KEYWORDS = (
    ("流程结束", ("感谢信", "流程终止", "流程结束", "未通过", "未能进入", "遗憾")),
    ("Offer", ("offer", "录用通知", "录用意向", "正式录用")),
    ("HR面", ("hr面", "hr 面", "人力面", "hr interview")),
    ("三面", ("三面", "第3面", "第三面", "第3轮面试", "第三轮面试")),
    ("二面", ("二面", "第2面", "第二面", "第2轮面试", "第二轮面试", "复试")),
    ("一面", ("一面", "第1面", "第一面", "第1轮面试", "第一轮面试", "初面")),
    ("笔试", ("笔试", "在线考试", "written test")),
    ("测评", ("测评", "assessment")),
    ("面试", ("面试", "interview")),
)


class ManualProposalRequest(BaseModel):
    record_id: str = Field(min_length=1, max_length=128)
    company: str = Field(default="", max_length=128)
    fields: dict[str, Any]


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_recruitment_feishu_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS recruitment_feishu_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recruitment_item_id INTEGER,
            source TEXT NOT NULL DEFAULT 'mail',
            company TEXT NOT NULL DEFAULT '',
            stage TEXT,
            latest_update TEXT,
            next_at TEXT,
            source_title TEXT,
            record_id TEXT,
            fields_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(recruitment_item_id, source)
        );
        CREATE INDEX IF NOT EXISTS idx_recruitment_feishu_proposal_status
        ON recruitment_feishu_proposals(status, id DESC);
        """
    )


def init_recruitment_feishu_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as connection:
        ensure_recruitment_feishu_tables(connection)
        connection.commit()


def infer_recruitment_stage(subject: str, body: str = "") -> str | None:
    text = f"{subject}\n{body[:6000]}".lower()
    for stage, keywords in STAGE_KEYWORDS:
        if any(keyword.lower() in text for keyword in keywords):
            return stage
    round_match = re.search(r"第?\s*([1-9一二三四五六七八九])\s*(?:轮)?\s*面", text)
    if round_match:
        value = round_match.group(1)
        number_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        number = number_map.get(value, int(value) if value.isdigit() else 0)
        return f"{number}面" if number else "面试"
    return None


def queue_recruitment_feishu_proposal(
    connection: sqlite3.Connection,
    recruitment_item_id: int,
    item: dict[str, Any],
    subject: str,
    body: str = "",
) -> bool:
    stage = infer_recruitment_stage(subject, body)
    if not stage:
        return False
    ensure_recruitment_feishu_tables(connection)
    next_at = item.get("start_at") or item.get("deadline_at")
    latest_update = subject.strip() or item.get("title") or stage
    now = _now_iso()
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO recruitment_feishu_proposals(
            recruitment_item_id, source, company, stage, latest_update, next_at,
            source_title, fields_json, status, created_at, updated_at
        ) VALUES (?, 'mail', ?, ?, ?, ?, ?, '{}', 'pending', ?, ?)
        """,
        (
            recruitment_item_id,
            item.get("company") or "",
            stage,
            latest_update[:512],
            next_at,
            subject[:512],
            now,
            now,
        ),
    )
    return cursor.rowcount > 0


def _field_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("name") or item.get("link") or ""))
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value.get("link") or "")
    return str(value)


def _normalize_company(value: str) -> str:
    text = re.sub(r"[\s·・._\-—（）()【】\[\]]+", "", (value or "").lower())
    for suffix in ("股份有限公司", "有限责任公司", "有限公司", "集团", "科技"):
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            text = text[: -len(suffix)]
    return text


def match_company_record(records: list[dict[str, Any]], company_field: str | None, company: str) -> dict[str, Any]:
    if not company_field or not company.strip():
        return {"status": "unmatched", "record": None, "candidates": []}
    target = _normalize_company(company)
    scored: list[tuple[int, dict[str, Any]]] = []
    for record in records:
        raw = _field_text((record.get("fields") or {}).get(company_field))
        candidate = _normalize_company(raw)
        if not candidate:
            continue
        score = 100 if candidate == target else 75 if target and (target in candidate or candidate in target) else 0
        if score:
            scored.append((score, record))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored:
        return {"status": "unmatched", "record": None, "candidates": []}
    top_score = scored[0][0]
    top = [record for score, record in scored if score == top_score]
    if len(top) != 1:
        return {"status": "ambiguous", "record": None, "candidates": top[:5]}
    return {"status": "matched", "record": top[0], "candidates": []}


def resolve_field_mapping(fields: list[dict[str, Any]]) -> dict[str, str | None]:
    names = {str(field.get("field_name") or ""): field for field in fields}
    configured = {
        "company": os.getenv("FEISHU_RECRUITMENT_COMPANY_FIELD", "公司").strip(),
        "stage": os.getenv("FEISHU_RECRUITMENT_STAGE_FIELD", "招聘进度").strip(),
        "latest": os.getenv("FEISHU_RECRUITMENT_LATEST_FIELD", "最新动态").strip(),
        "next": os.getenv("FEISHU_RECRUITMENT_NEXT_FIELD", "下一节点").strip(),
    }
    mapping: dict[str, str | None] = {}
    for key, preferred in configured.items():
        if preferred and preferred in names:
            mapping[key] = preferred
            continue
        aliases = [alias for alias in FIELD_ALIASES[key] if alias in names]
        mapping[key] = aliases[0] if len(aliases) == 1 else None
    return mapping


def _parse_datetime_millis(value: Any, tz: ZoneInfo) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    raw = str(value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return int(parsed.timestamp() * 1000)
    except ValueError:
        return None


def _coerce_field_value(field: dict[str, Any], value: Any, tz: ZoneInfo) -> Any:
    field_type = int(field.get("type") or 0)
    if field_type == 1:
        return "" if value is None else str(value)
    if field_type == 2:
        if value in (None, ""):
            return None
        return float(value)
    if field_type == 3:
        return "" if value is None else str(value)
    if field_type == 4:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return [item.strip() for item in str(value).split(",") if item.strip()]
    if field_type == 5:
        millis = _parse_datetime_millis(value, tz)
        if millis is None:
            raise ValueError(f"字段 {field.get('field_name')} 需要有效日期时间")
        return millis
    if field_type == 7:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "是"}
    raise ValueError(f"字段 {field.get('field_name')} 暂不支持从网页修改")


class FeishuBitableClient:
    def __init__(self) -> None:
        self.app_id = os.getenv("FEISHU_APP_ID", "").strip()
        self.app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        self.app_token = os.getenv("FEISHU_RECRUITMENT_APP_TOKEN", "").strip()
        self.wiki_token = os.getenv("FEISHU_RECRUITMENT_WIKI_TOKEN", "").strip()
        self.table_id = os.getenv("FEISHU_RECRUITMENT_TABLE_ID", "").strip()
        self.view_id = os.getenv("FEISHU_RECRUITMENT_VIEW_ID", "").strip()
        self._tenant_token: str | None = None
        self._resolved_app_token: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret and self.table_id and (self.app_token or self.wiki_token))

    def _request(self, method: str, path: str, *, body: dict[str, Any] | None = None, auth: bool = True) -> dict[str, Any]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = f"Bearer {self.tenant_access_token()}"
        request = urllib.request.Request(
            f"{FEISHU_API_BASE}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"飞书 API HTTP {exc.code}: {detail[:500]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"无法连接飞书 API：{exc.reason}") from exc
        if payload.get("code", 0) != 0:
            raise RuntimeError(f"飞书 API 错误 {payload.get('code')}: {payload.get('msg') or payload}")
        return payload

    def tenant_access_token(self) -> str:
        if self._tenant_token:
            return self._tenant_token
        payload = self._request(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            body={"app_id": self.app_id, "app_secret": self.app_secret},
            auth=False,
        )
        token = payload.get("tenant_access_token")
        if not token:
            raise RuntimeError("飞书未返回 tenant_access_token")
        self._tenant_token = str(token)
        return self._tenant_token

    def resolved_app_token(self) -> str:
        if self.app_token:
            return self.app_token
        if self._resolved_app_token:
            return self._resolved_app_token
        query = urllib.parse.urlencode({"token": self.wiki_token})
        payload = self._request("GET", f"/wiki/v2/spaces/get_node?{query}")
        node = (payload.get("data") or {}).get("node") or {}
        obj_token = str(node.get("obj_token") or "")
        obj_type = str(node.get("obj_type") or "")
        if not obj_token:
            raise RuntimeError("无法从飞书 Wiki 节点解析多维表格 app_token")
        if obj_type and obj_type != "bitable":
            raise RuntimeError(f"Wiki 节点类型为 {obj_type}，不是多维表格")
        self._resolved_app_token = obj_token
        return obj_token

    def fields(self) -> list[dict[str, Any]]:
        app_token = urllib.parse.quote(self.resolved_app_token(), safe="")
        table_id = urllib.parse.quote(self.table_id, safe="")
        payload = self._request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields?page_size=100")
        return list(((payload.get("data") or {}).get("items") or []))

    def records(self) -> list[dict[str, Any]]:
        app_token = urllib.parse.quote(self.resolved_app_token(), safe="")
        table_id = urllib.parse.quote(self.table_id, safe="")
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, str] = {"page_size": "500"}
            if self.view_id:
                params["view_id"] = self.view_id
            if page_token:
                params["page_token"] = page_token
            query = urllib.parse.urlencode(params)
            payload = self._request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records?{query}")
            data = payload.get("data") or {}
            items.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            page_token = str(data.get("page_token") or "")
            if not page_token:
                break
        return items

    def update_record(self, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        app_token = urllib.parse.quote(self.resolved_app_token(), safe="")
        table_id = urllib.parse.quote(self.table_id, safe="")
        record = urllib.parse.quote(record_id, safe="")
        payload = self._request(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record}",
            body={"fields": fields},
        )
        return (payload.get("data") or {}).get("record") or {}


def _field_by_name(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(field.get("field_name") or ""): field for field in fields}


def _mail_proposed_fields(row: sqlite3.Row, fields: list[dict[str, Any]], mapping: dict[str, str | None]) -> dict[str, Any]:
    by_name = _field_by_name(fields)
    tz = ZoneInfo(os.getenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai"))
    proposed: dict[str, Any] = {}
    stage_field = mapping.get("stage")
    if stage_field and row["stage"]:
        proposed[stage_field] = _coerce_field_value(by_name[stage_field], row["stage"], tz)
    latest_field = mapping.get("latest")
    if latest_field and row["latest_update"]:
        proposed[latest_field] = _coerce_field_value(by_name[latest_field], row["latest_update"], tz)
    next_field = mapping.get("next")
    if next_field and row["next_at"]:
        proposed[next_field] = _coerce_field_value(by_name[next_field], row["next_at"], tz)
    return proposed


def _manual_proposed_fields(row: sqlite3.Row, fields: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = _field_by_name(fields)
    tz = ZoneInfo(os.getenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai"))
    raw = json.loads(row["fields_json"] or "{}")
    proposed: dict[str, Any] = {}
    for name, value in raw.items():
        field = by_name.get(name)
        if not field:
            raise ValueError(f"飞书字段已不存在：{name}")
        if int(field.get("type") or 0) not in EDITABLE_FIELD_TYPES:
            raise ValueError(f"字段 {name} 暂不支持从网页修改")
        proposed[name] = _coerce_field_value(field, value, tz)
    return proposed


def _serialize_proposal(
    row: sqlite3.Row,
    *,
    fields: list[dict[str, Any]] | None = None,
    records: list[dict[str, Any]] | None = None,
    mapping: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    result = dict(row)
    result["stored_fields"] = json.loads(result.pop("fields_json") or "{}")
    result["match_status"] = "not_checked"
    result["current_fields"] = {}
    result["proposed_fields"] = result["stored_fields"]
    result["can_approve"] = False
    result["review_reason"] = "尚未连接飞书"
    if not fields or records is None or mapping is None or row["status"] != "pending":
        return result
    try:
        if row["source"] == "manual":
            record = next((item for item in records if item.get("record_id") == row["record_id"]), None)
            match = {"status": "matched" if record else "unmatched", "record": record, "candidates": []}
            proposed = _manual_proposed_fields(row, fields)
        else:
            match = match_company_record(records, mapping.get("company"), row["company"])
            proposed = _mail_proposed_fields(row, fields, mapping)
        result["match_status"] = match["status"]
        result["proposed_fields"] = proposed
        record = match.get("record")
        if record:
            result["record_id"] = record.get("record_id")
            result["current_fields"] = record.get("fields") or {}
        if match["status"] != "matched":
            result["review_reason"] = "未唯一匹配到飞书中的公司记录"
        elif not proposed:
            result["review_reason"] = "没有可写入的字段，请先检查字段映射"
        else:
            result["can_approve"] = True
            result["review_reason"] = "审核后可写入飞书"
    except (ValueError, KeyError) as exc:
        result["review_reason"] = str(exc)
    return result


def build_recruitment_feishu_router(db_path: Path, require_auth: Any) -> APIRouter:
    init_recruitment_feishu_db(db_path)
    router = APIRouter(prefix="/api/v1/recruitment/feishu", dependencies=[Depends(require_auth)])

    @router.get("/config-status")
    def config_status() -> dict[str, Any]:
        client = FeishuBitableClient()
        return {
            "configured": client.configured,
            "table_id": client.table_id or None,
            "view_id": client.view_id or None,
            "wiki_configured": bool(client.wiki_token),
            "app_token_configured": bool(client.app_token),
            "source_url": os.getenv("FEISHU_RECRUITMENT_SOURCE_URL", "").strip() or None,
            "review_required": True,
        }

    @router.get("/records")
    def list_records() -> dict[str, Any]:
        client = FeishuBitableClient()
        if not client.configured:
            raise HTTPException(status_code=503, detail="飞书招聘进度表尚未配置")
        try:
            fields = client.fields()
            records = client.records()
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        mapping = resolve_field_mapping(fields)
        return {
            "fields": fields,
            "records": records,
            "mapping": mapping,
            "editable_field_names": [
                field.get("field_name") for field in fields if int(field.get("type") or 0) in EDITABLE_FIELD_TYPES
            ],
        }

    @router.get("/proposals")
    def list_proposals(limit: int = 100) -> dict[str, Any]:
        limit = min(max(limit, 1), 300)
        client = FeishuBitableClient()
        fields: list[dict[str, Any]] | None = None
        records: list[dict[str, Any]] | None = None
        mapping: dict[str, str | None] | None = None
        sync_error: str | None = None
        if client.configured:
            try:
                fields = client.fields()
                records = client.records()
                mapping = resolve_field_mapping(fields)
            except RuntimeError as exc:
                sync_error = str(exc)
        with _connect(db_path) as connection:
            ensure_recruitment_feishu_tables(connection)
            rows = connection.execute(
                "SELECT * FROM recruitment_feishu_proposals ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {
            "proposals": [
                _serialize_proposal(row, fields=fields, records=records, mapping=mapping) for row in rows
            ],
            "mapping": mapping or {},
            "sync_error": sync_error,
        }

    @router.post("/proposals/manual")
    def create_manual_proposal(request: ManualProposalRequest) -> dict[str, Any]:
        client = FeishuBitableClient()
        if not client.configured:
            raise HTTPException(status_code=503, detail="飞书招聘进度表尚未配置")
        try:
            fields = client.fields()
            records = client.records()
            record = next((item for item in records if item.get("record_id") == request.record_id), None)
            if not record:
                raise HTTPException(status_code=404, detail="飞书记录不存在或当前视图不可见")
            by_name = _field_by_name(fields)
            unsupported = [
                name for name in request.fields
                if name not in by_name or int(by_name[name].get("type") or 0) not in EDITABLE_FIELD_TYPES
            ]
            if unsupported:
                raise HTTPException(status_code=400, detail=f"包含不可编辑字段：{', '.join(unsupported)}")
            tz = ZoneInfo(os.getenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai"))
            for name, value in request.fields.items():
                _coerce_field_value(by_name[name], value, tz)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        now = _now_iso()
        with _connect(db_path) as connection:
            ensure_recruitment_feishu_tables(connection)
            cursor = connection.execute(
                """
                INSERT INTO recruitment_feishu_proposals(
                    source, company, record_id, fields_json, status, created_at, updated_at
                ) VALUES ('manual', ?, ?, ?, 'pending', ?, ?)
                """,
                (request.company, request.record_id, json.dumps(request.fields, ensure_ascii=False), now, now),
            )
            proposal_id = cursor.lastrowid
            connection.commit()
        return {"ok": True, "proposal_id": proposal_id, "written": False, "review_required": True}

    @router.post("/proposals/{proposal_id}/approve")
    def approve_proposal(proposal_id: int) -> dict[str, Any]:
        client = FeishuBitableClient()
        if not client.configured:
            raise HTTPException(status_code=503, detail="飞书招聘进度表尚未配置")
        with _connect(db_path) as connection:
            ensure_recruitment_feishu_tables(connection)
            row = connection.execute("SELECT * FROM recruitment_feishu_proposals WHERE id=?", (proposal_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="审核建议不存在")
            if row["status"] != "pending":
                raise HTTPException(status_code=409, detail="该建议已经处理")
        try:
            fields = client.fields()
            records = client.records()
            mapping = resolve_field_mapping(fields)
            serialized = _serialize_proposal(row, fields=fields, records=records, mapping=mapping)
            if not serialized["can_approve"]:
                raise HTTPException(status_code=409, detail=serialized["review_reason"])
            record_id = str(serialized["record_id"])
            proposed = serialized["proposed_fields"]
            updated_record = client.update_record(record_id, proposed)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        with _connect(db_path) as connection:
            connection.execute(
                "UPDATE recruitment_feishu_proposals SET status='applied', record_id=?, fields_json=?, error=NULL, updated_at=? WHERE id=?",
                (record_id, json.dumps(proposed, ensure_ascii=False), _now_iso(), proposal_id),
            )
            connection.commit()
        return {"ok": True, "written": True, "record": updated_record, "fields": proposed}

    @router.post("/proposals/{proposal_id}/reject")
    def reject_proposal(proposal_id: int) -> dict[str, Any]:
        with _connect(db_path) as connection:
            ensure_recruitment_feishu_tables(connection)
            cursor = connection.execute(
                "UPDATE recruitment_feishu_proposals SET status='rejected', updated_at=? WHERE id=? AND status='pending'",
                (_now_iso(), proposal_id),
            )
            if cursor.rowcount == 0:
                exists = connection.execute("SELECT 1 FROM recruitment_feishu_proposals WHERE id=?", (proposal_id,)).fetchone()
                if not exists:
                    raise HTTPException(status_code=404, detail="审核建议不存在")
                raise HTTPException(status_code=409, detail="该建议已经处理")
            connection.commit()
        return {"ok": True, "written": False}

    return router
