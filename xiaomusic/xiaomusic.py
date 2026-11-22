#!/usr/bin/env python3
import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler

from xiaomusic import __version__
from xiaomusic.auth import AuthManager
from xiaomusic.config import Config
from xiaomusic.config_manager import ConfigManager
from xiaomusic.const import (
    PLAY_TYPE_ALL,
    PLAY_TYPE_ONE,
    PLAY_TYPE_RND,
    PLAY_TYPE_SEQ,
    PLAY_TYPE_SIN,
)
from xiaomusic.device_manager import DeviceManager
from xiaomusic.diagnostics import build_startup_diagnostics
from xiaomusic.events import (
    CONFIG_CHANGED,
    DEVICE_CONFIG_CHANGED,
    EventBus,
)
from xiaomusic.file_watcher import FileWatcherManager
from xiaomusic.music_library import MusicLibrary
from xiaomusic.playback.link_strategy import LinkPlaybackStrategy
from xiaomusic.security.token_store import TokenStore
from xiaomusic.services.online_music_service import OnlineMusicService
from xiaomusic.stream_gateway import StreamGateway
from xiaomusic.utils.file_utils import clean_temp_dir
from xiaomusic.utils.system_utils import deepcopy_data_no_sensitive_info
from xiaomusic.utils.text_utils import chinese_to_number


