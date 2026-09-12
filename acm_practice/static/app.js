const state = {
  problems: [],
  current: null,
};

const $ = (id) => document.getElementById(id);
const editor = $("codeEditor");
const lineNumbers = $("lineNumbers");
const problemSelect = $("problemSelect");
const verdictBadge = $("verdictBadge");
const resultSummary = $("resultSummary");
const stdoutBox = $("stdoutBox");
const stderrBox = $("stderrBox");
const compilerStatus = $("compilerStatus");
const toast = $("toast");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 1800);
}

function setBusy(busy) {
  $("runSample").disabled = busy;
  $("runCustom").disabled = busy;
  $("submitCode").disabled = busy;
}

function updateLineNumbers() {
  const lines = Math.max(1, editor.value.split("\n").length);
  lineNumbers.textContent = Array.from({ length: lines }, (_, i) => i + 1).join("\n");
  lineNumbers.scrollTop = editor.scrollTop;
}

function storageKey(problemId) {
  return `acm-practice:${problemId}:source`;
}

function saveSource() {
  if (state.current) {
    localStorage.setItem(storageKey(state.current.id), editor.value);
  }
}

function loadSource(problem) {
  editor.value = localStorage.getItem(storageKey(problem.id)) || problem.starter_code || "";
  updateLineNumbers();
}

function setVerdict(verdict, summary) {
  verdictBadge.textContent = verdict || "READY";
  verdictBadge.className = "verdict";
  const positive = new Set(["AC", "RUN_OK"]);
  const negative = new Set(["WA", "CE", "RE", "TLE"]);
  verdictBadge.classList.add(positive.has(verdict) ? "good" : negative.has(verdict) ? "bad" : "neutral");
  resultSummary.textContent = summary || "";
}

function renderProblem(problem) {
  state.current = problem;
  $("problemTitle").textContent = problem.title;
  $("difficultyBadge").textContent = problem.difficulty;
  $("timeLimit").textContent = `时间限制 ${problem.time_limit_ms} ms`;
  $("memoryLimit").textContent = `内存限制 ${problem.memory_limit_mb} MB`;
  $("problemDescription").textContent = problem.description;
  $("inputFormat").textContent = problem.input_format;
  $("outputFormat").textContent = problem.output_format;
  $("constraints").innerHTML = problem.constraints.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  $("samples").innerHTML = problem.samples.map((sample, index) => `
    <div class="sample-block">
      <div class="sample-heading">样例 ${index + 1}</div>
      <div class="sample-grid">
        <div><span>输入</span><pre>${escapeHtml(sample.input)}</pre></div>
        <div><span>输出</span><pre>${escapeHtml(sample.output)}</pre></div>
      </div>
    </div>
  `).join("");
  $("customInput").value = problem.samples[0]?.input || "";
  loadSource(problem);
  setVerdict("READY", "写完代码后先运行样例，再提交隐藏测试。");
  stdoutBox.textContent = "—";
  stderrBox.textContent = "—";
}

async function requestJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function renderExecution(data, expected = null) {
  const verdict = data.verdict || "ERROR";
  let summary = "";
  if (verdict === "RUN_OK") {
    if (expected == null) {
      summary = `运行完成 · 编译 ${data.compile_ms ?? "—"} ms · 执行 ${data.duration_ms ?? "—"} ms`;
    } else {
      const normalize = (value) => value.replace(/\r\n/g, "\n").split("\n").map((line) => line.trimEnd()).join("\n").replace(/\n+$/, "");
      const matched = normalize(data.stdout || "") === normalize(expected);
      setVerdict(matched ? "AC" : "WA", matched ? `样例通过 · ${data.duration_ms ?? "—"} ms` : "样例输出与期望不一致。");
      stdoutBox.textContent = data.stdout || "(无输出)";
      stderrBox.textContent = matched ? "—" : `期望输出:\n${expected}`;
      return;
    }
  } else if (verdict === "CE") {
    summary = `编译失败 · ${data.compile_ms ?? "—"} ms`;
  } else if (verdict === "TLE") {
    summary = `运行超时 · ${data.duration_ms ?? "—"} ms`;
  } else if (verdict === "RE") {
    summary = `运行时错误 · ${data.duration_ms ?? "—"} ms`;
  } else {
    summary = data.message || "执行失败";
  }
  setVerdict(verdict, summary);
  stdoutBox.textContent = data.stdout || "—";
  stderrBox.textContent = data.stderr || data.message || "—";
}

