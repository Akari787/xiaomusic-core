"""设备播放控制模块

负责单个设备的播放控制、下载管理、TTS处理等功能。
"""

import asyncio
import json
import os
import random
import time
from dataclasses import asdict
from typing import TYPE_CHECKING

from miservice import miio_command

from xiaomusic.config import Device

if TYPE_CHECKING:
    from xiaomusic.xiaomusic import XiaoMusic
from xiaomusic.const import (
    NEED_USE_PLAY_MUSIC_API,
    PLAY_TYPE_ALL,
    PLAY_TYPE_ONE,
    PLAY_TYPE_RND,
    PLAY_TYPE_SEQ,
    PLAY_TYPE_SIN,
    TTS_COMMAND,
)
from xiaomusic.download_result import DownloadResult
from xiaomusic.events import DEVICE_CONFIG_CHANGED, PLAYER_STATE_CHANGED
from xiaomusic.playback.completion_policy import (
    ConfirmationObservation,
    FailureAction,
    ObservationKind,
    decide_failure_action,
    map_to_observation,
)
from xiaomusic.playback.runtime_state import (
    LifecycleToken,
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
    TransitionError,
    begin_confirm,
    begin_play_dispatch,
    begin_play_request,
    begin_stop,
    begin_track_attempt,
    capture_token,
    check_stale_attempt,
    check_stale_queue,
    check_stale_strict,
    complete_stop,
    confirm_playing,
    new_queue_session,
    next_command,
    report_failure,
)
from xiaomusic.playback.runtime_state import (
    pause as _pause_transition,
)
from xiaomusic.playback.task_registry import (
    ATTEMPT_SCOPED_KINDS,
    SESSION_SCOPED_KINDS,
    PlaybackTaskRegistry,
    TaskGeneration,
    TaskKind,
)
from xiaomusic.utils.file_utils import chmodfile
from xiaomusic.utils.text_utils import custom_sort_key, list2str


