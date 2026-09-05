from pathlib import Path


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
    main = (Path(__file__).parents[1] / "collector" / "src" / "main.cpp").read_text(encoding="utf-8")
    snapshot_path = _function_body(main, "void emit_window()")

    assert "worker->submit" in snapshot_path
    for blocking_operation in ("uploader", "flush()", "save_sequence", "window_sampler", "WinHttp"):
        assert blocking_operation not in snapshot_path

