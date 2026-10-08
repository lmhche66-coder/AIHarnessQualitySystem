"""确定性演示裁判：用关键词与轻量解析产生严格 JSON，不需要模型。

真实裁判应当理解语义；这里只把平台侧的任务装配、结构化输出校验与判定折算跑通，
因此规则刻意做得简单、可预测。裁判按任务类型返回带 ``reasoning`` 的 JSON。
"""

from __future__ import annotations

import json
import re

REFUSAL_MARKERS = ("无法回答", "不知道", "抱歉，我无法", "信息不足")

_LABEL_RE = re.compile(r"(?:标签|路由|意图|label|route|intent)[=:：]\s*([\w\-.]+)")
_LIST_RE = re.compile(r"(?:工具|tools?)[=:：]\s*\[([^\]]*)\]")
_FIELD_RE = re.compile(r"([\w\-.]+)[=:：]([\w\-.]+)")


def build_keyword_judge():
    """返回一个裁判函数：``(task, case) -> json str``。"""

    def judge(task, case):
        return _dispatch(task, case)

    return judge


def _json(reasoning: str, **payload) -> str:
    return json.dumps({"reasoning": reasoning, **payload}, ensure_ascii=False)


def _dispatch(task, case) -> str:
    kind = task.kind.value
    if kind == "binary":
        refused = any(marker in case.output for marker in REFUSAL_MARKERS)
        verdict = "fail" if refused else "pass"
        reasoning = "输出包含拒答措辞" if refused else "输出正面回应了问题"
        return _json(reasoning, verdict=verdict)
    if kind == "classification":
        label = _extract_label(case.output) or "unknown"
        return _json(f"从输出解析到标签 {label}", label=label)
    if kind == "multi_label":
        labels = _extract_labels(case.output)
        return _json(f"从输出解析到 {len(labels)} 个标签", labels=labels)
    if kind == "extraction":
        fields = _extract_fields(case.output, task.fields)
        return _json("逐字段解析输出", fields=fields)
    if kind == "score":
        score = round(min(1.0, len(case.output) / 100.0), 3)
        return _json(f"按信息量给分 {score}", score=score)
    choice = "a" if len(case.output) >= len(case.reference) else "b"
    return _json(f"较长的回答为 {choice}", choice=choice)


def _extract_label(text: str) -> str | None:
    match = _LABEL_RE.search(text)
    return match.group(1) if match else None


def _extract_labels(text: str) -> list[str]:
    match = _LIST_RE.search(text)
    if not match:
        return []
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


def _extract_fields(text: str, fields) -> dict:
    wanted = set(fields) if fields else None
    found: dict[str, str] = {}
    for key, value in _FIELD_RE.findall(text):
        if wanted is None or key in wanted:
            found[key] = value
    return found
