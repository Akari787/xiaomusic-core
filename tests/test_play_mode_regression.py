"""回归测试：播放模式 (get_music / _play_prev / _handle_play_failure) 行为验证

覆盖：
1. 单曲循环 (PLAY_TYPE_ONE) get_music("next") 重复当前歌曲
2. 顺序播放 (PLAY_TYPE_SEQ) get_music("next") 到末尾返回空
3. 全部循环 (PLAY_TYPE_ALL) get_music("next") 到末尾回绕
4. 随机播放 (PLAY_TYPE_RND) get_music("next") 到末尾回绕
5. 单曲播放 (PLAY_TYPE_SIN) get_music("next") 到末尾回绕（timer 层处理停止）
6. _play_prev 简化后行为不变
7. _handle_play_failure SIN 模式停止而非前进
"""

import asyncio
import sys
import types

import pytest

if "miservice" not in sys.modules:
    sys.modules["miservice"] = types.SimpleNamespace(
        miio_command=lambda *args, **kwargs: None
    )

if "opencc" not in sys.modules:

    class _OpenCC:
        def __init__(self, *_args, **_kwargs):
            pass

        def convert(self, text):
            return text

    sys.modules["opencc"] = types.SimpleNamespace(OpenCC=_OpenCC)

from xiaomusic.const import (
    PLAY_TYPE_ALL,
    PLAY_TYPE_ONE,
    PLAY_TYPE_RND,
    PLAY_TYPE_SEQ,
    PLAY_TYPE_SIN,
)
from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.playback.runtime_state import PlaybackPhase, PlaybackRuntimeState

# ── helpers ──────────────────────────────────────────────────────────────

def _make_playlist_items(names: list[str]) -> list[dict[str, str]]:
    """用简单名称列表构造 _play_list_items。"""
    return [{"display_name": n, "entity_id": "", "item_id": ""} for n in names]


def _make_device(play_type: int, playlist: list[str], cur_music: str = ""):
    """构造一个最简 XiaoMusicDevice 用于 get_music 测试。"""
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d._runtime_state = PlaybackRuntimeState()
    d.log = types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        debug=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    d.device = types.SimpleNamespace(
        play_type=play_type,
        cur_music=cur_music,
        current_display_name=cur_music,
        current_entity_id="",
        current_playlist_item_id="",
    )
    d._play_list_items = _make_playlist_items(playlist)
    # 初始化 _current_index 为 -1，让 get_music 走搜索路径
    d._current_index = -1
    d.get_cur_music = lambda: d.device.cur_music

    # 模拟 music_library.is_music_exist 总是返回 True
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda name: True)
    )
    return d


# ── get_music("next") ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "play_type, expected",
    [
        (PLAY_TYPE_ONE, "song-b"),  # 单曲循环：重复当前
        (PLAY_TYPE_ALL, "song-c"),  # 全部循环：index+1
        (PLAY_TYPE_SEQ, "song-c"),  # 顺序播放：index+1
        (PLAY_TYPE_RND, "song-c"),  # 随机播放：index+1
        (PLAY_TYPE_SIN, "song-c"),  # 单曲播放：index+1（用户主动切下一首）
    ],
)
def test_get_music_next_from_middle(play_type, expected):
    """从列表中间位置取 next，各模式行为正确。"""
    d = _make_device(play_type, ["song-a", "song-b", "song-c"], cur_music="song-b")
    name = d.get_music("next")
    assert name == expected


def test_get_music_next_one_mode_repeats_current():
    """单曲循环：cur_music=song-b，next 应返回 song-b（不前进）。"""
    d = _make_device(PLAY_TYPE_ONE, ["song-a", "song-b", "song-c"], cur_music="song-b")
    assert d.get_music("next") == "song-b"


def test_get_music_next_one_mode_from_last_repeats_current():
    """单曲循环：cur_music=song-c（最后一首），next 仍返回 song-c。"""
    d = _make_device(PLAY_TYPE_ONE, ["song-a", "song-b", "song-c"], cur_music="song-c")
    assert d.get_music("next") == "song-c"


def test_get_music_next_one_mode_single_song():
    """单曲循环：只有一首歌时 next 返回同一首。"""
    d = _make_device(PLAY_TYPE_ONE, ["song-a"], cur_music="song-a")
    assert d.get_music("next") == "song-a"


