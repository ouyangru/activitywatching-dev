from __future__ import annotations

import threading
import time
from typing import Any, Callable, TypeVar

from .recruitment_feishu import FeishuBitableClient


T = TypeVar("T")

# 页面一次刷新会依次命中 /records、/proposals，前端增强脚本还会再次读取
# /records。原实现中每个请求都会重新换 tenant token、解析 Wiki token、读取
# fields 和 records。这里做进程内短缓存并用同一把锁做 single-flight，避免同一
# 时刻多个请求一起穿透到飞书。
_LOCK = threading.RLock()
_INSTALLED = False

_TOKEN_CACHE: dict[tuple[str], tuple[float, str]] = {}
_APP_TOKEN_CACHE: dict[tuple[str, str, str], tuple[float, str]] = {}
_FIELDS_CACHE: dict[tuple[str, ...], tuple[float, list[dict[str, Any]]]] = {}
_RECORDS_CACHE: dict[tuple[str, ...], tuple[float, list[dict[str, Any]]]] = {}

TOKEN_TTL_SECONDS = 6000.0
APP_TOKEN_TTL_SECONDS = 3600.0
FIELDS_TTL_SECONDS = 300.0
RECORDS_TTL_SECONDS = 30.0


def _get_cached(cache: dict[Any, tuple[float, T]], key: Any, ttl: float, loader: Callable[[], T]) -> T:
    now = time.monotonic()
    with _LOCK:
        cached = cache.get(key)
        if cached and cached[0] > now:
            return cached[1]

        # 故意在锁内执行 loader：请求量很小，换来相同资源的并发请求只会有一个
        # 真正访问飞书，其余请求随后直接复用结果。
        value = loader()
        cache[key] = (time.monotonic() + ttl, value)
        return value


def _resource_key(client: FeishuBitableClient) -> tuple[str, ...]:
    return (
        client.app_id,
        client.app_token,
        client.wiki_token,
        client.table_id,
        client.view_id,
    )


def _invalidate_records(client: FeishuBitableClient) -> None:
    key = _resource_key(client)
    with _LOCK:
        _RECORDS_CACHE.pop(key, None)


def install_feishu_cache() -> None:
    """Install a process-local cache on FeishuBitableClient once per process."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_tenant_access_token = FeishuBitableClient.tenant_access_token
    original_resolved_app_token = FeishuBitableClient.resolved_app_token
    original_fields = FeishuBitableClient.fields
    original_records = FeishuBitableClient.records
    original_create_record = FeishuBitableClient.create_record
    original_update_record = FeishuBitableClient.update_record

    def tenant_access_token(self: FeishuBitableClient) -> str:
        if self._tenant_token:
            return self._tenant_token
        key = (self.app_id,)
        token = _get_cached(
            _TOKEN_CACHE,
            key,
            TOKEN_TTL_SECONDS,
            lambda: original_tenant_access_token(self),
        )
        self._tenant_token = token
        return token

    def resolved_app_token(self: FeishuBitableClient) -> str:
        if self.app_token:
            return self.app_token
        if self._resolved_app_token:
            return self._resolved_app_token
        key = (self.app_id, self.wiki_token, self.table_id)
        token = _get_cached(
            _APP_TOKEN_CACHE,
            key,
            APP_TOKEN_TTL_SECONDS,
            lambda: original_resolved_app_token(self),
        )
        self._resolved_app_token = token
        return token

    def fields(self: FeishuBitableClient) -> list[dict[str, Any]]:
        key = _resource_key(self)
        return _get_cached(
            _FIELDS_CACHE,
            key,
            FIELDS_TTL_SECONDS,
            lambda: original_fields(self),
        )

    def records(self: FeishuBitableClient) -> list[dict[str, Any]]:
        key = _resource_key(self)
        return _get_cached(
            _RECORDS_CACHE,
            key,
            RECORDS_TTL_SECONDS,
            lambda: original_records(self),
        )

    def create_record(self: FeishuBitableClient, fields_value: dict[str, Any]) -> dict[str, Any]:
        result = original_create_record(self, fields_value)
        _invalidate_records(self)
        return result

    def update_record(
        self: FeishuBitableClient,
        record_id: str,
        fields_value: dict[str, Any],
    ) -> dict[str, Any]:
        result = original_update_record(self, record_id, fields_value)
        _invalidate_records(self)
        return result

    FeishuBitableClient.tenant_access_token = tenant_access_token
    FeishuBitableClient.resolved_app_token = resolved_app_token
    FeishuBitableClient.fields = fields
    FeishuBitableClient.records = records
    FeishuBitableClient.create_record = create_record
    FeishuBitableClient.update_record = update_record
    _INSTALLED = True
