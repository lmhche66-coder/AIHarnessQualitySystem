import { describe, fixed } from "../format.js";

export function Kv({ label, children }) {
  return (
    <span>
      {label} <b>{children}</b>
    </span>
  );
}

export function MetricsBlock({ metrics }) {
  if (!metrics) return null;
  const violations = metrics.budget_violations || [];
  return (
    <section className="block">
      <h3 className="block-title">指标</h3>
      <div className="kv">
        <Kv label="调用">{metrics.calls}</Kv>
        <Kv label="重复">{metrics.retries}</Kv>
        <Kv label="p50">{`${metrics.duration_p50_ms} ms`}</Kv>
        <Kv label="p95">{`${metrics.duration_p95_ms} ms`}</Kv>
        <Kv label="token">{`${metrics.input_tokens}/${metrics.output_tokens}`}</Kv>
        <Kv label="用量">{metrics.usage_reported ? "已上报" : "未覆盖"}</Kv>
      </div>
      {violations.length > 0 && (
        <ul className="notes bad">
          {violations.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function TaskBlock({ report, attempts }) {
  if (!report) return null;
  const repeated = attempts && attempts.attempts > attempts.tasks;
  return (
    <section className="block">
      <h3 className="block-title">任务</h3>
      <div className="kv">
        <Kv label="任务">{report.total}</Kv>
        <Kv label="已解决">{report.resolved}</Kv>
        <Kv label="通过率">{fixed(report.resolved_rate)}</Kv>
        {repeated && <Kv label="尝试">{attempts.attempts}</Kv>}
        {repeated && <Kv label="pass@1">{fixed(attempts.pass_at_1)}</Kv>}
        {repeated && <Kv label="pass@k">{fixed(attempts.pass_at_k)}</Kv>}
      </div>
      {(report.unresolved_tasks || []).length > 0 && (
        <ul className="notes warn">
          {report.unresolved_tasks.map((task) => (
            <li key={task}>未解决 {task}</li>
          ))}
        </ul>
      )}
      {(report.errored_tasks || []).length > 0 && (
        <ul className="notes bad">
          {report.errored_tasks.map((task) => (
            <li key={task}>出错 {task}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function CassetteBlock({ cassette }) {
  if (!cassette) return null;
  return (
    <section className="block">
      <h3 className="block-title">cassette</h3>
      <div className="kv">
        <Kv label="名称">{cassette.name}</Kv>
        <Kv label="模式">{cassette.mode}</Kv>
        <Kv label="交互">{cassette.interactions}</Kv>
        <Kv label="未使用">{(cassette.unused || []).length}</Kv>
      </div>
    </section>
  );
}

export function LoadBlock({ reports }) {
  if (!reports || reports.length === 0) return null;
  return (
    <section className="block">
      <h3 className="block-title">压测</h3>
      {reports.map((report) => {
        const verdict =
          report.passed === true ? "PASS" : report.passed === false ? "FAIL" : "INFO";
        const errors = Object.entries(report.error_kinds || {});
        const fanout = Object.entries(report.fanout || {});
        const notes = [...(report.reasons || []), ...(report.limitations || [])];
        return (
          <div className="sub" key={report.scenario_id}>
            <div className={`scenario ${verdict.toLowerCase()}`}>
              [{verdict}] {report.scenario_id}
            </div>
            <div className="kv">
              <Kv label="并发">{report.concurrency}</Kv>
              <Kv label="完成">{report.completed}</Kv>
              <Kv label="失败">{report.failed}</Kv>
              <Kv label="错误率">{fixed(report.error_rate)}</Kv>
              <Kv label="吞吐">{`${report.throughput_rps} rps`}</Kv>
              <Kv label="p50/p95/p99">
                {`${report.latency_p50_ms}/${report.latency_p95_ms}/${report.latency_p99_ms} ms`}
              </Kv>
              {report.ttfb_observed && (
                <Kv label="首字节 p50/p95">
                  {`${report.ttfb_p50_ms}/${report.ttfb_p95_ms} ms`}
                </Kv>
              )}
            </div>
            {errors.length > 0 && (
              <ul className="notes bad">
                {errors.map(([kind, count]) => (
                  <li key={kind}>
                    {kind} × {count}
                  </li>
                ))}
              </ul>
            )}
            {fanout.length > 0 && (
              <div className="kv">
                {fanout.map(([name, stats]) => (
                  <Kv key={name} label={name}>
                    {`均值 ${stats.mean} / p95 ${stats.p95}`}
                  </Kv>
                ))}
              </div>
            )}
            {notes.length > 0 && (
              <ul className="notes warn">
                {notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </section>
  );
}

export function CheckList({ checks }) {
  if (!checks || checks.length === 0) {
    return <div className="dim small">全部判据通过</div>;
  }
  return (
    <ul className="checks">
      {checks.map((check) => (
        <li key={check.name}>
          <span className="check-name">{check.name}</span>{" "}
          <span className="dim">
            期望 {describe(check.expected)}，实际 {describe(check.actual)}
          </span>
          {check.message && <span className="dim"> · {check.message}</span>}
        </li>
      ))}
    </ul>
  );
}
