from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
PROBLEMS_FILE = ROOT / "problems.json"
PROBLEM_PACKS_DIR = ROOT / "problem_packs"
MAX_SOURCE_BYTES = 64 * 1024
MAX_STDIN_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 128 * 1024
COMPILE_TIMEOUT_SECONDS = 10
RUN_TIMEOUT_SECONDS = 2


def _problem_files() -> list[Path]:
    files = [PROBLEMS_FILE]
    if PROBLEM_PACKS_DIR.is_dir():
        files.extend(sorted(PROBLEM_PACKS_DIR.glob("*.json")))
    return files


def _read_problems() -> dict[str, dict[str, Any]]:
    problems: dict[str, dict[str, Any]] = {}
    for path in _problem_files():
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("problems", [])
        if not isinstance(items, list):
            raise ValueError(f"{path.name}: 'problems' must be a list")

        default_source = data.get("source") or data.get("pack")
        if not default_source:
            default_source = "基础" if path == PROBLEMS_FILE else path.stem
        default_category = data.get("category")
        if not default_category:
            default_category = "basic" if path == PROBLEMS_FILE else path.stem

        for raw_problem in items:
            if not isinstance(raw_problem, dict) or not raw_problem.get("id"):
                raise ValueError(f"{path.name}: every problem must be an object with an id")
            problem = dict(raw_problem)
            problem.setdefault("source", default_source)
            problem.setdefault("category", default_category)
            problem_id = str(problem["id"])
            if problem_id in problems:
                problems[problem_id].update(problem)
            else:
                problems[problem_id] = problem
    return problems


def _public_problem(problem: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": problem["id"],
        "title": problem.get("title", problem["id"]),
        "source": problem.get("source", "unknown"),
        "category": problem.get("category", "uncategorized"),
        "difficulty": problem.get("difficulty", "Easy"),
        "time_limit_ms": problem.get("time_limit_ms", 2000),
        "memory_limit_mb": problem.get("memory_limit_mb", 256),
        "description": problem.get("description", ""),
        "input_format": problem.get("input_format", ""),
        "output_format": problem.get("output_format", ""),
        "constraints": problem.get("constraints", []),
        "samples": problem.get("samples", []),
        "starter_code": problem.get("starter_code", ""),
        "tags": problem.get("tags", []),
    }


