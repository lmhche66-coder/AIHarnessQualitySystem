"use strict";

const state = { runs: [], selected: null, expanded: new Set() };

const el = (id) => document.getElementById(id);

function esc(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function shortId(runId) {
  return runId.length > 24 ? runId.slice(-24) : runId;
}

function statusClass(status) {
  if (status === "pass") return "ok";
  if (status === "fail") return "warn";
  return "bad";
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error((await response.json().catch(() => ({}))).error || response.statusText);
  }
  return response.json();
}

async function loadRuns() {
  const data = await getJson("/api/runs");
  state.runs = data.runs || [];
  el("run-count").textContent = `${state.runs.length} 次运行`;
  renderRunList();
  if (!state.selected && state.runs.length) {
    await selectRun(state.runs[0].run_id);
  }
}

function renderRunList() {
  const list = el("run-list");
  const empty = el("run-empty");
  list.innerHTML = "";
  empty.hidden = state.runs.length > 0;
  for (const run of state.runs) {
    const summary = run.summary || {};
    const item = document.createElement("li");
    item.className = "run-item" + (run.run_id === state.selected ? " selected" : "");
    item.innerHTML =
      `<div class="run-id" title="${esc(run.run_id)}">${esc(shortId(run.run_id))}</div>` +
      `<div class="run-meta">` +
      `<span class="counts">` +
      `<span class="ok">${summary.passed || 0}</span>/` +
      `<span class="bad">${summary.failed || 0}</span>/` +
      `<span class="warn">${summary.errored || 0}</span>` +
      `</span>` +
      `<span>${esc((run.started_at || "").replace("T", " ").slice(0, 19))}</span>` +
      `</div>`;
    item.addEventListener("click", () => selectRun(run.run_id));
    list.appendChild(item);
  }
}

async function selectRun(runId) {
  state.selected = runId;
  state.expanded.clear();
  renderRunList();
  const detail = await getJson(`/api/runs/${encodeURIComponent(runId)}`);
  renderDetail(detail);
}

function renderDetail(run) {
  const detail = el("detail");
  const summary = run.summary || {};
  const metadata = run.metadata || {};
  const parts = [];

  parts.push(
    `<div class="detail-head">` +
      `<span class="detail-title">${esc(run.run_id)}</span>` +
      `<span class="muted">${esc((run.started_at || "").replace("T", " ").slice(0, 19))}</span>` +
      `</div>`
  );

  parts.push(
    `<div class="stats">` +
      stat(summary.total || 0, "用例", "") +
      stat(summary.passed || 0, "通过", "ok") +
      stat(summary.failed || 0, "失败", "warn") +
      stat(summary.errored || 0, "错误", "bad") +
      `</div>`
  );

  if (metadata.metrics) parts.push(renderMetrics(metadata.metrics));
  if (metadata.task_report) parts.push(renderTaskReport(metadata.task_report, metadata.attempt_report));
  if (metadata.cassette) parts.push(renderCassette(metadata.cassette));

  parts.push(renderCases(run));
  detail.innerHTML = parts.join("");
  bindCaseRows(run);
}

function stat(value, label, cls) {
  return `<div class="stat"><span class="stat-value ${cls}">${value}</span>` +
    `<span class="stat-label">${esc(label)}</span></div>`;
}

function renderMetrics(metrics) {
  const usage = metrics.usage_reported ? "已上报" : "未覆盖";
  const rows =
    kv("调用", metrics.calls) +
    kv("重复", metrics.retries) +
    kv("p50", `${metrics.duration_p50_ms} ms`) +
    kv("p95", `${metrics.duration_p95_ms} ms`) +
    kv("token", `${metrics.input_tokens}/${metrics.output_tokens}`) +
    kv("用量", usage);
  const violations = (metrics.budget_violations || []).length
    ? `<ul class="violations">${metrics.budget_violations.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>`
    : "";
  return `<div class="block"><div class="block-title">指标</div><div class="kv">${rows}</div>${violations}</div>`;
}

function kv(label, value) {
  return `<span>${esc(label)} <b>${esc(value)}</b></span>`;
}

function renderTaskReport(report, attempts) {
  const rows =
    kv("任务", report.total) +
    kv("已解决", report.resolved) +
    kv("通过率", Number(report.resolved_rate || 0).toFixed(4));
  const attemptRows =
    attempts && attempts.attempts > attempts.tasks
      ? kv("尝试", attempts.attempts) +
        kv("pass@1", Number(attempts.pass_at_1 || 0).toFixed(4)) +
        kv("pass@k", Number(attempts.pass_at_k || 0).toFixed(4))
      : "";
  const lists = [];
  if ((report.unresolved_tasks || []).length) {
    lists.push(`<ul class="violations">${report.unresolved_tasks.map((t) => `<li>未解决 ${esc(t)}</li>`).join("")}</ul>`);
  }
  if ((report.errored_tasks || []).length) {
    lists.push(`<ul class="violations">${report.errored_tasks.map((t) => `<li>出错 ${esc(t)}</li>`).join("")}</ul>`);
  }
  return `<div class="block"><div class="block-title">任务</div><div class="kv">${rows}${attemptRows}</div>${lists.join("")}</div>`;
}

