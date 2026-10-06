import { timestamp } from "../format.js";
import CasesTable from "./CasesTable.jsx";
import {
  CassetteBlock,
  LoadBlock,
  MetricsBlock,
  TaskBlock,
} from "./blocks.jsx";

function Stat({ value, label, tone = "" }) {
  return (
    <div className="stat">
      <span className={`stat-value ${tone}`}>{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

export default function RunDetail({ run }) {
  if (!run) return <div className="empty">从左侧选择一次运行</div>;
  const summary = run.summary || {};
  const metadata = run.metadata || {};
  return (
    <>
      <div className="detail-head">
        <span className="detail-title">{run.run_id}</span>
        <span className="dim">{timestamp(run.started_at)}</span>
      </div>
      <div className="stats">
        <Stat value={summary.total || 0} label="用例" />
        <Stat value={summary.passed || 0} label="通过" tone="ok" />
        <Stat value={summary.failed || 0} label="失败" tone="warn" />
        <Stat value={summary.errored || 0} label="错误" tone="bad" />
      </div>
      <MetricsBlock metrics={metadata.metrics} />
      <TaskBlock report={metadata.task_report} attempts={metadata.attempt_report} />
      <CassetteBlock cassette={metadata.cassette} />
      <LoadBlock reports={metadata.load_report} />
      <CasesTable run={run} />
    </>
  );
}
