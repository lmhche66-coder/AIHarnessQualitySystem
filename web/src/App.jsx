import { useCallback, useEffect, useState } from "react";

import {
  captureBaseline,
  getRun,
  listBaselines,
  listGoldSets,
  listReports,
  listRuns,
  listTraces,
  triggerRun,
  triggerGate,
  triggerJudge,
  triggerTasks,
} from "./api.js";
import BaselinesView from "./components/BaselinesView.jsx";
import ImportView from "./components/ImportView.jsx";
import LabelingView from "./components/LabelingView.jsx";
import ReportsView from "./components/ReportsView.jsx";
import ReflowView from "./components/ReflowView.jsx";
import RunDetail from "./components/RunDetail.jsx";
import RunList from "./components/RunList.jsx";

const VIEWS = [
  ["runs", "运行"],
  ["reports", "结论"],
  ["baselines", "基线"],
  ["labeling", "标注"],
  ["reflow", "回流"],
  ["import", "导入"],
];

export default function App() {
  const [view, setView] = useState("runs");
  const [runs, setRuns] = useState([]);
  const [reports, setReports] = useState([]);
  const [baselines, setBaselines] = useState([]);
  const [goldSets, setGoldSets] = useState([]);
  const [traces, setTraces] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const notify = useCallback((message) => {
    setNotice(message);
    setError(message && message.includes("失败") ? message : null);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [nextRuns, nextReports, nextBaselines] = await Promise.all([
        listRuns(),
        listReports(),
        listBaselines(),
      ]);
      setRuns(nextRuns);
      setReports(nextReports);
      setBaselines(nextBaselines);
      setGoldSets(await listGoldSets());
      setTraces(await listTraces());
      return nextRuns;
    } catch (failure) {
      setError(failure.message);
      return [];
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function selectRun(runId) {
    setSelectedId(runId);
    setDetail(null);
    try {
      setDetail(await getRun(runId));
    } catch (failure) {
      setError(failure.message);
    }
  }

  async function handleCapture(runId, name) {
    setBusy(true);
    setError(null);
    try {
      await captureBaseline(runId, name);
      await refresh();
      return true;
    } catch (failure) {
      setError(failure.message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function captureFromDetail(runId) {
    const name = window.prompt("基线名", "default");
    if (name === null) return;
    await handleCapture(runId, name.trim() || "default");
  }

  async function handleTrigger(mode, params) {
    setBusy(true);
    setNotice(null);
    setError(null);
    try {
      const payload =
        mode === "tasks"
          ? await triggerTasks(params.tasksFile, params.registry, params.agent)
          : await triggerRun(params.casesFile, params.registry);
      notify(
        mode === "tasks"
          ? `任务集已运行 ${payload.run_id}：解决 ${payload.resolved}/${payload.total}`
          : `已运行 ${payload.run_id}：通过 ${payload.passed} 失败 ${payload.failed} 错误 ${payload.errored}`
      );
      await refresh();
      setView("runs");
      await selectRun(payload.run_id);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleGate(runId, baseline) {
    setBusy(true);
    setError(null);
    try {
      const payload = await triggerGate(runId, baseline);
      notify(
        `门禁${payload.passed ? "通过" : "不通过"}：通过率 ${Number(
          payload.pass_rate || 0
        ).toFixed(4)}，已写入结论`
      );
      await refresh();
      return payload;
    } catch (failure) {
      setError(failure.message);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function handleCalibrate(gold, judge) {
    setBusy(true);
    setError(null);
    try {
      const payload = await triggerJudge(gold, judge);
      notify(
        `校准完成：一致率 ${Number(payload.agreement || 0).toFixed(4)}，` +
          `${payload.usable_for_gate ? "可用于门禁" : "仅供参考"}` +
          (payload.skipped ? `，跳过 ${payload.skipped} 条未标注` : "")
      );
      await refresh();
      return payload;
    } catch (failure) {
      setError(failure.message);
      return null;
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <header className="topbar">
        <span className="brand">agenteval</span>
        <nav className="tabs">
          {VIEWS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`tab${view === key ? " active" : ""}`}
              onClick={() => setView(key)}
            >
              {label}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        {error && <span className="bad small">{error}</span>}
        {!error && notice && <span className="dim small">{notice}</span>}
        <span className="dim small">{`${runs.length} 运行 · ${reports.length} 结论 · ${baselines.length} 基线 · ${goldSets.length} 金标 · ${traces.length} 轨迹`}</span>
        <button type="button" className="ghost" onClick={refresh}>
          刷新
        </button>
      </header>

      <main className="layout">
        {view === "runs" && (
          <>
            <aside className="sidebar">
              <div className="section-title">运行</div>
              <TriggerForm onTrigger={handleTrigger} busy={busy} />
              <RunList runs={runs} selectedId={selectedId} onSelect={selectRun} />
            </aside>
            <section className="detail">
              <RunDetail run={detail} onCaptureBaseline={captureFromDetail} />
            </section>
          </>
        )}
        {view === "reports" && (
          <section className="detail full">
            <ReportsView reports={reports} runs={runs} onGate={handleGate} busy={busy} />
          </section>
        )}
        {view === "baselines" && (
          <section className="detail full">
            <BaselinesView
              baselines={baselines}
              runs={runs}
              onCapture={handleCapture}
              busy={busy}
              error={error}
            />
          </section>
        )}
        {view === "labeling" && (
          <section className="detail full">
            <LabelingView
              sets={goldSets}
              onChanged={refresh}
              notify={notify}
              onCalibrate={handleCalibrate}
              busy={busy}
            />
          </section>
        )}
        {view === "reflow" && (
          <section className="detail full">
            <ReflowView runs={runs} onChanged={refresh} notify={notify} />
          </section>
        )}
        {view === "import" && (
          <section className="detail full">
            <ImportView traces={traces} onChanged={refresh} notify={notify} />
          </section>
        )}
      </main>
    </>
  );
}

function TriggerForm({ onTrigger, busy }) {
  const [mode, setMode] = useState("cases");
  const [casesFile, setCasesFile] = useState("");
  const [registry, setRegistry] = useState("agenteval.fakes:build_demo_registry");
  const [tasksFile, setTasksFile] = useState("examples/tasks.json");
  const [taskRegistry, setTaskRegistry] = useState("agenteval.fakes:build_task_registry");
  const [agent, setAgent] = useState("examples/demo_agent.py:build_agent");

  return (
    <form
      className="side-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (mode === "tasks") {
          if (tasksFile.trim() && taskRegistry.trim() && agent.trim()) {
            onTrigger("tasks", {
              tasksFile: tasksFile.trim(),
              registry: taskRegistry.trim(),
              agent: agent.trim(),
            });
          }
          return;
        }
        if (casesFile.trim() && registry.trim()) {
          onTrigger("cases", { casesFile: casesFile.trim(), registry: registry.trim() });
        }
      }}
    >
      <span className="dim small">触发运行</span>
      <select value={mode} onChange={(event) => setMode(event.target.value)}>
        <option value="cases">契约与过程用例</option>
        <option value="tasks">端到端任务集</option>
      </select>
      {mode === "cases" ? (
        <>
          <input
            type="text"
            value={casesFile}
            placeholder="用例文件路径"
            onChange={(event) => setCasesFile(event.target.value)}
          />
          <input
            type="text"
            value={registry}
            placeholder="注册表 module:factory"
            onChange={(event) => setRegistry(event.target.value)}
          />
        </>
      ) : (
        <>
          <input
            type="text"
            value={tasksFile}
            placeholder="任务集文件路径"
            onChange={(event) => setTasksFile(event.target.value)}
          />
          <input
            type="text"
            value={taskRegistry}
            placeholder="环境工厂 module:factory"
            onChange={(event) => setTaskRegistry(event.target.value)}
          />
          <input
            type="text"
            value={agent}
            placeholder="agent 工厂 module:factory"
            onChange={(event) => setAgent(event.target.value)}
          />
        </>
      )}
      <button type="submit" className="primary" disabled={busy}>
        运行
      </button>
    </form>
  );
}