function renderCassette(cassette) {
  const rows =
    kv("名称", cassette.name) +
    kv("模式", cassette.mode) +
    kv("交互", cassette.interactions) +
    kv("未使用", (cassette.unused || []).length);
  return `<div class="block"><div class="block-title">cassette</div><div class="kv">${rows}</div></div>`;
}

function renderCases(run) {
  const rows = (run.verdicts || [])
    .map((verdict) => {
      const failed = verdict.failed_checks || [];
      const first = failed.length ? failed[0].name : "";
      return (
        `<tr class="case-row" data-case="${esc(verdict.case_id)}">` +
        `<td><span class="pill ${esc(verdict.status)}">${esc(verdict.status)}</span></td>` +
        `<td class="case-id">${esc(verdict.case_id)}</td>` +
        `<td class="num">${Number(verdict.duration_ms || 0).toFixed(1)}</td>` +
        `<td class="num">${(verdict.metrics || {}).calls || 0}</td>` +
        `<td class="num">${(verdict.metrics || {}).retries || 0}</td>` +
        `<td class="check-detail">${esc(first)}</td>` +
        `</tr>` +
        `<tr class="case-detail" data-detail="${esc(verdict.case_id)}" hidden>` +
        `<td colspan="6">${renderCaseDetail(verdict)}</td></tr>`
      );
    })
    .join("");
  return (
    `<table class="cases"><thead><tr>` +
    `<th>状态</th><th>用例</th><th>耗时 ms</th><th>调用</th><th>重复</th><th>首个失败判据</th>` +
    `</tr></thead><tbody>${rows}</tbody></table>`
  );
}

function renderCaseDetail(verdict) {
  const parts = [];
  if (verdict.error) {
    parts.push(`<div class="error-text">${esc(verdict.error)}</div>`);
  }
  const failed = verdict.failed_checks || [];
  if (failed.length) {
    parts.push(
      `<ul class="checks">` +
        failed
          .map(
            (check) =>
              `<li><span class="check-name">${esc(check.name)}</span> ` +
              `<span class="check-detail">期望 ${esc(JSON.stringify(check.expected))}，` +
              `实际 ${esc(JSON.stringify(check.actual))}</span>` +
              (check.message ? ` <span class="check-detail">${esc(check.message)}</span>` : "") +
              `</li>`
          )
          .join("") +
        `</ul>`
    );
  }
  if (!parts.length) parts.push(`<div class="check-detail">全部判据通过</div>`);
  return `<div class="trace-slot" data-trace="${esc(verdict.case_id)}"></div>` + parts.join("");
}

function bindCaseRows(run) {
  const runId = run.run_id;
  document.querySelectorAll(".case-row").forEach((row) => {
    row.addEventListener("click", async () => {
      const caseId = row.dataset.case;
      const detailRow = document.querySelector(`[data-detail="${CSS.escape(caseId)}"]`);
      if (!detailRow) return;
      detailRow.hidden = !detailRow.hidden;
      if (detailRow.hidden) return;
      const slot = detailRow.querySelector(".trace-slot");
      if (!slot || slot.dataset.loaded === "1") return;
      slot.dataset.loaded = "1";
      try {
        const trace = await getJson(
          `/api/runs/${encodeURIComponent(runId)}/traces/${encodeURIComponent(caseId)}`
        );
        slot.innerHTML = renderTrace(trace);
      } catch (error) {
        slot.innerHTML = `<div class="check-detail">没有可用的轨迹</div>`;
      }
    });
  });
}

function renderTrace(trace) {
  const calls = (trace.events || []).filter((event) => event.name === "tool_call");
  if (!calls.length) {
    return `<div class="check-detail">该用例没有工具调用</div>`;
  }
  const items = calls
    .map((event) => {
      const payload = event.payload || {};
      const ok = payload.ok === true;
      return (
        `<li><span class="${ok ? "ok" : "bad"}">${ok ? "ok" : payload.error_kind || "failed"}</span>` +
        `<span>${esc(payload.target)}</span>` +
        `<span class="trace-args">${esc(JSON.stringify(payload.args || {}))}</span></li>`
      );
    })
    .join("");
  return `<div class="block-title">工具调用</div><ul class="trace">${items}</ul>`;
}

el("refresh").addEventListener("click", () => loadRuns());
loadRuns();
