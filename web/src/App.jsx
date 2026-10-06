import { useCallback, useEffect, useState } from "react";

import {
  getReflow,
  getRun,
  listBaselines,
  listGoldSets,
  listReports,
  listRuns,
  listTraces,
} from "./api.js";
import AssetsView from "./components/AssetsView.jsx";
import BaselinesView from "./components/BaselinesView.jsx";
import ReportsView from "./components/ReportsView.jsx";
import RunDetail from "./components/RunDetail.jsx";
import RunList from "./components/RunList.jsx";

// 四个分区：运行看判定，结论看门禁与归因，基线看回归面，资产看数据集与证据。
const VIEWS = [
  ["runs", "运行"],
  ["reports", "结论"],
  ["baselines", "基线"],
  ["assets", "资产"],
];

export default function App() {
  const [view, setView] = useState("runs");
  const [runs, setRuns] = useState([]);
  const [reports, setReports] = useState([]);
  const [baselines, setBaselines] = useState([]);
  const [goldSets, setGoldSets] = useState([]);
  const [traces, setTraces] = useState([]);
  const [reflow, setReflow] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [nextRuns, nextReports, nextBaselines, nextGold, nextTraces, nextReflow] =
        await Promise.all([
          listRuns(),
          listReports(),
          listBaselines(),
          listGoldSets(),
          listTraces(),
          getReflow(),
        ]);
      setRuns(nextRuns);
      setReports(nextReports);
      setBaselines(nextBaselines);
      setGoldSets(nextGold);
      setTraces(nextTraces);
      setReflow(nextReflow);
      setError(null);
    } catch (failure) {
      setError(failure.message);
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

  return (
    <>
      <header className="topbar">
        <span className="brand">agenteval</span>
        <nav className="tabs" aria-label="控制台分区">
          {VIEWS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`tab${view === key ? " active" : ""}`}
              aria-current={view === key ? "page" : undefined}
              onClick={() => setView(key)}
            >
              {label}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        {error && <span className="bad small">{error}</span>}
        <span className="dim small">
          {`${runs.length} 运行 · ${reports.length} 结论 · ${baselines.length} 基线 · ${goldSets.length} 金标 · ${traces.length} 轨迹`}
        </span>
        <button type="button" className="ghost" onClick={refresh}>
          刷新
        </button>
      </header>

      <main className="layout">
        {view === "runs" && (
          <>
            <aside className="sidebar">
              <div className="section-title">运行记录</div>
              <RunList runs={runs} selectedId={selectedId} onSelect={selectRun} />
            </aside>
            <section className="detail">
              <RunDetail run={detail} />
            </section>
          </>
        )}
        {view === "reports" && (
          <section className="detail full">
            <ReportsView reports={reports} />
          </section>
        )}
        {view === "baselines" && (
          <section className="detail full">
            <BaselinesView baselines={baselines} />
          </section>
        )}
        {view === "assets" && (
          <section className="detail full">
            <AssetsView goldSets={goldSets} traces={traces} reflow={reflow} />
          </section>
        )}
      </main>
    </>
  );
}
