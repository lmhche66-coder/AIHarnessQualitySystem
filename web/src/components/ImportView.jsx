import { useState } from "react";

import { listTraces, uploadTrace } from "../api.js";

export default function ImportView({ traces, onChanged, notify }) {
  const [file, setFile] = useState(null);
  const [name, setName] = useState("");
  const [caseId, setCaseId] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  async function submit(event) {
    event.preventDefault();
    if (!file) {
      notify("请选择审计文件");
      return;
    }
    const traceName = (name || file.name.replace(/\.[^.]+$/, "")).trim();
    if (!traceName) {
      notify("请给出轨迹名");
      return;
    }
    const sourceFormat = file.name.toLowerCase().endsWith(".csv") ? "csv" : "json";
    setBusy(true);
    setResult(null);
    try {
      const content = await file.text();
      const payload = await uploadTrace({
        name: traceName,
        case_id: caseId || traceName,
        content,
        format: sourceFormat,
      });
      setResult(payload);
      notify(`已导入轨迹 ${payload.name}，${payload.records} 条记录`);
      await onChanged();
    } catch (error) {
      notify(error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pane">
      <form className="inline-form" onSubmit={submit}>
        <label>
          <span className="dim small">审计文件（JSON 或 CSV）</span>
          <input
            type="file"
            accept=".json,.csv,application/json,text/csv"
            onChange={(event) => setFile(event.target.files[0] || null)}
          />
        </label>
        <label>
          <span className="dim small">轨迹名</span>
          <input
            type="text"
            value={name}
            placeholder="默认取文件名"
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
          <span className="dim small">用例标识</span>
          <input
            type="text"
            value={caseId}
            placeholder="默认同轨迹名"
            onChange={(event) => setCaseId(event.target.value)}
          />
        </label>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "导入中…" : "导入轨迹"}
        </button>
      </form>

      {result && (
        <section className="block">
          <h3 className="block-title">导入结果</h3>
          <div className="kv">
            <span>
              轨迹 <b>{result.name}</b>
            </span>
            <span>
              用例 <b>{result.case_id}</b>
            </span>
            <span>
              记录 <b>{result.records}</b>
            </span>
            <span>
              事件 <b>{result.events}</b>
            </span>
          </div>
          {(result.limitations || []).length > 0 && (
            <ul className="notes warn">
              {result.limitations.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      <section className="block">
        <h3 className="block-title">已保存轨迹</h3>
        {traces.length === 0 ? (
          <div className="dim small">还没有轨迹</div>
        ) : (
          <table className="cases">
            <thead>
              <tr>
                <th>名称</th>
                <th>用例</th>
                <th className="right">事件</th>
                <th>来源</th>
              </tr>
            </thead>
            <tbody>
              {traces.map((trace) => (
                <tr key={trace.name}>
                  <td className="case-id">{trace.name}</td>
                  <td className="case-id">{trace.case_id}</td>
                  <td className="num">{trace.events}</td>
                  <td className="dim small">{trace.source || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
