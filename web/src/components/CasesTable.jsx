import { useState } from "react";

import { getTrace } from "../api.js";
import { describe } from "../format.js";
import { CheckList } from "./blocks.jsx";

function TracePanel({ runId, caseId }) {
  const [state, setState] = useState({ status: "idle", calls: [], error: null });

  async function load() {
    if (state.status !== "idle") return;
    setState({ status: "loading", calls: [], error: null });
    try {
      const trace = await getTrace(runId, caseId);
      const calls = (trace.events || []).filter((event) => event.name === "tool_call");
      setState({ status: "ready", calls, error: null });
    } catch (error) {
      setState({ status: "ready", calls: [], error: error.message });
    }
  }

  if (state.status === "idle") {
    return (
      <button type="button" className="ghost" onClick={load}>
        载入工具调用
      </button>
    );
  }
  if (state.status === "loading") return <div className="dim small">载入中…</div>;
  if (state.calls.length === 0) {
    return <div className="dim small">该用例没有工具调用</div>;
  }
  return (
    <ul className="trace">
      {state.calls.map((event) => {
        const payload = event.payload || {};
        const ok = payload.ok === true;
        return (
          <li key={event.seq}>
            <span className={ok ? "ok" : "bad"}>
              {ok ? "ok" : payload.error_kind || "failed"}
            </span>
            <span>{payload.target}</span>
            <span className="dim truncate">{describe(payload.args)}</span>
          </li>
        );
      })}
    </ul>
  );
}

export default function CasesTable({ run }) {
  const [expanded, setExpanded] = useState(null);
  const verdicts = run.verdicts || [];
  if (verdicts.length === 0) {
    return <div className="empty">这次运行没有判定</div>;
  }
  return (
    <table className="cases">
      <thead>
        <tr>
          <th>状态</th>
          <th>用例</th>
          <th className="right">耗时 ms</th>
          <th className="right">调用</th>
          <th className="right">重复</th>
          <th>首个失败判据</th>
        </tr>
      </thead>
      <tbody>
        {verdicts.map((verdict) => {
          const metrics = verdict.metrics || {};
          const failed = verdict.failed_checks || [];
          const open = expanded === verdict.case_id;
          return [
            <tr
              key={verdict.case_id}
              className="case-row"
              onClick={() => setExpanded(open ? null : verdict.case_id)}
            >
              <td>
                <span className={`pill ${verdict.status}`}>{verdict.status}</span>
              </td>
              <td className="case-id">{verdict.case_id}</td>
              <td className="num">{Number(verdict.duration_ms || 0).toFixed(1)}</td>
              <td className="num">{metrics.calls || 0}</td>
              <td className="num">{metrics.retries || 0}</td>
              <td className="dim small">{failed.length ? failed[0].name : ""}</td>
            </tr>,
            open && (
              <tr key={`${verdict.case_id}-detail`} className="case-detail">
                <td colSpan={6}>
                  {verdict.error && <div className="error-text">{verdict.error}</div>}
                  <CheckList checks={failed} />
                  <TracePanel runId={run.run_id} caseId={verdict.case_id} />
                </td>
              </tr>
            ),
          ];
        })}
      </tbody>
    </table>
  );
}
