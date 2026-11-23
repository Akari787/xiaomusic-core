"""设备播放控制模块

负责单个设备的播放控制、下载管理等功能。
"""

import asyncio
import time
from typing import TYPE_CHECKING

from xiaomusic.config import Device

if TYPE_CHECKING:
    from xiaomusic.xiaomusic import XiaoMusic
from xiaomusic.const import (
    PLAY_TYPE_SIN,
)
from xiaomusic.events import DEVICE_CONFIG_CHANGED
from xiaomusic.playback.runtime_state import (
    LifecycleToken,
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
    TransitionError,
)
from xiaomusic.playback.task_registry import (
    PlaybackTaskRegistry,
    TaskGeneration,
    TaskKind,
)
from xiaomusic.player.commands import CommandArbiterMixin
from xiaomusic.player.constants import (
    QUEUE_FOLLOW_GRACE_SEC,
    TRACK_END_LEAD_SEC,
)  # noqa: F401  (re-exported)
from xiaomusic.player.external_play import ExternalPlayMixin
from xiaomusic.player.failure import FailurePolicyMixin
from xiaomusic.player.follower import PlaybackFollowerMixin
from xiaomusic.player.library_tasks import LibraryTasksMixin
from xiaomusic.player.lifecycle import PlaybackLifecycleMixin
from xiaomusic.player.playlist import PlaylistMixin
from xiaomusic.player.queue_dispatch import QueueDispatchMixin
from xiaomusic.speaker_client import SpeakerClient
from xiaomusic.utils.text_utils import list2str


