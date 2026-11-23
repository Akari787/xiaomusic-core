"""v1 API play entry: resolved URL plus optional playlist context.

Mixin of :class:`xiaomusic.device_player.XiaoMusicDevice`; relies on the
attributes it sets up in ``__init__``.
"""

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from xiaomusic.playback.runtime_state import (
    LifecycleToken,
    PlaybackPhase,
    TrackReference,
    TransitionError,
)
from xiaomusic.player.constants import (
    TRACK_END_LEAD_SEC,
)


class ExternalPlayMixin:
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

        # External URL playback must use the same hard preplay fence as the
        # regular playback path. Otherwise a stale/late stop can terminate the
        # newly dispatched direct URL track.
        try:
            await self._execute_group_stop(
                fast_stop=True,
                sid=self._play_session_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._is_lifecycle_token_stale(request_token):
                self._report_executor_failure(
                    reason="preplay_stop_failed",
                    sid=self._play_session_id,
                    token=request_token,
                )
            raise
        if self._is_lifecycle_token_stale(request_token):
            return

        # Step 2: external dispatch phase
        if not self._begin_runtime_external_dispatch_for_token(request_token):
            return

        fallback_plan = internal_ctx.get("_device_external_fallback")
        fallback_url = ""
        if isinstance(fallback_plan, dict):
            fallback_url = str(fallback_plan.get("final_url") or "").strip()

        # Each physical dispatch owns exactly one attempt increment. Keep
        # direct and fallback in this worker so q/c/sid remain unchanged.
        attempt_token = self._start_track_attempt(updated_at=time.time())
        failure_token = attempt_token
        try:
            try:
                queue_name = self._external_queue_name()
                if queue_name:
                    ret = await self.group_player_play(url, queue_name)
                else:
                    ret = await self.group_player_play(url)
            except asyncio.CancelledError:
                raise
            except Exception:
                if not fallback_url or self._is_lifecycle_token_stale(attempt_token):
                    raise
                fallback_token = self._start_track_attempt(updated_at=time.time())
                failure_token = fallback_token
                fallback_ret = await self.group_player_play(fallback_url)
                if self._is_lifecycle_token_stale(fallback_token):
                    self.log.info(
                        "lifecycle_stale external_play_executor did=%s", self.did
                    )
                    return
                if self._external_play_dispatch_succeeded(fallback_ret):
                    await self._external_started_when_fetched(internal_ctx, resolved, fallback_token)
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
                await self._external_started_when_fetched(internal_ctx, resolved, attempt_token)
                return

            if not fallback_url:
                raise RuntimeError(
                    "external play dispatch failed, no fallback configured"
                ) from None

            fallback_token = self._start_track_attempt(updated_at=time.time())
            failure_token = fallback_token
            fallback_ret = await self.group_player_play(fallback_url)
            if self._is_lifecycle_token_stale(fallback_token):
                self.log.info(
                    "lifecycle_stale external_play_executor did=%s", self.did
                )
                return
            if self._external_play_dispatch_succeeded(fallback_ret):
                await self._external_started_when_fetched(internal_ctx, resolved, fallback_token)
                return
            raise RuntimeError(
                "direct and fallback external playback failed"
            ) from None
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._is_lifecycle_token_stale(failure_token):
                self._report_executor_failure(
                    reason="external_play_failed",
                    sid=self._play_session_id,
                    token=failure_token,
                )
            raise

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
                ExternalPlayMixin._external_play_dispatch_succeeded(element)
                for element in result
            )
        return result is not None and result is not False

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

    async def _external_started_when_fetched(self, context, resolved, token) -> None:
        title = str(
            (resolved or {}).get("title") or (context or {}).get("title") or self.device.cur_music or ""
        )

        async def _started() -> None:
            await self.on_external_url_play_started(context=context, resolved=resolved, token=token)

        await self._confirm_when_fetched(
            name=title, sid=self._play_session_id, token=token, on_started=_started
        )

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

        self._set_duration(sec)

        if self._duration <= 0.1:
            self._start_duration_probe(title, sid, token=token)
            self.log.info(f"【{title or 'external_url'}】不会设置下一首歌的定时器")
            return True

        # Anchor at dispatch return like the legacy path: subtract the time
        # already spent since then (volume refresh, duration lookup).
        elapsed = max(time.time() - self._start_time, 0.0)
        adjusted_sec = max(self._duration - TRACK_END_LEAD_SEC - elapsed, 0.1)
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