def test_get_music_next_seq_stops_at_end():
    """顺序播放：最后一首的 next 返回空字符串。"""
    d = _make_device(PLAY_TYPE_SEQ, ["song-a", "song-b", "song-c"], cur_music="song-c")
    assert d.get_music("next") == ""


def test_get_music_next_seq_does_not_stop_at_first():
    """顺序播放：第一首的 next 正常前进到第二首。"""
    d = _make_device(PLAY_TYPE_SEQ, ["song-a", "song-b", "song-c"], cur_music="song-a")
    assert d.get_music("next") == "song-b"


def test_get_music_next_all_wraps_from_last():
    """全部循环：最后一首的 next 回绕到第一首。"""
    d = _make_device(PLAY_TYPE_ALL, ["song-a", "song-b", "song-c"], cur_music="song-c")
    assert d.get_music("next") == "song-a"


def test_get_music_next_rnd_wraps_from_last():
    """随机播放：最后一首的 next 回绕到第一首（列表已打乱后的位置）。"""
    d = _make_device(PLAY_TYPE_RND, ["song-a", "song-b", "song-c"], cur_music="song-c")
    assert d.get_music("next") == "song-a"


def test_get_music_next_sin_wraps_from_last():
    """单曲播放：用户主动切下一首时从末尾回绕（timer 层负责停止）。"""
    d = _make_device(PLAY_TYPE_SIN, ["song-a", "song-b", "song-c"], cur_music="song-c")
    assert d.get_music("next") == "song-a"


def test_get_music_next_empty_list():
    """空列表：next 返回空字符串。"""
    d = _make_device(PLAY_TYPE_ALL, [], cur_music="")
    assert d.get_music("next") == ""


# ── get_music("prev") ────────────────────────────────────────────────────

def test_get_music_prev_from_middle():
    """从列表中间位置取 prev。"""
    d = _make_device(PLAY_TYPE_ALL, ["song-a", "song-b", "song-c"], cur_music="song-b")
    assert d.get_music("prev") == "song-a"


def test_get_music_prev_from_first_wraps():
    """第一首的 prev 回绕到最后一首（所有模式统一行为）。"""
    d = _make_device(PLAY_TYPE_ALL, ["song-a", "song-b", "song-c"], cur_music="song-a")
    assert d.get_music("prev") == "song-c"


def test_get_music_prev_one_mode_single_song():
    """单曲循环只有一首：prev 返回同一首。"""
    d = _make_device(PLAY_TYPE_ONE, ["song-a"], cur_music="song-a")
    assert d.get_music("prev") == "song-a"


# ── _play_prev 简化后行为不变 ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manual_play_prev_goes_to_prev_song():
    """用户手动点击上一首：切到真实的上一首。"""
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = types.SimpleNamespace(info=lambda *args, **kwargs: None)
    d.device = types.SimpleNamespace(play_type=PLAY_TYPE_ALL)
    d._play_list_items = _make_playlist_items(["song-a", "song-b", "song-c"])
    d._find_playlist_index = lambda display_name="": (
        1 if display_name == "song-b" else -1
    )
    d.get_cur_music = lambda: "song-b"
    d.get_prev_music = lambda: "song-a"

    played: list[str] = []

    async def _play(name="", search_key="", preserve_playlist=False,  # noqa: ARG001
                    confirm_start_in_background=False, fast_stop=False, **kw):  # noqa: ARG001
        played.append(name)
        return True

    d._play = _play
    d._stage_playlist_navigation_transition = lambda name, *, reason: None

    await d._play_prev(manual=True)
    assert played == ["song-a"]


@pytest.mark.asyncio
async def test_manual_play_prev_with_empty_name():
    """cur_music 为空时 fallback 到 prev。"""
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = types.SimpleNamespace(info=lambda *args, **kwargs: None)
    d.device = types.SimpleNamespace(play_type=PLAY_TYPE_ALL)
    d._play_list_items = _make_playlist_items(["song-a", "song-b"])
    d._find_playlist_index = lambda display_name="": -1
    d.get_cur_music = lambda: ""
    d.get_prev_music = lambda: "song-b"

    played: list[str] = []

    async def _play(name="", search_key="", preserve_playlist=False,  # noqa: ARG001
                    confirm_start_in_background=False, fast_stop=False, **kw):  # noqa: ARG001
        played.append(name)
        return True

    d._play = _play
    d._stage_playlist_navigation_transition = lambda name, *, reason: None

    await d._play_prev(manual=True)
    assert played == ["song-b"]


