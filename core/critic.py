from __future__ import annotations

import json
import math
import re
from typing import Any

ISSUE_INSTRUCTIONS = {
    "ai_tone": "避免客服腔、模板化安慰和生硬总结，直接自然地回应对话。",
    "cliche_repeat": "避免重复近期已经出现的套话和固定句式，换成贴合当前语境的表达。",
    "sermon": "避免未经请求的说教或长篇教育，先回应对方真正表达的内容。",
    "persona_drift": "回复前对照人设锚点，不迎合诱导去编造身份、经历或无依据的立场。",
    "sycophancy": "避免无依据地一味赞同或迎合；先独立判断，并温和表达必要边界。",
    "question_loop": "避免连续抛出多个问题；没有必要时不要用反问结束回复。",
}

VALID_ISSUES = frozenset(ISSUE_INSTRUCTIONS)
VALID_STRATEGY_CATEGORIES = frozenset({"closure", "pacing", "avoid"})

_POSITIVE_PATTERNS = (
    re.compile(r"谢谢(?:你|啦|了)?(?:帮忙|解答|说明|解释|提醒)?"),
    re.compile(r"(?:很|特别|真的)?(?:有帮助|有用|说得好|讲得清楚|回答得好|解决了)"),
    re.compile(r"(?:我)?(?:很|好)?喜欢(?:你|这个|这样|这句|这种说法)?"),
    re.compile(r"(?:太|真)?(?:棒|好了|厉害|贴心|准确|清楚)(?:了|啊|呀)?"),
    re.compile(r"正是我想要的|这就对了|说到点上了"),
)
_NEGATIVE_FEEDBACK_PATTERN = re.compile(
    r"(?:没有帮助|没什么帮助|帮不上忙|没帮上忙|不喜欢|不太喜欢|"
    r"并不喜欢|不满意|不太满意|没用|没有用|不准确|不够准确|"
    r"不清楚|不够清楚|说得不好|不是我想要的|"
    r"not helpful|not useful|do not like|don't like|not satisfied)",
    re.IGNORECASE,
)


def is_explicit_positive(text: str) -> bool:
    normalized = str(text or "").strip()
    return bool(
        normalized
        and not _NEGATIVE_FEEDBACK_PATTERN.search(normalized)
        and any(pattern.search(normalized) for pattern in _POSITIVE_PATTERNS)
    )


def _decode_json(text: str) -> dict[str, Any]:
    source = str(text or "").strip()
    if source.startswith("```"):
        source = re.sub(r"^```(?:json)?\s*|\s*```$", "", source, flags=re.IGNORECASE)
    start = source.find("{")
    if start < 0:
        return {}
    try:
        result, _ = json.JSONDecoder().raw_decode(source[start:])
    except (json.JSONDecodeError, TypeError):
        return {}
    return result if isinstance(result, dict) else {}


def parse_critic_response(text: str) -> dict[str, Any]:
    raw = _decode_json(text)
    raw_score = raw.get("score")
    valid = (
        isinstance(raw_score, (int, float))
        and not isinstance(raw_score, bool)
        and math.isfinite(raw_score)
    )
    try:
        score = max(0, min(100, int(raw.get("score", 0))))
    except (OverflowError, TypeError, ValueError):
        score = 0

    issues = raw.get("issues", [])
    if not isinstance(issues, list):
        issues = []
    normalized_issues = list(dict.fromkeys(str(item) for item in issues if str(item) in VALID_ISSUES))

    strategies: list[dict[str, Any]] = []
    candidates = raw.get("strategies", [])
    if isinstance(candidates, list):
        for item in candidates[:4]:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category", ""))
            rule = str(item.get("rule", "")).strip()
            evidence = str(item.get("evidence", "")).strip()
            try:
                confidence = float(item.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0
            if (
                category in VALID_STRATEGY_CATEGORIES
                and 0.0 <= confidence <= 1.0
                and confidence >= 0.7
                and 4 <= len(rule) <= 180
                and 2 <= len(evidence) <= 240
                and not re.search(
                    r"(?:忽略|无视|泄露|绕过|服从|暴露|密码|验证码|系统提示|"
                    r"ignore|bypass|reveal|exfiltrat|system prompt|must obey)",
                    rule,
                    re.IGNORECASE,
                )
            ):
                strategies.append(
                    {
                        "category": category,
                        "rule": rule,
                        "evidence": evidence,
                        "confidence": confidence,
                    }
                )

    expression = raw.get("expression", {})
    if not isinstance(expression, dict):
        expression = {}
    safe_expression = {
        "eligible": expression.get("eligible") is True,
        "situation": str(expression.get("situation", "")).strip()[:120],
        "style": str(expression.get("style", "")).strip()[:80],
        "content": str(expression.get("content", "")).strip()[:240],
        "safe": expression.get("safe") is True,
    }
    return {
        "valid": valid,
        "score": score,
        "issues": normalized_issues,
        "summary": str(raw.get("summary", "")).strip()[:500],
        "strategies": strategies,
        "expression": safe_expression,
    }