class XiaoMusicDevice:
    """设备播放控制类

    负责单个小爱设备的播放控制，包括：
    - 播放控制（播放、暂停、上一首、下一首）
    - 播放列表管理
    - 下载管理
    - TTS（文字转语音）
    - 定时器管理
    - 设备状态管理
    """

    MAX_PLAYING_GRACE_EXTENSIONS = 0  # device may keep reporting playing after EOF/replay
    MAX_UNKNOWN_GRACE_EXTENSIONS = 3  # tolerate transient status-query failures

    def __init__(self, xiaomusic: "XiaoMusic", device: Device, group_name: str):
        """初始化设备播放控制器

        Args:
            xiaomusic: XiaoMusic 主类实例
            device: 设备配置对象
            group_name: 设备组名
        """
        self.group_name = group_name
        self.device = device
        self.config = xiaomusic.config
        self.device_id = device.device_id
        self.log = xiaomusic.log
        self.xiaomusic = xiaomusic
        self._runtime_state = PlaybackRuntimeState()
        self.auth_manager = xiaomusic.auth_manager
        self.ffmpeg_location = self.config.ffmpeg_location
        self.event_bus = getattr(xiaomusic, "event_bus", None)

        self._download_proc = None  # 下载对象
        self._next_timer = None
        self.is_playing = False
        # 播放进度
        self._start_time = 0
        self._duration = 0
        self._paused_time = 0
        self._play_failed_cnt = 0
        self._play_fail_first_ts = 0.0
        self._play_fail_last_reason = ""
        self._playback_tasks = PlaybackTaskRegistry()
        self._failure_retry_meta: dict = {}
        self._failure_retry_last_status = "idle"
        self._failure_retry_last_error = ""
        self._failure_retry_done_event: asyncio.Event | None = None
        self._degraded = False
        self._degraded_notified = False
        self._last_volume = 0

        # Async media-session invalidation id only. Queue, command, and attempt
        # identity live in LifecycleToken/runtime_state; this id does not decide
        # physical playback state.
        self._play_session_id = 0

        # Non-destructive completion tracking. The timer owns normal completion;
        # the background confirmation counter is diagnostic-only and never advances.
        self._timer_expiry_false_count = 0
        self._bg_confirm_false_count = 0
        self._timer_expiry_playing_grace_count = 0
        self._timer_expiry_unknown_grace_count = 0
        self._playlist_session_shuffled = False  # set by explicit context.shuffle, consumed by navigation
        self._manual_nav_lock = asyncio.Lock()
        self._manual_nav_generation = 0
        self._manual_nav_target = None
        self._command_arbiter = None  # lazy per-device; created on first manual intent

        # T04-C2c: bounded internal context registry for external URL play
        # Maps private command key → deepcopy of caller context.
        # Max 32 entries; FIFO eviction on overflow.
        self._external_context_registry: dict[str, dict] = {}
        self._external_context_registry_order: list[str] = []
        self._external_context_next_id: int = 0

        # 播放列表（单一权威：music_library，device_player 只持有运行时快照）
        # _play_list_items 是 music_library 播放列表的运行时快照，通过 update_playlist() 从
        # music_library 同步。不应自行修改，所有变更必须通过 music_library API 后再调用
        # update_playlist() 刷新。_get_playlist_names() 是从 _play_list_items 派生的只读视图。
        self._play_list_items = []
        self._current_index = -1  # 当前歌曲在播放列表中的索引

        self._last_cmd = None
        self.update_playlist()


    def _ensure_playback_tasks(self) -> PlaybackTaskRegistry:
        registry = getattr(self, "_playback_tasks", None)
        if registry is None:
            registry = PlaybackTaskRegistry()
            self._playback_tasks = registry
        return registry

    def _task_generation(self, token=None, *, sid: int | None = None) -> TaskGeneration:
        if token is None:
            token = self._capture_lifecycle_token()
        return TaskGeneration.from_token(token, session_id=self._play_session_id if sid is None else sid)

    def _task_ref(self, kind: TaskKind):
        legacy = getattr(self, "_legacy_task_values", {})
        if kind in legacy:
            return legacy[kind]
        return self._ensure_playback_tasks().get_task(kind)

    def _set_task_compat(self, kind: TaskKind, task) -> None:
        legacy = getattr(self, "_legacy_task_values", None)
        if legacy is None:
            legacy = {}
            self._legacy_task_values = legacy
        if task is None:
            legacy.pop(kind, None)
            # Cancel the active registry task for this kind (respects self-cancel guard).
            registry = getattr(self, "_playback_tasks", None)
            if registry is not None:
                registry.cancel(kind)
        elif isinstance(task, asyncio.Task):
            legacy.pop(kind, None)
            self._ensure_playback_tasks().adopt(kind, TaskGeneration(), task)
        else:
            # Historical tests use opaque sentinels to model an old reference;
            # keep that non-task marker without making it a task owner.
            legacy[kind] = task

    @property
    def _duration_probe_task(self):
        return self._task_ref(TaskKind.DURATION_PROBE)

    @_duration_probe_task.setter
    def _duration_probe_task(self, task):
        self._set_task_compat(TaskKind.DURATION_PROBE, task)

    @property
    def _playback_confirm_task(self):
        return self._task_ref(TaskKind.PLAYBACK_CONFIRMATION)

    @_playback_confirm_task.setter
    def _playback_confirm_task(self, task):
        self._set_task_compat(TaskKind.PLAYBACK_CONFIRMATION, task)

    @property
    def _playback_status_probe_task(self):
        return self._task_ref(TaskKind.STATUS_PROBE)

    @_playback_status_probe_task.setter
    def _playback_status_probe_task(self, task):
        self._set_task_compat(TaskKind.STATUS_PROBE, task)

    @property
    def _next_timer(self):
        return self._task_ref(TaskKind.COMPLETION_NEXT_TIMER)

    @_next_timer.setter
    def _next_timer(self, task):
        self._set_task_compat(TaskKind.COMPLETION_NEXT_TIMER, task)

    @property
    def _failure_retry_task(self):
        return self._task_ref(TaskKind.FAILURE_RETRY)

    @_failure_retry_task.setter
    def _failure_retry_task(self, task):
        self._set_task_compat(TaskKind.FAILURE_RETRY, task)

    @property
    def _tts_timer(self):
        return self._task_ref(TaskKind.TTS_TIMER)

    @_tts_timer.setter
    def _tts_timer(self, task):
        self._set_task_compat(TaskKind.TTS_TIMER, task)

    @property
    def _add_song_timer(self):
        return self._task_ref(TaskKind.ADD_SONG_TIMER)

    @_add_song_timer.setter
    def _add_song_timer(self, task):
        self._set_task_compat(TaskKind.ADD_SONG_TIMER, task)

    @property
    def _stop_timer(self):
        return self._task_ref(TaskKind.STOP_TIMER)

    @_stop_timer.setter
    def _stop_timer(self, task):
        self._set_task_compat(TaskKind.STOP_TIMER, task)

    @property
    def did(self):
        """获取设备DID"""
        return self.device.did

    @property
    def hardware(self):
        """获取设备硬件型号"""
        return self.device.hardware

    def get_runtime_state(self) -> PlaybackRuntimeState:
        """Return the current immutable runtime state snapshot."""
        return self._runtime_state

    def _set_runtime_state(self, new_state: PlaybackRuntimeState) -> PlaybackRuntimeState:
        """Replace runtime state with a new instance (internal use only).

        Raises TypeError if new_state is not a PlaybackRuntimeState.
        """
        if not isinstance(new_state, PlaybackRuntimeState):
            raise TypeError(
                f"_set_runtime_state requires PlaybackRuntimeState, got {type(new_state).__name__}"
            )
        self._runtime_state = new_state
        return new_state

    # ── lifecycle counter wrappers (delegate to pure runtime_state) ───

    def _start_queue_session(self, updated_at: float) -> PlaybackRuntimeState:
        """Begin a new queue session. Increments queue_session_id only."""
        return self._set_runtime_state(
            new_queue_session(self.get_runtime_state(), updated_at=updated_at)
        )

    def _accept_command(self, updated_at: float) -> PlaybackRuntimeState:
        """Accept a control intent. Increments command_generation only."""
        return self._set_runtime_state(
            next_command(self.get_runtime_state(), updated_at=updated_at)
        )

    def _start_track_attempt(self, updated_at: float) -> LifecycleToken:
        """Begin a physical play attempt. Increments track_attempt_id,
        then returns a strict LifecycleToken captured AFTER the increment.
        """
        state = self._set_runtime_state(
            begin_track_attempt(self.get_runtime_state(), updated_at=updated_at)
        )
        return capture_token(state)

    def _begin_runtime_play_request(
        self, *, desired_track: TrackReference, updated_at: float
    ) -> PlaybackRuntimeState:
        """Begin a higher-level play request via the pure runtime state model.

        Read → call pure model → write back. Pure wrapper, no I/O, no task.
        Does not mutate lifecycle counters (queue_session_id / command_generation /
        track_attempt_id).
        """
        return self._set_runtime_state(
            begin_play_request(
                self.get_runtime_state(),
                desired_track=desired_track,
                updated_at=updated_at,
            )
        )

    def _begin_runtime_play_dispatch(
        self, *, updated_at: float
    ) -> PlaybackRuntimeState:
        """Wrapper for begin_play_dispatch: get → dispatch → set.

        Pure wrapper, no I/O, no task, no legacy ID mutation.
        """
        return self._set_runtime_state(
            begin_play_dispatch(self.get_runtime_state(), updated_at=updated_at)
        )

    def _begin_runtime_external_dispatch_for_token(
        self, token: LifecycleToken
    ) -> bool:
        """Sync helper for external-request dispatch phase entry.

        - strict token stale → False
        - phase RESOLVING/SWITCHING → calls _begin_runtime_play_dispatch → True
        - phase DISPATCHING → idempotent True (fallback, same command)
        - other phase → False

        No I/O. No task. No lifecycle ID change.
        """
        if self._is_lifecycle_token_stale(token):
            return False
        state = self.get_runtime_state()
        if state.phase in {PlaybackPhase.RESOLVING, PlaybackPhase.SWITCHING}:
            try:
                self._begin_runtime_play_dispatch(updated_at=time.time())
            except TransitionError:
                return False
            return True
        if state.phase == PlaybackPhase.DISPATCHING:
            return True
        return False

    def _begin_runtime_confirmation(
        self, *, updated_at: float
    ) -> PlaybackRuntimeState:
        """Wrapper for begin_confirm: get → confirm → set.

        Pure wrapper, no I/O, no task, no legacy ID mutation.
        Only valid from DISPATCHING.
        """
        return self._set_runtime_state(
            begin_confirm(self.get_runtime_state(), updated_at=updated_at)
        )

    def _confirm_runtime_playing(
        self,
        *,
        confirmed_track: TrackReference | None = None,
        expected_end_at: float | None = None,
        updated_at: float,
    ) -> PlaybackRuntimeState:
        """Wrapper for confirm_playing: get → confirm_playing → set.

        Pure wrapper, no I/O, no task, no legacy ID mutation.
        Only valid from CONFIRMING or PAUSED.
        """
        return self._set_runtime_state(
            confirm_playing(
                self.get_runtime_state(),
                confirmed_track=confirmed_track,
                expected_end_at=expected_end_at,
                updated_at=updated_at,
            )
        )

    def _confirm_runtime_playing_for_attempt(
        self, *, token: LifecycleToken, updated_at: float
    ) -> bool:
        """Private sync helper: establish PLAYING fact for the current attempt.

        - token stale → log + False
        - current phase CONFIRMING → calls _confirm_runtime_playing + True
        - current phase PLAYING → idempotent True (late proxy recovery)
        - other phase → log + False

        No I/O. No task. No lifecycle ID changes.
        """
        if self._is_lifecycle_token_stale(token):
            self.log.info(
                "runtime_playing_guard_token_stale did=%s", self.did
            )
            return False
        state = self.get_runtime_state()
        if state.phase == PlaybackPhase.CONFIRMING:
            self._confirm_runtime_playing(updated_at=updated_at, expected_end_at=None)
            return True
        if state.phase == PlaybackPhase.PLAYING:
            self.log.info(
                "runtime_playing_guard_already_playing did=%s", self.did
            )
            return True
        self.log.info(
            "runtime_playing_guard_unexpected_phase did=%s phase=%s",
            self.did,
            state.phase.value,
        )
        return False

    # ── pause / stop runtime wrappers ──────────────────────────────────

    def _pause_runtime(self, updated_at: float) -> PlaybackRuntimeState:
        """Wrapper for runtime_state.pause: PLAYING → PAUSED.

        Pure wrapper, no I/O, no task, no lifecycle ID mutation.
        """
        return self._set_runtime_state(
            _pause_transition(self.get_runtime_state(), updated_at=updated_at)
        )

    def _begin_runtime_stop(self, updated_at: float) -> PlaybackRuntimeState:
        """Wrapper for runtime_state.begin_stop: active phase → STOPPING.

        Pure wrapper, no I/O, no task, no lifecycle ID mutation.
        """
        return self._set_runtime_state(
            begin_stop(self.get_runtime_state(), updated_at=updated_at)
        )

    def _complete_runtime_stop(self, updated_at: float) -> PlaybackRuntimeState:
        """Wrapper for runtime_state.complete_stop: STOPPING → STOPPED.

        Pure wrapper, no I/O, no task, no lifecycle ID mutation.
        """
        return self._set_runtime_state(
            complete_stop(self.get_runtime_state(), updated_at=updated_at)
        )

    def _report_runtime_failure(
        self,
        *,
        reason: str,
        degraded: bool | None = None,
        updated_at: float,
    ) -> PlaybackRuntimeState:
        """Report failure to runtime state model.

        Pure wrapper: calls report_failure. No I/O, no task, no legacy mutation.
        Only this wrapper may call report_failure in device_player (AST-guarded).
        """
        return self._set_runtime_state(
            report_failure(
                self.get_runtime_state(),
                reason=reason,
                degraded=degraded,
                updated_at=updated_at,
            )
        )

    def _capture_lifecycle_token(self) -> LifecycleToken:
        """Capture current lifecycle counters as a token (read-only)."""
        return capture_token(self.get_runtime_state())

    def _is_lifecycle_token_stale(self, token: LifecycleToken) -> bool:
        """True if any lifecycle counter has changed since token was captured."""
        return check_stale_strict(self.get_runtime_state(), token)

    def _is_barrier_stale(self, sid: int, token: LifecycleToken) -> bool:
        """True if the barrier context is stale.

        Checks sid, queue_session_id, and track_attempt_id — but NOT
        command_generation.  This allows pending after-barrier regular
        intents (which bump command_generation) without killing the
        active barrier executor.
        """
        state = self.get_runtime_state()
        return (
            sid != self._play_session_id
            or check_stale_queue(state, token)
            or check_stale_attempt(state, token)
        )

    def get_cur_music(self):
        """获取当前播放的音乐名称"""
        return str(
            getattr(self.device, "current_display_name", "") or self.device.cur_music
        )

    def _get_playlist_names(self) -> list[str]:
        """从 _play_list_items 派生当前播放列表的名称列表（只读视图）"""
        return [str(item.get("display_name") or "") for item in self._play_list_items]

    @staticmethod
    def _normalize_playlist_runtime_item(item) -> dict[str, str]:
        if not isinstance(item, dict):
            title = str(item or "").strip()
            return {
                "item_id": "",
                "entity_id": "",
                "display_name": title,
                "legacy_name": title,
            }
        title = str(
            item.get("display_name")
            or item.get("legacy_name")
            or item.get("title")
            or item.get("name")
            or ""
        ).strip()
        legacy_name = str(item.get("legacy_name") or title).strip()
        return {
            "item_id": str(item.get("item_id") or item.get("id") or "").strip(),
            "entity_id": str(item.get("entity_id") or "").strip(),
            "display_name": title,
            "legacy_name": legacy_name,
        }

    def _build_playlist_runtime_items(self, playlist_name: str) -> list[dict[str, str]]:
        music_library = getattr(self.xiaomusic, "music_library", None)
        getter = getattr(music_library, "get_playlist_items", None)
        if callable(getter):
            try:
                items = getter(playlist_name)
            except Exception:
                items = None
            if isinstance(items, list) and items:
                return [self._normalize_playlist_runtime_item(item) for item in items]

        music_list = getattr(music_library, "music_list", {}) or {}
        legacy_items = music_list.get(playlist_name, [])
        if not isinstance(legacy_items, list):
            return []
        return [self._normalize_playlist_runtime_item(name) for name in legacy_items]

    def _find_playlist_index(
        self,
        *,
        item_id: str = "",
        entity_id: str = "",
        display_name: str = "",
    ) -> int:
        items = list(getattr(self, "_play_list_items", []) or [])
        target_item_id = str(item_id or "").strip()
        target_entity_id = str(entity_id or "").strip()
        target_display = str(display_name or "").strip()
        if target_item_id:
            for idx, item in enumerate(items):
                if str(item.get("item_id") or "") == target_item_id:
                    return idx
        if target_entity_id:
            for idx, item in enumerate(items):
                if str(item.get("entity_id") or "") == target_entity_id:
                    return idx
        if target_display:
            # 如果同时提供了 entity_id，优先匹配 display_name + entity_id 都相符的项
            # 应对随机打乱或列表中有多首同名歌曲的情况
            best_idx = -1
            for idx, item in enumerate(items):
                if target_display in {
                    str(item.get("display_name") or "").strip(),
                    str(item.get("legacy_name") or "").strip(),
                }:
                    if target_entity_id and str(item.get("entity_id") or "") == target_entity_id:
                        return idx  # 精确命中：display_name + entity_id 双重匹配
                    if best_idx < 0:
                        best_idx = idx  # 兜底：第一个仅 display_name 匹配的
            if best_idx >= 0:
                return best_idx
        return -1

    def _set_runtime_track_reference(
        self,
        *,
        playlist_name: str | None = None,
        display_name: str = "",
        entity_id: str = "",
        playlist_item_id: str = "",
        current_index: int | None = None,
    ) -> None:
        playlist_name = str(
            playlist_name if playlist_name is not None else self.device.cur_playlist or ""
        ).strip()
        resolved_index = current_index if current_index is not None else -1
        if resolved_index < 0:
            resolved_index = self._find_playlist_index(
                item_id=playlist_item_id,
                entity_id=entity_id,
                display_name=display_name,
            )
        item = None
        if 0 <= resolved_index < len(getattr(self, "_play_list_items", []) or []):
            item = self._play_list_items[resolved_index]
        final_display = str(
            display_name
            or (item.get("display_name") if item else "")
            or getattr(self.device, "current_display_name", "")
            or getattr(self.device, "cur_music", "")
            or ""
        ).strip()
        final_entity_id = str(
            entity_id
            or (item.get("entity_id") if item else "")
            or getattr(self.device, "current_entity_id", "")
            or ""
        ).strip()
        final_playlist_item_id = str(
            playlist_item_id
            or (item.get("item_id") if item else "")
            or getattr(self.device, "current_playlist_item_id", "")
            or ""
        ).strip()

        self.device.cur_music = final_display
        self.device.current_display_name = final_display
        self.device.current_entity_id = final_entity_id
        self.device.current_playlist_item_id = final_playlist_item_id
        self._current_index = resolved_index if resolved_index >= 0 else -1

        playlist2music = getattr(self.device, "playlist2music", None)
        if playlist_name and isinstance(playlist2music, dict):
            playlist2music[playlist_name] = final_display

    def get_current_track_reference(self) -> dict[str, str | int]:
        return {
            "display_name": self.get_cur_music(),
            "entity_id": str(getattr(self.device, "current_entity_id", "") or ""),
            "playlist_item_id": str(
                getattr(self.device, "current_playlist_item_id", "") or ""
            ),
            "current_index": int(getattr(self, "_current_index", -1) or -1),
            "playlist_name": str(getattr(self.device, "cur_playlist", "") or ""),
        }

    def get_offset_duration(self):
        """获取播放偏移量和总时长——纯查询，无副作用。

        不启动任何 task，不查询设备状态，不触发切歌。
        自动结束判定仅由 set_next_music_timeout 的 expiry gate 负责。
        """
        duration = self._duration
        if not self.is_playing:
            return 0, duration
        # Defense: if _start_time was never set (self-cancellation race),
        # return 0 offset instead of epoch-based value.
        if self._start_time <= 0.1:
            return 0, duration
        offset = time.time() - self._start_time - self._paused_time
        return offset, duration

    @staticmethod
    def _extract_duration_seconds(info: dict) -> float:
        """Best-effort parse duration from player_get_status payload."""
        if not isinstance(info, dict):
            return 0.0
        for key in (
            "duration",
            "duration_ms",
            "media_duration",
            "audio_duration",
            "total_duration",
        ):
            val = info.get(key)
            if val is None:
                continue
            try:
                d = float(val)
            except Exception:
                continue
            if d <= 0:
                continue
            # Some firmwares return milliseconds.
            if d > 10000:
                d = d / 1000.0
            return max(d, 0.0)
        return 0.0

    @staticmethod
    def _normalize_volume_value(value, default: int = 0) -> int:
        try:
            volume = int(float(value))
        except (TypeError, ValueError):
            volume = int(default)
        return max(0, min(100, volume))

    def _remember_volume(self, value, default: int | None = None) -> int:
        fallback = self._last_volume if default is None else int(default)
        volume = self._normalize_volume_value(value, fallback)
        self._last_volume = volume
        return volume

    def _remember_volume_from_status(self, info: dict | None) -> int:
        if not isinstance(info, dict):
            return self._last_volume
        return self._remember_volume(info.get("volume"), self._last_volume)

    async def _refresh_runtime_volume(self, *, context: str = "") -> int:
        try:
            volume = await self.get_volume()
        except Exception as exc:
            self.log.warning("refresh_runtime_volume failed ctx=%s err=%s", context, exc)
            return self._last_volume
        self.log.info("refresh_runtime_volume ctx=%s volume=%d", context or "-", volume)
        return self._remember_volume(volume)

    def _log_measure(self, step_name: str, *, reset: bool = False):
        now = time.time()
        prev = None if reset else getattr(self, "_measure_prev_t", None)
        dt = 0.0 if prev is None else now - prev
        self._measure_prev_t = now
        if reset:
            self._measure_reset_t = now
        self.log.info("[measure] %s t=%.3f dt=%.3f", step_name, now, dt)
        return now

    def _start_duration_probe(
        self,
        name: str,
        sid: int,
        token: LifecycleToken | None = None,
    ):
        if token is None:
            token = self._capture_lifecycle_token()
        def _token_is_current(stage: str) -> bool:
            if not self._is_lifecycle_token_stale(token):
                return True
            self.log.info(
                "duration_probe_lifecycle_stale stage=%s name=%s did=%s",
                stage,
                name,
                self.did,
            )
            return False

        async def _probe():
            try:
                for _ in range(5):
                    await asyncio.sleep(2)
                    if not _token_is_current("wake"):
                        return
                    if sid != self._play_session_id:
                        return
                    try:
                        info = await self.get_player_status()
                    except Exception as e:
                        if not _token_is_current("status_error"):
                            return
                        self.log.debug("duration_probe_retry name=%s err=%s", name, e)
                        continue
                    if not _token_is_current("status_return"):
                        return
                    d = self._extract_duration_seconds(info)
                    if d > 0.1:
                        self._duration = d
                        # If original duration probe failed at play start, timer was not set.
                        # Rebuild a next-track timer once duration becomes known.
                        cur_offset, _ = self.get_offset_duration()
                        remaining = (
                            d - max(cur_offset, 0.0) + float(self.config.delay_sec)
                        )
                        if remaining > 0.1:
                            if not _token_is_current("timer_restore"):
                                return
                            await self.set_next_music_timeout(remaining, token=token)
                        self.log.info(
                            "duration_probe_success name=%s duration=%.3fs", name, d
                        )
                        return
                if _token_is_current("failed"):
                    self.log.info("duration_probe_failed name=%s", name)
            finally:
                pass

        self._ensure_playback_tasks().start(
            TaskKind.DURATION_PROBE,
            self._task_generation(token, sid=sid),
            _probe(),
            metadata={"name": name, "sid": sid},
        )

    # 自动搜歌并加入当前歌单
    async def auto_add_song(self, cur_list_name, sleep_sec=20):
        if self.xiaomusic.js_plugin_manager is None:
            return
        # 是否启用自动添加
        auto_add_song = self.xiaomusic.js_plugin_manager.get_auto_add_song()
        is_online = self.xiaomusic.music_library.is_online_music(cur_list_name)
        # 歌单循环方式：播放全部
        play_all = self.device.play_type == PLAY_TYPE_ALL
        # 当前播放的歌曲是歌单中的最后一曲
        is_last_song = False
        cur_playlist = self._get_playlist_names()
        cur_music = self.get_cur_music()
        play_list_len = len(cur_playlist)
        if play_list_len != 0:
            index = self._find_playlist_index(display_name=cur_music)
            is_last_song = index == play_list_len - 1
        # 四个条件都满足，才自动添加下一首
        if auto_add_song and is_online and play_all and is_last_song:
            await self._add_singer_song(cur_list_name, cur_music, sleep_sec)

    # 启用延时器，搜索当前歌曲歌手的其他不在歌单内的歌曲
    async def _add_singer_song(self, list_name, cur_music, sleep_sec):
        # 取消之前的定时器（如果存在）
        # self.cancel_add_song_timer()
        # 以 '-' 分割，获取歌手名称
        singer_name = cur_music.split("-")[1]
        # 创建新的定时器，20秒后执行
        self._ensure_playback_tasks().start(
            TaskKind.ADD_SONG_TIMER,
            self._task_generation(sid=self._play_session_id),
            self._delayed_add_singer_song(list_name, singer_name, sleep_sec),
            metadata={"list_name": list_name, "sid": self._play_session_id},
        )

    async def _delayed_add_singer_song(self, list_name, singer_name, sleep_sec):
        """延迟执行添加歌手歌曲的操作"""
        try:
            await asyncio.sleep(sleep_sec)
            await self.xiaomusic.add_singer_song(list_name, singer_name)
        except asyncio.CancelledError:
            return
        finally:
            pass

    def cancel_add_song_timer(self):
        """取消添加歌曲的定时器"""
        self.log.info("添加歌手歌曲的定时器已被取消")
        if self._ensure_playback_tasks().cancel(TaskKind.ADD_SONG_TIMER):
            return True
        return False

    async def play_music(self, name):
        """播放音乐（外部接口）"""
        self._invalidate_manual_navigation(reason="play_music")
        return await self._playmusic(name)

    def update_playlist(self):
        """初始化/更新播放列表"""
        # 没有重置 list 且非初始化
        if self.device.cur_playlist not in self.xiaomusic.music_library.music_list:
            self.device.cur_playlist = "全部"

        list_name = self.device.cur_playlist
        playlist_items = self._build_playlist_runtime_items(list_name)

        if self.device.play_type == PLAY_TYPE_RND:
            random.shuffle(playlist_items)
            self.log.info(
                f"随机打乱 {list_name} {list2str([item.get('display_name', '') for item in playlist_items], self.config.verbose)}"
            )
        else:
            playlist_items.sort(
                key=lambda item: custom_sort_key(str(item.get("display_name") or ""))
            )
            self.log.info(
                f"没打乱 {list_name} {list2str([item.get('display_name', '') for item in playlist_items], self.config.verbose)}"
            )

        self._play_list_items = playlist_items
        self._set_runtime_track_reference(
            playlist_name=list_name,
            display_name=str(
                getattr(self.device, "current_display_name", "")
                or getattr(self.device, "cur_music", "")
                or ""
            ),
            entity_id=str(getattr(self.device, "current_entity_id", "") or ""),
            playlist_item_id=str(
                getattr(self.device, "current_playlist_item_id", "") or ""
            ),
        )

    async def play(self, name="", search_key=""):
        """播放歌曲（外部接口）— sync accept, arbiter async.

        Immediately accepts the command (increments command_generation),
        invalidates manual navigation, sets last_cmd, submits PLAY intent
        to the arbiter, and returns True.  Does NOT await download,
        resolve, or physical play.  Does NOT bump sid or change phase
        during acceptance.

        When a STOP/PAUSE barrier is pending or active, the PLAY intent
        lands in the after_barrier slot and executes after the barrier
        completes.
        """
        self._invalidate_manual_navigation(reason="explicit_play")
        self._last_cmd = "play"
        self._accept_command(updated_at=time.time())
        from xiaomusic.playback.command_arbiter import IntentKind

        arbiter = self._get_or_create_arbiter()
        arbiter.submit(IntentKind.PLAY, payload={
            "mode": "play",
            "name": name,
            "search": search_key,
        })
        return True

    async def _check_and_download_music(self, name, search_key, allow_download):
        """检查本地歌曲是否存在，如果不存在则根据参数决定是否下载

        Args:
            name: 歌曲名称
            search_key: 搜索关键词
            allow_download: 是否允许下载

        Returns:
            bool: True表示歌曲存在或下载成功，False表示歌曲不存在且不允许下载
        """
        if self.xiaomusic.music_library.is_music_exist(name):
            return True

        self.log.info(f"本地不存在歌曲{name}")

        # 根据 allow_download 参数决定行为
        if not allow_download:
            # playlocal 的行为：不下载，直接提示
            await self.do_tts(f"本地不存在歌曲{name}")
            return False

        # _play 的行为：检查配置决定是否下载
        if self.config.disable_download:
            await self.do_tts(f"本地不存在歌曲{name}")
            return False

        # 下载歌曲
        await self.download(search_key, name)
        # 把文件插入到播放列表里
        await self.add_download_music(name)
        return True

    async def _play_internal(
        self,
        name="",
        search_key="",
        allow_download=True,
        preserve_playlist=False,
        confirm_start_in_background=False,
        fast_stop=False,
        navigation_generation: int | None = None,
        command_already_accepted: bool = False,
    ):
        """播放歌曲的内部统一实现

        Args:
            name: 歌曲名称
            search_key: 搜索关键词
            allow_download: 是否允许下载（True: _play行为，False: playlocal行为）
            command_already_accepted: True when command was already accepted
                upstream (arbiter PLAY executor).  False for legacy callers
                (auto/manual navigation, old paths).
        """
        # 初始检查逻辑
        if not search_key and not name:
            if self.check_play_next():
                return await self._play_next(
                    command_already_accepted=command_already_accepted
                )
            else:
                name = self.get_cur_music()

        if not command_already_accepted:
            self._accept_command(updated_at=time.time())

        self.log.info(
            f"play_internal. search_key:{search_key} name:{name} allow_download:{allow_download}"
        )

        if not name:
            self.log.info(f"没有歌曲播放了 name:{name} search_key:{search_key}")
            return

        # 模糊搜索
        names = self.xiaomusic.music_library.find_real_music_name(name, n=1)
        self.log.info(f"play_internal. names:{names} {len(names)}")

        if not names:
            # 检查本地是否存在歌曲，不存在则根据参数决定是否下载
            if not await self._check_and_download_music(
                name, search_key, allow_download
            ):
                return False

            # 播放歌曲
            return await self._playmusic(
                name,
                confirm_start_in_background=confirm_start_in_background,
                fast_stop=fast_stop,
                navigation_generation=navigation_generation,
            )

        name = names[0]
        if (not preserve_playlist) and (self._find_playlist_index(display_name=name) < 0):
            # 根据当前歌曲匹配歌曲列表
            self.device.cur_playlist = self.find_cur_playlist(name)
            self.update_playlist()

        self.log.debug(
            f"当前播放列表为：{list2str(self._get_playlist_names(), self.config.verbose)}"
        )
        # 本地存在歌曲，直接播放
        return await self._playmusic(
            name,
            confirm_start_in_background=confirm_start_in_background,
            fast_stop=fast_stop,
            navigation_generation=navigation_generation,
        )

    async def _play(
        self,
        name="",
        search_key="",
        preserve_playlist=False,
        confirm_start_in_background=False,
        fast_stop=False,
        navigation_generation: int | None = None,
        command_already_accepted: bool = False,
    ):
        """播放歌曲（内部实现）- 支持下载"""
        return await self._play_internal(
            name=name,
            search_key=search_key,
            allow_download=True,
            preserve_playlist=preserve_playlist,
            confirm_start_in_background=confirm_start_in_background,
            fast_stop=fast_stop,
            navigation_generation=navigation_generation,
            command_already_accepted=command_already_accepted,
        )

    def _ensure_manual_navigation_state(self) -> None:
        if not hasattr(self, "_manual_nav_lock"):
            self._manual_nav_lock = asyncio.Lock()
            self._manual_nav_generation = 0
            self._manual_nav_target = None

    def _manual_navigation_is_current(self, generation: int | None) -> bool:
        if generation is None:
            return True
        return generation == getattr(self, "_manual_nav_generation", 0)

    def _invalidate_manual_navigation(self, *, reason: str) -> None:
        self._ensure_manual_navigation_state()
        self._manual_nav_generation += 1
        self._manual_nav_target = None
        self.log.info(
            "manual_nav_invalidated generation=%d reason=%s",
            self._manual_nav_generation,
            reason,
        )

    # ── command arbiter integration (T04-B) ─────────────────────────────

    def _get_or_create_arbiter(self):
        """Lazy-create the per-device command arbiter on first manual intent.

        Must be called from the owning event loop.
        """
        if self._command_arbiter is None:
            from xiaomusic.playback.command_arbiter import DeviceCommandArbiter

            self._command_arbiter = DeviceCommandArbiter(self._arbiter_executor)
        return self._command_arbiter

    def _submit_auto_retry(
        self,
        kind,
        *,
        source_token,
        sid: int,
        payload: dict | None = None,
        reason: str = "",
    ) -> bool:
        """Submit an AUTO_NEXT or RETRY intent via the command arbiter.

        Guards:
        - sid must match ``_play_session_id``.
        - source_token must be strict-current (``_is_lifecycle_token_stale``).

        After a successful submit, ``_accept_command`` bumps c by 1, making
        the source_token naturally strict-stale for any subsequent calls —
        no explicit dedup set needed.  On failure (guard, ArbiterClosedError)
        c is unchanged, allowing retry.

        The expected accepted token (same q, c+1, same a) is constructed from
        the *current* runtime state and passed inside the payload for the
        executor to guard against.  ``_accept_command`` is called synchronously
        **after** a successful submit (no yield), so the actual state matches
        the expected token by the time the executor runs.

        Zero lifecycle writes on submit failure: c/q/sid/attempt/phase
        unchanged.
        """
        # ── Pre-guards ─────────────────────────────────────────────
        if sid != self._play_session_id:
            self.log.info(
                "auto_retry_submit_sid_mismatch kind=%s source_sid=%s cur_sid=%s",
                kind,
                sid,
                self._play_session_id,
            )
            return False

        if self._is_lifecycle_token_stale(source_token):
            self.log.info(
                "auto_retry_submit_token_stale kind=%s",
                kind,
            )
            return False

        # ── Construct expected accepted token ───────────────────────
        current = self.get_runtime_state()
        expected_accepted_token = LifecycleToken(
            queue_session_id=current.queue_session_id,
            command_generation=current.command_generation + 1,
            track_attempt_id=current.track_attempt_id,
        )

        # ── Submit to arbiter ───────────────────────────────────────
        from xiaomusic.playback.command_arbiter import ArbiterClosedError

        arbiter = self._get_or_create_arbiter()
        submit_payload: dict = {
            "sid": sid,
            "accepted_token": expected_accepted_token,
            "reason": reason,
        }
        if payload:
            submit_payload.update(payload)

        try:
            arbiter.submit(kind, submit_payload)
        except ArbiterClosedError:
            self.log.info(
                "auto_retry_submit_arbiter_closed kind=%s",
                kind,
            )
            return False

        # ── Only on success: accept command (bump c) ────────────────
        self._accept_command(updated_at=time.time())

        self.log.info(
            "auto_retry_submitted kind=%s source_sid=%s reason=%s",
            kind,
            sid,
            reason,
        )
        return True

    async def _arbiter_executor(self, intent) -> None:
        """Serial executor for DeviceCommandArbiter.

        Routes to the appropriate private physical method:
        - STOP  → _execute_stop_intent
        - PAUSE → _execute_pause_intent
        - PLAY  → _execute_play_intent
        - NEXT / PREVIOUS → manual navigation dispatch

        Exceptions propagate to arbiter.last_error; no fake completion.
        """
        from xiaomusic.playback.command_arbiter import IntentKind

        if intent.kind == IntentKind.STOP:
            await self._execute_stop_intent(intent.payload or {})
            return
        if intent.kind == IntentKind.PAUSE:
            await self._execute_pause_intent(intent.payload or {})
            return
        if intent.kind == IntentKind.PLAY:
            await self._execute_play_intent(intent.payload or {})
            return
        if intent.kind == IntentKind.AUTO_NEXT:
            await self._execute_auto_next_intent(intent)
            return
        if intent.kind == IntentKind.RETRY:
            await self._execute_retry_intent(intent)
            return
        if intent.kind == IntentKind.RESUME:
            await self._execute_resume_intent(intent)
            return
        if intent.kind not in (IntentKind.NEXT, IntentKind.PREVIOUS):
            return

        payload = intent.payload or {}
        generation = payload.get("generation")
        name = payload.get("target")

        await self._wait_manual_navigation_settle()

        arbiter = self._command_arbiter
        if arbiter is not None:
            pending_seq = arbiter.pending_sequence
            if pending_seq is not None and pending_seq > intent.sequence:
                self.log.info(
                    "arbiter_executor_skip_newer_pending seq=%d pending=%d",
                    intent.sequence,
                    pending_seq,
                )
                return

        if not self._manual_navigation_is_current(generation):
            self.log.info(
                "arbiter_executor_skip_stale_generation generation=%s",
                generation,
            )
            return

        self.log.info(
            "arbiter_executor_dispatch sequence=%d generation=%s target=%s",
            intent.sequence,
            generation,
            name,
        )
        await self._play(
            name,
            preserve_playlist=True,
            confirm_start_in_background=True,
            fast_stop=True,
            navigation_generation=generation,
            command_already_accepted=True,
        )

    async def _execute_stop_intent(self, payload: dict) -> None:
        """Physical stop work. Only called by arbiter executor.

        Uses barrier guard (_is_barrier_stale) which checks sid,
        queue_session_id, and track_attempt_id — but NOT command_generation
        (so after-barrier regular intents don't kill the barrier).
        Exceptions propagate to arbiter.last_error — no fake completion.
        """
        arg1 = payload.get("arg1", "")
        sid = payload.get("sid")
        token = payload.get("token")

        # Initial guard: barrier stale → no-op
        if self._is_barrier_stale(sid, token):
            return

        if arg1 != "notts":
            await self.do_tts(self.config.stop_tts_msg)
            if self._is_barrier_stale(sid, token):
                return
            await asyncio.sleep(3)  # 等它说完
            if self._is_barrier_stale(sid, token):
                return

        # 取消组内所有的下一首歌曲的定时器
        await self.cancel_group_next_timer()
        if self._is_barrier_stale(sid, token):
            return

        # 强制停止组内所有设备
        await self.group_force_stop_xiaoai()
        if self._is_barrier_stale(sid, token):
            return

        # Only current: complete stop → STOPPED, then event
        self._complete_runtime_stop(updated_at=time.time())
        self.log.info("stop now")
        if self.event_bus:
            self.event_bus.publish(PLAYER_STATE_CHANGED, device_id=self.did)

    async def _execute_pause_intent(self, payload: dict) -> None:
        """Physical pause work. Only called by arbiter executor.

        Uses barrier guard (_is_barrier_stale) which checks sid,
        queue_session_id, and track_attempt_id — but NOT command_generation
        (so after-barrier regular intents don't kill the barrier).
        Exceptions propagate to arbiter.last_error — no fake completion.
        """
        sid = payload.get("sid")
        token = payload.get("token")

        # Initial guard: barrier stale → no-op
        if self._is_barrier_stale(sid, token):
            return

        await self.cancel_group_next_timer()
        if self._is_barrier_stale(sid, token):
            return

        await self.group_force_stop_xiaoai()
        if self._is_barrier_stale(sid, token):
            return

        self.log.info("pause now")
        if self.event_bus:
            self.event_bus.publish(PLAYER_STATE_CHANGED, device_id=self.did)

    async def _execute_play_intent(self, payload: dict) -> None:
        """Physical play work. Only called by arbiter executor.

        Routes by mode:
        - "external"  → _execute_external_play_intent
        - "playlocal" → _play_internal(name, allow_download=False,
                                        command_already_accepted=True)
        - "play"      → _play(name, search, command_already_accepted=True)

        command_generation was already bumped by the public accept API;
        command_already_accepted=True prevents a second bump.
        Exceptions propagate to arbiter.last_error — no fake completion.
        """
        mode = payload.get("mode", "play")
        if mode == "external":
            await self._execute_external_play_intent(payload)
            return

        name = payload.get("name", "")
        search = payload.get("search", "")

        if mode == "playlocal":
            await self._play_internal(
                name=name,
                search_key=search,
                allow_download=False,
                command_already_accepted=True,
            )
        else:
            await self._play(
                name=name,
                search_key=search,
                command_already_accepted=True,
            )

    async def _execute_external_play_intent(self, payload: dict) -> None:
        """Physical external URL play. Only called by arbiter executor.

        Sequence:
        1. Resolve context: if ctx_key present, lookup internal registry;
           else use inline context (old direct path compat).
        2. Call on_external_url_play(internal_ctx, command_already_accepted=True,
           manual_already_invalidated=True) — q/c/sid/timer/legacy bootstrap.
           On first call, internal_ctx gets pinned marker; subsequent same-key
           calls see marker and reuse q/c.
        3. begin external dispatch (phase → DISPATCHING)
        4. attempt+1 → track_attempt_id bump
        5. await direct group_player_play(url)
        6. strict stale check; on raw failure, optionally attempt the
           internal fallback URL with one more attempt+1 in this worker
        7. on_external_url_play_started(final attempt token)

        ALL physical work happens here — submit_external_url_play only did
        c+1, manual invalidation (first time), and arbiter queueing.

        Exceptions propagate to arbiter.last_error — no fake completion.
        """
        url = str(payload.get("url", ""))
        resolved: dict = payload.get("resolved") if isinstance(payload.get("resolved"), dict) else {}

        # ── Resolve context: key-based lookup preferred ──────────────
        ctx_key: str | None = payload.get("ctx_key")
        if isinstance(ctx_key, str) and ctx_key in self._external_context_registry:
            internal_ctx = self._external_context_registry[ctx_key]
        else:
            # Old direct path compat: context passed inline in payload
            internal_ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}

        # Step 1: initialise — q+1, sid bump, cancel timer, legacy bootstrap
        request_token = await self.on_external_url_play(
            context=internal_ctx,
            command_already_accepted=True,
            manual_already_invalidated=True,
        )
        if request_token is None:
            return

        # Step 2: external dispatch phase
        if not self._begin_runtime_external_dispatch_for_token(request_token):
            return

        fallback_plan = internal_ctx.get("_device_external_fallback")
        fallback_url = ""
        if isinstance(fallback_plan, dict):
            fallback_url = str(fallback_plan.get("final_url") or "").strip()

        # Each physical dispatch owns exactly one attempt increment.  Keep
        # direct and fallback in this worker so q/c/sid remain unchanged.
        attempt_token = self._start_track_attempt(updated_at=time.time())
        try:
            ret = await self.group_player_play(url)
        except Exception:
            if not fallback_url or self._is_lifecycle_token_stale(attempt_token):
                raise
            fallback_token = self._start_track_attempt(updated_at=time.time())
            fallback_ret = await self.group_player_play(fallback_url)
            if self._is_lifecycle_token_stale(fallback_token):
                self.log.info(
                    "lifecycle_stale external_play_executor did=%s", self.did
                )
                return
            if self._external_play_dispatch_succeeded(fallback_ret):
                await self.on_external_url_play_started(
                    context=internal_ctx,
                    resolved=resolved,
                    token=fallback_token,
                )
                return
            raise RuntimeError(
                "direct and fallback external playback failed"
            ) from None

        # A STOP/PLAY accepted during direct await invalidates the attempt;
        # do not dispatch fallback and do not publish a stale callback.
        if self._is_lifecycle_token_stale(attempt_token):
            self.log.info(
                "lifecycle_stale external_play_executor did=%s", self.did
            )
            return

        if self._external_play_dispatch_succeeded(ret):
            await self.on_external_url_play_started(
                context=internal_ctx,
                resolved=resolved,
                token=attempt_token,
            )
            return

        if not fallback_url:
            raise RuntimeError(
                "external play dispatch failed, no fallback configured"
            ) from None

        fallback_token = self._start_track_attempt(updated_at=time.time())
        fallback_ret = await self.group_player_play(fallback_url)
        if self._is_lifecycle_token_stale(fallback_token):
            self.log.info(
                "lifecycle_stale external_play_executor did=%s", self.did
            )
            return
        if self._external_play_dispatch_succeeded(fallback_ret):
            await self.on_external_url_play_started(
                context=internal_ctx,
                resolved=resolved,
                token=fallback_token,
            )
            return
        raise RuntimeError(
            "direct and fallback external playback failed"
        ) from None

    @staticmethod
    def _external_play_dispatch_succeeded(result) -> bool:
        """Return whether a group dispatch contains at least one success.

        Dict: ``accepted`` bool wins; else ``code`` == 0 or ``code`` == "0"
        → success.  Any other explicit code → fail (no ValueError).
        Unknown non-empty dict → success (backward compat).
        List: recursive — each element evaluated via this same method.
        Others: None/False → fail; anything else → success.
        """
        if isinstance(result, dict):
            accepted = result.get("accepted")
            if isinstance(accepted, bool):
                return accepted
            if "code" in result:
                code = result["code"]
                if isinstance(code, (int, float)):
                    return int(code) == 0
                if isinstance(code, str) and code.strip() == "0":
                    return True
                return False
            return True
        if isinstance(result, list):
            return any(
                XiaoMusicDevice._external_play_dispatch_succeeded(element)
                for element in result
            )
        return result is not None and result is not False

    async def _wait_manual_navigation_settle(self) -> None:
        """0.2 s settle window for fast-click burst merging.

        Replacing the old ``_manual_navigation_worker`` sleep-before-dispatch
        pattern.  Used exclusively by the arbiter executor.

        Overridable in tests (patch to instant Event or zero-sleep) so that
        burst-merge behaviour can be verified deterministically.
        """
        await asyncio.sleep(0.2)

    async def _execute_auto_next_intent(self, intent) -> None:
        """Execute AUTO_NEXT: guard accepted_token strict, then _play_next.

        Only physical work happens here — single concurrency per arbiter.
        """
        payload = intent.payload or {}
        sid = payload.get("sid")
        accepted_token: LifecycleToken | None = payload.get("accepted_token")

        # Strict guard: sid must match.
        if sid != self._play_session_id:
            self.log.info(
                "auto_next_executor_sid_mismatch seq=%d intent_sid=%s cur_sid=%s",
                intent.sequence,
                sid,
                self._play_session_id,
            )
            return

        # Strict guard: accepted token must exactly match current state.
        if accepted_token is None:
            self.log.info(
                "auto_next_executor_no_accepted_token seq=%d",
                intent.sequence,
            )
            return

        current = self.get_runtime_state()
        if (
            current.queue_session_id != accepted_token.queue_session_id
            or current.command_generation != accepted_token.command_generation
            or current.track_attempt_id != accepted_token.track_attempt_id
        ):
            self.log.info(
                "auto_next_executor_token_mismatch seq=%d "
                "expected(q=%d,c=%d,a=%d) actual(q=%d,c=%d,a=%d)",
                intent.sequence,
                accepted_token.queue_session_id,
                accepted_token.command_generation,
                accepted_token.track_attempt_id,
                current.queue_session_id,
                current.command_generation,
                current.track_attempt_id,
            )
            return

        self.log.info(
            "auto_next_executor_dispatch seq=%d reason=%s",
            intent.sequence,
            payload.get("reason", ""),
        )
        await self._play_next(command_already_accepted=True)

    async def _execute_retry_intent(self, intent) -> None:
        """Execute RETRY: guard accepted_token strict, route same-song vs next.

        Only physical work happens here — single concurrency per arbiter.
        """
        payload = intent.payload or {}
        sid = payload.get("sid")
        accepted_token: LifecycleToken | None = payload.get("accepted_token")
        name: str = payload.get("name", "")
        retry_same_song: bool = payload.get("retry_same_song", False)

        # Strict guard: sid must match.
        if sid != self._play_session_id:
            self.log.info(
                "retry_executor_sid_mismatch seq=%d intent_sid=%s cur_sid=%s",
                intent.sequence,
                sid,
                self._play_session_id,
            )
            return

        # Strict guard: accepted token must exactly match current state.
        if accepted_token is None:
            self.log.info(
                "retry_executor_no_accepted_token seq=%d",
                intent.sequence,
            )
            return

        current = self.get_runtime_state()
        if (
            current.queue_session_id != accepted_token.queue_session_id
            or current.command_generation != accepted_token.command_generation
            or current.track_attempt_id != accepted_token.track_attempt_id
        ):
            self.log.info(
                "retry_executor_token_mismatch seq=%d "
                "expected(q=%d,c=%d,a=%d) actual(q=%d,c=%d,a=%d)",
                intent.sequence,
                accepted_token.queue_session_id,
                accepted_token.command_generation,
                accepted_token.track_attempt_id,
                current.queue_session_id,
                current.command_generation,
                current.track_attempt_id,
            )
            return

        if retry_same_song and name:
            self.log.info(
                "retry_executor_same_song seq=%d name=%s",
                intent.sequence,
                name,
            )
            await self._play(
                name,
                preserve_playlist=True,
                confirm_start_in_background=False,
                fast_stop=False,
                command_already_accepted=True,
            )
        else:
            self.log.info(
                "retry_executor_next seq=%d reason=%s",
                intent.sequence,
                payload.get("reason", ""),
            )
            await self._play_next(command_already_accepted=True)

    async def _execute_resume_intent(self, intent) -> None:
        """Execute RESUME: guard accepted_token strict current, then _play.

        Only physical work happens here — single concurrency per arbiter.
        sid + accepted token must be strict current before calling
        ``_play(command_already_accepted=True)``.

        RESUME is a normal latest intent: it can replace pending regular
        intents and can be replaced by PLAY/NEXT/external.  STOP/PAUSE
        barriers push it to after-barrier.  A stale executor (sid or c
        changed by STOP) no-ops without physical work.
        """
        payload = intent.payload or {}
        sid = payload.get("sid")
        accepted_token: LifecycleToken | None = payload.get("accepted_token")

        # Strict guard: sid must match.
        if sid != self._play_session_id:
            self.log.info(
                "resume_executor_sid_mismatch seq=%d intent_sid=%s cur_sid=%s",
                intent.sequence,
                sid,
                self._play_session_id,
            )
            return

        # Strict guard: accepted token must exactly match current state.
        if accepted_token is None:
            self.log.info(
                "resume_executor_no_accepted_token seq=%d",
                intent.sequence,
            )
            return

        current = self.get_runtime_state()
        if (
            current.queue_session_id != accepted_token.queue_session_id
            or current.command_generation != accepted_token.command_generation
            or current.track_attempt_id != accepted_token.track_attempt_id
        ):
            self.log.info(
                "resume_executor_token_mismatch seq=%d "
                "expected(q=%d,c=%d,a=%d) actual(q=%d,c=%d,a=%d)",
                intent.sequence,
                accepted_token.queue_session_id,
                accepted_token.command_generation,
                accepted_token.track_attempt_id,
                current.queue_session_id,
                current.command_generation,
                current.track_attempt_id,
            )
            return

        self.log.info(
            "resume_executor_dispatch seq=%d",
            intent.sequence,
        )
        await self._play(command_already_accepted=True)

    async def close_command_arbiter(self) -> None:
        """Close all Device playback tasks, then the independent arbiter worker."""
        await self._ensure_playback_tasks().close()
        arb = self._command_arbiter
        if arb is not None:
            await arb.close()
            self._command_arbiter = None

    def _cancel_failure_retry_task(self, *, wait: bool = False):
        task = self._ensure_playback_tasks().get_task(TaskKind.FAILURE_RETRY)
        if task is None or task.done() or task is asyncio.current_task():
            return None
        self._ensure_playback_tasks().cancel(TaskKind.FAILURE_RETRY)
        self._failure_retry_last_status = "cancelled"
        if wait:
            return task
        return None

    async def _await_cancelled_failure_retry(self):
        task = self._cancel_failure_retry_task(wait=True)
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self._failure_retry_last_error = str(exc)[:200]
                self.log.error("failure_retry_cancelled_task_error=%s", exc)

    async def _wait_failure_retry_backoff(self, delay: float) -> None:
        await asyncio.sleep(delay)

    def _token_to_safe_dict(self, token) -> dict:
        if token is None:
            return {}
        try:
            return {
                "queue_session_id": getattr(token, "queue_session_id", None),
                "command_generation": getattr(token, "command_generation", None),
                "track_attempt_id": getattr(token, "track_attempt_id", None),
            }
        except Exception:
            return {}

    def get_failure_retry_status(self) -> dict:
        task = getattr(self, "_failure_retry_task", None)
        last = getattr(self, "_failure_retry_last_status", "idle")
        if task is None:
            status = last if last in {"done", "cancelled"} else "idle"
        elif task.cancelled():
            status = "cancelled"
        elif task.done():
            status = "done"
        elif last == "running":
            status = "running"
        else:
            status = "pending"
        active = task is not None and not task.done()
        meta = getattr(self, "_failure_retry_meta", {}) or {}
        raw_token = meta.get("token")
        return {
            "status": status,
            "active": active,
            "action": meta.get("action", ""),
            "count": meta.get("count", 0),
            "reason": meta.get("reason", ""),
            "sid": meta.get("sid"),
            "token": self._token_to_safe_dict(raw_token),
            "last_error": getattr(self, "_failure_retry_last_error", ""),
        }

    def failure_retry_snapshot(self) -> dict:
        return self.get_failure_retry_status()

    # ── manual navigation queue ──────────────────────────────────────

    async def _queue_manual_navigation(self, *, direction: str) -> bool:
        self._ensure_manual_navigation_state()
        async with self._manual_nav_lock:
            if direction == "next":
                name = self.get_next_music(skip_one_repeat=True)
            else:
                name = self.get_prev_music()
            if not name:
                return False
            # Accept the control intent — bumps command_generation.
            self._accept_command(updated_at=time.time())
            self._stage_playlist_navigation_transition(
                name,
                reason="play_next" if direction == "next" else "play_prev",
            )
            self._manual_nav_generation += 1
            generation = self._manual_nav_generation
            self._manual_nav_target = (generation, name)
            self.log.info(
                "manual_nav_intent generation=%d direction=%s index=%d target=%s",
                generation,
                direction,
                self._current_index,
                name,
            )
            from xiaomusic.playback.command_arbiter import IntentKind

            arbiter = self._get_or_create_arbiter()
            arbiter.submit(
                IntentKind.NEXT if direction == "next" else IntentKind.PREVIOUS,
                payload={
                    "generation": generation,
                    "target": name,
                    "direction": direction,
                },
            )
        return True

    async def play_next(self):
        """Queue a manual next intent and return after it is accepted."""
        return await self._queue_manual_navigation(direction="next")

    def _stage_playlist_navigation_transition(self, name: str, *, reason: str) -> None:
        target = str(name or "").strip()
        if not target:
            return
        self.is_playing = False
        self._start_time = 0
        self._paused_time = 0
        self._duration = 0
        self._last_cmd = reason
        self._set_runtime_track_reference(
            playlist_name=str(getattr(self.device, "cur_playlist", "") or ""),
            display_name=target,
        )

    async def _play_next(self, manual: bool = False, command_already_accepted: bool = False):
        """播放下一首（内部实现）

        manual=True（用户主动点击）：单曲循环模式下前进到下一首
        manual=False（自动切歌）：单曲循环模式下重复当前歌曲

        始终 preserve_playlist=True：当前 _play_list_items 是会话权威快照，
        自动/手动切歌都不应触发 find_cur_playlist/update_playlist 重新打乱。

        command_already_accepted: True when command was already accepted
            upstream.  False (default) preserves legacy behavior.
        """
        if not command_already_accepted:
            self._accept_command(updated_at=time.time())
        self.log.info("开始播放下一首")
        name = self.get_next_music(skip_one_repeat=manual)
        self.log.info(f"get_next_music {name}")
        self.log.info(f"_play_next. name:{name}, cur_music:{self.get_cur_music()}")
        if name == "":
            self.log.info("本地没有歌曲")
            return False
        self._stage_playlist_navigation_transition(name, reason="play_next")
        return await self._play(
            name,
            preserve_playlist=True,
            confirm_start_in_background=not manual,
            fast_stop=not manual,
            command_already_accepted=True,
        )

    async def play_prev(self):
        """Queue a manual previous intent and return after it is accepted."""
        return await self._queue_manual_navigation(direction="previous")

    async def _play_prev(self, manual: bool = False):
        """播放上一首（内部实现）"""
        self.log.info("开始播放上一首")
        name = self.get_cur_music()
        # manual=True（用户主动点击）: 切到上一首
        # manual=False（自动触发）: 仅当当前歌曲无效时才切到上一首，否则重启当前歌曲
        if manual or name == "" or (self._find_playlist_index(display_name=name) < 0):
            name = self.get_prev_music()
        self.log.info(f"_play_prev. name:{name}, cur_music:{self.get_cur_music()}")
        if name == "":
            await self.do_tts("本地没有歌曲")
            return False
        self._stage_playlist_navigation_transition(name, reason="play_prev")
        return await self._play(
            name,
            preserve_playlist=True,
            confirm_start_in_background=not manual,
            fast_stop=not manual,
        )

    async def playlocal(self, name=""):
        """播放本地歌曲 - 不下载 — sync accept, arbiter async.

        Immediately accepts the command, invalidates manual navigation,
        sets last_cmd, submits PLAY intent (mode=playlocal) to the arbiter,
        and returns True.  Does NOT await download, resolve, or physical
        play.
        """
        self._invalidate_manual_navigation(reason="playlocal")
        self._last_cmd = "playlocal"
        self._accept_command(updated_at=time.time())
        from xiaomusic.playback.command_arbiter import IntentKind

        arbiter = self._get_or_create_arbiter()
        arbiter.submit(IntentKind.PLAY, payload={
            "mode": "playlocal",
            "name": name,
            "search": "",
        })
        return True

    async def submit_external_url_play(
        self,
        url: str,
        context: dict | None = None,
        resolved: dict | None = None,
    ):
        """Submit an external URL play intent — sync accept, no physical await.

        Context-aware: same caller context dict → same command, c+1 only
        on first submit, subsequent submits on same context only bump
        sequence (new attempt in same q/c generation).  Different context
        or context=None → new command.

        All state writes (registry, caller key, lifecycle) happen only
        after successful arbiter submit — no ghost writes on failure.

        Returns a JSON-serializable receipt (accepted, sequence).
        Physical dispatch is deferred to the arbiter executor.
        """
        import copy

        from xiaomusic.playback.command_arbiter import IntentKind, IntentReceipt

        # ── Determine if this is a new command or a same-context retry ──
        # context=None always new command (no key written).
        # If context already has legacy pinned marker, treat as old direct
        # path re-entry: don't add our key, don't c+1.
        is_new_command = True
        cmd_key: str | None = None
        proposed_ctx: dict | None = None
        evict_key: str | None = None

        if isinstance(context, dict):
            # Legacy marker guard: if context already went through
            # on_external_url_play (has pinned token), treat as re-entry.
            if context.get("_device_queue_session_initialized"):
                is_new_command = False
            else:
                existing_key = context.get("_ext_cmd_key")
                if isinstance(existing_key, str) and existing_key in self._external_context_registry:
                    # Same context reuse: not a new command
                    is_new_command = False
                    cmd_key = existing_key
                else:
                    # New context: prepare proposal (no writes yet)
                    proposed_id = self._external_context_next_id + 1
                    cmd_key = f"_ec_{self.did}_{proposed_id}"
                    proposed_ctx = copy.deepcopy(context)
                    # Identify eviction candidate (but don't evict yet)
                    if len(self._external_context_registry_order) >= 32:
                        evict_key = self._external_context_registry_order[0]

        # ── Prepare payload ─────────────────────────────────────
        payload: dict = {
            "mode": "external",
            "url": url,
            "resolved": copy.deepcopy(resolved) if isinstance(resolved, dict) else {},
        }
        if cmd_key is not None:
            payload["ctx_key"] = cmd_key
        elif isinstance(context, dict) and context.get("_device_queue_session_initialized"):
            # Old direct path: pass context inline for compatibility
            payload["context"] = copy.deepcopy(context)

        # ── Submit first — if this fails, nothing was written ────
        arbiter = self._get_or_create_arbiter()
        receipt: IntentReceipt = arbiter.submit(IntentKind.PLAY, payload=payload)

        # ── Only after successful submit: commit state changes ───
        if is_new_command and proposed_ctx is not None:
            # Commit registry
            self._external_context_next_id += 1
            if evict_key is not None:
                self._external_context_registry_order.pop(0)
                self._external_context_registry.pop(evict_key, None)
            self._external_context_registry[cmd_key] = proposed_ctx
            self._external_context_registry_order.append(cmd_key)
            # Write key back to caller context for future reuse
            context["_ext_cmd_key"] = cmd_key
            # Commit lifecycle writes
            self._invalidate_manual_navigation(reason="external_url_play")
            self._last_cmd = "external_play"
            self._accept_command(updated_at=time.time())
        elif is_new_command:
            # New command without dict context (None or non-dict)
            self._invalidate_manual_navigation(reason="external_url_play")
            self._last_cmd = "external_play"
            self._accept_command(updated_at=time.time())

        return receipt.to_dict()

    def _clear_degraded_state(self):
        self._cancel_failure_retry_task()
        self._failure_retry_meta = {}
        self._failure_retry_last_status = "idle"
        self._degraded = False
        self._degraded_notified = False
        self._play_failed_cnt = 0
        self._play_fail_first_ts = 0.0
        self._play_fail_last_reason = ""
        self._timer_expiry_false_count = 0
        self._timer_expiry_playing_grace_count = 0
        self._timer_expiry_unknown_grace_count = 0

    def _bump_play_session(self, reason: str = "") -> int:
        self._play_session_id += 1
        registry = self._ensure_playback_tasks()
        # Attempt-scoped: tied to individual track lifecycle.
        # Session-scoped: TTS timer (stops audio on session change), add-song,
        #   fast group-stop — all should be invalidated by a new session.
        # STOP_TIMER (global) intentionally excluded — must survive bumps.
        registry.cancel_by_kinds(*ATTEMPT_SCOPED_KINDS, *SESSION_SCOPED_KINDS)
        self._timer_expiry_false_count = 0
        self._bg_confirm_false_count = 0
        self._timer_expiry_playing_grace_count = 0
        self._timer_expiry_unknown_grace_count = 0
        self.log.info(
            "play_session_bump(session_id=%s, reason=%s)",
            self._play_session_id,
            reason or "",
        )
        return self._play_session_id

    def _resolve_fast_stop_wait_mode(self, *, fast_stop: bool) -> str:
        if not fast_stop:
            return "sync"
        mode = str(
            getattr(self.config, "auto_next_stop_wait_mode", "sync") or "sync"
        ).strip().lower()
        if mode in {"overlap", "async", "background"}:
            return "overlap"
        return "sync"

    async def _execute_group_stop(
        self,
        *,
        fast_stop: bool,
        sid: int,
        force_sync: bool = False,
    ):
        wait_mode = (
            "sync"
            if force_sync
            else self._resolve_fast_stop_wait_mode(fast_stop=fast_stop)
        )
        grace_ms = max(500, int(getattr(self.config, "auto_next_stop_grace_ms", 500) or 0))
        self.log.info(
            "group_stop_dispatch session_id=%s fast_stop=%s wait_mode=%s grace_ms=%d",
            sid,
            fast_stop,
            wait_mode,
            grace_ms,
        )
        if (not fast_stop) or wait_mode == "sync":
            await self.group_force_stop_xiaoai(fast=fast_stop)
            return None

        task = self._ensure_playback_tasks().start(
            TaskKind.FAST_GROUP_STOP,
            self._task_generation(sid=sid),
            self.group_force_stop_xiaoai(fast=True),
            metadata={"sid": sid, "label": "group_force_stop_fast"},
        )
        if grace_ms > 0:
            await asyncio.sleep(grace_ms / 1000.0)
        return task

    async def _playmusic(
        self,
        name,
        *,
        confirm_start_in_background: bool = False,
        fast_stop: bool = False,
        navigation_generation: int | None = None,
    ):
        """播放音乐的核心实现"""
        if not self._manual_navigation_is_current(navigation_generation):
            self.log.info(
                "manual_nav_stale generation=%s stage=playmusic_entry target=%s",
                navigation_generation,
                name,
            )
            return False
        playmusic_begin_t = time.time()
        self.log.info("[measure] playmusic_begin t=%.3f", playmusic_begin_t)

        # ── Pre-resolve target identity (no I/O) ────────────────────
        cur_playlist = self.device.cur_playlist
        music_library = getattr(self.xiaomusic, "music_library", None)
        entity_id = ""
        if music_library is not None:
            resolver = getattr(music_library, "resolve_playlist_item_identity", None)
            if callable(resolver):
                try:
                    entity_id = str(
                        resolver(cur_playlist, item_name=name) or ""
                    ).strip()
                except Exception:
                    entity_id = ""
            if not entity_id:
                resolver = getattr(music_library, "resolve_entity_id_by_name", None)
                if callable(resolver):
                    try:
                        entity_id = str(resolver(name) or "").strip()
                    except Exception:
                        entity_id = ""
        playlist_item_id = ""
        resolved_index = self._find_playlist_index(
            entity_id=entity_id,
            display_name=str(name or ""),
        )
        if resolved_index >= 0:
            items = getattr(self, "_play_list_items", []) or []
            item = items[resolved_index]
            playlist_item_id = str(item.get("item_id") or "").strip()

        # ── Begin runtime play request (legacy entry only) ──────────
        # Must be accepted before any side effects: bump, cancel, is_playing,
        # or legacy track write.  TransitionError (e.g. STOPPING) → abort.
        try:
            self._begin_runtime_play_request(
                desired_track=TrackReference(
                    entity_id=entity_id,
                    playlist_item_id=playlist_item_id,
                    display_name=str(name or ""),
                    source="legacy",
                ),
                updated_at=time.time(),
            )
        except TransitionError:
            self.log.info(
                "playmusic_transition_error phase=%s — aborting",
                self.get_runtime_state().phase.value,
            )
            return False
        # Capture request token immediately after acceptance.
        # No lifecycle ID increment — freezes snapshot for stale-guard checks.
        request_token = self._capture_lifecycle_token()

        # ── Only after acceptance: side effects ─────────────────────
        # New session: invalidate any pending delayed tasks from older sessions.
        sid = self._bump_play_session(reason="start_new_play")

        # 取消组内所有的下一首歌曲的定时器
        await self.cancel_group_next_timer()
        if not self._manual_navigation_is_current(navigation_generation):
            self.log.info(
                "manual_nav_stale generation=%s stage=cancel_timer target=%s",
                navigation_generation,
                name,
            )
            return False
        # Guard: request_token stale after cancel_timer → abort before legacy track write.
        if self._is_lifecycle_token_stale(request_token):
            self.log.info(
                "request_token_stale stage=cancel_timer name=%s",
                name,
            )
            return False

        self.is_playing = True
        self._set_runtime_track_reference(
            playlist_name=cur_playlist,
            display_name=name,
            entity_id=entity_id,
            playlist_item_id=playlist_item_id,
            current_index=resolved_index if resolved_index >= 0 else None,
        )
        self.log.info(f"cur_music {self.get_cur_music()}")
        url, origin_url = await self.xiaomusic.music_library.get_music_url(name)
        if not self._manual_navigation_is_current(navigation_generation):
            self.log.info(
                "manual_nav_stale generation=%s stage=resolve_url target=%s",
                navigation_generation,
                name,
            )
            return False
        # Guard: request_token stale after URL resolution → abort before stop.
        if self._is_lifecycle_token_stale(request_token):
            self.log.info(
                "request_token_stale stage=resolve_url name=%s",
                name,
            )
            return False
        self._log_measure("before_group_force_stop_xiaoai")
        if navigation_generation is None:
            stop_task = await self._execute_group_stop(fast_stop=fast_stop, sid=sid)
        else:
            stop_task = await self._execute_group_stop(
                fast_stop=fast_stop,
                sid=sid,
                force_sync=True,
            )
        self._log_measure("after_group_force_stop_xiaoai")
        if not self._manual_navigation_is_current(navigation_generation):
            self.log.info(
                "manual_nav_stale generation=%s stage=stop target=%s",
                navigation_generation,
                name,
            )
            return False
        # Guard: request_token stale after stop → abort before dispatch/attempt.
        if self._is_lifecycle_token_stale(request_token):
            self.log.info(
                "request_token_stale stage=stop name=%s",
                name,
            )
            return False
        self.log.info(f"播放 {url}")

        # Begin dispatch (transition phase to DISPATCHING) before track attempt.
        # Must happen after URL resolution/stop success, before _start_track_attempt.
        try:
            self._begin_runtime_play_dispatch(updated_at=time.time())
        except TransitionError:
            self.log.info(
                "playmusic_dispatch_transition_error phase=%s — aborting",
                self.get_runtime_state().phase.value,
            )
            return False

        # Begin track attempt: increment attempt_id, capture strict token.
        # Must happen after dispatch transition, before group_player_play.
        _attempt_token = self._start_track_attempt(updated_at=time.time())

        self._log_measure("before_group_player_play")
        if navigation_generation is None:
            results = await self.group_player_play(url, name)
        else:
            results = await self.group_player_play(
                url,
                name,
                navigation_generation=navigation_generation,
            )
        self._log_measure("after_group_player_play")
        if not self._manual_navigation_is_current(navigation_generation):
            self.log.info(
                "manual_nav_stale generation=%s stage=dispatch_return target=%s",
                navigation_generation,
                name,
            )
            return False
        if self._is_lifecycle_token_stale(_attempt_token):
            self.log.info(
                "lifecycle_stale stage=dispatch_return session=%s name=%s",
                self._play_session_id,
                name,
            )
            return False
        if stop_task is not None:
            self.log.info(
                "group_stop_overlap_state session_id=%s stop_done=%s",
                sid,
                str(stop_task.done()).lower(),
            )

        jellyfin_auto_candidate = self._is_jellyfin_auto_candidate(
            current_url=url,
            origin_url=origin_url,
        )

        if all(ele is None for ele in results):
            if jellyfin_auto_candidate:
                proxy_url = await self._try_proxy_fallback(
                    name=name,
                    sid=sid,
                    origin_url=origin_url,
                    fast_stop=fast_stop,
                    reason="player_play_failed",
                    verify_started=not confirm_start_in_background,
                )
                if proxy_url:
                    url = proxy_url
                    results = ["proxy"]
                    jellyfin_auto_candidate = False
                    # ── Capture handoff token from _try_proxy_fallback ────────
                    # _try_proxy_fallback internally called _start_track_attempt,
                    # so attempt_id is now old+1.  Validate before accepting.
                    handoff_token = self._capture_lifecycle_token()
                    if (
                        handoff_token.queue_session_id
                        != _attempt_token.queue_session_id
                        or handoff_token.command_generation
                        != _attempt_token.command_generation
                        or handoff_token.track_attempt_id
                        != _attempt_token.track_attempt_id + 1
                        or self._is_lifecycle_token_stale(handoff_token)
                    ):
                        self.log.info(
                            "proxy_handoff_token_invalid"
                            " old=(q=%s,c=%s,a=%s)"
                            " handoff=(q=%s,c=%s,a=%s)",
                            _attempt_token.queue_session_id,
                            _attempt_token.command_generation,
                            _attempt_token.track_attempt_id,
                            handoff_token.queue_session_id,
                            handoff_token.command_generation,
                            handoff_token.track_attempt_id,
                        )
                        return False
                    _attempt_token = handoff_token
                else:
                    await self._handle_play_failure(
                        name=name,
                        sid=sid,
                        reason="player_play_failed",
                        token=_attempt_token,
                    )
                    return False
            else:
                await self._handle_play_failure(
                    name=name,
                    sid=sid,
                    reason="player_play_failed",
                    token=_attempt_token,
                )
                return False

        # ── Dispatch succeeded (direct or proxy): begin runtime confirmation ──
        try:
            self._begin_runtime_confirmation(updated_at=time.time())
        except TransitionError:
            self.log.info(
                "playmusic_confirmation_transition_error phase=%s — aborting",
                self.get_runtime_state().phase.value,
            )
            return False

        if confirm_start_in_background:
            await self._mark_play_started(
                name=name,
                sid=sid,
                cur_playlist=cur_playlist,
                token=_attempt_token,
                measure_status=fast_stop,
                navigation_generation=navigation_generation,
            )
            self._schedule_playback_confirmation(
                name=name,
                sid=sid,
                cur_playlist=cur_playlist,
                origin_url=origin_url,
                current_url=url,
                fast_stop=fast_stop,
                token=_attempt_token,
            )
            return True

        started = await self._confirm_playback_started(name, sid)
        self.log.info(
            "play_start_confirmation_result(did=%s, session_id=%s, started=%s)",
            self.did,
            sid,
            "unknown" if started is None else str(started).lower(),
        )
        if started is False:
            if jellyfin_auto_candidate:
                proxy_url = await self._try_proxy_fallback(
                    name=name,
                    sid=sid,
                    origin_url=origin_url,
                    fast_stop=fast_stop,
                    reason="play_start_not_confirmed",
                    verify_started=True,
                )
                if proxy_url:
                    url = proxy_url
                    jellyfin_auto_candidate = False
                    # ── Capture handoff token from _try_proxy_fallback ────────
                    # _try_proxy_fallback internally called _start_track_attempt,
                    # so attempt_id is now old+1.  Validate before accepting.
                    handoff_token = self._capture_lifecycle_token()
                    if (
                        handoff_token.queue_session_id
                        != _attempt_token.queue_session_id
                        or handoff_token.command_generation
                        != _attempt_token.command_generation
                        or handoff_token.track_attempt_id
                        != _attempt_token.track_attempt_id + 1
                        or self._is_lifecycle_token_stale(handoff_token)
                    ):
                        await self._handle_play_failure(
                            name=name,
                            sid=sid,
                            reason="play_start_not_confirmed",
                            token=_attempt_token,
                        )
                        return False
                    _attempt_token = handoff_token
                else:
                    await self._handle_play_failure(
                        name=name,
                        sid=sid,
                        reason="play_start_not_confirmed",
                        token=_attempt_token,
                    )
                    return False
            else:
                await self._handle_play_failure(
                    name=name,
                    sid=sid,
                    reason="play_start_not_confirmed",
                    token=_attempt_token,
                )
                return False

        # Even if the API call succeeds, the speaker may not be able to reach a
        # direct Jellyfin URL. In auto mode, verify actual playback and fallback.
        if jellyfin_auto_candidate:
            await asyncio.sleep(1)
            if sid == self._play_session_id:
                try:
                    if not await self.get_if_xiaoai_is_playing():
                        proxy_url = await self._try_proxy_fallback(
                            name=name,
                            sid=sid,
                            origin_url=origin_url,
                            fast_stop=fast_stop,
                            reason="not_playing",
                            verify_started=True,
                        )
                        if proxy_url:
                            url = proxy_url
                except Exception:
                    # If status check fails, keep the original success path.
                    pass

        # ── Confirm runtime PLAYING fact before marking ──
        if not self._confirm_runtime_playing_for_attempt(
            token=_attempt_token, updated_at=time.time()
        ):
            return False

        await self._mark_play_started(
            name=name,
            sid=sid,
            cur_playlist=cur_playlist,
            token=_attempt_token,
            measure_status=fast_stop,
        )
        return True

    def _is_jellyfin_auto_candidate(self, *, current_url: str, origin_url: str) -> bool:
        jellyfin_mode = (
            getattr(self.config, "jellyfin_proxy_mode", "auto") or "auto"
        ).lower()
        strategy = getattr(self.xiaomusic, "link_playback_strategy", None)
        if strategy is not None:
            return strategy.should_jellyfin_auto_fallback(
                jellyfin_mode=jellyfin_mode,
                origin_url=origin_url,
                current_url=current_url,
            )
        return bool(
            jellyfin_mode == "auto"
            and origin_url
            and origin_url == current_url
            and self.xiaomusic.music_library.is_jellyfin_url(current_url)
        )

    async def _try_proxy_fallback(
        self,
        *,
        name: str,
        sid: int,
        origin_url: str,
        fast_stop: bool,
        reason: str,
        verify_started: bool,
    ) -> str:
        # Stale session: do not construct proxy, stop, attempt, or dispatch.
        if sid != self._play_session_id:
            self.log.info(
                "proxy_fallback_stale(old_sid=%s, cur_sid=%s)",
                sid,
                self._play_session_id,
            )
            return ""

        strategy = getattr(self.xiaomusic, "link_playback_strategy", None)
        try:
            if strategy is not None:
                proxy_url = strategy.build_proxy_url(origin_url, name=name)
            else:
                proxy_url = self.xiaomusic.music_library.get_proxy_url(
                    origin_url, name=name
                )
            if not proxy_url:
                self.log.info("proxy_fallback_empty_url name=%s", name)
                return ""

            self.log.info(
                "Jellyfin direct failed (%s), retry via proxy: %s",
                reason,
                proxy_url,
            )
            await self.group_force_stop_xiaoai(fast=fast_stop)
            self._start_track_attempt(updated_at=time.time())
            results2 = await self.group_player_play(proxy_url, name)
            if all(ele is None for ele in results2):
                return ""
            if not verify_started:
                return proxy_url
            await asyncio.sleep(1)
            if sid != self._play_session_id:
                self.log.info(
                    "timer_discard_due_to_sid_mismatch(old_sid=%s, cur_sid=%s)",
                    sid,
                    self._play_session_id,
                )
                return proxy_url
            try:
                if not await self.get_if_xiaoai_is_playing():
                    return ""
            except Exception:
                pass
            return proxy_url
        except Exception as e:
            self.log.warning("proxy fallback failed: %s", e)
            return ""

    def _schedule_playing_status_probe(self, *, sid: int, name: str) -> None:
        async def _runner():
            try:
                for _ in range(6):
                    await asyncio.sleep(0.25)
                    if sid != self._play_session_id:
                        return
                    try:
                        if await self.get_if_xiaoai_is_playing():
                            now = time.time()
                            anchor = getattr(self, "_measure_reset_t", None)
                            since_timer = -1.0 if anchor is None else now - anchor
                            self.log.info(
                                "[measure] status_playing_observed t=%.3f dt_from_timer=%.3f session_id=%s name=%s",
                                now,
                                since_timer,
                                sid,
                                name,
                            )
                            return
                    except Exception as exc:
                        self.log.debug(
                            "status_playing_probe_failed sid=%s name=%s err=%s",
                            sid,
                            name,
                            exc,
                        )
                        return
            except asyncio.CancelledError:
                return
            finally:
                pass

        self._ensure_playback_tasks().start(
            TaskKind.STATUS_PROBE,
            self._task_generation(sid=sid),
            _runner(),
            metadata={"name": name, "sid": sid},
        )

    async def _mark_play_started(
        self,
        *,
        name: str,
        sid: int,
        cur_playlist: str,
        token: LifecycleToken,
        measure_status: bool = False,
        navigation_generation: int | None = None,
    ):
        def _is_current() -> bool:
            if sid != self._play_session_id:
                return False
            if self._is_lifecycle_token_stale(token):
                return False
            if navigation_generation is not None:
                return self._manual_navigation_is_current(navigation_generation)
            return True

        if not _is_current():
            return
        # A confirmed start is the recovery boundary for prior playback failures.
        self._clear_degraded_state()

        self.log.info(f"【{name}】已经开始播放了")
        if measure_status:
            self._schedule_playing_status_probe(sid=sid, name=name)

        # 记录歌曲开始播放的时间
        self._start_time = time.time()
        self._paused_time = 0
        await self._refresh_runtime_volume(context="playmusic_started")
        if not _is_current():
            return

        if self.event_bus:
            self.event_bus.publish(PLAYER_STATE_CHANGED, device_id=self.did)

        sec = await self.xiaomusic.music_library.get_music_duration(name)
        if not _is_current():
            return
        # 存储真实歌曲时长
        self._duration = sec
        await self.xiaomusic.analytics.send_play_event(name, sec, self.hardware)
        if not _is_current():
            return

        # 设置下一首歌曲的播放定时器
        if sec <= 0.1:
            # After auth runtime relogin the first status query may lag behind.
            # Probe duration from player status so UI can recover from 00:00.
            self._start_duration_probe(name, sid, token=token)
            self.log.info(f"【{name}】不会设置下一首歌的定时器")
            return

        # 计算自动添加歌曲的延迟时间，为当前歌曲时长的一半，但不超过60秒
        if sec > 30:
            sleep_sec = min(sec / 2, 60)
            await self.auto_add_song(cur_playlist, sleep_sec)
            if not _is_current():
                return

        # 计算获取时长的执行耗时
        duration_execution_time = time.time() - self._start_time
        self.log.info(f"获取音乐时长耗时: {duration_execution_time:.3f} 秒")
        # 调整定时器时长，减去获取音乐时长的执行时间
        adjusted_sec = sec + self.config.delay_sec - duration_execution_time
        # 确保调整后的时长不会过小，最小保留0.1秒
        adjusted_sec = max(adjusted_sec, 0.1)
        self.log.info(
            f"原始歌曲时长: {sec:.3f} 秒, 调整后定时器时长: {adjusted_sec:.3f} 秒"
        )
        if not _is_current():
            return
        await self.set_next_music_timeout(adjusted_sec, token=token)
        if not _is_current():
            return
        # 发布设备配置变更事件
        if self.event_bus:
            self.event_bus.publish(DEVICE_CONFIG_CHANGED)

    def _schedule_playback_confirmation(
        self,
        *,
        name: str,
        sid: int,
        cur_playlist: str,
        origin_url: str,
        current_url: str,
        fast_stop: bool,
        token: LifecycleToken | None = None,
    ) -> None:
        # Capture token now if not provided, so background always has a concrete one.
        if token is None:
            token = self._capture_lifecycle_token()
        async def _runner():
            try:
                await self._background_confirm_playback_started(
                    name=name,
                    sid=sid,
                    cur_playlist=cur_playlist,
                    origin_url=origin_url,
                    current_url=current_url,
                    fast_stop=fast_stop,
                    token=token,
                )
            except asyncio.CancelledError:
                return
            finally:
                pass

        self._ensure_playback_tasks().start(
            TaskKind.PLAYBACK_CONFIRMATION,
            self._task_generation(token, sid=sid),
            _runner(),
            metadata={"name": name, "sid": sid},
        )

    def _get_auto_next_confirm_profile(self) -> dict[str, float | int]:
        delay_ms = max(
            1000,  # 最低 1000ms，覆盖音箱 stop→play 过渡
            int(getattr(self.config, "auto_next_confirm_delay_ms", 1000) or 0),
        )
        retries = max(
            2,  # 最低 2 次重试
            int(getattr(self.config, "auto_next_confirm_retries", 2) or 0),
        )
        interval_ms = max(
            100,
            int(getattr(self.config, "auto_next_confirm_interval_ms", 300) or 0),
        )
        return {
            "delay_sec": delay_ms / 1000.0,
            "retries": retries,
            "interval_sec": interval_ms / 1000.0,
        }

    async def _wait_confirmation_grace(self) -> None:
        """Wait 1.5s grace period before second confirmation probe.

        Extracted for testability; tests may replace with Event-based waiter.
        """
        await asyncio.sleep(1.5)

    async def _wait_jellyfin_confirmation_probe(self) -> None:
        """Wait 1s before Jellyfin after-started status probe.

        Extracted for testability; tests may replace with Event-based waiter.
        """
        await asyncio.sleep(1)

    async def _apply_confirmation_observation(
        self,
        observation: "ConfirmationObservation",
        *,
        name: str,
        sid: int,
        cur_playlist: str,
        origin_url: str,
        current_url: str,
        fast_stop: bool,
        token: "LifecycleToken",
        jellyfin_auto_candidate: bool,
    ) -> None:
        """Apply side effects based on confirmation observation (T05-A).

        Hard gate: never cancels _next_timer, never submits via
        IntentKind, never calls _play / _play_next.

        Entry guard: sid must match _play_session_id and token must
        be strict-current for ALL observation kinds.  Stale/sid-mismatch
        → zero writes, zero I/O beyond the guard.
        """
        # ── Entry guard for ALL kinds: sid + strict token ──
        if sid != self._play_session_id:
            self.log.info(
                "apply_obs_sid_mismatch did=%s kind=%s src_sid=%s cur_sid=%s",
                self.did,
                observation.kind.value,
                sid,
                self._play_session_id,
            )
            return
        if self._is_lifecycle_token_stale(token):
            self.log.info(
                "apply_obs_token_stale did=%s kind=%s",
                self.did,
                observation.kind.value,
            )
            return

        _now = time.time

        if observation.kind == ObservationKind.STARTED:
            # ── confirm runtime PLAYING ──
            if not self._confirm_runtime_playing_for_attempt(
                token=token, updated_at=_now()
            ):
                return
            self._bg_confirm_false_count = 0

            # ── Jellyfin after-started delayed probe ──
            if jellyfin_auto_candidate:
                await self._wait_jellyfin_confirmation_probe()
                if sid != self._play_session_id:
                    return
                if self._is_lifecycle_token_stale(token):
                    self.log.info(
                        "bg_confirm_lifecycle_stale post_jellyfin_sleep did=%s",
                        self.did,
                    )
                    return
                try:
                    is_playing = await self.get_if_xiaoai_is_playing()
                except Exception:
                    return
                if self._is_lifecycle_token_stale(token):
                    self.log.info(
                        "bg_confirm_lifecycle_stale post_status did=%s", self.did
                    )
                    return
                if not is_playing:
                    proxy_url = await self._try_proxy_fallback(
                        name=name,
                        sid=sid,
                        origin_url=origin_url,
                        fast_stop=fast_stop,
                        reason="not_playing",
                        verify_started=True,
                    )
                    if proxy_url:
                        handoff = self._capture_lifecycle_token()
                        if (
                            handoff.queue_session_id != token.queue_session_id
                            or handoff.command_generation != token.command_generation
                            or handoff.track_attempt_id != token.track_attempt_id + 1
                            or self._is_lifecycle_token_stale(handoff)
                        ):
                            return
                        if not self._confirm_runtime_playing_for_attempt(
                            token=handoff, updated_at=_now()
                        ):
                            return
                        await self._mark_play_started(
                            name=name,
                            sid=sid,
                            cur_playlist=cur_playlist,
                            token=handoff,
                            measure_status=fast_stop,
                        )
            return

        if observation.kind == ObservationKind.UNKNOWN:
            self.log.info(
                "apply_obs_unknown(did=%s, session_id=%s, name=%s, source=%s)",
                self.did,
                sid,
                name,
                observation.source,
            )
            # No state changes: preserve timer, no phase/failure/q/c/a changes,
            # do not touch false count, no dispatch.
            return

        if observation.kind == ObservationKind.NOT_STARTED:
            self._bg_confirm_false_count = 2
            self.log.info(
                "apply_obs_not_started(did=%s, session_id=%s, name=%s, count=%d)",
                self.did,
                sid,
                name,
                self._bg_confirm_false_count,
            )
            # Preserve timer.  No phase/failure/q/c/a changes.  No dispatch.

            # ── Jellyfin first-false proxy fallback ──
            if jellyfin_auto_candidate:
                proxy_url = await self._try_proxy_fallback(
                    name=name,
                    sid=sid,
                    origin_url=origin_url,
                    fast_stop=fast_stop,
                    reason="play_start_not_confirmed",
                    verify_started=True,
                )
                if proxy_url:
                    handoff = self._capture_lifecycle_token()
                    if (
                        handoff.queue_session_id != token.queue_session_id
                        or handoff.command_generation != token.command_generation
                        or handoff.track_attempt_id != token.track_attempt_id + 1
                        or self._is_lifecycle_token_stale(handoff)
                    ):
                        self.log.info(
                            "bg_confirm_lifecycle_stale post_fallback did=%s",
                            self.did,
                        )
                        return
                    if not self._confirm_runtime_playing_for_attempt(
                        token=handoff, updated_at=_now()
                    ):
                        return
                    await self._mark_play_started(
                        name=name,
                        sid=sid,
                        cur_playlist=cur_playlist,
                        token=handoff,
                        measure_status=fast_stop,
                    )
                    self._bg_confirm_false_count = 0
                    return
                if self._is_lifecycle_token_stale(token):
                    return
            # Fallback failed or not jellyfin: stay NOT_STARTED, no next/retry.
            return

    async def _background_confirm_playback_started(
        self,
        *,
        name: str,
        sid: int,
        cur_playlist: str,
        origin_url: str,
        current_url: str,
        fast_stop: bool,
        token: LifecycleToken | None = None,
    ) -> None:
        """Background playback confirmation — T05-A refactor.

        Forms observations via completion_policy and delegates side
        effects to _apply_confirmation_observation.  This method
        never cancels _next_timer, never submits AUTO_NEXT/RETRY,
        and never calls _play/_play_next.
        """
        if token is None:
            token = self._capture_lifecycle_token()
        if self._is_lifecycle_token_stale(token):
            self.log.info(
                "bg_confirm_lifecycle_stale entry did=%s name=%s",
                self.did,
                name,
            )
            return

        confirm_profile = self._get_auto_next_confirm_profile()
        _now = time.time
        try:
            raw = await self._confirm_playback_started(
                name,
                sid,
                delay_sec=float(confirm_profile["delay_sec"]),
                retries=int(confirm_profile["retries"]),
                interval_sec=float(confirm_profile["interval_sec"]),
            )
        except Exception as exc:
            obs = map_to_observation(exc, observed_at=_now(), source="first_probe")
            self.log.info(
                "play_start_confirmation_error(did=%s, session_id=%s, name=%s)",
                self.did,
                sid,
                name,
                exc_info=True,
            )
            if sid != self._play_session_id:
                return
            if self._is_lifecycle_token_stale(token):
                return
            await self._apply_confirmation_observation(
                obs,
                name=name,
                sid=sid,
                cur_playlist=cur_playlist,
                origin_url=origin_url,
                current_url=current_url,
                fast_stop=fast_stop,
                token=token,
                jellyfin_auto_candidate=False,
            )
            return

        obs = map_to_observation(raw, observed_at=_now(), source="first_probe")
        self.log.info(
            "play_start_confirmation_result(did=%s, session_id=%s, started=%s background=true)",
            self.did,
            sid,
            "unknown" if raw is None else str(raw).lower(),
        )

        # ── True / None ── no grace retry; delegate to handler directly ──
        if raw is True or raw is None:
            if sid != self._play_session_id:
                self.log.info(
                    "timer_discard_due_to_sid_mismatch(old_sid=%s, cur_sid=%s)",
                    sid,
                    self._play_session_id,
                )
                return
            if self._is_lifecycle_token_stale(token):
                self.log.info(
                    "bg_confirm_lifecycle_stale post_confirm did=%s name=%s",
                    self.did,
                    name,
                )
                return
            jellyfin_candidate = (
                self._is_jellyfin_auto_candidate(
                    current_url=current_url,
                    origin_url=origin_url,
                )
                if raw is True
                else False
            )
            await self._apply_confirmation_observation(
                obs,
                name=name,
                sid=sid,
                cur_playlist=cur_playlist,
                origin_url=origin_url,
                current_url=current_url,
                fast_stop=fast_stop,
                token=token,
                jellyfin_auto_candidate=jellyfin_candidate,
            )
            return

        # ── raw is False: first False → grace retry ──
        if sid != self._play_session_id:
            self.log.info(
                "timer_discard_due_to_sid_mismatch(old_sid=%s, cur_sid=%s)",
                sid,
                self._play_session_id,
            )
            return
        if self._is_lifecycle_token_stale(token):
            self.log.info(
                "bg_confirm_lifecycle_stale post_confirm did=%s name=%s",
                self.did,
                name,
            )
            return

        jellyfin_auto_candidate = self._is_jellyfin_auto_candidate(
            current_url=current_url,
            origin_url=origin_url,
        )

        # Grace sleep 1.5s before second probe
        await self._wait_confirmation_grace()
        if sid != self._play_session_id:
            return
        if self._is_lifecycle_token_stale(token):
            self.log.info(
                "bg_confirm_lifecycle_stale post_grace_sleep did=%s", self.did
            )
            return

        try:
            raw2 = await self._confirm_playback_started(
                name,
                sid,
                delay_sec=0.0,
                retries=2,
                interval_sec=0.5,
            )
        except Exception as exc2:
            obs2 = map_to_observation(
                exc2, observed_at=_now(), source="grace_retry"
            )
            self.log.info(
                "bg_confirm_retry_failed(did=%s, session_id=%s, name=%s)",
                self.did,
                sid,
                name,
            )
            if self._is_lifecycle_token_stale(token):
                return
            await self._apply_confirmation_observation(
                obs2,
                name=name,
                sid=sid,
                cur_playlist=cur_playlist,
                origin_url=origin_url,
                current_url=current_url,
                fast_stop=fast_stop,
                token=token,
                jellyfin_auto_candidate=False,
            )
            return

        if self._is_lifecycle_token_stale(token):
            self.log.info(
                "bg_confirm_lifecycle_stale post_retry did=%s", self.did
            )
            return

        obs2 = map_to_observation(raw2, observed_at=_now(), source="grace_retry")
        obs_jf = (
            jellyfin_auto_candidate
            and obs2.kind in (ObservationKind.NOT_STARTED, ObservationKind.STARTED)
        )
        await self._apply_confirmation_observation(
            obs2,
            name=name,
            sid=sid,
            cur_playlist=cur_playlist,
            origin_url=origin_url,
            current_url=current_url,
            fast_stop=fast_stop,
            token=token,
            jellyfin_auto_candidate=obs_jf,
        )

    async def _confirm_playback_started(
        self,
        name: str,
        sid: int,
        *,
        delay_sec: float = 1.2,
        retries: int = 2,
        interval_sec: float = 0.6,
    ) -> bool | None:
        """确认音箱已真正开始播放。"""
        self.log.info(
            "play_start_confirmation_attempted(did=%s, session_id=%s, retries=%d, delay_ms=%d, interval_ms=%d)",
            self.did,
            sid,
            retries,
            int(delay_sec * 1000),
            int(interval_sec * 1000),
        )
        result = None
        await asyncio.sleep(delay_sec)
        saw_true = False
        saw_false = False
        saw_drop_after_true = False
        for idx in range(retries + 1):
            try:
                started = await self.get_if_xiaoai_is_playing()
            except Exception as e:
                self.log.warning(
                    "play_start_confirmation_probe_failed(did=%s, session_id=%s, error=%s)",
                    self.did,
                    sid,
                    e.__class__.__name__,
                )
                result = None
                break
            if started:
                saw_true = True
            elif saw_true:
                saw_drop_after_true = True
                saw_false = True
            else:
                saw_false = True
            if idx < retries:
                await asyncio.sleep(interval_sec)
        else:
            if saw_drop_after_true:
                result = False
            elif saw_true:
                result = True
            elif saw_false:
                result = False
            else:
                result = None
        self._log_measure("after_confirm_playback_started")
        return result

    async def do_tts(self, value):
        """执行TTS（文字转语音）"""
        self.log.info(f"try do_tts value:{value}")
        if not value:
            self.log.info("do_tts no value")
            return

        # await self.group_force_stop_xiaoai()
        await self.text_to_speech(value)

        # 最大等8秒
        sec = min(8, int(len(value) / 3))
        await asyncio.sleep(sec)
        self.log.info(f"do_tts ok. cur_music:{self.get_cur_music()}")
        await self.check_replay()

    async def force_stop_xiaoai(self, device_id, *, fast: bool = False):
        """强制停止小爱播放"""
        try:
            if fast:
                ret = await self.auth_manager.mina_call(
                    "player_stop", device_id, retry=1, ctx="force_stop_xiaoai_fast"
                )
                self.log.info(
                    f"force_stop_xiaoai_fast player_stop device_id:{device_id} ret:{ret}"
                )
                return ret
            ret = await self.auth_manager.mina_call(
                "player_pause", device_id, retry=1, ctx="force_stop_xiaoai"
            )
            self.log.info(
                f"force_stop_xiaoai player_pause device_id:{device_id} ret:{ret}"
            )
            await self.stop_if_xiaoai_is_playing(device_id)
        except Exception as e:
            self.log.warning(f"Execption {e}")

    async def get_if_xiaoai_is_playing(self, device_id=None):
        """检查小爱是否正在播放"""
        target_device_id = device_id or self.device_id
        playing_info = await self.auth_manager.mina_call(
            "player_get_status",
            target_device_id,
            retry=1,
            ctx="get_if_xiaoai_is_playing",
        )
        self.log.info(playing_info)
        # WTF xiaomi api
        is_playing = (
            json.loads(playing_info.get("data", {}).get("info", "{}")).get("status", -1)
            == 1
        )
        return is_playing

    async def stop_if_xiaoai_is_playing(self, device_id):
        """如果小爱正在播放则停止"""
        if self.config.enable_force_stop:
            ret = await self.auth_manager.mina_call(
                "player_stop", device_id, retry=1, ctx="stop_if_xiaoai_is_playing"
            )
            self.log.info(
                f"stop_if_xiaoai_is_playing player_stop device_id:{device_id} enable_force_stop:{self.config.enable_force_stop} ret:{ret}"
            )
            return
        is_playing = await self.get_if_xiaoai_is_playing(device_id)
        if is_playing:
            # stop it
            ret = await self.auth_manager.mina_call(
                "player_stop", device_id, retry=1, ctx="stop_if_xiaoai_is_playing"
            )
            self.log.info(
                f"stop_if_xiaoai_is_playing player_stop device_id:{device_id} enable_force_stop:{self.config.enable_force_stop} ret:{ret}"
            )

    def isdownloading(self):
        """检查是否正在下载"""
        if not self._download_proc:
            return False

        if self._download_proc.returncode is not None:
            self.log.info(
                f"Process exited with returncode:{self._download_proc.returncode}"
            )
            return False

        self.log.info("Download Process is still running.")
        return True

    async def download(self, search_key, name):
        """下载歌曲"""
        # Outbound network access is denied unless explicitly allowlisted.
        if not (
            getattr(self.config, "outbound_allowlist_domains", [])
            or getattr(self.config, "allowlist_domains", [])
        ):
            msg = "出站网络未允许，已禁止下载（请配置 outbound_allowlist_domains）"
            self.log.warning(msg)
            try:
                await self.do_tts(msg)
            except Exception:
                pass
            res = DownloadResult(
                success=False,
                reason="outbound not allowlisted",
                provider="yt-dlp",
            )
            self.xiaomusic.last_download_result = asdict(res)
            return

        if self._download_proc:
            try:
                self._download_proc.kill()
            except ProcessLookupError:
                pass

        sbp_args = (
            "yt-dlp",
            f"{self.config.search_prefix}{search_key}",
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",
            "--paths",
            self.config.download_path,
            "-o",
            f"{name}.mp3",
            "--ffmpeg-location",
            f"{self.ffmpeg_location}",
            "--no-playlist",
        )

        if self.config.proxy:
            sbp_args += ("--proxy", f"{self.config.proxy}")

        if self.config.enable_yt_dlp_cookies:
            sbp_args += ("--cookies", f"{self.config.yt_dlp_cookies_path}")

        if self.config.loudnorm:
            sbp_args += ("--postprocessor-args", f"-af {self.config.loudnorm}")

        cmd = " ".join(sbp_args)
        self.log.info(f"download cmd: {cmd}")

        start = time.time()
        self._download_proc = await asyncio.create_subprocess_exec(
            *sbp_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await self.do_tts(f"正在下载歌曲{search_key}")
        self.log.info(f"正在下载中 {search_key} {name}")
        stdout, stderr = await self._download_proc.communicate()
        elapsed_ms = int((time.time() - start) * 1000)
        stderr_text = (stderr or b"").decode("utf-8", errors="replace")
        stderr_tail = "\n".join(stderr_text.splitlines()[-20:])
        # 下载完成后，修改文件权限
        file_path = os.path.join(self.config.download_path, f"{name}.mp3")
        chmodfile(file_path)

        ok = self._download_proc.returncode == 0 and os.path.isfile(file_path)
        reason = "" if ok else f"yt-dlp exit={self._download_proc.returncode}"
        res = DownloadResult(
            success=ok,
            reason=reason,
            filepath=file_path if ok else "",
            stderr_tail=stderr_tail,
            provider="yt-dlp",
            elapsed_ms=elapsed_ms,
        )
        self.xiaomusic.last_download_result = asdict(res)

    async def check_replay(self):
        """Check and resume interrupted playback via DeviceCommandArbiter RESUME.

        Condition semantic:
        - is_playing=False or isdownloading()→ return False, zero arbiter/lifecycle write
        - continue_play=True → return False, zero write (caller maintains legacy compat)
        - Need replay: submit RESUME (C3a pattern: expected accepted token, sid in payload),
          then accept command synchronously (c+1), return True.

        Does NOT await physical.  Executor resolves sid+token strict current
        before calling ``_play(command_already_accepted=True)``.
        """
        if not self.is_playing or self.isdownloading():
            self.log.info(
                "check_replay_skip isplaying=%s isdownloading=%s",
                self.is_playing,
                self.isdownloading(),
            )
            return False

        if self.config.continue_play:
            self.log.info(
                "check_replay_continue_play continue_play=%s",
                self.config.continue_play,
            )
            return False

        # ── Need replay: construct expected accepted token (C3a pattern) ──
        current = self.get_runtime_state()
        expected_accepted_token = LifecycleToken(
            queue_session_id=current.queue_session_id,
            command_generation=current.command_generation + 1,
            track_attempt_id=current.track_attempt_id,
        )

        from xiaomusic.playback.command_arbiter import ArbiterClosedError, IntentKind

        arbiter = self._get_or_create_arbiter()
        try:
            arbiter.submit(IntentKind.RESUME, payload={
                "sid": self._play_session_id,
                "accepted_token": expected_accepted_token,
            })
        except ArbiterClosedError:
            self.log.info("check_replay_arbiter_closed")
            return False

        # ── Only on successful submit: accept command (bump c by +1) ──
        self._accept_command(updated_at=time.time())

        self.log.info("check_replay_resume_submitted")
        return True

    async def add_download_music(self, name):
        """把下载的音乐加入播放列表"""
        filepath = os.path.join(self.config.download_path, f"{name}.mp3")
        await self.xiaomusic.music_library.add_music(name, filepath)
        # 应该很快，阻塞运行
        await self.xiaomusic.music_library._gen_all_music_tag({name: filepath})
        if self._find_playlist_index(display_name=name) < 0:
            # 通过 music_library API 添加歌曲后再调用 update_playlist() 同步快照
            await self.xiaomusic.music_library.add_music(name, filepath)
            self.update_playlist()
            self.log.debug(self._get_playlist_names())

    def get_music(self, direction="next", *, skip_one_repeat: bool = False):
        """获取下一首或上一首音乐

        skip_one_repeat=True：单曲循环模式下强制前进到下一首（手动切歌场景）

        索引解析优先级（从快到慢，从可靠到不可靠）：
        1. _current_index — 运行时维护，指向打乱后列表中的当前位置（最快，最可靠）
        2. 按 display_name 搜索 _play_list_items — 兜底（device.model 可能携带原始顺序 ID）
        3. index=0 硬兜底
        """
        play_list_len = len(self._play_list_items)
        if play_list_len == 0:
            self.log.warning("当前播放列表没有歌曲")
            return ""

        # 确定当前索引
        index = -1
        cur_music = self.get_cur_music()

        # Step 1: 优先使用 _current_index，但必须验证其有效性
        if 0 <= self._current_index < play_list_len:
            item_at_idx = self._play_list_items[self._current_index]
            idx_display = str(
                item_at_idx.get("display_name") or item_at_idx.get("legacy_name") or ""
            ).strip()
            if idx_display and idx_display == cur_music:
                index = self._current_index
            else:
                self.log.info(
                    "_current_index=%d 与当前歌曲 %s 不匹配（位置上是 %s），回退搜索",
                    self._current_index,
                    cur_music,
                    idx_display,
                )

        # Step 2: 兜底搜索 —— 优先按 display_name + entity_id（最可靠），其次用 device.model 的 ID 辅助
        if index < 0:
            # 先按 display_name 搜索（打乱后的 _play_list_items），同时传入 entity_id 辅助去重
            index = self._find_playlist_index(
                display_name=cur_music,
                entity_id=str(getattr(self.device, "current_entity_id", "") or ""),
            )
        if index < 0:
            # 再尝试 device.model 的 ID（可能携带原始顺序索引，但在打乱列表中搜索仍然能找到正确位置）
            index = self._find_playlist_index(
                item_id=str(getattr(self.device, "current_playlist_item_id", "") or ""),
                entity_id=str(getattr(self.device, "current_entity_id", "") or ""),
            )
        if index < 0:
            index = 0
        # 如果 Step 1 的 _current_index 与搜索结果不一致，立即同步 runtime 索引
        # 随机模式下常见：列表打乱后 _current_index 指向旧位置，必须强制更正
        if index >= 0 and index != self._current_index:
            self.log.info(
                "get_music 索引修正: _current_index %d -> %d (cur_music=%s)",
                self._current_index,
                index,
                cur_music,
            )
            self._current_index = index

        self.log.debug(
            "get_music direction=%s play_type=%d play_list_len=%d _current_index=%d resolved_index=%d cur_music=%s",
            direction,
            self.device.play_type,
            play_list_len,
            self._current_index,
            index,
            cur_music,
        )

        if play_list_len == 1:
            new_index = index  # 当只有一首歌曲时保持当前索引不变
        else:
            if direction == "next":
                if self.device.play_type == PLAY_TYPE_ONE and not skip_one_repeat:
                    # 单曲循环：自动切歌时重复当前歌曲；手动切歌时前进到下一首
                    new_index = index
                else:
                    new_index = index + 1
                    if (
                        self.device.play_type == PLAY_TYPE_SEQ
                        and new_index >= play_list_len
                    ):
                        self.log.info("顺序播放结束")
                        return ""
                    if new_index >= play_list_len:
                        new_index = 0
            elif direction == "prev":
                new_index = index - 1
                if new_index < 0:
                    new_index = play_list_len - 1
            else:
                self.log.error("无效的方向参数")
                return ""

        names = self._get_playlist_names()
        name = names[new_index]
        if not self.xiaomusic.music_library.is_music_exist(name):
            self._play_list_items.pop(new_index)
            return self.get_music(direction)
        return name

    def get_next_music(self, *, skip_one_repeat: bool = False):
        """获取下一首音乐

        skip_one_repeat=True：单曲循环模式下强制前进到下一首
        """
        return self.get_music(direction="next", skip_one_repeat=skip_one_repeat)

    def get_prev_music(self):
        """获取上一首音乐"""
        return self.get_music(direction="prev")

    def check_play_next(self):
        """判断是否需要播放下一首歌曲"""
        # 当前歌曲不在当前播放列表
        if self._find_playlist_index(display_name=self.get_cur_music()) < 0:
            self.log.info(f"当前歌曲 {self.get_cur_music()} 不在当前播放列表")
            return True

        # 当前没我在播放的歌曲
        if self.get_cur_music() == "":
            self.log.info("当前没我在播放的歌曲")
            return True
        else:
            # 当前播放的歌曲不存在了
            if not self.xiaomusic.music_library.is_music_exist(self.get_cur_music()):
                self.log.info(f"当前播放的歌曲 {self.get_cur_music()} 不存在了")
                return True
        return False

    async def text_to_speech(self, value):
        """文字转语音"""
        try:
            # 检查是否配置了 edge-tts 语音角色
            if self.config.edge_tts_voice:
                await self._text_to_speech_edge_tts(value)
            else:
                # 使用原有的 TTS 逻辑
                # 有 tts command 优先使用 tts command 说话
                if self.hardware in TTS_COMMAND:
                    tts_cmd = TTS_COMMAND[self.hardware]
                    self.log.info("Call MiIOService tts.")
                    value = value.replace(" ", ",")  # 不能有空格
                    await self.auth_manager.miio_call(
                        lambda: miio_command(
                            self.auth_manager.miio_service,
                            self.did,
                            f"{tts_cmd} {value}",
                        ),
                        retry=1,
                        ctx="text_to_speech_miio",
                    )
                else:
                    self.log.debug("Call MiNAService tts.")
                    await self.auth_manager.mina_call(
                        "text_to_speech",
                        self.device_id,
                        value,
                        retry=1,
                        ctx="text_to_speech",
                    )
        except Exception as e:
            self.log.exception(f"Execption {e}")

    async def _text_to_speech_edge_tts(self, value):
        """使用 edge-tts 进行文字转语音"""
        from xiaomusic.utils.music_utils import get_local_music_duration
        from xiaomusic.utils.network_utils import text_to_mp3

        self.log.info(f"_text_to_speech_edge_tts {value}")
        try:
            # 取消之前的 TTS 定时器
            if self._ensure_playback_tasks().cancel(TaskKind.TTS_TIMER):
                self.log.info("已取消之前的 TTS 定时器")

            # 使用 edge-tts 生成 MP3 文件
            self.log.info(
                f"使用 edge-tts 生成语音: {value}, voice: {self.config.edge_tts_voice}"
            )
            mp3_path = await text_to_mp3(
                text=value,
                save_dir=self.config.temp_dir,
                voice=self.config.edge_tts_voice,
            )
            self.log.info(f"edge-tts 生成的文件路径: {mp3_path}")

            # 生成播放 URL
            url = self.xiaomusic.music_library._get_file_url(mp3_path)
            self.log.info(f"TTS 播放 URL: {url}")

            # 播放 TTS 音频
            await self.group_player_play(url)

            # 获取 MP3 时长
            duration = await get_local_music_duration(mp3_path, self.config)
            self.log.info(f"TTS 音频时长: {duration} 秒")

            # 创建定时器，时长到后停止
            if duration > 0:

                async def _tts_timeout():
                    await asyncio.sleep(duration)
                    try:
                        self.log.info("TTS 播放定时器时间到")
                        # The registry deliberately ignores cancellation of the
                        # current task; never self-cancel or self-await here.
                        await self.stop(arg1="notts")
                    except Exception as e:
                        self.log.error(f"TTS 定时器异常: {e}")

                self._ensure_playback_tasks().start(
                    TaskKind.TTS_TIMER,
                    TaskGeneration(),
                    _tts_timeout(),
                    metadata={"scope": "tts", "duration": duration},
                )
                self.log.info(f"已设置 TTS 定时器，{duration} 秒后停止")

        except Exception as e:
            self.log.exception(f"edge-tts 播放失败: {e}")

    async def group_player_play(
        self,
        url,
        name="",
        *,
        navigation_generation: int | None = None,
    ):
        """同一组设备播放"""
        device_id_list = self.xiaomusic.device_manager.get_group_device_id_list(
            self.group_name
        )
        if navigation_generation is None:
            tasks = [
                self.play_one_url(device_id, url, name)
                for device_id in device_id_list
            ]
        else:
            tasks = [
                self.play_one_url(
                    device_id,
                    url,
                    name,
                    navigation_generation=navigation_generation,
                )
                for device_id in device_id_list
            ]
        results = await asyncio.gather(*tasks)
        self.log.info(f"group_player_play {url} {device_id_list} {results}")
        return results

    def _bootstrap_playlist_session_for_external_url(self, context: dict | None = None):
        context = context if isinstance(context, dict) else {}
        context_hint = context.get("context_hint")
        if not isinstance(context_hint, dict):
            context_hint = {}
        source_payload = context.get("source_payload")
        if not isinstance(source_payload, dict):
            source_payload = {}

        context_type = str(
            context_hint.get("context_type") or source_payload.get("context_type") or ""
        ).strip().lower()
        playlist_name = str(
            context_hint.get("context_name")
            or context_hint.get("context_id")
            or source_payload.get("playlist_name")
            or source_payload.get("context_name")
            or ""
        ).strip()
        music_name = str(
            source_payload.get("music_name")
            or source_payload.get("track_name")
            or context.get("title")
            or ""
        ).strip()
        if context_type != "playlist":
            return
        if not playlist_name:
            return

        playlist_items = self._build_playlist_runtime_items(playlist_name)
        if not playlist_items:
            return

        self.device.cur_playlist = playlist_name
        # Shuffle when play_type is RND (persistent) OR context carries shuffle=true (single-session).
        # WebUI next/prev no longer re-POSTs /play, so each external session bootstraps only once;
        # subsequent control next/prev consume the existing snapshot via _play_next/prev.
        should_shuffle = (
            self.device.play_type == PLAY_TYPE_RND
            or bool(context.get("shuffle", False))
        )
        self._playlist_session_shuffled = should_shuffle
        if should_shuffle:
            random.shuffle(playlist_items)
            self.log.info(
                f"external_url playlist shuffled {playlist_name} {list2str([item.get('display_name', '') for item in playlist_items], self.config.verbose)}"
            )
        self._play_list_items = playlist_items
        self._set_runtime_track_reference(
            playlist_name=playlist_name,
            display_name=music_name,
            entity_id=str(
                source_payload.get("entity_id") or context_hint.get("entity_id") or ""
            ).strip(),
            playlist_item_id=str(
                source_payload.get("playlist_item_id")
                or source_payload.get("item_id")
                or source_payload.get("id")
                or context_hint.get("playlist_item_id")
                or context_hint.get("item_id")
                or ""
            ).strip(),
        )

    async def on_external_url_play(
        self,
        context: dict | None = None,
        *,
        command_already_accepted: bool = False,
        manual_already_invalidated: bool = False,
    ):
        """External URL play initialisation with lifecycle discipline.

        Returns LifecycleToken on success, or None when rejected (STOPPING,
        TransitionError, stale identity, or forged marker).

        Stores a pinned LifecycleToken in the context dict to prevent
        old contexts from re-binding to newer lifecycle generations.

        Args:
            command_already_accepted: True when command was already accepted
                upstream (arbiter submit_external_url_play).  False (default)
                preserves legacy behavior — _accept_command is called here.
            manual_already_invalidated: True when manual navigation was
                already invalidated upstream.  False (default) preserves
                legacy behavior — _invalidate_manual_navigation is called here.
        """
        # ── Normalize context to dict for marker storage ──────────────
        if not isinstance(context, dict):
            context = {}

        # ── One-time init guard: validate pinned identity ─────────────
        if context.get("_device_queue_session_initialized"):
            pinned = context.get("_device_runtime_pinned_token")
            if not isinstance(pinned, LifecycleToken):
                return None
            now_state = self.get_runtime_state()
            if (
                now_state.queue_session_id != pinned.queue_session_id
                or now_state.command_generation != pinned.command_generation
            ):
                return None
            # q/c match → same lifecycle generation; attempt may vary
            return self._capture_lifecycle_token()

        # ── Extract track identity BEFORE any side effects ────────────
        context_hint = context.get("context_hint")
        if not isinstance(context_hint, dict):
            context_hint = {}
        source_payload = context.get("source_payload")
        if not isinstance(source_payload, dict):
            source_payload = {}

        display_name = str(
            context.get("title")
            or source_payload.get("music_name")
            or source_payload.get("track_name")
            or context_hint.get("display_name")
            or ""
        ).strip()
        entity_id = str(
            source_payload.get("entity_id")
            or context_hint.get("entity_id")
            or ""
        ).strip()
        playlist_item_id = str(
            source_payload.get("playlist_item_id")
            or source_payload.get("item_id")
            or source_payload.get("id")
            or context_hint.get("playlist_item_id")
            or context_hint.get("item_id")
            or ""
        ).strip()

        # ── Begin runtime play request BEFORE any side effects ───────
        # STOPPING / TransitionError → return None, no marker, no q/c,
        # no sid/timer/legacy mutation.
        try:
            self._begin_runtime_play_request(
                desired_track=TrackReference(
                    entity_id=entity_id,
                    playlist_item_id=playlist_item_id,
                    display_name=display_name,
                    source="external",
                ),
                updated_at=time.time(),
            )
        except TransitionError:
            self.log.info(
                "external_play_transition_error phase=%s — aborting",
                self.get_runtime_state().phase.value,
            )
            return None

        # ── Only after acceptance: write marker and bump lifecycle ───
        context["_device_queue_session_initialized"] = True
        now = time.time()
        self._start_queue_session(updated_at=now)
        if not command_already_accepted:
            self._accept_command(updated_at=now)
        request_token = self._capture_lifecycle_token()
        # Pin the original lifecycle identity in context so re-entry
        # validates q/c match — prevents old contexts from re-binding.
        context["_device_runtime_pinned_token"] = request_token

        # ── Legacy bootstrap (only after acceptance) ─────────────────
        if not manual_already_invalidated:
            self._invalidate_manual_navigation(reason="external_url_play")
        self._playlist_session_shuffled = False  # reset; bootstrap may set to True
        previous_playlist = str(self.device.cur_playlist or "").strip()
        self._bump_play_session(reason="external_url_play")
        await self.cancel_group_next_timer()

        # Strict guard after cancel: if lifecycle changed, return None
        # without legacy clear/bootstrap.  The pinned token stays in
        # context with old identity, so any retry with same context will
        # fail the q/c match check.
        if self._is_lifecycle_token_stale(request_token):
            self.log.info(
                "external_play_token_stale after cancel_timer did=%s", self.did
            )
            return None

        self.is_playing = False
        self._start_time = 0
        self._paused_time = 0
        self._duration = 0
        self._last_cmd = "external_play"
        self._current_index = -1
        self._play_list_items = []
        self.device.cur_playlist = ""
        self.device.cur_music = ""
        self.device.current_display_name = ""
        self.device.current_entity_id = ""
        self.device.current_playlist_item_id = ""
        if previous_playlist:
            self.device.playlist2music[previous_playlist] = ""
        self._bootstrap_playlist_session_for_external_url(context)
        if not self.device.cur_music:
            self.device.cur_music = ""

        return request_token

    async def on_external_url_play_started(
        self,
        context: dict | None = None,
        resolved: dict | None = None,
        *,
        token: LifecycleToken,
    ) -> bool:
        """Finalize local runtime state after external URL dispatch succeeds.

        - Entry: capture sid, define _is_current (=sid match + strict token)
        - Construct final TrackReference from resolved/context/device
        - phase DISPATCHING only → _begin_runtime_confirmation →
          _confirm_runtime_playing(confirmed_track=...) → PLAYING
        - TransitionError or unexpected phase → silent return False
        - After phase fact: legacy title / is_playing / start_time /
          volume / duration / event / timer
        - Each await re-checks _is_current; stale → silent return False
        - _start_duration_probe and set_next_music_timeout inherit token
        """
        sid = self._play_session_id

        def _is_current():
            return (
                sid == self._play_session_id
                and not self._is_lifecycle_token_stale(token)
            )
        if not _is_current():
            return False

        context = context if isinstance(context, dict) else {}
        resolved = resolved if isinstance(resolved, dict) else {}

        # ── construct final TrackReference ──────────────────────────
        title = str(
            resolved.get("title")
            or context.get("title")
            or self.device.cur_music
            or ""
        ).strip()
        entity_id = str(
            resolved.get("entity_id")
            or resolved.get("id")
            or (context.get("source_payload") or {}).get("entity_id")
            or getattr(self.device, "current_entity_id", "")
            or ""
        ).strip()
        playlist_item_id = str(
            resolved.get("playlist_item_id")
            or resolved.get("item_id")
            or (context.get("source_payload") or {}).get("playlist_item_id")
            or getattr(self.device, "current_playlist_item_id", "")
            or ""
        ).strip()

        confirmed_track = TrackReference(
            entity_id=entity_id,
            playlist_item_id=playlist_item_id,
            display_name=title,
            source="external",
        )

        # ── phase transition: DISPATCHING → CONFIRMING → PLAYING ───
        state = self.get_runtime_state()
        if state.phase != PlaybackPhase.DISPATCHING:
            return False

        try:
            self._begin_runtime_confirmation(updated_at=time.time())
        except TransitionError:
            return False

        try:
            self._confirm_runtime_playing(
                confirmed_track=confirmed_track,
                updated_at=time.time(),
            )
        except TransitionError:
            return False

        # ── phase now PLAYING — legacy state ────────────────────────
        if title:
            self._set_runtime_track_reference(
                playlist_name=str(self.device.cur_playlist or "").strip(),
                display_name=title,
                entity_id=entity_id,
                playlist_item_id=playlist_item_id,
            )

        self.is_playing = True
        self._start_time = time.time()
        self._paused_time = 0
        self._last_cmd = "external_play"

        # ── recovery boundary: confirmed PLAYING clears legacy failure ──
        # Runtime failure already cleared by confirm_playing; do NOT clear
        # runtime here.
        self._clear_degraded_state()

        # ── volume refresh ──────────────────────────────────────────
        await self._refresh_runtime_volume(context="external_url_play_started")
        if not _is_current():
            return False

        # ── duration resolution ─────────────────────────────────────
        duration_hint = 0.0
        for key in ("duration_seconds", "duration", "duration_ms"):
            value = resolved.get(key)
            if value is None:
                continue
            try:
                parsed = float(value)
            except Exception:
                continue
            if parsed > 10000:
                parsed = parsed / 1000.0
            if parsed > 0.1:
                duration_hint = parsed
                break

        sec = duration_hint
        if sec <= 0.1 and title:
            try:
                sec = await self.xiaomusic.music_library.get_music_duration(title)
            except Exception:
                sec = 0.0

        if not _is_current():
            return False

        self._duration = max(float(sec or 0.0), 0.0)
        if self.event_bus:
            self.event_bus.publish(PLAYER_STATE_CHANGED, device_id=self.did)

        if self._duration <= 0.1:
            self._start_duration_probe(title, sid, token=token)
            self.log.info(f"【{title or 'external_url'}】不会设置下一首歌的定时器")
            return True

        adjusted_sec = max(self._duration + self.config.delay_sec, 0.1)
        self.log.info(
            "external_url_post_start duration=%.3f adjusted_delay=%.3f title=%s",
            self._duration,
            adjusted_sec,
            title,
        )
        await self.set_next_music_timeout(adjusted_sec, token=token)
        if not _is_current():
            return False
        return True

    def _resolve_play_url_dispatch_mode(self) -> str:
        mode = str(getattr(self.config, "play_url_mode", "auto") or "auto").strip().lower()
        if mode in {"play_by_url", "url"}:
            return "play_by_url"
        if mode in {"play_by_music_url", "music_url"}:
            return "play_by_music_url"
        if self.config.continue_play:
            return "continue_play"
        if self.config.use_music_api or (self.hardware in NEED_USE_PLAY_MUSIC_API):
            return "play_by_music_url"
        return "play_by_url"

    async def play_one_url(
        self,
        device_id,
        url,
        name,
        *,
        navigation_generation: int | None = None,
    ):
        """在单个设备上播放URL"""
        ret = None
        try:
            audio_id = await self._get_audio_id(name)
            if not self._manual_navigation_is_current(navigation_generation):
                self.log.info(
                    "manual_nav_stale generation=%s stage=before_device_dispatch target=%s",
                    navigation_generation,
                    name,
                )
                return None
            dispatch_mode = self._resolve_play_url_dispatch_mode()
            self.log.info(
                "play_one_url dispatch_mode=%s hardware=%s use_music_api=%s continue_play=%s device_id=%s",
                dispatch_mode,
                self.hardware,
                self.config.use_music_api,
                self.config.continue_play,
                device_id,
            )
            if dispatch_mode == "continue_play":
                ret = await self.auth_manager.mina_call(
                    "play_by_music_url",
                    device_id,
                    url,
                    _type=1,
                    audio_id=audio_id,
                    retry=1,
                    ctx="play_one_url_continue",
                )
                self.log.info(
                    f"play_one_url continue_play device_id:{device_id} ret:{ret} url:{url} audio_id:{audio_id}"
                )
            elif dispatch_mode == "play_by_music_url":
                ret = await self.auth_manager.mina_call(
                    "play_by_music_url",
                    device_id,
                    url,
                    audio_id=audio_id,
                    retry=1,
                    ctx="play_one_url_music_api",
                )
                self.log.info(
                    f"play_one_url play_by_music_url device_id:{device_id} ret:{ret} url:{url} audio_id:{audio_id}"
                )
            else:
                ret = await self.auth_manager.mina_call(
                    "play_by_url",
                    device_id,
                    url,
                    retry=1,
                    ctx="play_one_url",
                )
                self.log.info(
                    f"play_one_url play_by_url device_id:{device_id} ret:{ret} url:{url}"
                )
        except Exception as e:
            self.log.exception(f"Execption {e}")
        return ret

    async def _get_audio_id(self, name):
        """获取音频ID"""
        audio_id = self.config.use_music_audio_id or "1582971365183456177"
        if not (self.config.use_music_api or self.config.continue_play):
            return str(audio_id)
        try:
            params = {
                "query": name,
                "queryType": 1,
                "offset": 0,
                "count": 6,
                "timestamp": int(time.time_ns() / 1000),
            }
            response = await self.auth_manager.mina_call(
                "mina_request",
                "/music/search",
                params,
                retry=1,
                ctx="get_audio_id",
            )
            for song in response["data"]["songList"]:
                if song["originName"] == "QQ音乐":
                    audio_id = song["audioID"]
                    break
            # 没找到QQ音乐的歌曲，取第一个
            if audio_id == 1582971365183456177:
                audio_id = response["data"]["songList"][0]["audioID"]
            self.log.debug(f"_get_audio_id. name: {name} songId:{audio_id}")
        except Exception as e:
            self.log.error(f"_get_audio_id {e}")
        return str(audio_id)

    async def reset_timer_when_answer(self, answer_length):
        """重置计时器（当小爱回答时）"""
        if not (self.is_playing and self.config.continue_play):
            return
        pause_time = answer_length / 5 + 1
        offset, duration = self.get_offset_duration()
        self._paused_time += pause_time
        new_time = duration - offset + pause_time
        await self.set_next_music_timeout(new_time)
        self.log.info(
            f"reset_timer 延长定时器. answer_length:{answer_length} pause_time:{pause_time}"
        )

    async def set_next_music_timeout(
        self, sec, token: LifecycleToken | None = None
    ):
        """设置下一首歌曲的播放定时器。

        到期后不直接切歌：先检查设备播放状态，连续两个 False 才切歌。
        设备在播放或状态检查异常/未知时，按固定短宽限重新调度。
        """
        if token is None:
            token = self._capture_lifecycle_token()
        sid = self._play_session_id

        await self.cancel_next_timer()
        if self._is_lifecycle_token_stale(token):
            self.log.info(
                "timer_lifecycle_stale(stage=cancel_return, session_id=%s, did=%s)",
                sid,
                self.did,
            )
            return

        def _token_is_current(stage: str) -> bool:
            if not self._is_lifecycle_token_stale(token):
                return True
            self.log.info(
                "timer_lifecycle_stale(stage=%s, session_id=%s, did=%s)",
                stage,
                sid,
                self.did,
            )
            return False

        # NOTE: Do NOT reset _timer_expiry_false_count here.
        # It is only reset on success (_mark_play_started) or after two-False advance.
        # Resetting here would cause infinite reschedule loops.

        async def _do_next():
            try:
                await asyncio.sleep(sec)
                if not _token_is_current("wake"):
                    return
                if sid != self._play_session_id:
                    self.log.info(
                        "timer_discard_due_to_sid_mismatch(old_sid=%s, cur_sid=%s)",
                        sid,
                        self._play_session_id,
                    )
                    return
                self._log_measure("timer_fired", reset=True)
                self.log.info(f"定时器时间到了 did: {self.did}")
                if self.device.play_type == PLAY_TYPE_SIN:
                    self.log.info(f"单曲播放不继续播放下一首 did: {self.did}")
                    await self.stop(arg1="notts")
                    return

                # Check device status before advancing.
                try:
                    is_playing = await self.get_if_xiaoai_is_playing()
                except Exception:
                    if not _token_is_current("status_error"):
                        return
                    # Status check failed (unknown): grace-limited extension.
                    self._timer_expiry_unknown_grace_count += 1
                    self.log.info(
                        "timer_expiry_status_check_failed(did=%s, unknown_grace=%d)",
                        self.did,
                        self._timer_expiry_unknown_grace_count,
                    )
                    if self._timer_expiry_unknown_grace_count > self.MAX_UNKNOWN_GRACE_EXTENSIONS:
                        # Max 3 unknown extensions reached: treat as complete.
                        if not _token_is_current("unknown_advance"):
                            return
                        self._timer_expiry_unknown_grace_count = 0
                        self._timer_expiry_playing_grace_count = 0
                        self._timer_expiry_false_count = 0
                        from xiaomusic.playback.command_arbiter import IntentKind
                        self._submit_auto_retry(
                            IntentKind.AUTO_NEXT,
                            source_token=token,
                            sid=sid,
                            reason="timer_unknown_advance",
                        )
                        return
                    if not _token_is_current("unknown_reschedule"):
                        return
                    await self.set_next_music_timeout(3.0, token=token)
                    return

                if not _token_is_current("status_return"):
                    return
                if is_playing:
                    # Device still playing: grace-limited extension (max 3).
                    self._timer_expiry_false_count = 0
                    self._timer_expiry_unknown_grace_count = 0
                    self._timer_expiry_playing_grace_count += 1
                    self.log.info(
                        "timer_expiry_device_still_playing(did=%s, playing_grace=%d)",
                        self.did,
                        self._timer_expiry_playing_grace_count,
                    )
                    if self._timer_expiry_playing_grace_count > self.MAX_PLAYING_GRACE_EXTENSIONS:
                        # The duration timer is authoritative; playing may mean device-side replay.
                        if not _token_is_current("playing_advance"):
                            return
                        self._timer_expiry_playing_grace_count = 0
                        self.log.info(
                            "timer_expiry_playing_grace_exhausted(did=%s), advancing",
                            self.did,
                        )
                        from xiaomusic.playback.command_arbiter import IntentKind
                        self._submit_auto_retry(
                            IntentKind.AUTO_NEXT,
                            source_token=token,
                            sid=sid,
                            reason="timer_playing_advance",
                        )
                        return
                    if not _token_is_current("playing_reschedule"):
                        return
                    await self.set_next_music_timeout(3.0, token=token)
                    return

                # Device not playing: increment false count.
                self._timer_expiry_false_count += 1
                self.log.info(
                    "timer_expiry_false_count(did=%s, count=%d)",
                    self.did,
                    self._timer_expiry_false_count,
                )

                if self._timer_expiry_false_count < 2:
                    # First False: reschedule with short grace.
                    if not _token_is_current("false_reschedule"):
                        return
                    await self.set_next_music_timeout(3.0, token=token)
                    return

                # Two consecutive False: advance.
                if not _token_is_current("false_advance"):
                    return
                self._timer_expiry_false_count = 0
                from xiaomusic.playback.command_arbiter import IntentKind
                self._submit_auto_retry(
                    IntentKind.AUTO_NEXT,
                    source_token=token,
                    sid=sid,
                    reason="timer_false_advance",
                )

            except asyncio.CancelledError:
                self.log.info(
                    "timer_cancel(session_id=%s, did=%s)",
                    sid,
                    self.did,
                )
                raise
            except Exception as e:
                self.log.error(f"Execption {e}")

        self._ensure_playback_tasks().start(
            TaskKind.COMPLETION_NEXT_TIMER,
            self._task_generation(token, sid=sid),
            _do_next(),
            metadata={"sid": sid, "delay_sec": sec},
        )
        self.log.info(
            "timer_start(session_id=%s, delay_sec=%.3f, did=%s)",
            sid,
            sec,
            self.did,
        )

    async def _handle_play_failure(
        self,
        *,
        name: str,
        sid: int,
        reason: str,
        token: LifecycleToken,
    ):
        if self._is_lifecycle_token_stale(token) or sid != self._play_session_id:
            return
        state = self.get_runtime_state()
        if state.phase not in {
            PlaybackPhase.RESOLVING, PlaybackPhase.SWITCHING,
            PlaybackPhase.DISPATCHING, PlaybackPhase.CONFIRMING,
            PlaybackPhase.PLAYING, PlaybackPhase.PAUSED, PlaybackPhase.FAILED,
        }:
            return

        now = time.time()
        if self._play_fail_first_ts <= 0:
            self._play_fail_first_ts = now
        new_count = self._play_failed_cnt + 1
        total_elapsed = now - self._play_fail_first_ts
        decision = decide_failure_action(
            new_count,
            total_elapsed,
            getattr(getattr(self, "device", None), "play_type", None) == PLAY_TYPE_SIN,
            reason=reason,
        )
        try:
            self._report_runtime_failure(
                reason=reason,
                degraded=decision.action == FailureAction.DEGRADED,
                updated_at=now,
            )
        except TransitionError:
            return
        self._play_failed_cnt = new_count
        self._play_fail_last_reason = reason
        self._failure_retry_meta = {
            "action": decision.action.value, "count": new_count,
            "reason": reason, "sid": sid, "token": token,
        }
        logger = getattr(self, "log", None)
        if logger is not None:
            logger.info("播放 %s 失败. reason=%s cnt=%d action=%s", name, reason, new_count, decision.action.value)

        if decision.action == FailureAction.DEGRADED:
            self._degraded = True
            await self._await_cancelled_failure_retry()
            if not self._degraded_notified:
                self._degraded_notified = True
                try:
                    await self.do_tts("播放失败过多，已停止自动切歌，请稍后再试")
                except Exception:
                    pass
                if sid != self._play_session_id or self._is_lifecycle_token_stale(token):
                    return
            return
        if decision.action == FailureAction.STOP:
            await self._await_cancelled_failure_retry()
            if sid == self._play_session_id and not self._is_lifecycle_token_stale(token):
                await self.stop(arg1="notts")
            return

        await self._await_cancelled_failure_retry()
        from xiaomusic.playback.command_arbiter import IntentKind

        evt = asyncio.Event()
        self._failure_retry_done_event = evt

        async def _retry_runner():
            self._failure_retry_last_status = "running"
            await self._wait_failure_retry_backoff(decision.delay)
            if sid != self._play_session_id or self._is_lifecycle_token_stale(token):
                return
            runtime_phase = self.get_runtime_state().phase
            if runtime_phase in {
                PlaybackPhase.IDLE,
                PlaybackPhase.STOPPING,
                PlaybackPhase.STOPPED,
                PlaybackPhase.PAUSED,
            } or self._degraded:
                return
            try:
                is_playing = await self.get_if_xiaoai_is_playing()
            except Exception:
                if sid != self._play_session_id or self._is_lifecycle_token_stale(token):
                    return
            else:
                if sid != self._play_session_id or self._is_lifecycle_token_stale(token):
                    return
                if is_playing:
                    self.log.info("retry_skip_speaker_already_playing(session_id=%s, name=%s)", sid, name)
                    return
            if sid != self._play_session_id or self._is_lifecycle_token_stale(token):
                return
            self._submit_auto_retry(
                IntentKind.RETRY,
                source_token=token,
                sid=sid,
                payload={
                    "name": name,
                    "retry_same_song": decision.action == FailureAction.RETRY_SAME,
                },
                reason=reason,
            )

        task = self._ensure_playback_tasks().start(
            TaskKind.FAILURE_RETRY,
            self._task_generation(token, sid=sid),
            _retry_runner(),
            metadata={
                "action": decision.action.value,
                "count": new_count,
                "reason": reason,
                "sid": sid,
            },
        )
        self._failure_retry_last_status = "pending"

        def _consume_retry_done(done_task):
            if done_task.cancelled():
                self._failure_retry_last_status = "cancelled"
            else:
                exc_info = None
                try:
                    exc_info = done_task.exception()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                if exc_info is not None:
                    self._failure_retry_last_error = str(exc_info)[:200]
                    self._failure_retry_last_status = "done"
                    logger = getattr(self, "log", None)
                    if logger is not None:
                        logger.error("failure_retry_task_error=%s", exc_info)
                else:
                    self._failure_retry_last_status = "done"
            if getattr(self, "_failure_retry_task", None) is done_task:
                self._failure_retry_task = None
            evt.set()

        if task is not None:
            task.add_done_callback(_consume_retry_done)

    async def set_volume(self, volume: int):
        """设置音量"""
        volume = self._remember_volume(volume)
        self.log.info(f"set_volume.  did: {self.did} volume: {volume}")
        try:
            await self.auth_manager.mina_call(
                "player_set_volume",
                self.device_id,
                volume,
                retry=1,
                ctx="set_volume",
            )
        except Exception as e:
            self.log.exception(f"Execption {e}")

    async def get_volume(self):
        """获取音量"""
        volume = 0
        try:
            playing_info = await self.auth_manager.mina_call(
                "player_get_status",
                self.device_id,
                retry=1,
                ctx="get_volume",
            )
            self.log.info(f"get_volume. playing_info:{playing_info}")
            volume = json.loads(playing_info.get("data", {}).get("info", "{}")).get(
                "volume", 0
            )
        except Exception as e:
            self.log.warning(f"Execption {e}")
        volume = self._remember_volume(volume)
        self.log.info("get_volume. volume:%d", volume)
        return volume

    async def get_player_status(self):
        """获取完整播放状态"""
        try:
            playing_info = await self.auth_manager.mina_call(
                "player_get_status",
                self.device_id,
                retry=1,
                ctx="get_player_status",
            )
            self.log.info(f"get_player_status. playing_info:{playing_info}")
            info = json.loads(playing_info.get("data", {}).get("info", "{}"))
            self._remember_volume_from_status(info)
            return info
        except Exception as e:
            self.log.warning(f"Execption {e}")
        return {"volume": 0, "status": 0}

    async def set_play_type(self, play_type, dotts=True, refresh_playlist=True):
        """设置播放类型"""
        self.device.play_type = play_type
        # 发布设备配置变更事件
        if self.event_bus:
            self.event_bus.publish(DEVICE_CONFIG_CHANGED)
        if dotts:
            tts = self.config.get_play_type_tts(play_type)
            await self.do_tts(tts)
        if refresh_playlist:
            self.update_playlist()

    async def play_music_list(self, list_name, music_name):
        """播放指定播放列表"""
        self._invalidate_manual_navigation(reason="play_music_list")
        self._last_cmd = "play_music_list"
        self.device.cur_playlist = list_name
        self.update_playlist()
        if not music_name:
            music_name = self.device.playlist2music.get(list_name, "")
        self.log.info(f"开始播放列表{list_name} {music_name}")
        return await self._play(music_name)

    async def stop(self, arg1=""):
        """停止播放。Phase eligibility first, then accept and arbiter submit.

        IDLE/STOPPED → False, zero lifecycle writes (no command bump, no sid
        bump, no arbiter). Active/FAILED/STOPPING → accepted, STOPPING phase,
        arbiter submit. Physical TTS/timer/group work deferred to
        _execute_stop_intent.
        """
        state = self.get_runtime_state()

        # IDLE / STOPPED: zero lifecycle writes, no arbiter
        if state.phase in {PlaybackPhase.IDLE, PlaybackPhase.STOPPED}:
            return False

        # Active phases, FAILED, STOPPING (idempotent) → enter STOPPING
        try:
            self._begin_runtime_stop(updated_at=time.time())
        except TransitionError:
            return False

        self._accept_command(updated_at=time.time())
        self._invalidate_manual_navigation(reason="stop")
        self._last_cmd = "stop"
        self.is_playing = False
        sid = self._bump_play_session(reason="stop")
        token = self._capture_lifecycle_token()

        # Submit to arbiter — return immediately (no await of physical work)
        from xiaomusic.playback.command_arbiter import IntentKind

        arbiter = self._get_or_create_arbiter()
        arbiter.submit(IntentKind.STOP, payload={
            "arg1": arg1,
            "sid": sid,
            "token": token,
        })
        return True

    async def pause(self):
        """暂停播放。Phase eligibility first, then accept and arbiter submit.

        PLAYING→PAUSED, PAUSED idempotent. Other phases (STOPPING, IDLE,
        STOPPED, FAILED, etc.) → False, zero lifecycle writes (no command
        bump, no sid bump, no arbiter). Physical work deferred to
        _execute_pause_intent.
        """
        state = self.get_runtime_state()

        if state.phase == PlaybackPhase.PLAYING:
            self._pause_runtime(updated_at=time.time())
        elif state.phase != PlaybackPhase.PAUSED:
            # STOPPING, IDLE, STOPPED, FAILED, etc.: reject, zero lifecycle writes
            return False
        # PAUSED: idempotent — accept and submit

        self._accept_command(updated_at=time.time())
        self._invalidate_manual_navigation(reason="pause")
        self._last_cmd = "pause"
        self.is_playing = False
        sid = self._bump_play_session(reason="pause")
        token = self._capture_lifecycle_token()

        # Submit to arbiter — return immediately (no await of physical work)
        from xiaomusic.playback.command_arbiter import IntentKind

        arbiter = self._get_or_create_arbiter()
        arbiter.submit(IntentKind.PAUSE, payload={
            "sid": sid,
            "token": token,
        })
        return True

    async def group_force_stop_xiaoai(self, *, fast: bool = False):
        """强制停止组内所有设备"""
        device_id_list = self.xiaomusic.device_manager.get_group_device_id_list(
            self.group_name
        )
        self.log.info(
            f"group_force_stop_xiaoai fast:{fast} {self.group_name} {device_id_list}"
        )
        tasks = [
            self.force_stop_xiaoai(device_id, fast=fast) for device_id in device_id_list
        ]
        results = await asyncio.gather(*tasks)
        self.log.info(f"group_force_stop_xiaoai {device_id_list} {results}")
        return results

    async def stop_after_minute(self, minute: int):
        """定时关机"""
        if self._ensure_playback_tasks().cancel(TaskKind.STOP_TIMER):
            self.log.info("关机定时器已取消")

        async def _do_stop():
            await asyncio.sleep(minute * 60)
            try:
                await self.stop(arg1="notts")
            except Exception as e:
                self.log.exception(f"Execption {e}")

        self._ensure_playback_tasks().start(
            TaskKind.STOP_TIMER,
            TaskGeneration(),
            _do_stop(),
            metadata={"scope": "global_stop_timer", "minute": minute},
        )
        await self.do_tts(f"收到,{minute}分钟后将关机")

    async def cancel_next_timer(self):
        """取消下一首定时器"""
        self.log.info(
            "timer_cancel(session_id=%s, did=%s)",
            self._play_session_id,
            self.did,
        )
        task = self._ensure_playback_tasks().get_task(TaskKind.COMPLETION_NEXT_TIMER)
        if task is not None and task is not asyncio.current_task():
            self._ensure_playback_tasks().cancel(TaskKind.COMPLETION_NEXT_TIMER)
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.log.info(f"下一曲定时器已取消 did: {self.did}")
        else:
            self.log.info(f"下一曲定时器不见了 did: {self.did}")

    async def cancel_group_next_timer(self):
        """取消组内所有设备的下一首定时器"""
        devices = self.xiaomusic.device_manager.get_group_devices(self.group_name)
        self.log.info(f"cancel_group_next_timer {devices}")
        for device in devices.values():
            await device.cancel_next_timer()

    def get_cur_play_list(self):
        """获取当前播放列表名称"""
        return self.device.cur_playlist

    def cancel_all_timer(self):
        """清空所有定时器"""
        self.log.info("in cancel_all_timer")
        registry = self._ensure_playback_tasks()
        for kind in (TaskKind.COMPLETION_NEXT_TIMER, TaskKind.STOP_TIMER, TaskKind.TTS_TIMER):
            if registry.cancel(kind):
                self.log.info("cancel_all_timer kind=%s", kind.value)

    @classmethod
    def dict_clear(cls, d):
        """清空设备字典并取消所有定时器"""
        for key in list(d):
            val = d.pop(key)
            val.cancel_all_timer()

    def find_cur_playlist(self, name):
        """根据当前歌曲匹配歌曲列表

        匹配顺序：
        1. 收藏
        2. 最近新增
        3. 排除（全部,所有歌曲,所有电台）
        4. 所有歌曲
        5. 所有电台
        6. 全部
        """
        music_list = self.xiaomusic.music_library.music_list
        if name in music_list.get("收藏", []):
            return "收藏"
        if name in music_list.get("最近新增", []):
            return "最近新增"
        for list_name, play_list in music_list.items():
            if (list_name not in ["全部", "所有歌曲", "所有电台"]) and (
                name in play_list
            ):
                return list_name
        if name in music_list.get("所有歌曲", []):
            return "所有歌曲"
        if name in music_list.get("所有电台", []):
            return "所有电台"
        return "全部"
