"""音乐管理路由"""

import base64
import json

from fastapi import (
    APIRouter,
    Depends,
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
    MusicInfoObj,
    MusicItem,
)

router = APIRouter(dependencies=[Depends(verification)])


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


"""======================在线搜索相关接口END============================="""


@router.get("/playingmusic")
async def playingmusic(did: str = ""):
    """当前播放音乐（legacy 兼容视图，附带 identity 字段）"""
    if not xiaomusic.did_exist(did):
        return api_response.ok(contract="ret", ret="Did not exist")

    from xiaomusic.playback.facade import PlaybackFacade

    snapshot = await PlaybackFacade(xiaomusic).build_player_state_snapshot(did)
    track = snapshot.get("track") or {}
    context = snapshot.get("context") or {}

    return api_response.ok(
        {
            "is_playing": snapshot.get("transport_state") == "playing",
            "cur_music": str(track.get("title") or ""),
            "cur_playlist": str(context.get("name") or context.get("id") or ""),
            "offset": int(snapshot.get("position_ms") or 0) / 1000,
            "duration": int(snapshot.get("duration_ms") or 0) / 1000,
            "entity_id": str(track.get("entity_id") or ""),
            "playlist_item_id": str(track.get("id") or ""),
            "current_index": context.get("current_index"),
            "context_id": str(context.get("id") or ""),
        },
        contract="ret",
    )


@router.get("/musiclist")
async def musiclist(structured: bool = False):
    """音乐列表。默认返回 legacy `{playlist: [name]}` 视图；structured=true 时返回结构化 playlist items。"""
    if structured:
        return api_response.ok(
            {
                "legacy": False,
                "playlists": xiaomusic.music_library.get_playlist_items(),
            },
            contract="raw",
        )
    return api_response.ok(xiaomusic.music_library.get_music_list(), contract="raw")


@router.get("/musicinfo")
async def musicinfo(name: str = "", entity_id: str = "", musictag: bool = False):
    """音乐信息"""
    music_name = str(name or "").strip()
    requested_entity_id = str(entity_id or "").strip()
    library = xiaomusic.music_library
    if requested_entity_id:
        music_name = str(
            getattr(library, "get_legacy_name_for_entity", lambda *_args, **_kwargs: "")(
                requested_entity_id
            )
            or ""
        ).strip()
        url, _ = await library.get_music_url_by_entity(requested_entity_id)
    else:
        url, _ = await library.get_music_url(music_name)
    info = {"name": music_name, "entity_id": requested_entity_id, "url": url}
    if musictag:
        info["tags"] = (
            await library.get_music_tags_by_entity(requested_entity_id)
            if requested_entity_id
            else await library.get_music_tags(music_name)
        )
    return api_response.ok(info, contract="ret")


@router.get("/musicinfos")
async def musicinfos(
    name: list[str] = Query(None),
    entity_id: list[str] = Query(None),
    musictag: bool = False,
):
    """批量音乐信息"""
    ret = []
    names = name or []
    entity_ids = entity_id or []
    library = xiaomusic.music_library
    if entity_ids:
        resolver = getattr(library, "get_legacy_name_for_entity", None)
        if callable(resolver):
            names = [str(resolver(item) or "").strip() for item in entity_ids]
        else:
            names = ["" for _ in entity_ids]
    for idx, music_name in enumerate(names):
        current_entity_id = entity_ids[idx] if idx < len(entity_ids) else ""
        if current_entity_id:
            url, _ = await library.get_music_url_by_entity(current_entity_id)
        else:
            url, _ = await library.get_music_url(music_name)
        info = {
            "name": music_name,
            "entity_id": current_entity_id,
            "url": url,
        }
        if musictag:
            info["tags"] = (
                await library.get_music_tags_by_entity(current_entity_id)
                if current_entity_id
                else await library.get_music_tags(music_name)
            )
        ret.append(info)
    return api_response.ok(ret, contract="raw")


@router.post("/setmusictag")
async def setmusictag(info: MusicInfoObj):
    """设置音乐标签"""
    entity_id = str(getattr(info, "entity_id", "") or "").strip()
    if entity_id and hasattr(xiaomusic.music_library, "set_music_tag_by_entity"):
        ret = xiaomusic.music_library.set_music_tag_by_entity(entity_id, info)
    else:
        ret = xiaomusic.music_library.set_music_tag(info.musicname, info)
    return api_response.ok(contract="ret", ret=ret)


@router.post("/delmusic")
async def delmusic(data: MusicItem):
    """删除音乐"""
    log.info(data)
    await xiaomusic.del_music(data.name)
    return api_response.ok("success", contract="raw")


@router.post("/api/music/refreshlist")
async def refreshlist(Verifcation=Depends(verification)):
    """刷新歌曲列表"""
    await xiaomusic.gen_music_list()
    return api_response.ok(contract="ret")
