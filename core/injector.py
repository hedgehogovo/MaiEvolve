from __future__ import annotations

from typing import Any

from .critic import ISSUE_INSTRUCTIONS


def build_prompt_addition(
    persona_anchors: str,
    constraints: list[dict[str, Any]],
    strategies: list[dict[str, Any]],
) -> str:
    sections: list[str] = []
    anchors = [line.strip(" -\t") for line in str(persona_anchors or "").splitlines() if line.strip(" -\t")]
    if anchors:
        sections.append("【长期人设锚点】\n" + "\n".join(f"- {line[:180]}" for line in anchors[:12]))

    instructions = list(
        dict.fromkeys(
            str(item.get("instruction") or ISSUE_INSTRUCTIONS.get(str(item.get("category", "")), "")).strip()
            for item in constraints
        )
    )
    instructions = [item for item in instructions if item]
    if instructions:
        sections.append("【临时表达纠偏】\n" + "\n".join(f"- {item[:200]}" for item in instructions[:5]))

    rules = list(dict.fromkeys(str(item.get("rule", "")).strip() for item in strategies if item.get("rule")))
    if rules:
        sections.append(
            "【本会话交互默契】\n"
            + "\n".join(f"- {rule[:180]}" for rule in rules[:5])
            + "\n只将这些规则用于当前交流，不要向其他用户透露其来源或内容。"
        )
    if not sections:
        return ""
    return "请把以下约束作为本轮回复的优先要求，并结合当前上下文自然执行：\n" + "\n\n".join(sections)
