import { useCallback, useEffect, useState } from "react";

import {
  captureBaseline,
  getRun,
  listBaselines,
  listReports,
  listRuns,
} from "./api.js";
import BaselinesView from "./components/BaselinesView.jsx";
import ReportsView from "./components/ReportsView.jsx";
import RunDetail from "./components/RunDetail.jsx";
import RunList from "./components/RunList.jsx";

const VIEWS = [
  ["runs", "运行"],
  ["reports", "结论"],
  ["baselines", "基线"],
];

export default function App() {
  const [view, setView] = useState("runs");
  const [runs, setRuns] = useState([]);
  const [reports, setReports] = useState([]);
  const [baselines, setBaselines] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

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
        <span className="dim small">{`${runs.length} 次运行 · ${reports.length} 条结论 · ${baselines.length} 个基线`}</span>
        <button type="button" className="ghost" onClick={refresh}>
          刷新
        </button>
      </header>

      <main className="layout">
        {view === "runs" && (
          <>
            <aside className="sidebar">
              <div className="section-title">运行</div>
              <RunList runs={runs} selectedId={selectedId} onSelect={selectRun} />
            </aside>
            <section className="detail">
              <RunDetail run={detail} onCaptureBaseline={captureFromDetail} />
            </section>
          </>
        )}
        {view === "reports" && (
          <section className="detail">
            <ReportsView reports={reports} />
          </section>
        )}
        {view === "baselines" && (
          <section className="detail">
            <BaselinesView
              baselines={baselines}
              runs={runs}
              onCapture={handleCapture}
              busy={busy}
              error={error}
            />
          </section>
        )}
      </main>
    </>
  );
}
