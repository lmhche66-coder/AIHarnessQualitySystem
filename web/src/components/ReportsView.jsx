import { useState } from "react";

import { fixed, timestamp } from "../format.js";
import { Kv } from "./blocks.jsx";

const KIND_LABELS = {
  gate: "门禁",
  triage: "失败归因",
  judge: "裁判校准",
  report: "评测记分卡",
};

const MODULE_LABELS = {
  perception: "感知",
  planning: "规划",
  memory: "记忆",
  tool: "工具",
  retrieval: "检索",
};

const DATASET_LABELS = {
  basic_function: "基础技能",
  knowledge_qa: "知识问答",
  multi_turn: "多轮对话",
  abnormal_input: "异常输入",
  tool_call: "工具调用",
  multi_intent: "多意图",
  ambiguous_intent: "模糊意图",
  long_context_decay: "长对话衰减",
};

const EVAL_MODE_LABELS = { e2e_real: "端到端真实", e2e_mock: "端到端 Mock" };

function tone(passed) {
  if (passed === true) return "ok";
  if (passed === false) return "bad";
  return "";
}

function rateTone(rate) {
  if (rate >= 0.999) return "ok";
  if (rate >= 0.7) return "warn";
  return "bad";
}

function Stat({ value, label, tone = "" }) {
  return (
    <div className="stat">
      <span className={`stat-value ${tone}`}>{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

function label(table, key, fallback) {
  if (key === undefined || key === null || key === "") return fallback ?? "—";
  return table[key] || key;
}

function Bar({ rate }) {
  const width = Math.max(0, Math.min(1, Number(rate) || 0)) * 100;
  return (
    <div className="bar">
      <span className={rateTone(rate)} style={{ width: `${width}%` }} />
    </div>
  );
}

function MetricRow({ name, rate, note }) {
  return (
    <div className="metric-row">
      <span className="metric-name">{name}</span>
      <span className={`metric-value ${rateTone(rate)}`}>{fixed(rate)}</span>
      {note && <span className="metric-note dim">{note}</span>}
      <Bar rate={rate} />
    </div>
  );
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

function isScorecard(payload) {
  return Boolean(payload && typeof payload === "object" && payload.run_id && payload.scope);
}

function QualityColumn({ metrics, modules }) {
  return (
    <section className="block scorecard-col">
      <h3 className="block-title">质量</h3>
      {metrics.length === 0 ? (
        <div className="dim small">没有质量指标</div>
      ) : (
        metrics.map((stat) => (
          <MetricRow
            key={stat.metric}
            name={stat.metric}
            rate={stat.pass_rate}
            note={`pass ${stat.passed}/${stat.total - stat.skipped - stat.errored} · skip ${stat.skipped}`}
          />
        ))
      )}
      {modules.length > 0 && (
        <>
          <div className="section-title">模块</div>
          {modules.map((stat) => (
            <MetricRow
              key={stat.module}
              name={label(MODULE_LABELS, stat.module)}
              rate={stat.pass_rate}
              note={`pass ${stat.passed}/${stat.total - stat.skipped - stat.errored}`}
            />
          ))}
        </>
      )}
    </section>
  );
}

function CostColumn({ cost }) {
  return (
    <section className="block scorecard-col">
      <h3 className="block-title">成本</h3>
      <div className="kv">
        <Kv label="工具调用">{cost.tool_calls}</Kv>
        <Kv label="模型调用">{cost.model_calls}</Kv>
        <Kv label="重试">{cost.retries}</Kv>
        <Kv label="输入 token">{cost.input_tokens}</Kv>
        <Kv label="输出 token">{cost.output_tokens}</Kv>
        <Kv label="用量">{cost.usage_reported ? "已上报" : "未上报"}</Kv>
      </div>
    </section>
  );
}

function PerformanceColumn({ performance }) {
  return (
    <section className="block scorecard-col">
      <h3 className="block-title">性能</h3>
      <div className="kv">
        <Kv label="p50">{`${performance.duration_p50_ms} ms`}</Kv>
        <Kv label="p95">{`${performance.duration_p95_ms} ms`}</Kv>
        <Kv label="总耗时">{`${performance.total_duration_ms} ms`}</Kv>
      </div>
    </section>
  );
}

function ScenesBlock({ scenes }) {
  if (!scenes || scenes.length === 0) return null;
  return (
    <section className="block">
      <h3 className="block-title">场景通过率</h3>
      {scenes.map((scene) => (
        <MetricRow
          key={scene.scene}
          name={scene.scene}
          rate={scene.pass_rate}
          note={`pass ${scene.passed}/${scene.total - scene.skipped - scene.errored}`}
        />
      ))}
    </section>
  );
}

function DiagnosedCases({ cases }) {
  const flagged = (cases || []).filter((item) => item.primary_state !== "pass");
  if (cases && cases.length > 0 && flagged.length === 0) {
    return <div className="dim small">没有未通过用例</div>;
  }
  if (flagged.length === 0) {
    return <div className="dim small">没有用例明细</div>;
  }
  return (
    <table className="cases">
      <thead>
        <tr>
          <th>主指标状态</th>
          <th>用例</th>
          <th>数据集</th>
          <th>场景</th>
          <th>指标</th>
        </tr>
      </thead>
      <tbody>
        {flagged.map((item) => (
          <tr key={item.case_id}>
            <td>
              <span className={`pill ${item.primary_state}`}>{item.primary_state}</span>
            </td>
            <td className="case-id">{item.case_id}</td>
            <td className="dim small">
              {label(DATASET_LABELS, item.dataset_type)}
            </td>
            <td className="dim small">{item.scene || "—"}</td>
            <td className="dim small">
              {item.primary_metric
                ? `${item.primary_metric}: ${item.metrics?.[item.primary_metric] || "—"}`
                : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ReportCard({ record }) {
  const card = isScorecard(record.payload) ? record.payload : null;
  if (!card) {
    return (
      <section className="block">
        <h3 className="block-title">评测记分卡</h3>
        <div className="empty">这条记分卡没有可展示的载荷。</div>
      </section>
    );
  }
  const datasets = card.datasets || [];
  return (
    <>
      <section className="block">
        <h3 className="block-title">概览</h3>
        <div className="kv">
          <Kv label="运行">{card.run_id}</Kv>
          <Kv label="范围">{card.scope}</Kv>
          <Kv label="评测模式">{EVAL_MODE_LABELS[card.eval_mode] || card.eval_mode}</Kv>
          <Kv label="主指标">{card.primary_metric || "—"}</Kv>
          <Kv label="数据集">
            {datasets.map((item) => label(DATASET_LABELS, item)).join("、") || "—"}
          </Kv>
        </div>
      </section>
      <section className="block">
        <h3 className="block-title">用例</h3>
        <div className="stats">
          <Stat value={card.total} label="用例" />
          <Stat value={card.passed} label="通过" tone="ok" />
          <Stat value={card.failed} label="失败" tone="warn" />
          <Stat value={card.errored} label="错误" tone="bad" />
          <Stat value={card.skipped} label="跳过" />
        </div>
        <div className="kv">
          <Kv label="判定通过率">{fixed(card.pass_rate)}</Kv>
          <Kv label="主指标通过率">{fixed(card.primary_pass_rate)}</Kv>
        </div>
      </section>
      <div className="scorecard-cols">
        <QualityColumn metrics={card.quality || []} modules={card.modules || []} />
        <CostColumn cost={card.cost || {}} />
        <PerformanceColumn performance={card.performance || {}} />
      </div>
      <ScenesBlock scenes={card.scenes} />
      <section className="block">
        <h3 className="block-title">用例诊断</h3>
        <DiagnosedCases cases={card.cases} />
      </section>
    </>
  );
}

export default function ReportsView({ reports }) {
  const [selectedId, setSelectedId] = useState(null);
  if (reports.length === 0) {
    return (
      <div className="empty">
        还没有结论记录。执行门禁、失败归因、裁判校准或记分卡后会出现。
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
                {record.kind === "report" ? (
                  <span
                    className={rateTone(record.summary?.primary_pass_rate ?? 0)}
                  >
                    {`主指标 ${fixed(record.summary?.primary_pass_rate ?? 0)}`}
                  </span>
                ) : (
                  <span className={tone(record.passed)}>
                    {record.passed === true
                      ? "通过"
                      : record.passed === false
                        ? "不通过"
                        : "无结论"}
                  </span>
                )}
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
        {selected.kind === "report" ? (
          <ReportCard record={selected} />
        ) : (
          <section className="block">
            <h3 className="block-title">{KIND_LABELS[selected.kind] || selected.kind}</h3>
            <div className="kv">
              <Summary record={selected} />
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
