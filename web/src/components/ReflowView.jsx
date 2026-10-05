import { useState } from "react";

import { analyzeReflow, writeCases } from "../api.js";
import { describe } from "../format.js";
import { Kv } from "./blocks.jsx";

export default function ReflowView({ runs, onChanged, notify }) {
  const [runId, setRunId] = useState("");
  const [casesFile, setCasesFile] = useState("");
  const [report, setReport] = useState(null);
  const [candidates, setCandidates] = useState([]);
  const [selected, setSelected] = useState({});
  const [outputName, setOutputName] = useState("repro");
  const [busy, setBusy] = useState(false);

  async function analyze() {
    const target = runId || (runs[0] && runs[0].run_id);
    if (!target) {
      notify("没有可归因的运行");
      return;
    }
    setBusy(true);
    setReport(null);
    try {
      const payload = await analyzeReflow(target, casesFile.trim());
      setReport(payload.report);
      setCandidates(payload.candidates || []);
      setSelected(
        Object.fromEntries((payload.candidates || []).map((item) => [item.id, true]))
      );
    } catch (error) {
      notify(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function write() {
    const chosen = candidates.filter((item) => selected[item.id]);
    setBusy(true);
    try {
      const payload = await writeCases(outputName.trim() || "repro", chosen);
      notify(
        payload.written > 0
          ? `已写出 ${payload.written} 条候选用例到 ${payload.name}`
          : "没有勾选任何候选用例，未写出文件"
      );
      await onChanged();
    } catch (error) {
      notify(error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pane">
      <form
        className="inline-form"
        onSubmit={(event) => {
          event.preventDefault();
          analyze();
        }}
      >
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
          <span className="dim small">用例文件（可选）</span>
          <input
            type="text"
            value={casesFile}
            placeholder="默认取运行记录里的路径"
            onChange={(event) => setCasesFile(event.target.value)}
          />
        </label>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "归因中…" : "执行归因"}
        </button>
      </form>

      {report && (
        <section className="block">
          <h3 className="block-title">归因</h3>
          <div className="kv">
            <Kv label="失败">{report.total_failures}</Kv>
            <Kv label="可回流">{report.reflowable}</Kv>
            <Kv label="已验证">{report.verified}</Kv>
            <Kv label="候选用例">{candidates.length}</Kv>
          </div>
          <ul className="checks">
            {(report.details || []).map((detail) => (
              <li key={detail.case_id}>
                <span className={detail.reflowable ? "ok" : "dim"}>{detail.case_id}</span>
                {detail.reflowable ? (
                  <label className="inline-check">
                    <input
                      type="checkbox"
                      checked={selected[detail.candidate_id] || false}
                      disabled={!detail.verified}
                      onChange={(event) =>
                        setSelected((prev) => ({
                          ...prev,
                          [detail.candidate_id]: event.target.checked,
                        }))
                      }
                    />
                    已验证，可回流
                  </label>
                ) : (
                  <span className="dim small"> · {detail.reason}</span>
                )}
                {detail.failed_checks && detail.failed_checks.length > 0 && (
                  <span className="dim small">
                    {" "}
                    · {detail.failed_checks[0].name} 期望{" "}
                    {describe(detail.failed_checks[0].expected)}
                  </span>
                )}
              </li>
            ))}
          </ul>
          <div className="inline-form">
            <label>
              <span className="dim small">输出名</span>
              <input
                type="text"
                value={outputName}
                onChange={(event) => setOutputName(event.target.value)}
              />
            </label>
            <button type="button" className="primary" disabled={busy} onClick={write}>
              写出选中的候选用例
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
