import { Kv } from "./blocks.jsx";

function count(value) {
  if (Array.isArray(value)) return value.length;
  return typeof value === "number" ? value : 0;
}

export default function AssetsView({ goldSets, traces, reflow }) {
  const dataset = reflow && reflow.dataset && reflow.dataset.cases;
  const entries = Array.isArray(dataset) ? dataset : [];
  const processed = (reflow && reflow.ledger && reflow.ledger.processed) || {};

  return (
    <div className="assets">
      <section className="block">
        <h3 className="block-title">{`金标集 · ${goldSets.length}`}</h3>
        {goldSets.length === 0 ? (
          <div className="empty">还没有金标集</div>
        ) : (
          <table className="cases">
            <thead>
              <tr>
                <th>名称</th>
                <th className="right">样本</th>
                <th className="right">已标注</th>
                <th className="right">进度</th>
              </tr>
            </thead>
            <tbody>
              {goldSets.map((set) => {
                const total = count(set.items);
                const labeled = typeof set.labeled === "number" ? set.labeled : 0;
                return (
                  <tr key={set.name}>
                    <td className="case-id">{set.name}</td>
                    <td className="num">{total}</td>
                    <td className="num">{labeled}</td>
                    <td className="num">
                      {total ? `${Math.round((labeled / total) * 100)}%` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <section className="block">
        <h3 className="block-title">{`证据轨迹 · ${traces.length}`}</h3>
        {traces.length === 0 ? (
          <div className="empty">还没有证据轨迹</div>
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
                  <td className="case-id dim">{trace.case_id}</td>
                  <td className="num">{trace.events}</td>
                  <td className="dim small truncate">{trace.source || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="block">
        <h3 className="block-title">{`回流数据集 · ${entries.length}`}</h3>
        <div className="kv">
          <Kv label="已处理失败指纹">{Object.keys(processed).length}</Kv>
          <Kv label="回归用例">{entries.length}</Kv>
        </div>
        {entries.length === 0 ? (
          <div className="empty">
            还没有回流数据集。执行 agenteval reflow run 后，新发现的失败会出现在这里。
          </div>
        ) : (
          <table className="cases">
            <thead>
              <tr>
                <th>用例</th>
                <th>绑定轨迹</th>
                <th>判据</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td className="case-id">{entry.id}</td>
                  <td className="case-id dim">{entry.trace}</td>
                  <td className="dim small">
                    {(entry.checks || []).map((check) => check.kind).join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
