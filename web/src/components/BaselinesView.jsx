import { useState } from "react";

import { timestamp } from "../format.js";
import { Kv } from "./blocks.jsx";

export default function BaselinesView({ baselines, runs, onCapture, busy, error }) {
  const [runId, setRunId] = useState("");
  const [name, setName] = useState("default");

  async function submit(event) {
    event.preventDefault();
    const target = runId || (runs[0] && runs[0].run_id) || "";
    if (!target) return;
    const ok = await onCapture(target, name || "default");
    if (ok) setName("default");
  }

  return (
    <div className="pane">
      <form className="inline-form" onSubmit={submit}>
        <label>
          <span className="dim small">运行</span>
          <select value={runId} onChange={(event) => setRunId(event.target.value)}>
            <option value="">（最新一次）</option>
            {runs.map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {run.run_id}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="dim small">基线名</span>
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="default"
          />
        </label>
        <button type="submit" className="primary" disabled={busy || runs.length === 0}>
          {busy ? "写入中…" : "捕获为基线"}
        </button>
        {error && <span className="bad small">{error}</span>}
      </form>

      {baselines.length === 0 ? (
        <div className="empty">还没有基线</div>
      ) : (
        <table className="cases">
          <thead>
            <tr>
              <th>名称</th>
              <th>来源运行</th>
              <th className="right">用例</th>
              <th>状态分布</th>
              <th>捕获时间</th>
            </tr>
          </thead>
          <tbody>
            {baselines.map((baseline) => (
              <tr key={baseline.name}>
                <td className="case-id">{baseline.name}</td>
                <td className="case-id">{baseline.run_id}</td>
                <td className="num">{baseline.cases}</td>
                <td className="kv">
                  {Object.entries(baseline.counts || {}).map(([status, count]) => (
                    <Kv key={status} label={status}>
                      {count}
                    </Kv>
                  ))}
                </td>
                <td className="dim small">{timestamp(baseline.captured_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
