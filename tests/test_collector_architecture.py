from pathlib import Path


def _collector_source(name: str) -> str:
    return (Path(__file__).parents[1] / "collector" / "src" / name).read_text(encoding="utf-8")


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening : index + 1]
    raise AssertionError(f"unterminated function: {signature}")


def test_hook_message_thread_contains_no_file_or_network_work():
    main = _collector_source("main.cpp")
    snapshot_path = _function_body(main, "void emit_window()")

    assert "worker->submit" in snapshot_path
    for blocking_operation in ("uploader", "flush()", "save_sequence", "window_sampler", "WinHttp", "Reg"):
        assert blocking_operation not in snapshot_path


def test_heartbeat_is_sent_by_worker_thread_only():
    worker = _collector_source("CollectorWorker.cpp")
    run_body = _function_body(worker, "void CollectorWorker::run()")

    assert "post_heartbeat" in run_body
    assert "60s" in run_body

    main = _collector_source("main.cpp")
    assert "post_heartbeat" not in main


def test_heartbeat_posts_to_backend_contract():
    uploader = _collector_source("BatchUploader.cpp")

    assert "/api/v1/heartbeat" in uploader
    assert '\\"platform\\":\\"windows\\"' in uploader
    assert "ACTIVITY_COLLECTOR_VERSION" in uploader


def test_autostart_uses_hkcu_run_key_and_tray_toggle():
    main = _collector_source("main.cpp")

    assert "Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Run" in main
    assert "RegGetValueW" in main and "RegSetKeyValueW" in main and "RegDeleteKeyValueW" in main
    assert "开机自启" in main
    assert "HKEY_CURRENT_USER" in main