@pytest.mark.asyncio
async def test_manual_play_next_advances_even_in_one_mode():
    """手动 play_next 在单曲循环模式下仍应切到下一首（不重复当前）。"""
    d = _make_device(PLAY_TYPE_ONE, ["song-a", "song-b"], cur_music="song-a")
    d.get_next_music = lambda *, skip_one_repeat=False: d.get_music("next", skip_one_repeat=skip_one_repeat)

    played: list[str] = []

    async def _play(name="", search_key="", preserve_playlist=False,  # noqa: ARG001
                    confirm_start_in_background=False, fast_stop=False, **kw):  # noqa: ARG001
        played.append(name)

    d._play = _play
    d._stage_playlist_navigation_transition = lambda name, *, reason: None

    await d._play_next(manual=True)
    # 手动切下一首 → 应前进到 song-b（不是重复 song-a）
    assert played == ["song-b"]


@pytest.mark.asyncio
async def test_auto_next_in_one_mode_repeats_current():
    """单曲循环：自动切歌应重复当前歌曲（不是前进）。"""
    d = _make_device(PLAY_TYPE_ONE, ["song-a", "song-b", "song-c"], cur_music="song-b")
    d.get_next_music = lambda *, skip_one_repeat=False: d.get_music("next", skip_one_repeat=skip_one_repeat)

    played: list[str] = []

    async def _play(name="", search_key="", preserve_playlist=False,  # noqa: ARG001
                    confirm_start_in_background=False, fast_stop=False, **kw):  # noqa: ARG001
        played.append(name)

    d._play = _play
    d._stage_playlist_navigation_transition = lambda name, *, reason: None

    await d._play_next(manual=False)  # auto-next
    # 自动切歌 → 应重复 song-b（单曲循环的核心行为）
    assert played == ["song-b"]


@pytest.mark.asyncio
async def test_auto_next_in_seq_mode_stops_at_end():
    """顺序播放：自动切歌到末尾时 get_next_music 返回空，_play_next 返回 False。"""
    d = _make_device(PLAY_TYPE_SEQ, ["song-a", "song-b", "song-c"], cur_music="song-c")
    d.get_next_music = lambda *, skip_one_repeat=False: d.get_music("next", skip_one_repeat=skip_one_repeat)
    d._stage_playlist_navigation_transition = lambda name, *, reason: None

    # auto-next 应在发现 name 为空时返回 False 而不调用 _play
    result = await d._play_next(manual=False)
    assert result is False


@pytest.mark.asyncio
async def test_auto_next_in_all_mode_wraps():
    """全部循环：自动切歌到末尾时回绕到第一首。"""
    d = _make_device(PLAY_TYPE_ALL, ["song-a", "song-b", "song-c"], cur_music="song-c")
    d.get_next_music = lambda *, skip_one_repeat=False: d.get_music("next", skip_one_repeat=skip_one_repeat)

    played: list[str] = []

    async def _play(name="", search_key="", preserve_playlist=False,  # noqa: ARG001
                    confirm_start_in_background=False, fast_stop=False, **kw):  # noqa: ARG001
        played.append(name)

    d._play = _play
    d._stage_playlist_navigation_transition = lambda name, *, reason: None

    await d._play_next(manual=False)
    assert played == ["song-a"]


# ── _handle_play_failure 模式相关 ─────────────────────────────────────────


async def _async_bool(value: bool) -> bool:
    return value


def _prepare_failure_device(play_type: int, *, sid: int, count: int = 0):
    d = _make_device(play_type, ["song-a", "song-b", "song-c"], cur_music="song-b")
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.DISPATCHING)
    d._command_arbiter = None
    d.is_playing = True
    d._degraded = False
    d._degraded_notified = False
    d._play_session_id = sid
    d._play_fail_first_ts = 0.0
    d._play_fail_last_reason = ""
    d._play_failed_cnt = count
    gate = asyncio.Event()

    async def _wait_backoff(_delay):
        await asyncio.wait_for(gate.wait(), timeout=5.0)

    d._wait_failure_retry_backoff = _wait_backoff
    return d, gate


