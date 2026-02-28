from xiaomusic.js_plugin_manager import JSPluginManager
from xiaomusic.managers.js_plugin_manager import JSPluginManager as SplitManager
from xiaomusic.online_music import OnlineMusicService
from xiaomusic.services.online_music_service import OnlineMusicService as SplitService


def test_online_music_wrapper_keeps_public_service_class():
    assert OnlineMusicService is SplitService


def test_js_plugin_manager_wrapper_keeps_public_manager_class():
    assert JSPluginManager is SplitManager
