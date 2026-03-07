"""音乐管理路由"""

import base64
import json
import urllib.parse

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import RedirectResponse

from xiaomusic.api import response as api_response
from xiaomusic.api.dependencies import (
    log,
    verification,
    xiaomusic,
)
from xiaomusic.api.models import (
    DidPlayMusic,
    MusicInfoObj,
    MusicItem,
)
from xiaomusic.playback.facade import PlaybackFacade

router = APIRouter(dependencies=[Depends(verification)])
_facade: PlaybackFacade | None = None


def _get_facade() -> PlaybackFacade:
    global _facade
    if _facade is None:
        _facade = PlaybackFacade(xiaomusic)
    return _facade


@router.get("/searchmusic")
def searchmusic(name: str = ""):
    """搜索音乐"""
    return api_response.ok(xiaomusic.music_library.searchmusic(name), contract="raw")


"""======================在线搜索相关接口============================="""


@router.get("/api/search/online")
async def search_online_music(
    keyword: str = Query(..., description="搜索关键词"),
    plugin: str = Query("all", description="指定插件名称，all表示搜索所有插件"),
    page: int = Query(1, description="页码"),
    limit: int = Query(20, description="每页数量"),
):
    """在线音乐搜索API"""
    try:
        if not keyword:
            return api_response.fail(
                "E_BAD_REQUEST",
                "Keyword required",
                contract="success_error",
            )

        return api_response.ok(
            await xiaomusic.get_music_list_online(
                keyword=keyword, plugin=plugin, page=page, limit=limit
            ),
            contract="raw",
        )
    except Exception as e:
        return api_response.fail(
            "E_INTERNAL",
            str(e),
            contract="success_error",
            exc=e,
        )


@router.get("/api/proxy/real-url")
async def get_real_music_url(url: str = Query(..., description="原始url")):
    """通过服务端代理获取真实的URL，不止是音频url,可能还有图片url"""
    try:
        # 获取真实的URL
        real_url = await xiaomusic.get_real_url_of_openapi(url)
        # 直接重定向到真实URL
        return RedirectResponse(url=real_url)

    except Exception as e:
        log.error(f"获取真实URL失败: {e}")
        # 如果代理获取失败，重定向到原始URL
        return RedirectResponse(url=url)


@router.get("/api/proxy/plugin-url")
async def get_plugin_source_url(
    data: str = Query(..., description="json对象压缩的base64"),
):
    try:
        # 获取请求数据
        # 将Base64编码的URL解码为Json字符串
        json_str = base64.b64decode(data).decode("utf-8")
        # 将json字符串转换为json对象
        json_data = json.loads(json_str)
        # 调用公共函数处理
        media_source = await xiaomusic.online_music_service.get_media_source_url(
            json_data
        )
        if media_source and media_source.get("url"):
            source_url = media_source.get("url")
        else:
            source_url = xiaomusic.default_url()
        log.info(f"plugin-url {json_data} {source_url}")
        # 直接重定向到真实URL
        return RedirectResponse(url=source_url)
    except Exception as e:
        log.error(f"获取真实音乐URL失败: {e}")
        # 如果代理获取失败，重定向到原始URL
        source_url = xiaomusic.default_url()
        return RedirectResponse(url=source_url)


@router.post("/api/play/getMediaSource")
async def get_media_source(request: Request):
    """获取音乐真实播放URL"""
    try:
        # 获取请求数据
        data = await request.json()
        # 调用公共函数处理
        return api_response.ok(
            await xiaomusic.online_music_service.get_media_source_url(data),
            contract="raw",
        )
    except Exception as e:
        return api_response.fail(
            "E_INTERNAL",
            str(e),
            contract="success_error",
            exc=e,
        )


@router.post("/api/play/getLyric")
async def get_media_lyric(request: Request):
    """获取音乐歌词"""
    try:
        # 获取请求数据
        data = await request.json()
        # 调用公共函数处理
        return api_response.ok(await xiaomusic.get_media_lyric(data), contract="raw")
    except Exception as e:
        return api_response.fail(
            "E_INTERNAL",
            str(e),
            contract="success_error",
            exc=e,
        )


@router.post("/api/device/pushUrl")
async def device_push_url(request: Request):
    """推送媒体给设备播放（通过统一 core 链路）。"""
    try:
        data = await request.json()
        did = str(data.get("did") or "")
        out = await _get_facade().play_payload(data, did)
        if not out.get("ok"):
            return api_response.fail(
                "E_XIAOMI_PLAY_FAILED",
                "play failed",
                contract="success_error",
            )
        return api_response.ok(out.get("raw") or {"ret": "OK"}, contract="raw")
    except Exception as e:
        return api_response.fail(
            "E_INTERNAL",
            str(e),
            contract="success_error",
            exc=e,
        )


