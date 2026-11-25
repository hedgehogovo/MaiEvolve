from __future__ import annotations

from maibot_sdk import PluginConfigBase
from pydantic import Field, model_validator


class EvolveConfig(PluginConfigBase):
    __ui_label__ = "麦进化"
    __ui_icon__ = "shield-check"

    enabled: bool = True
    audit_enabled: bool = True
    audit_interval_minutes: int = Field(default=5, ge=1, le=1440)
    idle_minutes: int = Field(default=30, ge=5, le=1440)
    reflection_night_start: str = Field(default="23:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    reflection_night_end: str = Field(default="06:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    recent_turns: int = Field(default=10, ge=2, le=40)
    max_calls_per_day: int = Field(default=30, ge=0, le=1000)
    critic_task_name: str = Field(default="fast_model", max_length=100)
    critic_timeout_seconds: int = Field(default=90, ge=10, le=300)
    minimum_safe_score: int = Field(default=80, ge=50, le=100)
    issue_window: int = Field(default=5, ge=3, le=20)
    issue_threshold: int = Field(default=3, ge=2, le=20)
    constraint_ttl_hours: int = Field(default=24, ge=1, le=720)
    constraint_ttl_turns: int = Field(default=10, ge=1, le=200)
    strategy_max_items: int = Field(default=5, ge=1, le=20)
    persona_anchors: str = Field(
        default="自然、真诚地交流，不使用客服腔。\n不凭空编造经历、事实或关系。\n不复读固定套话；没有必要时不以反问收尾。",
        max_length=3000,
    )

    @model_validator(mode="after")
    def validate_issue_threshold(self) -> EvolveConfig:
        if self.issue_threshold > self.issue_window:
            raise ValueError("问题触发次数不能大于统计窗口")
        if self.reflection_night_start == self.reflection_night_end:
            raise ValueError("夜间复盘开始时间和结束时间不能相同")
        return self
