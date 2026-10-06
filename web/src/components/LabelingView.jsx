import { useEffect, useMemo, useState } from "react";

import { createGoldSet, getGoldSet, labelGoldItem, listGoldSets } from "../api.js";

export default function LabelingView({ sets, onChanged, notify, onCalibrate, busy: parentBusy }) {
  const [name, setName] = useState(null);
  const [detail, setDetail] = useState(null);
  const [cursor, setCursor] = useState(0);
  const [busy, setBusy] = useState(false);
  const [judgeRef, setJudgeRef] = useState("examples/demo_judges.py:build_keyword_judge");

  async function calibrate() {
    if (!name) return;
    setBusy(true);
    try {
      await onCalibrate(name, judgeRef.trim());
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (name == null && sets.length > 0) setName(sets[0].name);
  }, [sets, name]);

  useEffect(() => {
    if (name == null) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    getGoldSet(name)
      .then((payload) => {
        if (!cancelled) setDetail(payload);
      })
      .catch((error) => notify(error.message));
    return () => {
      cancelled = true;
    };
  }, [name, notify]);

  const pending = useMemo(
    () => (detail ? detail.items.findIndex((item) => item.expected == null) : -1),
    [detail]
  );

  useEffect(() => {
    if (detail && pending >= 0) setCursor(pending);
  }, [detail, pending]);

  async function importFile(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const text = await file.text();
    let payload;
    try {
      payload = JSON.parse(text);
    } catch (error) {
      notify(`不是合法 JSON：${error.message}`);
      return;
    }
    const items = Array.isArray(payload) ? payload : payload.items || [];
    const base = (file.name || "gold").replace(/\.[^.]+$/, "");
    setBusy(true);
    try {
      await createGoldSet(base, items);
      notify(`已创建金标集 ${base}，共 ${items.length} 条`);
      await onChanged();
      setName(base);
    } catch (error) {
      notify(error.message);
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }

  async function label(choice) {
    if (!detail) return;
    const item = detail.items[cursor];
    if (!item) return;
    setBusy(true);
    try {
      await labelGoldItem(detail.name, item.id, choice);
      const next = await getGoldSet(detail.name);
      setDetail(next);
      await onChanged();
      const remaining = next.items.findIndex((entry) => entry.expected == null);
      setCursor(remaining >= 0 ? remaining : Math.min(cursor + 1, next.items.length - 1));
    } catch (error) {
      notify(error.message);
    } finally {
      setBusy(false);
    }
  }

  if (sets.length === 0) {
    return (
      <div className="pane">
        <UploadRow onSelect={importFile} busy={busy} />
        <div className="empty">还没有金标集。上传一份配对样本即可开始标注。</div>
      </div>
    );
  }

  const item = detail && detail.items[cursor];
  return (
    <div className="pane">
      <UploadRow onSelect={importFile} busy={busy} />
      <form
        className="inline-form"
        onSubmit={(event) => {
          event.preventDefault();
          calibrate();
        }}
      >
        <label>
          <span className="dim small">判定器工厂</span>
          <input
            type="text"
            value={judgeRef}
            onChange={(event) => setJudgeRef(event.target.value)}
          />
        </label>
        <button
          type="submit"
          className="primary"
          disabled={busy || parentBusy || !name || pending >= detail?.items?.length}
        >
          {busy ? "校准中…" : "校准当前金标集"}
        </button>
        <span className="dim small">只使用已标注样本</span>
      </form>
      <div className="split">
        <ul className="run-list">
          {sets.map((entry) => (
            <li key={entry.name}>
              <button
                type="button"
                className={`run-item${entry.name === name ? " selected" : ""}`}
                onClick={() => setName(entry.name)}
              >
                <span className="run-id">{entry.name}</span>
                <span className="run-meta">
                  <span className={entry.labeled === entry.items ? "ok" : "warn"}>
                    {entry.labeled}/{entry.items}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
        <div>
          {!item && <div className="empty">该金标集没有样本</div>}
          {item && (
            <>
              <div className="detail-head">
                <span className="detail-title">{item.id}</span>
                <span className="dim small">
                  {cursor + 1} / {detail.items.length}
                </span>
                <span className="spacer" />
                <button type="button" className="ghost" onClick={() => setCursor(Math.max(0, cursor - 1))}>
                  上一条
                </button>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => setCursor(Math.min(detail.items.length - 1, cursor + 1))}
                >
                  下一条
                </button>
              </div>
              <div className="prompt-line">{item.prompt}</div>
              <div className="pair">
                <AnswerCard label="A" text={item.response_a} chosen={item.expected === "a"} />
                <AnswerCard label="B" text={item.response_b} chosen={item.expected === "b"} />
              </div>
              <div className="choice-row">
                <button type="button" className="primary" disabled={busy} onClick={() => label("a")}>
                  选 A
                </button>
                <button type="button" className="primary" disabled={busy} onClick={() => label("b")}>
                  选 B
                </button>
                <button type="button" className="ghost" disabled={busy} onClick={() => label("tie")}>
                  平局
                </button>
                {item.expected && (
                  <span className="dim small">当前标注：{item.expected}</span>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function UploadRow({ onSelect, busy }) {
  return (
    <div className="inline-form">
      <label>
        <span className="dim small">导入金标集（JSON）</span>
        <input type="file" accept=".json,application/json" onChange={onSelect} disabled={busy} />
      </label>
      <span className="dim small">文件名即金标集名称</span>
    </div>
  );
}

function AnswerCard({ label, text, chosen }) {
  return (
    <div className={`answer${chosen ? " chosen" : ""}`}>
      <div className="block-title">
        {label}
        {chosen && <span className="ok"> · 已选</span>}
      </div>
      <div className="answer-text">{text || "（空）"}</div>
    </div>
  );
}