class XiaoMusic:
    def __init__(self, config: Config):
        self.config = config

        # 初始化事件总线
        self.event_bus = EventBus()

        # 初始化认证管理器（延迟初始化部分属性）
        self.auth_manager = None

        # 初始化设备管理器（延迟初始化）
        self.device_manager = None

        self.running_task = []
        self.keepalive_task = None
        self.jellyfin_sync_task = None
        self.temp_clean_task = None

        # 音乐库管理器（延迟初始化，在配置准备好之后）
        self.music_library = None

        # 配置管理器（延迟初始化）
        self.config_manager = None

        # 初始化配置
        self.init_config()

        # 初始化文件监控管理器
        self.file_watcher = None

        # Coalesced refresh task for large libraries
        self._library_refresh_task = None
        self._library_refresh_pending = False

        # 初始化在线音乐服务（延迟初始化，在 js_plugin_manager 之后）
        self.online_music_service = None

        # Diagnostics
        self.startup_diagnostics = None

        # 初始化日志
        self.setup_logger()

        # Token store (env precedence + optional persistence)
        self.token_store = TokenStore(self.config, self.log)

        # 初始化 JS 插件管理器
        try:
            from xiaomusic.managers.js_plugin_manager import JSPluginManager

            self.js_plugin_manager = JSPluginManager(self)
            self.log.info("JS Plugin Manager initialized successfully")
        except Exception as e:
            self.log.error(f"Failed to initialize JS Plugin Manager: {e}")
            self.js_plugin_manager = None

        # 初始化 JS 插件适配器
        try:
            from xiaomusic.js_adapter import JSAdapter

            self.js_adapter = JSAdapter(self)
            self.log.info("JS Adapter initialized successfully")
        except Exception as e:
            self.log.error(f"Failed to initialize JS Adapter: {e}")

        # 初始化配置管理器（在日志准备好之后）
        self.config_manager = ConfigManager(
            config=self.config,
            log=self.log,
        )

        # 尝试从设置里加载配置
        config_data = self.config_manager.try_init_setting()
        if config_data:
            self.update_config_from_setting(config_data)

        # 初始化音乐库管理器（在配置准备好之后）
        self.music_library = MusicLibrary(
            config=self.config,
            log=self.log,
            event_bus=self.event_bus,
        )
        self.link_playback_strategy = LinkPlaybackStrategy(
            music_library=self.music_library,
            log=self.log,
        )

        # 启动时重新生成一次播放列表
        self.music_library.gen_all_music_list()

        # 初始化在线音乐服务（在 js_plugin_manager 准备好之后）
        self.online_music_service = OnlineMusicService(
            log=self.log,
            js_plugin_manager=self.js_plugin_manager,
            xiaomusic_instance=self,  # 传递xiaomusic实例
        )

        # Stable signed stream addresses for the speaker (ADR-0007).
        self.stream_gateway = StreamGateway(
            self.config,
            self.log,
            music_library_getter=lambda: self.music_library,
            jellyfin_client_getter=lambda: getattr(
                self.online_music_service, "jellyfin_client", None
            ),
        )

        # 初始化设备管理器（在配置准备好之后）
        self.device_manager = DeviceManager(
            config=self.config,
            log=self.log,
            xiaomusic=self,
        )

        # 初始化认证管理器（在配置和设备管理器准备好之后）
        self.auth_manager = AuthManager(
            config=self.config,
            log=self.log,
            device_manager=self.device_manager,
            token_store=self.token_store,
        )

        # 订阅配置变更事件
        self.event_bus.subscribe(CONFIG_CHANGED, self.save_cur_config)
        self.event_bus.subscribe(DEVICE_CONFIG_CHANGED, self.save_cur_config)

        debug_config = deepcopy_data_no_sensitive_info(self.config)
        self.log.info(f"Startup OK. {debug_config}")

        if self.config.conf_path == self.config.music_path:
            self.log.warning("配置文件目录和音乐目录建议设置为不同的目录")

    def init_config(self):
        if not os.path.exists(self.config.music_path):
            os.makedirs(self.config.music_path)

        if not os.path.exists(self.config.download_path):
            os.makedirs(self.config.download_path)

    def setup_logger(self):
        log_format = f"%(asctime)s [{__version__}] [%(levelname)s] %(filename)s:%(lineno)d: %(message)s"
        date_format = "[%Y-%m-%d %H:%M:%S]"
        if getattr(self.config, "log_redact", True):
            from xiaomusic.security.logging import RedactingLogFormatter

            formatter = RedactingLogFormatter(fmt=log_format, datefmt=date_format)
        else:
            formatter = logging.Formatter(fmt=log_format, datefmt=date_format)

        self.log = logging.getLogger("xiaomusic")
        self.log.handlers.clear()  # 清除已有的 handlers
        self.log.setLevel(logging.DEBUG if self.config.verbose else logging.INFO)

        # 文件日志处理器
        log_file = self.config.log_file
        log_path = os.path.dirname(log_file)
        if log_path and not os.path.exists(log_path):
            os.makedirs(log_path)
        if os.path.exists(log_file):
            try:
                os.remove(log_file)
            except Exception as e:
                print(f"无法删除旧日志文件: {log_file} {e}")

        # File logging may fail in hardened containers (read_only rootfs).
        log_file = self.config.log_file
        try:
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,
                backupCount=1,
                encoding="utf-8",
            )
        except OSError:
            # Fallback to tmpfs.
            log_file = "/tmp/xiaomusic.log.txt"
            self.config.log_file = log_file
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,
                backupCount=1,
                encoding="utf-8",
            )
            self.log.warning("log file not writable, fallback to %s", log_file)

        file_handler.stream.flush()
        file_handler.setFormatter(formatter)
        self.log.addHandler(file_handler)

        # 控制台日志处理器
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        self.log.addHandler(console_handler)

    async def jellyfin_sync_loop(self):
        """Periodically re-import Jellyfin playlists so edits made in Jellyfin
        show up without restarting. Interval <= 0 disables it."""
        while True:
            minutes = int(getattr(self.config, "jellyfin_sync_interval_minutes", 30) or 0)
            if minutes <= 0:
                await asyncio.sleep(300)
                continue
            await asyncio.sleep(minutes * 60)
            await self.sync_jellyfin_music_lists_if_needed()

    async def temp_clean_loop(self):
        """Empty the temp directory once a day."""
        while True:
            await asyncio.sleep(24 * 3600)
            await clean_temp_dir(self.config)

    def start_file_watch(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if not self.file_watcher:
            self.file_watcher = FileWatcherManager(
                config=self.config,
                log=self.log,
                on_change_callback=self._on_file_change,
            )
        self.file_watcher.start(loop)

    def _on_file_change(self):
        # FileWatcher already debounces filesystem events; here we further
        # coalesce expensive refresh work into a single background task.
        self.log.info("检测到目录音乐文件变化，已加入刷新队列。")
        self._queue_library_refresh(reason="file_watch")

    def _queue_library_refresh(self, reason: str = ""):
        self._library_refresh_pending = True
        if (
            self._library_refresh_task is not None
            and not self._library_refresh_task.done()
        ):
            return

        async def _run():
            while True:
                self._library_refresh_pending = False
                try:
                    # Heavy scans should not block the event loop.
                    await asyncio.to_thread(self.music_library.gen_all_music_list)
                    self.update_all_playlist()
                    self.log.info("library refresh ok (%s)", reason)
                except Exception as e:
                    self.log.exception("library refresh failed (%s): %s", reason, e)

                if not self._library_refresh_pending:
                    return

        self._library_refresh_task = asyncio.create_task(_run())

    def stop_file_watch(self):
        if self.file_watcher:
            self.file_watcher.stop()

    async def run_forever(self):
        self.log.info("run_forever start")
        try:
            self.startup_diagnostics = build_startup_diagnostics(self.config)
            if not self.startup_diagnostics.ok:
                self.log.warning(
                    "startup self-check failed: %s", self.startup_diagnostics
                )
        except Exception as e:
            self.log.exception("startup self-check error: %s", e)
        self.music_library.try_gen_all_music_tag()  # 事件循环开始后调用一次
        # 取配置 enable_file_watch 循环开始时调用一次，控制目录监控开关
        if self.config.enable_file_watch:
            self.start_file_watch()
        self.temp_clean_task = asyncio.create_task(self.temp_clean_loop())
        # 先完成认证与设备初始化，避免 Jellyfin 同步耗时导致 runtime_auth_ready 长时间为 false。
        await self.auth_manager.init_all_data()
        await self.sync_jellyfin_music_lists_if_needed()
        self.keepalive_task = asyncio.create_task(self.auth_manager.keepalive_loop())
        self.jellyfin_sync_task = asyncio.create_task(self.jellyfin_sync_loop())
        try:
            await asyncio.Event().wait()  # run until cancelled by the app lifespan
        finally:
            if self.temp_clean_task is not None:
                self.temp_clean_task.cancel()
                await asyncio.gather(self.temp_clean_task, return_exceptions=True)
                self.temp_clean_task = None
            if self.keepalive_task is not None:
                self.keepalive_task.cancel()
                await asyncio.gather(self.keepalive_task, return_exceptions=True)
                self.keepalive_task = None
            if self.jellyfin_sync_task is not None:
                self.jellyfin_sync_task.cancel()
                await asyncio.gather(self.jellyfin_sync_task, return_exceptions=True)
                self.jellyfin_sync_task = None

    def append_running_task(self, task):
        self.running_task.append(task)

    async def cancel_all_tasks(self):
        if len(self.running_task) == 0:
            self.log.info("cancel_all_tasks no task")
            return
        for task in self.running_task:
            self.log.info(f"cancel_all_tasks {task}")
            task.cancel()
        await asyncio.gather(*self.running_task, return_exceptions=True)
        self.running_task = []

    async def is_task_finish(self):
        if len(self.running_task) == 0:
            return True
        task = self.running_task[0]
        if task and task.done():
            return True
        return False

    def did_exist(self, did):
        return did in self.device_manager.devices

    # 播放一个 url
    async def play_url(self, did="", arg1="", **kwargs):
        """Fast accepted external URL play via DeviceCommandArbiter.

        Submits the intent and returns immediately with an accepted receipt.
        Physical work (on_external_url_play / group_player_play /
        on_external_url_play_started) is deferred to the arbiter executor.

        Returns a JSON-serializable receipt dict with at least
        {"accepted": True, "sequence": N}.
        """
        self.log.info(f"手动推送链接：{arg1}")
        url = arg1
        device = self.device_manager.devices[did]
        raw_context = kwargs.get("context")
        context = raw_context if isinstance(raw_context, dict) else {}
        resolved = kwargs.get("resolved") or {}
        return await device.submit_external_url_play(
            url=url, context=context, resolved=resolved
        )

    async def set_play_type_one(self, did="", **kwargs):
        await self.set_play_type(
            did,
            PLAY_TYPE_ONE,
            refresh_playlist=kwargs.get("refresh_playlist", True),
        )

    async def set_play_type_all(self, did="", **kwargs):
        await self.set_play_type(
            did,
            PLAY_TYPE_ALL,
            refresh_playlist=kwargs.get("refresh_playlist", True),
        )

    async def set_play_type_rnd(self, did="", **kwargs):
        await self.set_play_type(
            did,
            PLAY_TYPE_RND,
            refresh_playlist=kwargs.get("refresh_playlist", True),
        )

    async def set_play_type_sin(self, did="", **kwargs):
        await self.set_play_type(
            did,
            PLAY_TYPE_SIN,
            refresh_playlist=kwargs.get("refresh_playlist", True),
        )

    async def set_play_type_seq(self, did="", **kwargs):
        await self.set_play_type(
            did,
            PLAY_TYPE_SEQ,
            refresh_playlist=kwargs.get("refresh_playlist", True),
        )

    async def set_play_type(
        self,
        did="",
        play_type=PLAY_TYPE_RND,
        refresh_playlist=True,
    ):
        await self.device_manager.devices[did].set_play_type(
            play_type,
            refresh_playlist,
        )

    # 口令:刷新列表
    async def gen_music_list(self, **kwargs):
        self.music_library.gen_all_music_list()
        self.update_all_playlist()
        self.log.info("gen_music_list ok")

    async def del_music(self, name):
        filename = self.music_library.get_filename(name)
        if filename == "":
            self.log.info(f"${name} not exist")
            return
        try:
            os.remove(filename)
            self.log.info(f"del ${filename} success")
        except OSError:
            self.log.error(f"del ${filename} failed")
        # 重新生成音乐列表
        self.music_library.gen_all_music_list()
        self.update_all_playlist()

    # ===========================在线搜索函数================================

    def default_url(self):
        """委托给 online_music_service"""
        return self.online_music_service.default_url()

    # 在线获取歌曲列表（委托给 online_music_service）
    async def get_music_list_online(
        self, plugin="all", keyword="", page=1, limit=20, **kwargs
    ):
        """委托给 online_music_service"""
        return await self.online_music_service.get_music_list_online(
            plugin, keyword, page, limit, **kwargs
        )

    @staticmethod
    async def get_real_url_of_openapi(url: str, timeout: int = 10) -> str:
        """委托给 OnlineMusicService 的静态方法"""
        return await OnlineMusicService.get_real_url_of_openapi(url, timeout)

    # 调用MusicFree插件获取歌曲列表（委托给 online_music_service）
    async def get_music_list_mf(
        self, plugin="all", keyword="", artist="", page=1, limit=20, **kwargs
    ):
        """委托给 online_music_service"""
        return await self.online_music_service.get_music_list_mf(
            plugin, keyword, artist, page, limit, **kwargs
        )

    # 调用MusicFree插件获取歌词（委托给 online_music_service）
    async def get_media_lyric(self, music_item):
        """委托给 online_music_service"""
        return await self.online_music_service.get_media_lyric(music_item)

    # 追加歌手歌曲
    async def add_singer_song(self, list_name, name):
        """委托给 online_music_service"""
        return await self.online_music_service.add_singer_song(list_name, name)

    # ===========================================================

    def _find_real_music_list_name(self, list_name):
        """模糊搜索播放列表名称（委托给 music_library）"""
        return self.music_library.find_real_music_list_name(list_name)

    async def do_play_music_list(self, did, list_name, music_name=""):
        # 查找并获取真实的音乐列表名称
        list_name = self._find_real_music_list_name(list_name)
        # 检查音乐列表是否存在，如果不存在则进行语音提示并返回
        if list_name not in self.music_library.music_list:
            await self.do_tts(did, f"播放列表{list_name}不存在")
            return False

        # 调用设备播放音乐列表的方法
        return await self.device_manager.devices[did].play_music_list(
            list_name, music_name
        )

    # 口令:播放歌曲
    async def play(self, did="", arg1="", **kwargs):
        parts = arg1.split("|")
        search_key = parts[0]
        name = parts[1] if len(parts) > 1 else search_key
        if not name:
            name = search_key

        # 语音播放会根据歌曲匹配更新当前播放列表
        return await self.do_play(did, name, search_key)

    # 网页面板搜索播放
    async def do_play(self, did, name, search_key=""):
        return await self.device_manager.devices[did].play(name, search_key)

    # 口令:播放本地歌曲
    async def playlocal(self, did="", arg1="", **kwargs):
        return await self.device_manager.devices[did].playlocal(arg1)

    # 口令:下一首
    async def play_next(self, did="", **kwargs):
        return await self.device_manager.devices[did].play_next()

    # 口令:上一首
    async def play_prev(self, did="", **kwargs):
        return await self.device_manager.devices[did].play_prev()

    # 口令:停止
    async def stop(self, did="", arg1="", **kwargs):
        return await self.device_manager.devices[did].stop(arg1=arg1)

    # 口令:分钟后关机
    async def stop_after_minute(self, did="", arg1=0, **kwargs):
        try:
            # 尝试阿拉伯数字转换中文数字
            minute = int(arg1)
        except (KeyError, ValueError):
            # 如果阿拉伯数字转换失败，尝试中文数字
            minute = chinese_to_number(str(arg1))
        return await self.device_manager.devices[did].stop_after_minute(minute)

    def _resolve_current_track_reference(self, did: str, fallback: str = "") -> str:
        device_player = getattr(getattr(self, "device_manager", None), "devices", {}).get(did)
        if device_player is not None:
            direct_entity_id = str(
                getattr(getattr(device_player, "device", None), "current_entity_id", "")
                or ""
            ).strip()
            if direct_entity_id:
                return direct_entity_id
        token = str(fallback or self.playingmusic(did) or "").strip()
        if not token:
            return ""
        music_library = getattr(self, "music_library", None)
        if music_library is None:
            return token

        playlist_name = str(self.get_cur_play_list(did) or "").strip()
        resolver = getattr(music_library, "resolve_playlist_item_identity", None)
        if callable(resolver):
            try:
                entity_id = str(
                    resolver(playlist_name, item_name=token) or ""
                ).strip()
            except Exception:
                entity_id = ""
            if entity_id:
                return entity_id

        resolver = getattr(music_library, "resolve_entity_id_by_name", None)
        if callable(resolver):
            try:
                entity_id = str(resolver(token) or "").strip()
            except Exception:
                entity_id = ""
            if entity_id:
                return entity_id
        return token

    # 口令:加入收藏,收藏歌曲
    async def add_to_favorites(self, did="", arg1="", **kwargs):
        target = self._resolve_current_track_reference(did, fallback=arg1)
        self.log.info(f"add_to_favorites {target}")
        if not target:
            self.log.warning("当前没有在播放歌曲，添加歌曲到收藏列表失败")
            return

        self.music_library.play_list_add_music("收藏", [target])

    # 口令:取消收藏
    async def del_from_favorites(self, did="", arg1="", **kwargs):
        target = self._resolve_current_track_reference(did, fallback=arg1)
        self.log.info(f"del_from_favorites {target}")
        if not target:
            self.log.warning("当前没有在播放歌曲，从收藏列表中移除失败")
            return

        self.music_library.play_list_del_music("收藏", [target])

    # 更新每个设备的歌单
    def update_all_playlist(self):
        """更新每个设备的歌单"""
        for device in self.device_manager.devices.values():
            device.update_playlist()

    # 获取音量
    async def get_volume(self, did="", **kwargs):
        return await self.device_manager.devices[did].get_volume()

    # 获取完整播放状态
    async def get_player_status(self, did="", **kwargs):
        return await self.device_manager.devices[did].get_player_status()

    # 设置音量
    async def set_volume(self, did="", arg1=0, **kwargs):
        if did not in self.device_manager.devices:
            self.log.info(f"设备 did:{did} 不存在, 不能设置音量")
            return
        volume = int(arg1)
        return await self.device_manager.devices[did].set_volume(volume)

    # 获取当前的播放列表
    def get_cur_play_list(self, did):
        return self.device_manager.devices[did].get_cur_play_list()

    # 正在播放中的音乐
    def playingmusic(self, did):
        cur_music = self.device_manager.devices[did].get_cur_music()
        self.log.debug(f"playingmusic. cur_music:{cur_music}")
        return cur_music

    def get_offset_duration(self, did):
        return self.device_manager.devices[did].get_offset_duration()

    # 当前是否正在播放歌曲
    def isplaying(self, did):
        return self.device_manager.devices[did].is_playing

    # 获取当前配置
    def getconfig(self):
        """获取当前配置（委托给 config_manager）"""
        return self.config_manager.get_config()

    # 保存配置并重新启动
    async def saveconfig(self, data):
        """保存配置并重新启动"""
        # 更新配置
        self.update_config_from_setting(data)
        await self.sync_jellyfin_music_lists_if_needed()
        # 配置文件落地
        self.save_cur_config()
        # 重新初始化
        await self.reinit()

    async def sync_jellyfin_music_lists_if_needed(self):
        if not self.config.jellyfin_enabled:
            return
        if not self.online_music_service:
            return
        try:
            ret = await self.online_music_service.sync_jellyfin_music_lists()
            self.log.info(f"sync_jellyfin_music_lists_if_needed {ret}")
        except Exception as e:
            self.log.error(f"sync_jellyfin_music_lists_if_needed failed: {e}")

    # 把当前配置落地
    def save_cur_config(self):
        """把当前配置落地（委托给 config_manager）"""
        self.config_manager.save_cur_config(self.device_manager.devices)

    def update_config_from_setting(self, data):
        """从设置更新配置"""
        # 委托给 config_manager 更新配置
        self.config_manager.update_config(data)

        # 重新初始化配置相关的属性
        self.init_config()

        debug_config = deepcopy_data_no_sensitive_info(self.config)
        self.log.info(f"update_config_from_setting ok. data:{debug_config}")

        # 根据新配置控制文件监控
        if self.config.enable_file_watch:
            self.log.info("配置更新：开启目录监控")
            self.start_file_watch()
        else:
            self.log.info("配置更新：关闭目录监控")
            self.stop_file_watch()

    # 重新初始化
    async def reinit(
        self,
        auth_already_verified: bool = False,
        refresh_device_map: bool = True,
    ):
        for handler in self.log.handlers:
            handler.close()
        self.setup_logger()
        await self.auth_manager.init_all_data(
            verified_runtime_only=auth_already_verified,
            refresh_device_map=refresh_device_map,
        )
        self.music_library.gen_all_music_list()
        self.update_all_playlist()

        debug_config = deepcopy_data_no_sensitive_info(self.config)
        self.log.info(f"reinit success. data:{debug_config}")

    # 获取所有设备
    def _cached_device_list(self):
        cached = []
        for did, dev in self.config.devices.items():
            if not dev.device_id:
                continue
            cached.append(
                {
                    "deviceID": dev.device_id,
                    "miotDID": did,
                    "hardware": dev.hardware,
                    "name": dev.name,
                    "alias": dev.name,
                }
            )
        return cached

    async def getalldevices(self, **kwargs):
        device_list = []
        try:
            device_list = await self.auth_manager.mina_call(
                "device_list", retry=1, ctx="getalldevices"
            )
            # 运行时可能拿到设备列表但本地 device_manager 仍为空（例如重建会话后）。
            # 这里做一次轻量自愈，保证 did_exist 与控制接口可用。
            if device_list and not self.device_manager.devices:
                await self.device_manager.update_device_info(self.auth_manager)
        except Exception as e:
            self.log.warning(f"Execption {e}")
            if getattr(self.auth_manager, "is_auth_locked", lambda: False)():
                cached = self._cached_device_list()
                if cached:
                    self.log.info(
                        "getalldevices auth locked, return cached list count=%d",
                        len(cached),
                    )
                    return cached
            # mina_call/auth_call 已负责统一恢复；查询层只返回缓存，避免二次
            # ensure/relogin 在并发查询中形成恢复风暴。
            cached = self._cached_device_list()
            if cached:
                self.log.info(
                    "getalldevices fallback to cached list count=%d", len(cached)
                )
                return cached
        return device_list

    async def debug_play_by_music_url(self, arg1=None):
        if arg1 is None:
            arg1 = {}
        data = arg1
        device_id = self.config.get_one_device_id()
        self.log.info(f"debug_play_by_music_url: {data} {device_id}")
        return await self.auth_manager.mina_call(
            "ubus_request",
            device_id,
            "player_play_music",
            "mediaplayer",
            data,
            retry=1,
            ctx="debug_play_by_music_url",
        )

    # 此接口用于插件中获取当前设备
    def get_cur_did(self):
        return self.auth_manager._cur_did

    async def do_tts(self, did, value):
        return await self.device_manager.devices[did].do_tts(value)