def _normalize_output(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in value.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _read_limited(path: Path) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    data = path.read_bytes()
    truncated = len(data) > MAX_OUTPUT_BYTES
    data = data[:MAX_OUTPUT_BYTES]
    return data.decode("utf-8", errors="replace"), truncated


def _resource_limiter(cpu_seconds: int, memory_mb: int):
    if os.name != "posix":
        return None

    def apply_limits() -> None:
        try:
            import resource

            memory = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
            resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        except Exception:
            pass

    return apply_limits


def _compile(source: str, workdir: Path) -> dict[str, Any]:
    compiler = shutil.which("g++")
    if not compiler:
        return {
            "ok": False,
            "verdict": "CE",
            "message": "找不到 g++，请先确认 g++ 已安装并位于 PATH 中。",
            "stderr": "",
        }

    source_path = workdir / "main.cpp"
    binary_path = workdir / ("main.exe" if os.name == "nt" else "main")
    stderr_path = workdir / "compile.stderr"
    source_path.write_text(source, encoding="utf-8")
    command = [
        compiler,
        "-std=c++11",
        "-O2",
        "-pipe",
        "-Wall",
        "-Wextra",
        "-pedantic",
        str(source_path),
        "-o",
        str(binary_path),
    ]
    started = time.perf_counter()
    try:
        with stderr_path.open("wb") as err:
            result = subprocess.run(
                command,
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=err,
                timeout=COMPILE_TIMEOUT_SECONDS,
                check=False,
                preexec_fn=_resource_limiter(COMPILE_TIMEOUT_SECONDS, 1024),
            )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "verdict": "CE",
            "message": "编译超时。",
            "stderr": "compiler timeout",
            "compile_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    stderr, truncated = _read_limited(stderr_path)
    compile_ms = round((time.perf_counter() - started) * 1000, 1)
    if result.returncode != 0 or not binary_path.exists():
        return {
            "ok": False,
            "verdict": "CE",
            "message": "编译失败。",
            "stderr": stderr,
            "stderr_truncated": truncated,
            "compile_ms": compile_ms,
        }
    return {
        "ok": True,
        "binary": binary_path,
        "stderr": stderr,
        "stderr_truncated": truncated,
        "compile_ms": compile_ms,
    }


def _run_binary(binary: Path, stdin: str, timeout_seconds: float, memory_mb: int) -> dict[str, Any]:
    workdir = binary.parent
    input_path = workdir / "stdin.txt"
    output_path = workdir / "stdout.txt"
    error_path = workdir / "stderr.txt"
    input_path.write_text(stdin, encoding="utf-8")
    started = time.perf_counter()
    try:
        with input_path.open("rb") as inp, output_path.open("wb") as out, error_path.open("wb") as err:
            result = subprocess.run(
                [str(binary)],
                cwd=workdir,
                stdin=inp,
                stdout=out,
                stderr=err,
                timeout=timeout_seconds,
                check=False,
                preexec_fn=_resource_limiter(max(1, int(timeout_seconds) + 1), memory_mb),
            )
    except subprocess.TimeoutExpired:
        return {
            "verdict": "TLE",
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "stdout": "",
            "stderr": "Time Limit Exceeded",
        }

    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    stdout, stdout_truncated = _read_limited(output_path)
    stderr, stderr_truncated = _read_limited(error_path)
    if stdout_truncated:
        return {
            "verdict": "RE",
            "duration_ms": duration_ms,
            "stdout": stdout,
            "stderr": "输出超过限制。",
            "stdout_truncated": True,
        }
    if result.returncode != 0:
        return {
            "verdict": "RE",
            "duration_ms": duration_ms,
            "stdout": stdout,
            "stderr": stderr or f"程序异常退出，return code = {result.returncode}",
            "stderr_truncated": stderr_truncated,
        }
    return {
        "verdict": "OK",
        "duration_ms": duration_ms,
        "stdout": stdout,
        "stderr": stderr,
        "stderr_truncated": stderr_truncated,
    }


def run_custom(source: str, stdin: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="acm-practice-") as tmp:
        workdir = Path(tmp)
        compiled = _compile(source, workdir)
        if not compiled["ok"]:
            return compiled
        executed = _run_binary(compiled["binary"], stdin, RUN_TIMEOUT_SECONDS, 256)
        executed["compile_ms"] = compiled["compile_ms"]
        if executed["verdict"] == "OK":
            executed["verdict"] = "RUN_OK"
        return executed


def submit_solution(problem: dict[str, Any], source: str) -> dict[str, Any]:
    time_limit_ms = int(problem.get("time_limit_ms", 2000))
    memory_limit_mb = int(problem.get("memory_limit_mb", 256))
    timeout_seconds = max(0.1, time_limit_ms / 1000)
    tests = [
        *[{**sample, "visibility": "sample"} for sample in problem.get("samples", [])],
        *[{**case, "visibility": "hidden"} for case in problem.get("hidden_tests", [])],
    ]

    with tempfile.TemporaryDirectory(prefix="acm-submit-") as tmp:
        workdir = Path(tmp)
        compiled = _compile(source, workdir)
        if not compiled["ok"]:
            return compiled

        passed = 0
        total_ms = 0.0
        for index, test in enumerate(tests, start=1):
            executed = _run_binary(compiled["binary"], test["input"], timeout_seconds, memory_limit_mb)
            total_ms += float(executed.get("duration_ms", 0.0))
            if executed["verdict"] != "OK":
                return {
                    "ok": False,
                    "verdict": executed["verdict"],
                    "passed": passed,
                    "total": len(tests),
                    "failed_test": index,
                    "visibility": test["visibility"],
                    "duration_ms": round(total_ms, 1),
                    "compile_ms": compiled["compile_ms"],
                    "stderr": executed.get("stderr", ""),
                }

            actual = _normalize_output(executed["stdout"])
            expected = _normalize_output(test["output"])
            if actual != expected:
                failure = {
                    "ok": False,
                    "verdict": "WA",
                    "passed": passed,
                    "total": len(tests),
                    "failed_test": index,
                    "visibility": test["visibility"],
                    "duration_ms": round(total_ms, 1),
                    "compile_ms": compiled["compile_ms"],
                }
                if test["visibility"] == "sample":
                    failure.update({
                        "input": test["input"],
                        "expected": test["output"],
                        "actual": executed["stdout"],
                    })
                return failure
            passed += 1

        return {
            "ok": True,
            "verdict": "AC",
            "passed": passed,
            "total": len(tests),
            "duration_ms": round(total_ms, 1),
            "compile_ms": compiled["compile_ms"],
        }


class ACMHandler(BaseHTTPRequestHandler):
    server_version = "ACMPractice/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[acm] {self.address_string()} - {fmt % args}")

    def _json(self, payload: dict[str, Any] | list[Any], status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length <= 0 or length > MAX_SOURCE_BYTES + MAX_STDIN_BYTES + 4096:
            raise ValueError("request body too large or empty")
        raw = self.rfile.read(length)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _validate_source(self, payload: dict[str, Any]) -> str:
        source = payload.get("source", "")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source is required")
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError("source is too large")
        return source

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json({
                "ok": True,
                "compiler": shutil.which("g++"),
                "standard": "C++11",
                "local_only": True,
            })
            return
        if path == "/api/problems":
            problems = _read_problems()
            self._json({
                "problems": [_public_problem(problem) for problem in problems.values()],
                "compiler_available": bool(shutil.which("g++")),
            })
            return

        static_map = {
            "/": "index.html",
            "/index.html": "index.html",
            "/app.js": "app.js",
            "/style.css": "style.css",
        }
        filename = static_map.get(path)
        if not filename:
            self.send_error(404)
            return
        target = STATIC_DIR / filename
        if not target.exists():
            self.send_error(404)
            return
        raw = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._body_json()
            source = self._validate_source(payload)

            if path == "/api/run":
                stdin = payload.get("stdin", "")
                if not isinstance(stdin, str):
                    raise ValueError("stdin must be a string")
                if len(stdin.encode("utf-8")) > MAX_STDIN_BYTES:
                    raise ValueError("stdin is too large")
                self._json(run_custom(source, stdin))
                return

            if path == "/api/submit":
                problem_id = payload.get("problem_id", "")
                problems = _read_problems()
                problem = problems.get(problem_id)
                if not problem:
                    self._json({"error": "unknown problem"}, 404)
                    return
                self._json(submit_solution(problem, source))
                return

            self._json({"error": "not found"}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": f"judge internal error: {exc}"}, 500)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local C++11 ACM practice judge")
    parser.add_argument("--host", default="127.0.0.1", help="default: 127.0.0.1; do not expose publicly")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if not STATIC_DIR.exists() or not PROBLEMS_FILE.exists():
        raise SystemExit("acm_practice files are incomplete")

    server = ThreadingHTTPServer((args.host, args.port), ACMHandler)
    print(f"ACM Practice: http://{args.host}:{args.port}")
    print("Compiler:", shutil.which("g++") or "NOT FOUND")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("WARNING: this service executes submitted C++ code; keep it on a trusted private network only.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
