import { shortId, timestamp } from "../format.js";

export default function RunList({ runs, selectedId, onSelect }) {
  if (runs.length === 0) {
    return <div className="empty">还没有运行记录</div>;
  }
  return (
    <ul className="run-list">
      {runs.map((run) => {
        const summary = run.summary || {};
        const active = run.run_id === selectedId;
        return (
          <li key={run.run_id}>
            <button
              type="button"
              className={`run-item${active ? " selected" : ""}`}
              onClick={() => onSelect(run.run_id)}
              title={run.run_id}
            >
              <span className="run-id">{shortId(run.run_id)}</span>
              <span className="run-meta">
                <span className="counts">
                  <span className="ok">{summary.passed || 0}</span>/
                  <span className="warn">{summary.failed || 0}</span>/
                  <span className="bad">{summary.errored || 0}</span>
                </span>
                <span className="dim">{timestamp(run.started_at)}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