async function runInput(stdin, expected = null) {
  saveSource();
  setBusy(true);
  setVerdict("RUNNING", "正在编译并执行…");
  stdoutBox.textContent = "…";
  stderrBox.textContent = "…";
  try {
    const data = await requestJson("/api/run", { source: editor.value, stdin });
    renderExecution(data, expected);
  } catch (error) {
    setVerdict("ERROR", error.message);
    stdoutBox.textContent = "—";
    stderrBox.textContent = error.stack || error.message;
  } finally {
    setBusy(false);
  }
}

async function submit() {
  if (!state.current) return;
  saveSource();
  setBusy(true);
  setVerdict("JUDGING", "正在运行公开与隐藏测试…");
  stdoutBox.textContent = "隐藏测试不会展示 stdout。";
  stderrBox.textContent = "…";
  try {
    const data = await requestJson("/api/submit", {
      problem_id: state.current.id,
      source: editor.value,
    });
    if (data.verdict === "AC") {
      setVerdict("AC", `全部通过 ${data.passed}/${data.total} · 总执行 ${data.duration_ms} ms · 编译 ${data.compile_ms} ms`);
      stdoutBox.textContent = "Accepted";
      stderrBox.textContent = "—";
      return;
    }

    const where = data.visibility === "hidden" ? `隐藏用例 #${data.failed_test}` : `样例 #${data.failed_test}`;
    setVerdict(data.verdict || "ERROR", `${where} 未通过 · 已通过 ${data.passed ?? 0}/${data.total ?? "—"}`);
    if (data.visibility === "sample" && data.input != null) {
      stdoutBox.textContent = data.actual || "(无输出)";
      stderrBox.textContent = `输入:\n${data.input}\n期望:\n${data.expected}`;
    } else {
      stdoutBox.textContent = "隐藏测试输入不公开。";
      stderrBox.textContent = data.stderr || "请检查边界条件、复杂度、溢出和输入输出格式。";
    }
  } catch (error) {
    setVerdict("ERROR", error.message);
    stdoutBox.textContent = "—";
    stderrBox.textContent = error.stack || error.message;
  } finally {
    setBusy(false);
  }
}

async function init() {
  try {
    const response = await fetch("/api/problems", { cache: "no-store" });
    const data = await response.json();
    state.problems = data.problems || [];
    compilerStatus.textContent = data.compiler_available ? "g++ READY" : "g++ 未找到";
    compilerStatus.classList.toggle("warning", !data.compiler_available);
    problemSelect.innerHTML = state.problems.map((problem) => `<option value="${problem.id}">[${escapeHtml(problem.source)}] ${escapeHtml(problem.title)}</option>`).join("");
    if (state.problems.length) {
      renderProblem(state.problems[0]);
    }
  } catch (error) {
    compilerStatus.textContent = "服务不可用";
    compilerStatus.classList.add("warning");
    setVerdict("ERROR", `无法加载题目：${error.message}`);
  }
}

problemSelect.addEventListener("change", () => {
  saveSource();
  const problem = state.problems.find((item) => item.id === problemSelect.value);
  if (problem) renderProblem(problem);
});

editor.addEventListener("input", () => {
  updateLineNumbers();
  saveSource();
});
editor.addEventListener("scroll", () => {
  lineNumbers.scrollTop = editor.scrollTop;
});
editor.addEventListener("keydown", (event) => {
  if (event.key === "Tab") {
    event.preventDefault();
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    editor.setRangeText("    ", start, end, "end");
    updateLineNumbers();
    saveSource();
  }
  if (event.ctrlKey && event.key === "Enter") {
    event.preventDefault();
    if (event.shiftKey) {
      submit();
    } else {
      const sample = state.current?.samples?.[0];
      if (sample) runInput(sample.input, sample.output);
    }
  }
});

$("resetCode").addEventListener("click", () => {
  if (!state.current) return;
  if (!confirm("恢复题目模板？当前代码会被覆盖。")) return;
  localStorage.removeItem(storageKey(state.current.id));
  loadSource(state.current);
  showToast("已恢复模板");
});

$("runSample").addEventListener("click", () => {
  const sample = state.current?.samples?.[0];
  if (sample) runInput(sample.input, sample.output);
});

$("runCustom").addEventListener("click", () => runInput($("customInput").value));
$("submitCode").addEventListener("click", submit);

init();
