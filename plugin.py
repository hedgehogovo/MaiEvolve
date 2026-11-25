from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from maibot_sdk import API, ConfigWidget, ConfigWidgetState, HookHandler, MaiBotPlugin
from maibot_sdk.types import CONFIG_RELOAD_SCOPE_SELF, HookMode

try:
    from .config import EvolveConfig
    from .core import Store, build_prompt_addition, is_explicit_positive
    from .core.reflexion import is_night_window, local_day, reflect_session
except ImportError:
    from config import EvolveConfig
    from core import Store, build_prompt_addition, is_explicit_positive
    from core.reflexion import is_night_window, local_day, reflect_session

_LOGGER = logging.getLogger("plugin.aaorxilv.mai_evolve")
_IDENTITY_NESTED_KEYS = (
    "message",
    "message_info",
    "user_info",
    "group_info",
    "target_message",
    "reply_message",
    "sender",
    "author",
    "user_info",
    "chat_info",
    "reply_tool_args",
    "event",
)


def _dicts(value: Any, depth: int = 0):
    if depth > 3 or not isinstance(value, dict):
        return
    yield value
    for key in _IDENTITY_NESTED_KEYS:
        nested = value.get(key)
        if isinstance(nested, dict):
            yield from _dicts(nested, depth + 1)


def _pick_text(values: list[dict[str, Any]], *keys: str) -> str:
    for value in values:
        for key in keys:
            item = value.get(key)
            if isinstance(item, (str, int)) and str(item).strip():
                return str(item).strip()
    return ""


def _private_flag(values: list[dict[str, Any]]) -> bool:
    for value in values:
        if value.get("is_group") is True:
            return False
        if isinstance(value.get("group_info"), dict):
            return False
        message_info = value.get("message_info")
        if isinstance(message_info, dict):
            if isinstance(message_info.get("group_info"), dict):
                return False
            if isinstance(message_info.get("user_info"), dict):
                return True
        for key in ("chat_type", "message_type", "conversation_type"):
            chat_type = str(value.get(key, "")).lower()
            if chat_type in {"group", "guild", "channel"}:
                return False
            if chat_type in {"private", "friend", "direct"}:
                return True
    return False


def _group_flag(values: list[dict[str, Any]]) -> bool:
    for value in values:
        if value.get("is_group") is True or isinstance(value.get("group_info"), dict):
            return True
        message_info = value.get("message_info")
        if isinstance(message_info, dict) and isinstance(message_info.get("group_info"), dict):
            return True
        for key in ("chat_type", "message_type", "conversation_type"):
            if str(value.get(key, "")).lower() in {"group", "guild", "channel"}:
                return True
    return False


