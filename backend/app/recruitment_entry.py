from __future__ import annotations

import asyncio
import logging
import os
import secrets
from pathlib import Path

from fastapi import Cookie, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse

from .main import DEFAULT_DB, STATIC_DIR, create_app
from .recruitment import build_recruitment_router, scan_qq_mail


LOG = logging.getLogger("activitywatch.recruitment")
app = create_app()


def require_recruitment_auth(
    request: Request,
    activity_token: str | None = Cookie(default=None),
) -> None:
    configured_token = app.state.api_token
    if not configured_token:
        return
    authorization = request.headers.get("authorization", "")
    bearer = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    header_token = request.headers.get("x-activity-token", "")
    if not any(
        secrets.compare_digest(candidate.encode(), configured_token.encode())
        for candidate in (bearer, header_token, activity_token or "")
        if candidate
    ):
        raise HTTPException(status_code=401, detail="authentication required")


db_path = Path(os.getenv("ACTIVITYWATCH_DB_PATH", str(DEFAULT_DB)))
app.include_router(build_recruitment_router(db_path, require_recruitment_auth))


@app.get("/api/v1/recruitment/config-status")
def recruitment_config_status(request: Request, activity_token: str | None = Cookie(default=None)) -> dict[str, object]:
    require_recruitment_auth(request, activity_token)
    email_address = os.getenv("QQ_EMAIL", "").strip()
    auth_code = os.getenv("QQ_EMAIL_AUTH_CODE", "").strip()
    auto_scan = os.getenv("RECRUITMENT_AUTO_SCAN", "1") != "0"
    try:
        interval = max(60, int(os.getenv("RECRUITMENT_SCAN_INTERVAL_SECONDS", "600")))
    except ValueError:
        interval = 600
    account_hint = ""
    if email_address:
        local, _, domain = email_address.partition("@")
        if len(local) <= 2:
            masked = local[:1] + "*"
        else:
            masked = local[:2] + "***" + local[-1:]
        account_hint = f"{masked}@{domain}" if domain else masked
    return {
        "mail_configured": bool(email_address and auth_code),
        "email_configured": bool(email_address),
        "auth_code_configured": bool(auth_code),
        "account_hint": account_hint,
        "auto_scan_enabled": auto_scan,
        "scan_interval_seconds": interval,
        "debug_view_enabled": os.getenv("ACTIVITYWATCH_DEBUG_VIEW", "0") == "1",
    }


@app.get("/recruitment", include_in_schema=False)
def recruitment_page(request: Request, token: str | None = Query(default=None)) -> Response:
    production = os.getenv("ACTIVITYWATCH_ENV", "development") == "production"
    configured_token = app.state.api_token
    if production:
        try:
            require_recruitment_auth(request, request.cookies.get("activity_token"))
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
    if not production and token and configured_token and secrets.compare_digest(token, configured_token):
        response = RedirectResponse("/recruitment", status_code=303)
        response.set_cookie("activity_token", token, httponly=True, samesite="lax")
        return response
    return FileResponse(STATIC_DIR / "recruitment.html")


async def _recruitment_poll_loop() -> None:
    try:
        interval = max(60, int(os.getenv("RECRUITMENT_SCAN_INTERVAL_SECONDS", "600")))
    except ValueError:
        interval = 600
    while True:
        try:
            if os.getenv("QQ_EMAIL") and os.getenv("QQ_EMAIL_AUTH_CODE"):
                result = await asyncio.to_thread(scan_qq_mail, db_path)
                if result.get("imported") or result.get("uncertain"):
                    LOG.info("recruitment scan result=%s", result)
            else:
                LOG.warning("recruitment auto scan skipped: QQ_EMAIL / QQ_EMAIL_AUTH_CODE not configured")
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("recruitment background scan failed")
        await asyncio.sleep(interval)


@app.on_event("startup")
async def start_recruitment_polling() -> None:
    if os.getenv("RECRUITMENT_AUTO_SCAN", "1") != "0":
        app.state.recruitment_poll_task = asyncio.create_task(_recruitment_poll_loop())


@app.on_event("shutdown")
async def stop_recruitment_polling() -> None:
    task = getattr(app.state, "recruitment_poll_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