class XiaoMusicDevice(
    ExternalPlayMixin,
    PlaybackFollowerMixin,
    QueueDispatchMixin,
    CommandArbiterMixin,
    FailurePolicyMixin,
    LibraryTasksMixin,
    PlaylistMixin,
    PlaybackLifecycleMixin,
):
    """设备播放控制类

    负责单个小爱设备的播放控制，包括：
    - 播放控制（播放、暂停、上一首、下一首）
    - 播放列表管理
    - 下载管理
    - 定时器管理
    - 设备状态管理
    """

    MAX_PLAYING_GRACE_EXTENSIONS = 0  # device may keep reporting playing after EOF/replay
    MAX_UNKNOWN_GRACE_EXTENSIONS = 3  # tolerate transient status-query failures
    # Failures that happen before the new track is dispatched to the speaker.
    # For these, a "speaker is playing" probe reflects the previous track.
    _FAILURE_REASONS_WITHOUT_DISPATCH = frozenset({"preplay_stop_failed"})

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
        self._playlist_session_shuffled = False  # set by explicit context.shuffle, consumed by navigation
        self._manual_nav_lock = asyncio.Lock()
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

        # Queue dispatch (ADR-0007 step 3): the window last sent to the speaker
        # (None = single-track dispatch) and the loop type last set per device.
        self._queue_dispatch: dict | None = None
        self._loop_type_sent: dict[str, int] = {}

        self._last_cmd = None
        self.update_playlist()


    @property
    def did(self):
        """获取设备DID"""
        return self.device.did

    @property
    def speaker(self) -> SpeakerClient:
        """Cloud commands for this device go through SpeakerClient (ADR-0007)."""
        return SpeakerClient(self.auth_manager)

    @property
    def hardware(self):
        """获取设备硬件型号"""
        return self.device.hardware

    # ── lifecycle counter wrappers (delegate to pure runtime_state) ───

    # ── stop runtime wrappers ──────────────────────────────────

    def get_offset_duration(self):
        """获取播放偏移量和总时长——纯查询，无副作用。

        不启动任何 task，不查询设备状态，不触发切歌。
        自动结束判定仅由 set_next_music_timeout 的 expiry gate 负责。
        """
        duration = self._duration
        phase = self.get_runtime_state().phase
        if phase != PlaybackPhase.PLAYING:
            return 0, duration
        # Defense: if _start_time was never set (self-cancellation race),
        # return 0 offset instead of epoch-based value.
        if self._start_time <= 0.1:
            return 0, duration
        offset = time.time() - self._start_time
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
        """Read a status volume without committing runtime cache."""
        if not isinstance(info, dict):
            return self._last_volume
        return self._normalize_volume_value(info.get("volume"), self._last_volume)

    async def _refresh_runtime_volume(self, *, context: str = "") -> int:
        old = self._last_volume
        try:
            volume = await self.get_volume()
        except Exception as exc:
            self.log.warning("refresh_runtime_volume failed ctx=%s err=%s", context, exc)
            return self._last_volume
        self.log.info("refresh_runtime_volume ctx=%s volume=%d", context or "-", volume)
        value = self._remember_volume(volume)
        if value != old:
            self._publish_state_changed()
        return value

    def _set_duration(self, value: float) -> float:
        old = float(getattr(self, "_duration", 0) or 0)
        self._duration = max(float(value or 0), 0.0)
        if self._duration != old:
            self._publish_state_changed()
        return self._duration

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
                        self._set_duration(d)
                        # If original duration probe failed at play start, timer was not set.
                        # Rebuild a next-track timer once duration becomes known.
                        cur_offset, _ = self.get_offset_duration()
                        remaining = (
                            d - max(cur_offset, 0.0) - TRACK_END_LEAD_SEC
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
    # 启用延时器，搜索当前歌曲歌手的其他不在歌单内的歌曲
    async def play_music(self, name):
        """播放音乐（外部接口）"""
        self._invalidate_manual_navigation(reason="play_music")
        return await self._playmusic(name)

    async def play(self, name="", search_key=""):
        """播放歌曲（外部接口）— sync accept, arbiter async.

        Immediately accepts the command (increments command_generation),
        invalidates manual navigation, sets last_cmd, submits PLAY intent
        to the arbiter, and returns True.  Does NOT await download,
        resolve, or physical play.  Does NOT bump sid or change phase
        during acceptance.

        When a STOP barrier is pending or active, the PLAY intent
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
        auto_advance: bool = False,
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
                auto_advance=auto_advance,
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
            auto_advance=auto_advance,
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
        auto_advance: bool = False,
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
            auto_advance=auto_advance,
        )

    # ── command arbiter integration (T04-B) ─────────────────────────────

    # ── manual navigation queue ──────────────────────────────────────

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

    @staticmethod
    def _group_stop_result_succeeded(result) -> bool:
        # An empty device group is a successful no-op. A non-empty group
        # still requires every physical stop result to succeed.
        if result == []:
            return True
        return XiaoMusicDevice._physical_result_succeeded(result)

    async def _execute_group_stop(self, *, fast_stop: bool, sid: int):
        """Await the physical group stop to an exact terminal result.

        This runs inside the DeviceCommandArbiter worker.  It deliberately has
        no deadline: a slow transport delays the following intent, but cannot
        turn an in-flight stop into an unowned child that may overtake play.
        """
        started_at = time.monotonic()
        self.log.info(
            "group_stop_dispatch session_id=%s fast_stop=%s",
            sid,
            fast_stop,
        )
        try:
            results = await self.group_force_stop_xiaoai(fast=fast_stop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.log.exception("group_stop_failed session_id=%s", sid)
            raise RuntimeError("group stop failed before terminal result") from exc
        if not self._group_stop_result_succeeded(results):
            raise RuntimeError("group stop returned a non-success result")
        self.log.info(
            "group_stop_complete session_id=%s elapsed_ms=%.1f",
            sid,
            (time.monotonic() - started_at) * 1000,
        )

    async def _playmusic(
        self,
        name,
        *,
        confirm_start_in_background: bool = False,
        fast_stop: bool = False,
        navigation_generation: int | None = None,
        auto_advance: bool = False,
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
        # Automatic advances never stop first: the dispatch replaces the
        # speaker queue anyway, and player_stop is a cloud round trip measured
        # up to 31 s on the OH2P. Manual commands and retries clear the
        # speaker first.
        skip_preplay_stop = auto_advance
        try:
            if skip_preplay_stop:
                self.log.info("auto_next_skip_preplay_stop name=%s", name)
            else:
                await self._execute_group_stop(fast_stop=fast_stop, sid=sid)
        except asyncio.CancelledError:
            # Cancellation during the preplay stop is a failed play attempt.
            # Cancellation of this playback coroutine itself must still propagate.
            current = asyncio.current_task()
            if current is not None and self._task_cancel_requested(current):
                raise
            try:
                self._report_runtime_failure(
                    reason="preplay_stop_cancelled", updated_at=time.time()
                )
            except TransitionError:
                self.log.info(
                    "preplay_stop_cancelled phase already advanced name=%s phase=%s",
                    name,
                    self.get_runtime_state().phase.value,
                )
            self.log.warning("preplay_stop_cancelled name=%s", name)
            return False
        except Exception:
            self.log.exception("preplay_stop_failed name=%s", name)
            # Route through the failure policy so retry / advance / degrade
            # still happen.  Reporting the runtime failure alone left the
            # server with no retry and no next timer, while the speaker kept
            # repeating its single-item list (observed as "单曲循环").
            await self._handle_play_failure(
                name=name,
                sid=sid,
                reason="preplay_stop_failed",
                token=request_token,
            )
            return False
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
        if all(ele is None for ele in results):
            await self._handle_play_failure(
                name=name,
                sid=sid,
                reason="player_play_failed",
                token=_attempt_token,
            )
            return False

        # ── Dispatch accepted: confirm once the speaker fetches the track ──
        try:
            self._begin_runtime_confirmation(updated_at=time.time())
        except TransitionError:
            self.log.info(
                "playmusic_confirmation_transition_error phase=%s — aborting",
                self.get_runtime_state().phase.value,
            )
            return False

        async def _started() -> None:
            if not self._confirm_runtime_playing_for_attempt(
                token=_attempt_token, updated_at=time.time()
            ):
                return
            await self._mark_play_started(
                name=name,
                sid=sid,
                cur_playlist=cur_playlist,
                token=_attempt_token,
                navigation_generation=navigation_generation,
            )

        await self._confirm_when_fetched(
            name=name, sid=sid, token=_attempt_token, on_started=_started
        )
        return True

    async def _mark_play_started(
        self,
        *,
        name: str,
        sid: int,
        cur_playlist: str,
        token: LifecycleToken,
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
        # 记录歌曲开始播放的时间
        self._start_time = time.time()
        await self._refresh_runtime_volume(context="playmusic_started")
        if not _is_current():
            return

        sec = await self.xiaomusic.music_library.get_music_duration(name)
        if not _is_current():
            return
        # 存储真实歌曲时长
        self._set_duration(sec)
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
        adjusted_sec = sec - TRACK_END_LEAD_SEC - duration_execution_time
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

    async def force_stop_xiaoai(self, device_id, *, fast: bool = False):
        """Stop one device and return an explicit physical success boolean."""
        if fast:
            ret = await self.speaker.stop(device_id, ctx="force_stop_xiaoai_fast")
            self.log.info(
                f"force_stop_xiaoai_fast player_stop device_id:{device_id} ret:{ret}"
            )
            return self._physical_result_succeeded(ret)
        ret = await self.speaker.pause(device_id, ctx="force_stop_xiaoai")
        self.log.info(
            f"force_stop_xiaoai player_pause device_id:{device_id} ret:{ret}"
        )
        if not self._physical_result_succeeded(ret):
            return False
        return await self.stop_if_xiaoai_is_playing(device_id)

    async def get_if_xiaoai_is_playing(self, device_id=None):
        """检查小爱是否正在播放"""
        target_device_id = device_id or self.device_id
        playing_info = await self.speaker.status_raw(
            target_device_id, ctx="get_if_xiaoai_is_playing"
        )
        self.log.info(playing_info)
        # WTF xiaomi api
        is_playing = (
            SpeakerClient.parse_status(playing_info).get("status", -1) == 1
        )
        return is_playing

    async def stop_if_xiaoai_is_playing(self, device_id):
        """Send player_stop. No status probe first: the OH2P keeps reporting
        status=1 while paused, so the probe only added a cloud round trip."""
        ret = await self.speaker.stop(device_id, ctx="stop_if_xiaoai_is_playing")
        self.log.info(
            f"stop_if_xiaoai_is_playing player_stop device_id:{device_id} ret:{ret}"
        )
        return self._physical_result_succeeded(ret)

    # ── queue dispatch (ADR-0007 step 3) ────────────────────────────────

    async def set_next_music_timeout(
        self, sec, token: LifecycleToken | None = None
    ):
        """设置下一首歌曲的播放定时器。

        逐首下发时，到期即切到下一首（不查询音箱状态）。队列下发时
        （ADR-0007）音箱自己切歌，这里只做兜底：推迟到预期结束后
        QUEUE_FOLLOW_GRACE_SEC 才触发，音箱自己循环一首时不设定时器。
        """
        if token is None:
            token = self._capture_lifecycle_token()
        sid = self._play_session_id

        queue = self._active_queue()
        if queue is not None and queue["timer"] == "none":
            await self.cancel_next_timer()
            self.log.info("timer_skipped_speaker_loops(session_id=%s, did=%s)", sid, self.did)
            return
        if queue is not None and queue["timer"] == "grace":
            sec = sec + TRACK_END_LEAD_SEC + QUEUE_FOLLOW_GRACE_SEC

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

                # The duration timer is authoritative. A status probe here
                # never changed the decision (the speaker reports "playing"
                # while it loops its one-item queue) and only delayed the
                # switch by one cloud round trip, or 3-9 s more on "not
                # playing"/probe errors -- long enough for the speaker to
                # audibly restart the finished track.
                if not _token_is_current("advance"):
                    return
                from xiaomusic.playback.command_arbiter import IntentKind
                self._submit_auto_retry(
                    IntentKind.AUTO_NEXT,
                    source_token=token,
                    sid=sid,
                    reason="timer_track_end",
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

    async def set_volume(self, volume: int):
        """设置音量 and publish only after the physical command succeeds."""
        volume = self._normalize_volume_value(volume, self._last_volume)
        self.log.info(f"set_volume.  did: {self.did} volume: {volume}")
        try:
            ret = await self.speaker.set_volume(self.device_id, volume)
        except Exception as e:
            self.log.exception(f"Execption {e}")
            return False
        if ret is None or ret is False:
            return False
        old = self._last_volume
        self._last_volume = volume
        if volume != old:
            self._publish_state_changed()
        return True

    async def get_volume(self):
        """Read volume without committing the runtime cache."""
        fallback = self._last_volume
        try:
            playing_info = await self.speaker.status_raw(self.device_id, ctx="get_volume")
            self.log.info(f"get_volume. playing_info:{playing_info}")
            volume = SpeakerClient.parse_status(playing_info).get("volume", fallback)
        except Exception as e:
            self.log.warning(f"Execption {e}")
            return fallback
        volume = self._normalize_volume_value(volume, fallback)
        self.log.info("get_volume. volume:%d", volume)
        return volume

    async def get_player_status(self):
        """获取完整播放状态"""
        try:
            playing_info = await self.speaker.status_raw(
                self.device_id, ctx="get_player_status"
            )
            self.log.info(f"get_player_status. playing_info:{playing_info}")
            info = SpeakerClient.parse_status(playing_info)
            return info
        except Exception as e:
            self.log.warning(f"Execption {e}")
            # A failed observation is not evidence that the player is stopped
            # or muted. Keep the fact unknown so probes cannot release a stop
            # barrier from a synthetic status=0 response.
            return {
                "status": None,
                "volume": None,
                "probe_ok": False,
                "probe_error": str(e)[:200],
            }
        return info

    async def set_play_type(self, play_type, refresh_playlist=True):
        """设置播放类型"""
        self.device.play_type = play_type
        # 发布设备配置变更事件
        if self.event_bus:
            self.event_bus.publish(DEVICE_CONFIG_CHANGED)
        if refresh_playlist:
            self.update_playlist()
        await self._on_play_type_changed_for_queue(play_type)

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
        self.log.info("%s 分钟后停止播放", minute)

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

    def cancel_all_timer(self):
        """清空所有定时器"""
        self.log.info("in cancel_all_timer")
        registry = self._ensure_playback_tasks()
        for kind in (TaskKind.COMPLETION_NEXT_TIMER, TaskKind.STOP_TIMER):
            if registry.cancel(kind):
                self.log.info("cancel_all_timer kind=%s", kind.value)

    @classmethod
    def dict_clear(cls, d):
        """清空设备字典并取消所有定时器"""
        for key in list(d):
            val = d.pop(key)
            val.cancel_all_timer()
