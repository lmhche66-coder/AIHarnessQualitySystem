export function shortId(value) {
  if (!value) return "";
  return value.length > 26 ? value.slice(-26) : value;
}

export function timestamp(value) {
  if (!value) return "";
  return String(value).replace("T", " ").slice(0, 19);
}

export function fixed(value, digits = 4) {
  const number = Number(value || 0);
  return number.toFixed(digits);
}

export function statusTone(status) {
  if (status === "pass") return "ok";
  if (status === "fail") return "warn";
  return "bad";
}

export function describe(value) {
  if (value === undefined || value === null) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
