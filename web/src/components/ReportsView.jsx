import { useState } from "react";

import { fixed, timestamp } from "../format.js";
import { Kv } from "./blocks.jsx";

const KIND_LABELS = {
  gate: "门禁",
  triage: "失败归因",
  judge: "裁判校准",
};

function tone(passed) {
  if (passed === true) return "ok";
  if (passed === false) return "bad";
  return "";
}

function GateSummary({ summary }) {
  return (
    <>
      <Kv label="运行">{summary.run_id}</Kv>
      <Kv label="通过率">{fixed(summary.pass_rate)}</Kv>
      <Kv label="失败">{summary.failed}</Kv>
      <Kv label="错误">{summary.errored}</Kv>
      <Kv label="回归">{(summary.regressions || []).length}</Kv>
      <Kv label="缺失">{(summary.missing || []).length}</Kv>
    </>
  );
}

function TriageSummary({ summary }) {
  return (
    <>
      <Kv label="运行">{summary.run_id}</Kv>
      <Kv label="失败">{summary.total_failures}</Kv>
      <Kv label="可回流">{summary.reflowable}</Kv>
      <Kv label="已验证">{summary.verified}</Kv>
      <Kv label="候选用例">{(summary.candidates || []).length}</Kv>
    </>
  );
}

function JudgeSummary({ summary }) {
  return (
    <>
      <Kv label="样本">{summary.items}</Kv>
      <Kv label="一致率">{fixed(summary.agreement)}</Kv>
      <Kv label="置信区间">
        {`${fixed(summary.ci_low)}–${fixed(summary.ci_high)}`}
      </Kv>
      <Kv label="位置翻转">{fixed(summary.position_flip_rate)}</Kv>
      <Kv label="长度偏置">{fixed(summary.length_bias)}</Kv>
    </>
  );
}

function Summary({ record }) {
  if (record.kind === "gate") return <GateSummary summary={record.summary || {}} />;
  if (record.kind === "triage") return <TriageSummary summary={record.summary || {}} />;
  return <JudgeSummary summary={record.summary || {}} />;
}

export default function ReportsView({ reports }) {
  const [selectedId, setSelectedId] = useState(null);
  if (reports.length === 0) {
    return (
      <div className="empty">
        还没有结论记录。执行 gate、triage 或 judge calibrate 后会出现。
      </div>
    );
  }
  const selected = reports.find((record) => record.id === selectedId) || reports[0];
  return (
    <div className="split">
      <ul className="run-list">
        {reports.map((record) => (
          <li key={record.id}>
            <button
              type="button"
              className={`run-item${record.id === selected.id ? " selected" : ""}`}
              onClick={() => setSelectedId(record.id)}
            >
              <span className="run-id">{KIND_LABELS[record.kind] || record.kind}</span>
              <span className="run-meta">
                <span className={tone(record.passed)}>
                  {record.passed === true
                    ? "通过"
                    : record.passed === false
                      ? "不通过"
                      : "无结论"}
                </span>
                <span className="dim">{timestamp(record.created_at)}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      <div className="pane">
        <div className="detail-head">
          <span className="detail-title">{selected.title}</span>
          <span className="dim">{timestamp(selected.created_at)}</span>
        </div>
        <section className="block">
          <h3 className="block-title">{KIND_LABELS[selected.kind] || selected.kind}</h3>
          <div className="kv">
            <Summary record={selected} />
          </div>
        </section>
      </div>
    </div>
  );
}