def _message_text(message: dict[str, Any]) -> str:
    for key in ("processed_plain_text", "plain_text", "raw_message", "text", "content"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _short_owner(session_id: str, user_id: str) -> str:
    session = session_id[-8:] if len(session_id) > 8 else session_id
    user = user_id[-5:] if len(user_id) > 5 else user_id
    return f"会话 ·{session} / 用户 ·{user}"


class MaiEvolvePlugin(MaiBotPlugin):
    config_model = EvolveConfig
    config_widgets = [ConfigWidget(id="evolve_status", kind="status", label="运行状态")]

    def __init__(self) -> None:
        super().__init__()
        self._settings = EvolveConfig()
        self._store: Store | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._shutdown = asyncio.Event()
        self._reflection_lock = asyncio.Lock()
        self._last_prune_day = ""

    async def on_load(self) -> None:
        data_dir = Path(self.ctx.paths.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self._store = Store(data_dir / "mai_evolve.sqlite3")

        try:
            configured = await self.ctx.config.get_plugin()
        except Exception:
            _LOGGER.exception("读取 MaiBot 插件配置失败，将使用默认配置")
            configured = {}
        if isinstance(configured, dict):
            fields = configured.get("plugin", configured)
            if isinstance(fields, dict):
                try:
                    self._settings = EvolveConfig.model_validate(fields)
                except Exception:
                    _LOGGER.exception("读取插件配置失败，使用默认配置")

        saved = self._store.get_settings()
        if saved:
            try:
                self._settings = EvolveConfig.model_validate(saved)
            except Exception:
                _LOGGER.exception("读取 WebUI 设置失败，使用 MaiBot 插件配置")
        else:
            self._store.save_settings(self._settings.model_dump())

        self._store.prune(before=time.time() - 90 * 86400)
        self._last_prune_day = local_day()
        self._shutdown.clear()
        self._worker_task = asyncio.create_task(self._background_worker(), name="mai-evolve-reflexion")
        _LOGGER.info("麦进化已加载")

    async def on_unload(self) -> None:
        self._shutdown.set()
        if self._worker_task is not None:
            self._worker_task.cancel()
            await asyncio.gather(self._worker_task, return_exceptions=True)
            self._worker_task = None
        if self._store is not None:
            self._store.close()
            self._store = None
        _LOGGER.info("麦进化已卸载")

    async def on_config_update(
        self,
        scope: str,
        config_data: dict[str, object],
        version: str,
    ) -> None:
        del version
        if scope != CONFIG_RELOAD_SCOPE_SELF or not isinstance(config_data, dict):
            return
        fields: Any = config_data.get("plugin", config_data)
        if isinstance(fields, dict):
            try:
                self._settings = EvolveConfig.model_validate(fields)
            except Exception:
                _LOGGER.exception("配置更新无效，保留现有设置")
                return
            if self._store is not None:
                self._store.save_settings(self._settings.model_dump())
    async def get_config_widget_state(self) -> dict[str, ConfigWidgetState]:
        calls = self._store.dashboard_stats(local_day())["calls_today"] if self._store else 0
        status = "运行中" if self._settings.enabled else "已暂停"
        tone = "success" if self._settings.enabled else "warning"
        if self._settings.audit_enabled:
            status += (
                f" · 夜间 {self._settings.reflection_night_start}—{self._settings.reflection_night_end}"
                f" · 今日审计 {calls}/{self._settings.max_calls_per_day}"
            )
        return {
            "evolve_status": ConfigWidgetState(
                text=f"{status}。仅夜间复盘；打开「麦进化」侧栏查看策略、审计评分与配置。",
                tone=tone,
            )
        }

    @API("dashboard", description="读取麦进化运行状态与审计面板", version="1")
    async def dashboard(self) -> dict[str, Any]:
        store = self._require_store()
        today = local_day()
        stats = store.dashboard_stats(today)
        strategies = store.all_strategies(50)
        return {
            "settings": self._settings.model_dump(),
            "stats": stats,
            "constraints": store.active_constraints(),
            "strategies": [
                {
                    "owner": _short_owner(item["session_id"], item["user_id"]),
                    "category": item["category"],
                    "rule": item["rule"],
                    "weight": item["weight"],
                    "updated_at": item["updated_at"],
                }
                for item in strategies
            ],
            "audits": [
                {
                    "owner": _short_owner(item["session_id"], item["user_id"]),
                    "score": item["score"],
                    "issues": item["issues"],
                    "summary": item["summary"],
                    "expression_saved": item["expression_saved"],
                    "created_at": item["created_at"],
                }
                for item in store.recent_audits(20)
            ],
            "runtime": {
                "enabled": self._settings.enabled,
                "audit_enabled": self._settings.audit_enabled,
                "night_window_open": is_night_window(
                    self._settings.reflection_night_start,
                    self._settings.reflection_night_end,
                ),
                "calls_today": stats["calls_today"],
                "daily_limit": self._settings.max_calls_per_day,
            },
        }

    @API("save_settings", description="保存麦进化配置", version="1")
    async def save_settings(
        self,
        enabled: bool,
        audit_enabled: bool,
        audit_interval_minutes: int,
        idle_minutes: int,
        reflection_night_start: str,
        reflection_night_end: str,
        recent_turns: int,
        max_calls_per_day: int,
        critic_task_name: str,
        minimum_safe_score: int,
        constraint_ttl_hours: int,
        constraint_ttl_turns: int,
        strategy_max_items: int,
        persona_anchors: str,
        critic_timeout_seconds: int = 90,
        issue_window: int = 5,
        issue_threshold: int = 3,
    ) -> dict[str, Any]:
        fields = {
            "enabled": enabled,
            "audit_enabled": audit_enabled,
            "audit_interval_minutes": audit_interval_minutes,
            "idle_minutes": idle_minutes,
            "reflection_night_start": reflection_night_start,
            "reflection_night_end": reflection_night_end,
            "recent_turns": recent_turns,
            "max_calls_per_day": max_calls_per_day,
            "critic_task_name": critic_task_name,
            "critic_timeout_seconds": critic_timeout_seconds,
            "minimum_safe_score": minimum_safe_score,
            "issue_window": issue_window,
            "issue_threshold": issue_threshold,
            "constraint_ttl_hours": constraint_ttl_hours,
            "constraint_ttl_turns": constraint_ttl_turns,
            "strategy_max_items": strategy_max_items,
            "persona_anchors": persona_anchors,
        }
        self._settings = EvolveConfig.model_validate(fields)
        self._require_store().save_settings(self._settings.model_dump())
        return {"ok": True, "message": "设置已保存"}

    @HookHandler(
        "chat.receive.after_process",
        name="capture_user_turn",
        description="记录待反思的用户发言",
        mode=HookMode.OBSERVE,
    )
    async def capture_user_turn(self, **kwargs: Any) -> None:
        store = self._store
        if store is None:
            return
        values = list(_dicts(kwargs))
        message = kwargs.get("message")
        if not isinstance(message, dict):
            message = {}
        event_values = list(_dicts(message))
        if any(value.get("is_bot") is True or value.get("is_self") is True for value in event_values):
            return
        user_id = _pick_text(event_values + values, "user_id", "sender_id", "author_id")
        session_id = _pick_text(values + event_values, "session_id", "chat_id", "stream_id")
        stream_id = _pick_text(values + event_values, "stream_id", "chat_id")
        content = _message_text(message)
        event_id = _pick_text(event_values + values, "message_id", "msg_id", "id")
        store.add_interaction(
            session_id=session_id,
            stream_id=stream_id,
            user_id=user_id,
            speaker="user",
            content=content,
            positive_feedback=is_explicit_positive(content),
            is_private=_private_flag(event_values + values),
            event_id=event_id,
            message_id=event_id,
        )

    @HookHandler(
        "maisaka.reply.before_post_process",
        name="capture_assistant_turn",
        description="记录最终回复供后台审计",
        mode=HookMode.OBSERVE,
    )
    async def capture_assistant_turn(self, **kwargs: Any) -> None:
        store = self._store
        if store is None:
            return
        values = list(_dicts(kwargs))
        session_id = _pick_text(values, "session_id", "chat_id", "stream_id")
        if not session_id:
            return
        user_id = _pick_text(values, "user_id", "sender_id", "author_id")
        reply_to = _pick_text(values, "reply_message_id")
        target = store.user_for_message(session_id, reply_to) if reply_to else None
        if target is None and not user_id:
            target = store.latest_user(session_id)
        if not user_id and target is None:
            return
        user_id = user_id or str(target["user_id"])
        content = str(kwargs.get("response") or "").strip()
        if not content:
            return
        store.add_interaction(
            session_id=session_id,
            stream_id=_pick_text(values, "stream_id", "chat_id"),
            user_id=user_id,
            speaker="assistant",
            content=content,
            is_private=bool(target["is_private"]) if target else _private_flag(values),
            event_id=reply_to or _pick_text(values, "message_id", "reply_id"),
            reply_to=reply_to,
        )

    @HookHandler(
        "maisaka.planner.before_request",
        name="inject_persona_and_rapport",
        description="向本轮规划注入人设锚点、纠偏规则和会话策略",
        mode=HookMode.BLOCKING,
        timeout_ms=50,
    )
    async def inject_context(self, **kwargs: Any) -> dict[str, Any]:
        store = self._store
        if store is None or not self._settings.enabled:
            return {"action": "continue"}
        values = list(_dicts(kwargs))
        session_id = _pick_text(values, "session_id", "chat_id", "stream_id")
        if not session_id:
            return {"action": "continue"}
        user_id = _pick_text(values, "user_id", "sender_id", "author_id")
        private_chat = _private_flag(values)
        if not private_chat and not _group_flag(values):
            recent_user = store.latest_user(session_id, max_age_seconds=45)
            if recent_user and recent_user["is_private"]:
                private_chat = True
                user_id = str(recent_user["user_id"])

        constraints, strategies = store.consume_prompt_context(
            session_id,
            user_id if private_chat else "",
            strategy_limit=self._settings.strategy_max_items,
            include_constraints=self._settings.audit_enabled,
        )
        extra = build_prompt_addition(self._settings.persona_anchors, constraints, strategies)
        if not extra:
            return {"action": "continue"}
        existing = str(kwargs.get("extra_prompt") or "").strip()
        kwargs["extra_prompt"] = "\n\n".join(item for item in (existing, extra) if item)
        return {"action": "continue", "modified_kwargs": kwargs}

    @HookHandler(
        "maisaka.replyer.before_request",
        name="inject_target_speaker_strategy",
        description="按回复目标消息 ID 注入对应群聊发言者的交互策略",
        mode=HookMode.BLOCKING,
        timeout_ms=50,
    )
    async def inject_target_speaker_strategy(self, **kwargs: Any) -> dict[str, Any]:
        store = self._store
        if store is None or not self._settings.enabled:
            return {"action": "continue"}
        session_id = _pick_text(list(_dicts(kwargs)), "session_id", "chat_id", "stream_id")
        reply_to = _pick_text(list(_dicts(kwargs)), "reply_message_id")
        if not session_id or not reply_to:
            return {"action": "continue"}
        target = store.user_for_message(session_id, reply_to)
        if target is None or target["is_private"]:
            return {"action": "continue"}
        strategies = store.strategies_for(
            session_id,
            str(target["user_id"]),
            self._settings.strategy_max_items,
        )
        extra = build_prompt_addition("", [], strategies)
        if not extra:
            return {"action": "continue"}
        existing = str(kwargs.get("extra_prompt") or "").strip()
        kwargs["extra_prompt"] = "\n\n".join(item for item in (existing, extra) if item)
        return {"action": "continue", "modified_kwargs": kwargs}

    async def _background_worker(self) -> None:
        while not self._shutdown.is_set():
            try:
                if self._store is not None and self._last_prune_day != local_day():
                    self._store.prune(before=time.time() - 90 * 86400)
                    self._last_prune_day = local_day()
                await self._process_due_sessions()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("麦进化后台任务失败")
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=15)
            except asyncio.TimeoutError:
                continue

    async def _process_due_sessions(self) -> None:
        store = self._store
        settings = self._settings
        if store is None or not settings.enabled or not settings.audit_enabled:
            return
        if not is_night_window(settings.reflection_night_start, settings.reflection_night_end):
            return
        due = store.due_sessions(
            now=time.time(),
            idle_seconds=settings.idle_minutes * 60,
            interval_seconds=settings.audit_interval_minutes * 60,
        )
        if not due:
            return
        async with self._reflection_lock:
            for session in due:
                if self._shutdown.is_set():
                    return
                interactions = store.interactions_for(
                    session["session_id"], session["user_id"], settings.recent_turns
                )
                if not any(item["speaker"] == "assistant" for item in interactions):
                    store.mark_reflected(
                        session["session_id"], session["user_id"], float(session["last_activity"])
                    )
                    continue
                if not store.reserve_daily_call(settings.max_calls_per_day, local_day()):
                    return
                store.mark_audit_attempt(session["session_id"], session["user_id"])
                await reflect_session(self.ctx, store, session, settings.model_dump())

    def _require_store(self) -> Store:
        if self._store is None:
            raise RuntimeError("插件尚未完成加载")
        return self._store


def create_plugin() -> MaiEvolvePlugin:
    return MaiEvolvePlugin()
