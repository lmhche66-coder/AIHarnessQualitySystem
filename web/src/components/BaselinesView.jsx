import { timestamp } from "../format.js";
import { Kv } from "./blocks.jsx";

export default function BaselinesView({ baselines }) {
  return (
    <div className="pane">
      {baselines.length === 0 ? (
        <div className="empty">还没有基线。用 agenteval baseline 捕获一次运行作为基线。</div>
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
