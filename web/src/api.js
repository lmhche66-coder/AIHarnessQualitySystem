async function request(url) {
  const response = await fetch(url);
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = { error: text };
  }
  if (!response.ok) {
    throw new Error((payload && payload.error) || response.statusText);
  }
  return payload;
}

// 控制台是只读的：这里只保留读接口，评测由 CLI 或 CI 触发。
export const listRuns = () => request("/api/runs").then((data) => data.runs || []);
export const getRun = (runId) => request(`/api/runs/${encodeURIComponent(runId)}`);
export const getTrace = (runId, caseId) =>
  request(
    `/api/runs/${encodeURIComponent(runId)}/traces/${encodeURIComponent(caseId)}`
  );
export const listReports = () => request("/api/reports").then((data) => data.reports || []);
export const listBaselines = () =>
  request("/api/baselines").then((data) => data.baselines || []);
export const listGoldSets = () => request("/api/gold").then((data) => data.sets || []);
export const listTraces = () => request("/api/traces").then((data) => data.traces || []);
export const getReflow = () => request("/api/reflow");
