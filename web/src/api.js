async function request(url, options) {
  const response = await fetch(url, options);
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

export const listRuns = () => request("/api/runs").then((data) => data.runs || []);
export const getRun = (runId) => request(`/api/runs/${encodeURIComponent(runId)}`);
export const getTrace = (runId, caseId) =>
  request(
    `/api/runs/${encodeURIComponent(runId)}/traces/${encodeURIComponent(caseId)}`
  );
export const listReports = () => request("/api/reports").then((data) => data.reports || []);
export const listBaselines = () =>
  request("/api/baselines").then((data) => data.baselines || []);
export const captureBaseline = (runId, name) =>
  request("/api/baselines", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId, name }),
  });
