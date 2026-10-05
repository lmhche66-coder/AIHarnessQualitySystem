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

function postJson(url, body) {
  return request(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const listGoldSets = () => request("/api/gold").then((data) => data.sets || []);
export const getGoldSet = (name) => request(`/api/gold/${encodeURIComponent(name)}`);
export const createGoldSet = (name, items) => postJson("/api/gold", { name, items });
export const labelGoldItem = (name, itemId, expected) =>
  postJson(`/api/gold/${encodeURIComponent(name)}/labels`, {
    item_id: itemId,
    expected,
  });

export const listTraces = () => request("/api/traces").then((data) => data.traces || []);
export const uploadTrace = (payload) => postJson("/api/traces", payload);

export const analyzeReflow = (runId, casesFile) =>
  postJson("/api/reflow", { run_id: runId, cases_file: casesFile || null });
export const writeCases = (name, cases) => postJson("/api/cases", { name, cases });
export const listCaseFiles = () => request("/api/cases").then((data) => data.cases || []);

export const triggerRun = (casesFile, registry) =>
  postJson("/api/runs", { cases_file: casesFile, registry });
