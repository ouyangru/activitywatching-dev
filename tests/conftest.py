from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(
        db_path=tmp_path / "test.db",
        rules_path=Path(__file__).parents[1] / "backend" / "config" / "rules.yaml",
        timezone_name="Asia/Shanghai",
    )
    with TestClient(app) as test_client:
        yield test_client


def event(
    sequence: int,
    start_time: str,
    process: str = "Code.exe",
    title: str = "mini-nccl - Visual Studio Code",
    duration_ms: int = 10_000,
    idle_ms: int = 0,
) -> dict:
    return {
        "device_id": "test-pc",
        "sequence": sequence,
        "start_time": start_time,
        "duration_ms": duration_ms,
        "context": {"process": process, "window_title": title},
        "interaction": {
            "key_count": 10,
            "mouse_click_count": 2,
            "scroll_count": 1,
            "idle_ms": idle_ms,
            "clipboard_copy_count": 0,
            "clipboard_paste_count": 0,
            "clipboard_events": [],
        },
    }