@router.post("/api/device/pushList")
async def device_push_list(request: Request):
    """WEB前端推送歌单给设备端播放"""
    try:
        # 获取请求数据
        data = await request.json()
        did = data.get("did")
        song_list = data.get("songList")
        list_name = data.get("playlistName")
        # 调用公共函数处理,处理歌曲信息 -> 添加歌单 -> 播放歌单
        return api_response.ok(
            await xiaomusic.push_music_list_play(
                did=did, song_list=song_list, list_name=list_name
            ),
            contract="raw",
        )
    except Exception as e:
        return api_response.fail(
            "E_INTERNAL",
            str(e),
            contract="success_error",
            exc=e,
        )


"""======================在线搜索相关接口END============================="""


@router.get("/playingmusic")
def playingmusic(did: str = ""):
    """当前播放音乐"""
    if not xiaomusic.did_exist(did):
        return api_response.ok(contract="ret", ret="Did not exist")

    is_playing = xiaomusic.isplaying(did)
    cur_music = xiaomusic.playingmusic(did)
    cur_playlist = xiaomusic.get_cur_play_list(did)
    # 播放进度
    offset, duration = xiaomusic.get_offset_duration(did)
    return api_response.ok(
        {
            "is_playing": is_playing,
            "cur_music": cur_music,
            "cur_playlist": cur_playlist,
            "offset": offset,
            "duration": duration,
        },
        contract="ret",
    )


@router.get("/musiclist")
async def musiclist():
    """音乐列表"""
    return api_response.ok(xiaomusic.music_library.get_music_list(), contract="raw")


@router.get("/musicinfo")
async def musicinfo(name: str, musictag: bool = False):
    """音乐信息"""
    url, _ = await xiaomusic.music_library.get_music_url(name)
    info = {"name": name, "url": url}
    if musictag:
        info["tags"] = await xiaomusic.music_library.get_music_tags(name)
    return api_response.ok(info, contract="ret")


@router.get("/musicinfos")
async def musicinfos(
    name: list[str] = Query(None),
    musictag: bool = False,
):
    """批量音乐信息"""
    ret = []
    for music_name in name:
        url, _ = await xiaomusic.music_library.get_music_url(music_name)
        info = {
            "name": music_name,
            "url": url,
        }
        if musictag:
            info["tags"] = await xiaomusic.music_library.get_music_tags(music_name)
        ret.append(info)
    return api_response.ok(ret, contract="raw")


@router.post("/setmusictag")
async def setmusictag(info: MusicInfoObj):
    """设置音乐标签"""
    ret = xiaomusic.music_library.set_music_tag(info.musicname, info)
    return api_response.ok(contract="ret", ret=ret)


@router.post("/delmusic")
async def delmusic(data: MusicItem):
    """删除音乐"""
    log.info(data)
    await xiaomusic.del_music(data.name)
    return api_response.ok("success", contract="raw")


@router.post("/playmusic")
async def playmusic(data: DidPlayMusic):
    """播放音乐（compatibility_layer: legacy endpoint, remove after v2 API migration）"""
    did = data.did
    musicname = data.musicname
    searchkey = data.searchkey
    if not xiaomusic.did_exist(did):
        return api_response.ok(contract="ret", ret="Did not exist")

    log.info(f"playmusic {did} musicname:{musicname} searchkey:{searchkey}")
    out = await _get_facade().play_local_music(did, musicname, searchkey)
    if not out.get("ok"):
        return api_response.ok(contract="ret", ret="Did not exist")
    return api_response.ok(contract="ret")


@router.post("/refreshmusictag")
async def refreshmusictag(Verifcation=Depends(verification)):
    """刷新音乐标签"""
    xiaomusic.music_library.refresh_music_tag()
    return api_response.ok(contract="ret")


@router.post("/debug_play_by_music_url")
async def debug_play_by_music_url(request: Request, Verifcation=Depends(verification)):
    """调试播放音乐URL"""
    try:
        data = await request.body()
        data_dict = json.loads(data.decode("utf-8"))
        log.info(f"data:{data_dict}")
        return api_response.ok(
            await xiaomusic.debug_play_by_music_url(arg1=data_dict),
            contract="raw",
        )
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="Invalid JSON") from err


@router.post("/api/music/refreshlist")
async def refreshlist(Verifcation=Depends(verification)):
    """刷新歌曲列表"""
    await xiaomusic.gen_music_list()
    return api_response.ok(contract="ret")