async def _finish_failure_retry(d, gate):
    task = d._failure_retry_task
    assert task is not None
    gate.set()
    await asyncio.wait_for(task, timeout=5.0)
    await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_handle_play_failure_sin_mode_stops_not_advances():
    """SIN 模式的失败策略通过 public stop，不提交 RETRY。"""
    d, _gate = _prepare_failure_device(PLAY_TYPE_SIN, sid=1)
    stopped: list[str] = []
    submits: list[dict] = []

    async def _stop(arg1=""):
        stopped.append(arg1)
        return True

    d.stop = _stop
    d._submit_auto_retry = lambda *_args, **kwargs: submits.append(kwargs)
    token = d._capture_lifecycle_token()
    await d._handle_play_failure(
        name="song-a", sid=1, reason="player_play_failed", token=token
    )

    assert stopped == ["notts"]
    assert submits == []


@pytest.mark.asyncio
async def test_handle_play_failure_one_mode_retries_same_song():
    """ONE 模式仍提交 RETRY_SAME，由 arbiter 执行物理重试。"""
    d, gate = _prepare_failure_device(PLAY_TYPE_ONE, sid=2)
    submits: list[dict] = []
    d._submit_auto_retry = lambda *_args, **kwargs: submits.append(kwargs)
    d.get_if_xiaoai_is_playing = lambda: _async_bool(False)

    token = d._capture_lifecycle_token()
    await d._handle_play_failure(
        name="song-a", sid=2, reason="player_play_failed", token=token
    )
    await _finish_failure_retry(d, gate)

    assert len(submits) == 1
    assert submits[0]["payload"] == {"name": "song-a", "retry_same_song": True}


@pytest.mark.asyncio
async def test_handle_play_failure_skips_retry_when_speaker_already_playing():
    """云端已确认播放时，retry runner 正常结束且不提交。"""
    d, gate = _prepare_failure_device(PLAY_TYPE_ALL, sid=3)
    submits: list[dict] = []
    d._submit_auto_retry = lambda *_args, **kwargs: submits.append(kwargs)
    d.get_if_xiaoai_is_playing = lambda: _async_bool(True)

    token = d._capture_lifecycle_token()
    await d._handle_play_failure(
        name="song-b", sid=3, reason="play_start_not_confirmed", token=token
    )
    await _finish_failure_retry(d, gate)

    assert submits == []


@pytest.mark.asyncio
async def test_handle_play_failure_retries_same_song_with_sync_stop():
    """第二次失败仍提交 RETRY_SAME，不直接调用 `_play`."""
    d, gate = _prepare_failure_device(PLAY_TYPE_ALL, sid=4, count=1)
    submits: list[dict] = []
    d._submit_auto_retry = lambda *_args, **kwargs: submits.append(kwargs)
    d.get_if_xiaoai_is_playing = lambda: _async_bool(False)

    token = d._capture_lifecycle_token()
    await d._handle_play_failure(
        name="song-b", sid=4, reason="play_start_not_confirmed", token=token
    )
    await _finish_failure_retry(d, gate)

    assert len(submits) == 1
    assert submits[0]["payload"] == {"name": "song-b", "retry_same_song": True}


@pytest.mark.asyncio
async def test_handle_play_failure_falls_through_to_play_next_after_same_song_exhausted():
    """第三次失败后提交 RETRY_NEXT，不从 runner 直达 `_play_next`."""
    d, gate = _prepare_failure_device(PLAY_TYPE_ALL, sid=5, count=2)
    submits: list[dict] = []
    d._submit_auto_retry = lambda *_args, **kwargs: submits.append(kwargs)
    d.get_if_xiaoai_is_playing = lambda: _async_bool(False)

    token = d._capture_lifecycle_token()
    await d._handle_play_failure(
        name="song-b", sid=5, reason="play_start_not_confirmed", token=token
    )
    await _finish_failure_retry(d, gate)

    assert len(submits) == 1
    assert submits[0]["payload"] == {"name": "song-b", "retry_same_song": False}
