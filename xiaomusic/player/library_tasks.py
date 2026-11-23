"""Downloads and the auto-add-song timer.

Mixin of :class:`xiaomusic.device_player.XiaoMusicDevice`; relies on the
attributes it sets up in ``__init__``.
"""

import asyncio
import os
import time
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from xiaomusic.const import (
    PLAY_TYPE_ALL,
)
from xiaomusic.download_result import DownloadResult
from xiaomusic.playback.task_registry import (
    TaskKind,
)
from xiaomusic.utils.file_utils import chmodfile


class LibraryTasksMixin:
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
            self.log.info("本地不存在歌曲 %s", name)
            return False

        # _play 的行为：检查配置决定是否下载
        if self.config.disable_download:
            self.log.info("本地不存在歌曲 %s，下载已关闭", name)
            return False

        # 下载歌曲
        await self.download(search_key, name)
        # 把文件插入到播放列表里
        await self.add_download_music(name)
        return True

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
