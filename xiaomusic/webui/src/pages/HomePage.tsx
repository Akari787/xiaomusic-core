import { useEffect, useMemo, useState } from "react";

import { apiGet, apiPost } from "../services/apiClient";
import "../styles/home.css";

type Device = {
  did?: string;
  deviceID?: string;
  miotDID?: string;
  hardware?: string;
  name?: string;
  alias?: string;
};

type PlayingInfo = {
  ret?: string;
  is_playing?: boolean;
  cur_music?: string;
  cur_playlist?: string;
  offset?: number;
  duration?: number;
};

type OnlineSearchItem = {
  name?: string;
  title?: string;
  artist?: string;
};

type OAuthStatus = {
  token_valid?: boolean;
  runtime_auth_ready?: boolean;
  login_in_progress?: boolean;
  last_error?: string;
};

type QrcodeResp = {
  success?: boolean;
  already_logged_in?: boolean;
  qrcode_url?: string;
  expire_seconds?: number;
  message?: string;
  error?: string;
};

const PLAY_MODES = [
  { icon: "repeat_one", cmd: "单曲循环" },
  { icon: "repeat", cmd: "全部循环" },
  { icon: "shuffle", cmd: "随机播放" },
  { icon: "filter_1", cmd: "单曲播放" },
  { icon: "playlist_play", cmd: "顺序播放" },
] as const;

type SettingField = {
  key: string;
  label: string;
  kind?: "text" | "number" | "bool" | "password" | "textarea" | "select";
  options?: Array<{ value: string; label: string }>;
};

const EDGE_TTS_VOICE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "不使用(默认)" },
  { value: "zh-CN-XiaoxiaoNeural", label: "晓晓 (女声,温柔)" },
  { value: "zh-CN-XiaoyiNeural", label: "晓伊 (女声,活泼)" },
  { value: "zh-CN-YunjianNeural", label: "云健 (男声,成熟)" },
  { value: "zh-CN-YunxiNeural", label: "云希 (男声,阳光)" },
  { value: "zh-CN-YunxiaNeural", label: "云夏 (男声,少年)" },
  { value: "zh-CN-YunyangNeural", label: "云扬 (男声,新闻)" },
  { value: "zh-CN-liaoning-XiaobeiNeural", label: "晓北 (女声,东北)" },
  { value: "zh-CN-shaanxi-XiaoniNeural", label: "晓妮 (女声,陕西)" },
  { value: "zh-HK-HiuGaaiNeural", label: "曉佳 (女声,粤语)" },
  { value: "zh-HK-HiuMaanNeural", label: "曉曼 (女声,粤语)" },
  { value: "zh-HK-WanLungNeural", label: "雲龍 (男声,粤语)" },
  { value: "zh-TW-HsiaoChenNeural", label: "曉臻 (女声,台湾)" },
  { value: "zh-TW-YunJheNeural", label: "雲哲 (男声,台湾)" },
  { value: "zh-TW-HsiaoYuNeural", label: "曉雨 (女声,台湾)" },
];

const ADVANCED_TABS: Array<{ key: string; label: string; fields: SettingField[] }> = [
  {
    key: "filepath",
    label: "文件路径",
    fields: [
      { key: "music_path", label: "音乐目录" },
      { key: "download_path", label: "音乐下载目录" },
      { key: "conf_path", label: "配置文件目录" },
      { key: "cache_dir", label: "缓存文件目录" },
      { key: "temp_path", label: "临时文件目录" },
      { key: "ffmpeg_location", label: "ffmpeg 路径" },
    ],
  },
  {
    key: "filewatch",
    label: "文件监控",
    fields: [
      { key: "enable_file_watch", label: "启用目录监控", kind: "bool" },
      { key: "file_watch_debounce", label: "刷新延迟(秒)", kind: "number" },
      { key: "enable_auto_clean_temp", label: "启用定时清理临时文件", kind: "bool" },
      { key: "exclude_dirs", label: "忽略目录" },
      { key: "ignore_tag_dirs", label: "不扫描标签目录" },
      { key: "music_path_depth", label: "目录深度", kind: "number" },
      { key: "recently_added_playlist_len", label: "最近新增歌曲数量", kind: "number" },
    ],
  },
  {
    key: "playback",
    label: "播放控制",
    fields: [
      { key: "delay_sec", label: "下一首延迟秒数", kind: "number" },
      { key: "continue_play", label: "启用继续播放", kind: "bool" },
      { key: "use_music_api", label: "型号兼容模式", kind: "bool" },
      { key: "use_music_audio_id", label: "触屏版歌曲ID" },
      { key: "use_music_id", label: "触屏版分段ID" },
      { key: "active_cmd", label: "允许唤醒命令" },
    ],
  },
  {
    key: "search",
    label: "搜索匹配",
    fields: [
      { key: "fuzzy_match_cutoff", label: "模糊匹配阈值", kind: "number" },
      { key: "enable_fuzzy_match", label: "开启模糊搜索", kind: "bool" },
    ],
  },
  {
    key: "audio",
    label: "音频处理",
    fields: [
      { key: "get_duration_type", label: "获取时长方式" },
      { key: "loudnorm", label: "均衡音量参数" },
      { key: "remove_id3tag", label: "去除 MP3 ID3v2", kind: "bool" },
      { key: "convert_to_mp3", label: "转换为 MP3", kind: "bool" },
      { key: "enable_save_tag", label: "启用 ID3 写入", kind: "bool" },
    ],
  },
  {
    key: "tts",
    label: "语音TTS",
    fields: [
      {
        key: "edge_tts_voice",
        label: "Edge-TTS 语音角色",
        kind: "select",
        options: EDGE_TTS_VOICE_OPTIONS,
      },
      { key: "stop_tts_msg", label: "停止提示音" },
      { key: "play_type_one_tts_msg", label: "单曲循环提示音" },
      { key: "play_type_all_tts_msg", label: "全部循环提示音" },
      { key: "play_type_rnd_tts_msg", label: "随机播放提示音" },
      { key: "play_type_sin_tts_msg", label: "单曲播放提示音" },
      { key: "play_type_seq_tts_msg", label: "顺序播放提示音" },
    ],
  },
  {
    key: "voicecmd",
    label: "语音命令",
    fields: [
      { key: "keywords_playlocal", label: "播放本地歌曲口令" },
      { key: "keywords_play", label: "播放歌曲口令" },
      { key: "keywords_playlist", label: "播放列表口令" },
      { key: "keywords_stop", label: "停止口令" },
      { key: "keywords_online_play", label: "在线播放口令" },
      { key: "keywords_singer_play", label: "播放歌手口令" },
      { key: "enable_cmd_del_music", label: "启用语音删除歌曲", kind: "bool" },
    ],
  },
  {
    key: "network",
    label: "网络下载",
    fields: [
      { key: "search_prefix", label: "歌曲下载方式前缀" },
      { key: "proxy", label: "代理地址" },
      { key: "web_music_proxy", label: "网络歌曲走代理", kind: "bool" },
      { key: "disable_download", label: "关闭下载功能", kind: "bool" },
      { key: "enable_yt_dlp_cookies", label: "启用 yt-dlp-cookies", kind: "bool" },
      { key: "yt_dlp_cookies_path", label: "yt-dlp-cookies 文件路径" },
    ],
  },
  {
    key: "device",
    label: "设备对话",
    fields: [
      { key: "group_list", label: "设备分组配置" },
      { key: "enable_pull_ask", label: "获取对话记录", kind: "bool" },
      { key: "pull_ask_sec", label: "对话轮询间隔(秒)", kind: "number" },
      { key: "get_ask_by_mina", label: "特殊型号对话记录", kind: "bool" },
    ],
  },
  {
    key: "playlist",
    label: "歌单定时",
    fields: [
      { key: "music_list_url", label: "歌单地址" },
      { key: "music_list_json", label: "歌单内容", kind: "textarea" },
      { key: "crontab_json", label: "定时任务", kind: "textarea" },
    ],
  },
  {
    key: "security",
    label: "安全访问",
    fields: [
      { key: "disable_httpauth", label: "关闭控制台密码验证", kind: "bool" },
      { key: "httpauth_username", label: "控制台账户" },
      { key: "httpauth_password", label: "控制台密码", kind: "password" },
    ],
  },
  {
    key: "other",
    label: "其他",
    fields: [
      { key: "verbose", label: "开启调试日志", kind: "bool" },
      { key: "log_file", label: "日志路径" },
      { key: "enable_analytics", label: "开启谷歌数据统计", kind: "bool" },
    ],
  },
];

function resolveDisplayName(d: Device): string {
  return d.alias || d.name || d.did || d.deviceID || d.miotDID || "未知设备";
}

function deviceCandidates(d: Device): string[] {
  const vals = [d.miotDID, d.did, d.deviceID].filter((v): v is string => Boolean(v));
  return Array.from(new Set(vals));
}

function formatTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

function displayModelName(d: Device): string {
  const raw = String(d.name || d.alias || "").trim();
  if (!raw) {
    return resolveDisplayName(d);
  }
  return raw;
}

function explainPlaybackError(
  errorCode?: string | null,
  message?: string | null,
  stage?: string | null,
): string {
  const code = String(errorCode || "");
  if (code === "E_RESOLVE_NONZERO_EXIT") {
    return "链接解析失败（源站限制/风控/地区限制）。建议换直链或更换来源。";
  }
  if (code === "E_STREAM_NOT_FOUND") {
    return "未找到可用流会话，请重试或切换播放模式。";
  }
  if (code === "E_XIAOMI_PLAY_FAILED") {
    return "小爱端播放失败，请检查设备在线状态与当前播放权限。";
  }
  if (stage === "resolve") {
    return `解析阶段失败：${message || code || "未知错误"}`;
  }
  return message || code || "未知错误";
}

export function HomePage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [activeDid, setActiveDid] = useState<string>("");
  const [playlists, setPlaylists] = useState<Record<string, string[]>>({});
  const [playlist, setPlaylist] = useState<string>("");
  const [music, setMusic] = useState<string>("");
  const [volume, setVolume] = useState<number>(50);
  const [status, setStatus] = useState<PlayingInfo>({});
  const [message, setMessage] = useState<string>("");
  const [version, setVersion] = useState<string>("");
  const [playModeIndex, setPlayModeIndex] = useState<number>(2);
  const [switchingPlayMode, setSwitchingPlayMode] = useState<boolean>(false);

  const [showSearch, setShowSearch] = useState<boolean>(false);
  const [showTimer, setShowTimer] = useState<boolean>(false);
  const [showPlaylink, setShowPlaylink] = useState<boolean>(false);
  const [showVolume, setShowVolume] = useState<boolean>(false);
  const [showSettings, setShowSettings] = useState<boolean>(false);

  const [linkUrl, setLinkUrl] = useState<string>("https://www.youtube.com/watch?v=iPnaF8Ngk3Q");
  const [ttsText, setTtsText] = useState<string>("播放文字测试");
  const [customCmd, setCustomCmd] = useState<string>("");
  const [searchKeyword, setSearchKeyword] = useState<string>("");
  const [searchResults, setSearchResults] = useState<OnlineSearchItem[]>([]);
  const [selectedSearchIndex, setSelectedSearchIndex] = useState<number>(-1);

  const [oauthStatus, setOauthStatus] = useState<OAuthStatus>({});
  const [qrcodeUrl, setQrcodeUrl] = useState<string>("");
  const [qrcodeStatus, setQrcodeStatus] = useState<string>("点击上方按钮获取登录二维码");
  const [settingData, setSettingData] = useState<Record<string, unknown>>({});
  const [settingJsonText, setSettingJsonText] = useState<string>("{}");
  const [settingDeviceList, setSettingDeviceList] = useState<Device[]>([]);
  const [selectedSettingDids, setSelectedSettingDids] = useState<string[]>([]);
  const [advancedOpen, setAdvancedOpen] = useState<boolean>(false);
  const [activeAdvancedTab, setActiveAdvancedTab] = useState<string>("filepath");
  const [operationOpen, setOperationOpen] = useState<boolean>(false);
  const [toolsOpen, setToolsOpen] = useState<boolean>(false);
  const [qrcodeExpireAt, setQrcodeExpireAt] = useState<number>(0);
  const [qrcodeRemain, setQrcodeRemain] = useState<number>(0);
  const [pullAskEnabled, setPullAskEnabled] = useState<boolean>(false);

  const songs = useMemo(() => playlists[playlist] || [], [playlists, playlist]);
  const oauthLoggedIn = Boolean(oauthStatus.token_valid);
  const oauthReady = Boolean(oauthStatus.runtime_auth_ready);
  const oauthInProgress = Boolean(oauthStatus.login_in_progress);
  const oauthStatusLabel = oauthReady ? "已登录" : oauthLoggedIn ? "登录待恢复" : "未登录";
  const oauthStatusClass = oauthReady ? "ok" : "warn";
  const progress = useMemo(() => {
    const d = Number(status.duration || 0);
    const o = Number(status.offset || 0);
    if (!d) {
      return 0;
    }
    return Math.max(0, Math.min(100, Math.round((o / d) * 100)));
  }, [status.duration, status.offset]);

  useEffect(() => {
    const mainStyleId = "webui-default-main-css";
    if (!document.getElementById(mainStyleId)) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "/static/default/main.css";
      link.id = mainStyleId;
      document.head.appendChild(link);
    }
    const settingStyleId = "webui-default-setting-css";
    if (!document.getElementById(settingStyleId)) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "/static/default/setting.css";
      link.id = settingStyleId;
      document.head.appendChild(link);
    }
    document.body.classList.add("index_page");
    document.body.classList.add("fonts-loaded");
    document.body.classList.add("webui-refactor-mode");
    return () => {
      document.body.classList.remove("index_page");
      document.body.classList.remove("webui-refactor-mode");
    };
  }, []);

  useEffect(() => {
    if (!showSettings) {
      return;
    }
    const timer = window.setInterval(() => {
      void loadOAuthStatus();
    }, 2500);
    return () => window.clearInterval(timer);
  }, [showSettings]);

  useEffect(() => {
    if (!qrcodeExpireAt) {
      return;
    }
    const tick = () => {
      const remain = Math.max(0, Math.ceil((qrcodeExpireAt - Date.now()) / 1000));
      setQrcodeRemain(remain);
      if (remain === 0) {
        setQrcodeStatus("二维码已过期，请重新获取");
        setQrcodeExpireAt(0);
      } else {
        setQrcodeStatus(`请使用米家 App 扫码（约 ${remain}s）`);
      }
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [qrcodeExpireAt]);

  useEffect(() => {
    const loggedIn = Boolean(oauthStatus.runtime_auth_ready);
    const inProgress = Boolean(oauthStatus.login_in_progress);
    if (loggedIn && !inProgress) {
      if (qrcodeUrl) {
        setQrcodeUrl("");
      }
      if (qrcodeExpireAt) {
        setQrcodeExpireAt(0);
        setQrcodeRemain(0);
      }
      setQrcodeStatus("已登录，可获取设备列表");
      void loadDevices();
    }
  }, [oauthStatus.runtime_auth_ready, oauthStatus.login_in_progress]);

  async function tryResolveDid(device: Device): Promise<string> {
    const candidates = deviceCandidates(device);
    for (const did of candidates) {
      const out = (await apiGet<PlayingInfo>(`/playingmusic?did=${encodeURIComponent(did)}`)) as PlayingInfo;
      if (out.ret !== "Did not exist") {
        return did;
      }
    }
    return "";
  }

  async function loadVersion() {
    const out = (await apiGet<{ version?: string }>("/getversion")) as { version?: string };
    setVersion(out.version || "");
  }

  async function loadOAuthStatus() {
    const out = (await apiGet<OAuthStatus>("/api/oauth2/status")) as OAuthStatus;
    setOauthStatus(out);
  }

  async function loadSettingData() {
    const out = (await apiGet<Record<string, unknown> & { device_list?: Device[] }>(
      "/getsetting?need_device_list=true",
    )) as Record<string, unknown> & { device_list?: Device[] };
    setSettingData(out);
    setSettingJsonText(JSON.stringify(out, null, 2));
    const dids = String(out.mi_did || "")
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
    setSelectedSettingDids(dids);
    const rows = Array.isArray(out.device_list) ? out.device_list : [];
    setSettingDeviceList(rows);
    if (rows.length) {
      setDevices(rows);
      const fallbackDid = deviceCandidates(rows[0])[0] || "";
      const preferredDid = dids[0] || fallbackDid;
      if (preferredDid) {
        setActiveDid((prev) => prev || preferredDid);
      }
    }
    setPullAskEnabled(Boolean(out.enable_pull_ask));
  }

  function updateSettingField(key: string, value: unknown) {
    setSettingData((prev) => {
      const next = { ...prev, [key]: value };
      setSettingJsonText(JSON.stringify(next, null, 2));
      return next;
    });
  }

  function toggleSettingDid(did: string, checked: boolean) {
    setSelectedSettingDids((prev) => {
      if (checked) {
        return prev.includes(did) ? prev : [...prev, did];
      }
      return prev.filter((x) => x !== did);
    });
  }

  async function loadDevices() {
    const out = (await apiGet<{ devices?: Device[] }>("/device_list")) as { devices?: Device[] };
    const rows = out.devices || [];
    setDevices(rows);
    if (!rows.length) {
      setMessage("未获取到设备，请在设置页完成 OAuth2 登录。");
      return;
    }
    const fallbackDid = deviceCandidates(rows[0])[0] || "";
    if (fallbackDid) {
      setActiveDid((prev) => prev || fallbackDid);
      void (async () => {
        const probe = (await apiGet<PlayingInfo>(`/playingmusic?did=${encodeURIComponent(fallbackDid)}`)) as PlayingInfo;
        if (probe.ret !== "Did not exist") {
          return;
        }
        const resolved = await tryResolveDid(rows[0]);
        if (resolved) {
          setActiveDid(resolved);
          return;
        }
        setMessage("未匹配到可用设备 DID，请在设置页刷新设备列表。");
      })();
      return;
    }

    const did = await tryResolveDid(rows[0]);
    if (did) {
      setActiveDid(did);
      return;
    }
    setMessage("未匹配到可用设备 DID，请在设置页刷新设备列表。");
  }

  async function loadPlaylists() {
    const out = (await apiGet<Record<string, string[]>>("/musiclist")) as Record<string, string[]>;
    setPlaylists(out);
    const names = Object.keys(out);
    if (names.length) {
      setPlaylist((prev) => (prev && out[prev] ? prev : names[0]));
    }
  }

  async function loadStatus(did: string) {
    if (!did) {
      return;
    }
    const [playingResp, volumeResp] = await Promise.allSettled([
      apiGet<PlayingInfo>(`/playingmusic?did=${encodeURIComponent(did)}`),
      apiGet<{ volume?: number }>(`/getvolume?did=${encodeURIComponent(did)}`),
    ]);
    if (playingResp.status === "fulfilled") {
      setStatus(playingResp.value as PlayingInfo);
    }
    if (volumeResp.status === "fulfilled") {
      const vol = volumeResp.value as { volume?: number };
      if (typeof vol.volume === "number") {
        setVolume(vol.volume);
      }
    }
  }

  useEffect(() => {
    void (async () => {
      await Promise.allSettled([loadVersion(), loadOAuthStatus(), loadSettingData(), loadPlaylists()]);
      void loadDevices();
    })();
  }, []);

  useEffect(() => {
    if (!activeDid) {
      return;
    }
    void loadStatus(activeDid);
    const timer = window.setInterval(() => {
      void loadStatus(activeDid);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [activeDid]);

  useEffect(() => {
    if (!songs.length) {
      setMusic("");
      return;
    }
    setMusic((prev) => (prev && songs.includes(prev) ? prev : songs[0]));
  }, [songs]);

  function requireDid(): boolean {
    if (activeDid) {
      return true;
    }
    setMessage("当前无可用设备，无法执行设备控制。");
    return false;
  }

  async function callRetApi(path: string, payload: unknown, okText: string) {
    if (!requireDid()) {
      return;
    }
    const out = (await apiPost<{ ret?: string }>(path, payload)) as { ret?: string };
    setMessage(out.ret === "OK" ? okText : out.ret || "执行失败");
    await loadStatus(activeDid);
  }

  async function playCurrent() {
    if (!requireDid()) {
      return;
    }
    const out = (await apiPost<{ ok?: boolean; error_code?: string; message?: string }>(
      "/api/v1/play_music_list",
      {
        speaker_id: activeDid,
        list_name: playlist,
        music_name: music || "",
      },
    )) as { ok?: boolean; error_code?: string; message?: string };
    if (out.ok) {
      setMessage("开始播放");
      await loadStatus(activeDid);
    } else {
      setMessage(`播放失败：${explainPlaybackError(out.error_code, out.message, null)}`);
    }
  }

  async function togglePlayMode() {
    if (switchingPlayMode) {
      return;
    }
    const prev = playModeIndex;
    const next = (playModeIndex + 1) % PLAY_MODES.length;
    const cmd = PLAY_MODES[next].cmd;
    setPlayModeIndex(next);
    if (!requireDid()) {
      setPlayModeIndex(prev);
      return;
    }
    setSwitchingPlayMode(true);
    try {
      const out = (await apiPost<{ ok?: boolean; message?: string; error_code?: string }>(
        "/api/v1/set_play_mode",
        { speaker_id: activeDid, mode_index: next },
      )) as {
        ok?: boolean;
        message?: string;
        error_code?: string;
      };
      if (out.ok) {
        setMessage(`已切换为${cmd}`);
        return;
      }
      setPlayModeIndex(prev);
      setMessage(out.message || out.error_code || "切换播放模式失败");
    } finally {
      setSwitchingPlayMode(false);
    }
  }

  async function playLink(proxy: boolean) {
    if (!requireDid()) {
      return;
    }
    try {
      const runApiV1Play = async (preferProxy: boolean) => {
        const out = (await apiPost<{ ok?: boolean; error_code?: string; message?: string }>("/api/v1/play_url", {
          speaker_id: activeDid,
          url: linkUrl,
          options: { mode: "network_audio_link", no_cache: false, prefer_proxy: preferProxy, prefer_codec: "auto" },
        })) as {
          ok?: boolean;
          error_code?: string;
          message?: string;
          stage?: string;
          sid?: string;
        };
        return out;
      };

      if (proxy) {
        const out = await runApiV1Play(true);
        if (out.ok) {
          setMessage(`代理播放已发送${out.sid ? `（sid: ${out.sid}）` : ""}`);
        } else {
          setMessage(`代理播放失败：${explainPlaybackError(out.error_code, out.message, out.stage)}`);
        }
        return;
      }

      // Align with original default theme behavior:
      // "播放链接" also uses /api/v1/play_url.
      const directOut = await runApiV1Play(false);
      if (directOut.ok) {
        setMessage(`链接播放已发送${directOut.sid ? `（sid: ${directOut.sid}）` : ""}`);
      } else {
        const p = await runApiV1Play(true);
        if (p.ok) {
          setMessage(
            `链接播放失败：${explainPlaybackError(directOut.error_code, directOut.message, directOut.stage)}；已自动切换代理播放成功${
              p.sid ? `（sid: ${p.sid}）` : ""
            }`,
          );
        } else {
          setMessage(
            `链接播放失败：${explainPlaybackError(directOut.error_code, directOut.message, directOut.stage)}；代理播放也失败：${explainPlaybackError(
              p.error_code,
              p.message,
              p.stage,
            )}`,
          );
        }
      }
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err || "未知错误");
      setMessage(`播放失败：${reason}`);
    }
  }

  async function playTts() {
    if (!requireDid()) {
      return;
    }
    const out = (await apiGet<{ ret?: string }>(
      `/playtts?did=${encodeURIComponent(activeDid)}&text=${encodeURIComponent(ttsText)}`,
    )) as { ret?: string };
    setMessage(out.ret === "OK" ? "文字播放已发送" : out.ret || "文字播放失败");
  }

  async function sendCustomCmd() {
    const cmd = customCmd.trim();
    if (!cmd) {
      setMessage("请输入口令");
      return;
    }
    await callRetApi("/cmd", { did: activeDid, cmd }, "口令已发送");
  }

  async function searchOnline() {
    const kw = searchKeyword.trim();
    if (!kw) {
      setMessage("请输入搜索关键词");
      return;
    }
    const out = (await apiGet<{ data?: OnlineSearchItem[]; success?: boolean; error?: string }>(
      `/api/search/online?keyword=${encodeURIComponent(kw)}&plugin=all&page=1&limit=20`,
    )) as {
      data?: OnlineSearchItem[];
      success?: boolean;
      error?: string;
    };
    if (out.success === false) {
      setMessage(out.error || "搜索失败");
      setSearchResults([]);
      return;
    }
    setSearchResults(out.data || []);
    setSelectedSearchIndex(-1);
    setMessage(`搜索到 ${out.data?.length || 0} 条结果`);
  }

  async function confirmSearch() {
    if (!requireDid()) {
      return;
    }
    const item = searchResults[selectedSearchIndex];
    if (!item) {
      setMessage("请先选择搜索结果");
      return;
    }
    const title = String(item.title || item.name || "");
    if (!title) {
      setMessage("选中结果缺少歌曲名");
      return;
    }
    const out = (await apiPost<{ ok?: boolean; error_code?: string; message?: string }>(
      "/api/v1/play_music",
      {
        speaker_id: activeDid,
        music_name: title,
        search_key: searchKeyword || "",
      },
    )) as { ok?: boolean; error_code?: string; message?: string };
    if (out.ok) {
      setMessage("已发送播放");
      setShowSearch(false);
      await loadStatus(activeDid);
    } else {
      setMessage(`播放失败：${explainPlaybackError(out.error_code, out.message, null)}`);
    }
  }

  async function timedShutdown(label: string) {
    await callRetApi("/cmd", { did: activeDid, cmd: label }, `${label}已发送`);
  }

  async function getQrcode() {
    const out = (await apiGet<QrcodeResp>("/api/get_qrcode")) as QrcodeResp;
    if (out.success === false) {
      setQrcodeStatus(out.message || out.error || "二维码获取失败");
      return;
    }
    if (out.already_logged_in) {
      setQrcodeUrl("");
      setQrcodeExpireAt(0);
      setQrcodeStatus(out.message || "已登录，无需扫码");
      await loadOAuthStatus();
      await loadSettingData();
      await loadDevices();
      return;
    }
    setQrcodeUrl(out.qrcode_url || "");
    const expireSeconds = Number(out.expire_seconds || 120);
    setQrcodeExpireAt(Date.now() + expireSeconds * 1000);
    setQrcodeRemain(expireSeconds);
    setQrcodeStatus(`请使用米家 App 扫码（约 ${expireSeconds}s）`);
    await loadOAuthStatus();
  }

  async function refreshOAuthRuntime() {
    const out = (await apiPost<Record<string, unknown>>("/api/oauth2/refresh", {})) as Record<string, unknown>;
    if (out.refreshed) {
      setQrcodeStatus("刷新成功，正在更新设备列表");
    } else {
      setQrcodeStatus(String(out.last_error || "刷新失败"));
    }
    await loadOAuthStatus();
    await loadSettingData();
    await loadDevices();
  }

  async function logoutOAuth() {
    await apiPost("/api/oauth2/logout", {});
    setQrcodeUrl("");
    setQrcodeExpireAt(0);
    setQrcodeRemain(0);
    setQrcodeStatus("已退出登录");
    await loadOAuthStatus();
    await loadSettingData();
    await loadDevices();
  }

  async function autoFillHost() {
    const out = (await apiGet<{ ok?: boolean; base_url?: string; message?: string }>(
      "/api/v1/detect_base_url",
    )) as { ok?: boolean; base_url?: string; message?: string };
    if (!out.base_url) {
      setMessage(out.message || "自动检测失败");
      return;
    }
    try {
      const u = new URL(out.base_url);
      updateSettingField("hostname", `${u.protocol}//${u.hostname}`);
      updateSettingField("public_port", Number(u.port || (u.protocol === "https:" ? "443" : "80")));
      setMessage(`已自动填充 ${out.base_url}`);
    } catch {
      setMessage("自动检测结果解析失败");
    }
  }

  async function saveSettings() {
    let parsed: Record<string, unknown> = settingData;
    try {
      parsed = JSON.parse(settingJsonText) as Record<string, unknown>;
    } catch {
      setMessage("配置 JSON 格式错误，请先修正");
      return;
    }
    const payload: Record<string, unknown> = {
      ...parsed,
      mi_did: selectedSettingDids.join(","),
    };
    const out = (await apiPost<unknown>("/savesetting", payload)) as unknown;
    if (typeof out === "string" && out.includes("save success")) {
      setMessage("配置已保存");
    } else {
      setMessage("配置已提交");
    }
    await loadSettingData();
    await loadDevices();
    await loadPlaylists();
  }

  useEffect(() => {
    const devices = (settingData.devices || {}) as Record<string, { play_type?: number }>;
    const mode = devices?.[activeDid]?.play_type;
    if (typeof mode === "number" && mode >= 0 && mode < PLAY_MODES.length) {
      setPlayModeIndex(mode);
    }
  }, [activeDid, settingData]);

  async function togglePullAsk() {
    const next = !pullAskEnabled;
    const out = (await apiPost<{ success?: boolean; message?: string }>(
      "/api/system/modifiysetting",
      { enable_pull_ask: next },
    )) as { success?: boolean; message?: string };
    if (out.success) {
      setPullAskEnabled(next);
      updateSettingField("enable_pull_ask", next);
      setMessage(next ? "语音口令已开启" : "语音口令已关闭");
      return;
    }
    setMessage(out.message || "切换失败，请重试");
  }

  async function fetchMusicListJson() {
    const out = (await apiPost<{ ret?: string; content?: string }>("/api/file/fetch_playlist_json", {
      url: fieldValue("music_list_url"),
    })) as { ret?: string; content?: string };
    if (out.ret === "OK") {
      updateSettingField("music_list_json", out.content || "");
      setMessage("歌单内容已获取");
      return;
    }
    setMessage(out.ret || "获取歌单失败");
  }

  async function refreshMusicTag() {
    const out = (await apiPost<{ ret?: string }>("/refreshmusictag", {})) as { ret?: string };
    setMessage(out.ret || "已触发刷新");
  }

  function clearCache() {
    localStorage.clear();
    setMessage("浏览器缓存已清除");
  }

  async function cleanTempDir() {
    const out = (await apiPost<{ ret?: string }>("/api/file/cleantempdir", {})) as { ret?: string };
    setMessage(out.ret || "临时目录清理已触发");
  }

  function fieldValue(key: string): string {
    const v = settingData[key];
    if (v === undefined || v === null) {
      return "";
    }
    if (typeof v === "string") {
      return v;
    }
    if (typeof v === "number" || typeof v === "boolean") {
      return String(v);
    }
    try {
      return JSON.stringify(v);
    } catch {
      return "";
    }
  }

  function renderSettingField(field: SettingField) {
    const value = fieldValue(field.key);
    if (field.kind === "select") {
      const options = field.options || [];
      return (
        <>
          <label htmlFor={field.key}>{field.label}:</label>
          <select id={field.key} value={value} onChange={(e) => updateSettingField(field.key, e.target.value)}>
            {options.map((opt) => (
              <option key={`${field.key}-${opt.value || "empty"}`} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </>
      );
    }
    if (field.kind === "bool") {
      return (
        <>
          <label htmlFor={field.key}>{field.label}:</label>
          <select
            id={field.key}
            value={String(value === "true")}
            onChange={(e) => updateSettingField(field.key, e.target.value === "true")}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        </>
      );
    }
    if (field.kind === "number") {
      return (
        <>
          <label htmlFor={field.key}>{field.label}:</label>
          <input
            id={field.key}
            type="number"
            value={value}
            onChange={(e) => updateSettingField(field.key, Number(e.target.value || 0))}
          />
        </>
      );
    }
    if (field.kind === "password") {
      return (
        <>
          <label htmlFor={field.key}>{field.label}:</label>
          <input
            id={field.key}
            type="password"
            value={value}
            onChange={(e) => updateSettingField(field.key, e.target.value)}
          />
        </>
      );
    }
    if (field.kind === "textarea") {
      return (
        <>
          <label htmlFor={field.key}>{field.label}:</label>
          <textarea
            id={field.key}
            value={value}
            onChange={(e) => updateSettingField(field.key, e.target.value)}
          />
        </>
      );
    }
    return (
      <>
        <label htmlFor={field.key}>{field.label}:</label>
        <input
          id={field.key}
          type="text"
          value={value}
          onChange={(e) => updateSettingField(field.key, e.target.value)}
        />
      </>
    );
  }

  return (
    <>
      <div className="player" role="main" aria-label="音乐播放器">
        <h1>
          XiaoMusic 播放器
          <a
            href="https://github.com/Akari787/xiaomusic-oauth2"
            target="_blank"
            rel="noopener noreferrer"
            className="version-link"
          >
            <span>{version || "-"}</span>
          </a>
        </h1>

        <label htmlFor="did" className="label-with-toggle">
          选择播放设备:
          <div className="toggle-switch-container">
            <label className="toggle-label">语音口令</label>
            <div
              id="pullAskToggle"
              className={`toggle-switch ${pullAskEnabled ? "active" : ""}`}
              role="switch"
              aria-checked={pullAskEnabled ? "true" : "false"}
              aria-label="语音口令开关"
              onClick={() => void togglePullAsk()}
            >
              <div className="toggle-slider"></div>
            </div>
          </div>
        </label>
        <select id="did" className="device-selector" value={activeDid} onChange={(e) => setActiveDid(e.target.value)}>
          {devices.map((d) => {
            const did = d.miotDID || d.did || d.deviceID || "";
            return (
              <option key={`${did}-${resolveDisplayName(d)}`} value={did}>
                {resolveDisplayName(d)}
              </option>
            );
          })}
        </select>

        <label htmlFor="music_list" className="label-with-action">
          选择播放列表:
          <div
            className="option-inline"
            role="button"
            tabIndex={0}
            onClick={() =>
              void (async () => {
                await apiPost("/api/music/refreshlist", {});
                await loadPlaylists();
                setMessage("列表已刷新");
              })()
            }
          >
            <span className="material-icons" aria-hidden="true">
              refresh
            </span>
            <span className="tooltip">刷新列表</span>
          </div>
        </label>
        <select id="music_list" className="playlist-selector" value={playlist} onChange={(e) => setPlaylist(e.target.value)}>
          {Object.keys(playlists).map((name) => (
            <option key={name} value={name}>
              {`${name} (${(playlists[name] || []).length})`}
            </option>
          ))}
        </select>

        <label htmlFor="music_name" className="label-with-action">
          选择歌曲:
        </label>
        <select id="music_name" className="song-selector" value={music} onChange={(e) => setMusic(e.target.value)}>
          {songs.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>

        <div id="device-audio" className="audio-section">
          <progress className="progress" id="progress" value={progress} max={100}></progress>
          <div className="time-info">
            <span className="current-time" id="current-time">
              {formatTime(Number(status.offset || 0))}
            </span>
            <div className="current-song" id="playering-music">
              当前播放歌曲：{status.cur_music || "无"}
            </div>
            <span className="duration" id="duration">
              {formatTime(Number(status.duration || 0))}
            </span>
          </div>
        </div>

        <div className="buttons">
          <div className="player-controls button-group">
            <div id="modeBtn" onClick={() => void togglePlayMode()} className="control-button device-enable" role="button" tabIndex={0}>
              <span className="material-icons" aria-hidden="true">
                {PLAY_MODES[playModeIndex]?.icon || "shuffle"}
              </span>
              <span className="tooltip">{PLAY_MODES[playModeIndex]?.cmd || "切换播放模式"}</span>
            </div>
            <div onClick={() => void callRetApi("/cmd", { did: activeDid, cmd: "上一首" }, "已发送上一首")} className="control-button device-enable" role="button" tabIndex={0}>
              <span className="material-icons" aria-hidden="true">
                skip_previous
              </span>
              <span className="tooltip">上一首</span>
            </div>
            <div onClick={() => void playCurrent()} className="control-button" role="button" tabIndex={0}>
              <span className="material-icons-outlined play" aria-hidden="true">
                play_circle_outline
              </span>
              <span className="tooltip">播放</span>
            </div>
            <div onClick={() => void callRetApi("/cmd", { did: activeDid, cmd: "下一首" }, "已发送下一首")} className="control-button device-enable" role="button" tabIndex={0}>
              <span className="material-icons" aria-hidden="true">
                skip_next
              </span>
              <span className="tooltip">下一首</span>
            </div>
            <div
              onClick={() =>
                void (async () => {
                  if (!requireDid()) {
                    return;
                  }
                  const out = (await apiPost<{ ok?: boolean; error_code?: string; message?: string }>(
                    "/api/v1/stop",
                    { speaker_id: activeDid },
                  )) as { ok?: boolean; error_code?: string; message?: string };
                  setMessage(
                    out.ok
                      ? "已停止"
                      : `停止失败：${explainPlaybackError(out.error_code, out.message, null)}`,
                  );
                  await loadStatus(activeDid);
                })()
              }
              className="control-button device-enable"
              role="button"
              tabIndex={0}
            >
              <span className="material-icons" aria-hidden="true">
                stop
              </span>
              <span className="tooltip">关机</span>
            </div>
          </div>

          <div className="mode-controls button-group">
            <div onClick={() => void callRetApi("/cmd", { did: activeDid, cmd: "加入收藏" }, "已发送收藏命令")} className="favorite icon-item device-enable" role="button" tabIndex={0}>
              <span className="material-icons" aria-hidden="true">
                favorite
              </span>
              <p>收藏</p>
            </div>
            <div onClick={() => setShowVolume(true)} className="icon-item device-enable" role="button" tabIndex={0}>
              <span className="material-icons" aria-hidden="true">
                volume_up
              </span>
              <p>音量</p>
            </div>
            <div onClick={() => setShowSearch(true)} className="icon-item device-enable" role="button" tabIndex={0}>
              <span className="material-icons" aria-hidden="true">
                search
              </span>
              <p>搜索</p>
            </div>
            <div onClick={() => setShowTimer(true)} className="icon-item device-enable" role="button" tabIndex={0}>
              <span className="material-icons" aria-hidden="true">
                timer
              </span>
              <p>定时</p>
            </div>
            <div onClick={() => setShowPlaylink(true)} className="icon-item device-enable" role="button" tabIndex={0}>
              <span className="material-icons" aria-hidden="true">
                emoji_nature
              </span>
              <p>测试</p>
            </div>
            <div onClick={() => setShowSettings(true)} className="icon-item" role="button" tabIndex={0}>
              <span className="material-icons" aria-hidden="true">
                settings
              </span>
              <p>设置</p>
            </div>
          </div>
        </div>
      </div>

      <div
        className={`component-overlay ${
          showSearch || showTimer || showPlaylink || showVolume || showSettings ? "show" : ""
        }`}
        style={{ display: showSearch || showTimer || showPlaylink || showVolume || showSettings ? "block" : "none" }}
        onClick={() => {
          setShowSearch(false);
          setShowTimer(false);
          setShowPlaylink(false);
          setShowVolume(false);
          setShowSettings(false);
        }}
      ></div>

      <div className={`component ${showSearch ? "show" : ""}`} id="search-component" style={{ display: showSearch ? "block" : "none" }}>
        <h2>搜索歌曲</h2>
        <input
          type="text"
          className="search-input"
          placeholder="请输入搜索关键词(如:MV高清版 周杰伦 七里香)"
          value={searchKeyword}
          onChange={(e) => setSearchKeyword(e.target.value)}
        />
        <div className="component-button-group">
          <button onClick={() => void searchOnline()}>搜索</button>
        </div>
        <label>搜索结果:</label>
        <div className="search-results">
          {searchResults.map((it, idx) => {
            const title = String(it.title || it.name || `结果 ${idx + 1}`);
            return (
              <button
                key={`${title}-${idx}`}
                className={selectedSearchIndex === idx ? "selected-item" : ""}
                onClick={() => setSelectedSearchIndex(idx)}
                style={{ width: "100%", marginBottom: 8 }}
              >
                {title}
                {it.artist ? ` - ${String(it.artist)}` : ""}
              </button>
            );
          })}
        </div>
        <div className="component-button-group">
          <button onClick={() => void confirmSearch()}>确定</button>
          <button onClick={() => setShowSearch(false)} className="close-button">
            关闭
          </button>
        </div>
      </div>

      <div className={`component ${showTimer ? "show" : ""}`} id="timer-component" style={{ display: showTimer ? "block" : "none" }}>
        <h2>定时关机</h2>
        <button onClick={() => void timedShutdown("10分钟后关机")}>10分钟后关机</button>
        <button onClick={() => void timedShutdown("30分钟后关机")}>30分钟后关机</button>
        <button onClick={() => void timedShutdown("60分钟后关机")}>60分钟后关机</button>
        <div className="component-button-one">
          <button onClick={() => setShowTimer(false)} className="close-button">
            关闭
          </button>
        </div>
      </div>

      <div className={`component ${showPlaylink ? "show" : ""}`} id="playlink-component" style={{ display: showPlaylink ? "block" : "none" }}>
        <h2>播放测试</h2>
        <div className="card-section">
          <h3 className="card-title">🔗 播放链接</h3>
          <input type="text" className="search-input" value={linkUrl} onChange={(e) => setLinkUrl(e.target.value)} />
          <div className="component-button-group">
            <button onClick={() => void playLink(false)}>播放链接</button>
            <button onClick={() => void playLink(true)}>代理播放</button>
          </div>
        </div>

        <div className="card-section">
          <h3 className="card-title">💬 播放文字</h3>
          <input type="text" className="search-input" value={ttsText} onChange={(e) => setTtsText(e.target.value)} />
          <div className="component-button-group">
            <button onClick={() => void playTts()}>播放文字</button>
          </div>
        </div>

        <div className="card-section">
          <h3 className="card-title">🎤 自定义口令</h3>
          <input type="text" className="search-input" value={customCmd} onChange={(e) => setCustomCmd(e.target.value)} placeholder="请输入自定义口令" />
          <div className="component-button-group">
            <button onClick={() => void sendCustomCmd()}>发送口令</button>
          </div>
        </div>

        {message ? <div className="play-test-message">{message}</div> : null}

        <div className="component-button-one">
          <button onClick={() => setShowPlaylink(false)} className="close-button">
            关闭
          </button>
        </div>
      </div>

      <div className={`component ${showVolume ? "show" : ""}`} id="volume-component" style={{ display: showVolume ? "block" : "none" }}>
        <h2>调节音量</h2>
        <input
          type="range"
          id="volume"
          className="volume-slider"
          min={0}
          max={100}
          value={volume}
          onChange={(e) => setVolume(Number(e.target.value))}
          onMouseUp={() => void callRetApi("/setvolume", { did: activeDid, volume }, "音量已设置")}
          onTouchEnd={() => void callRetApi("/setvolume", { did: activeDid, volume }, "音量已设置")}
        />
        <div className="component-button-one">
          <button onClick={() => setShowVolume(false)} className="close-button">
            关闭
          </button>
        </div>
      </div>

      <div className={`component ${showSettings ? "show" : ""}`} id="settings-component" style={{ display: showSettings ? "block" : "none" }}>
        <h2>XiaoMusic 设置面板</h2>
        <div className="setting-card setting-panel">
          <div className="card-content">
            <label htmlFor="setting-mi-did">*勾选设备(至少勾选1个):</label>
            <button className="option-inline mini-button" onClick={() => void loadSettingData()}>
              获取设备列表
            </button>
            <div id="setting-mi-did" className="device-selection">
              {settingDeviceList.length ? (
                settingDeviceList.map((d) => {
                  const did = String(d.miotDID || "");
                  if (!did) {
                    return null;
                  }
                  const checked = selectedSettingDids.includes(did);
                  return (
                    <label key={`did-${did}`} className="checkbox-label" style={{ display: "block" }}>
                      <input
                        className="custom-checkbox"
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => toggleSettingDid(did, e.target.checked)}
                      />
                      {displayModelName(d)}
                    </label>
                  );
                })
              ) : (
                <div className="login-tips">未发现可用设备，请先完成 OAuth2 扫码登录。</div>
              )}
            </div>
          </div>
        </div>

        <div className="setting-card setting-panel">
          <h3 className="card-title">OAuth2 登录</h3>
          <div className="card-content">
            <div className="component-button-group">
              {!oauthLoggedIn || oauthInProgress ? (
                <button onClick={() => void getQrcode()}>{oauthInProgress ? "重新获取二维码" : "获取二维码"}</button>
              ) : null}
              {oauthLoggedIn && !oauthReady ? (
                <button onClick={() => void refreshOAuthRuntime()}>刷新运行时</button>
              ) : null}
              {oauthLoggedIn ? <button onClick={() => void logoutOAuth()}>退出登录</button> : null}
            </div>
            <div className="oauth-status-item single">
              <span className="oauth-label">登录状态</span>
              <span className={`status-pill ${oauthStatusClass}`}>{oauthStatusLabel}</span>
            </div>
            {!oauthLoggedIn || oauthInProgress ? (
              <p className="oauth-hint">{qrcodeExpireAt ? `请使用米家 App 扫码（约 ${qrcodeRemain}s）` : qrcodeStatus}</p>
            ) : null}
            {qrcodeUrl ? <img src={qrcodeUrl} alt="二维码" style={{ width: 220, maxWidth: "100%" }} /> : null}
          </div>
        </div>

        <div className="setting-card setting-panel">
          <h3 className="card-title">基础设置</h3>
          <div className="card-content">
            <label htmlFor="hostname" className="setting-label">
              *NAS的IP或域名:
            </label>
            <input
              id="hostname"
              type="text"
              value={String(settingData.hostname || "")}
              onChange={(e) => updateSettingField("hostname", e.target.value)}
            />
            <div className="component-button-group">
              <button onClick={() => void autoFillHost()}>自动填</button>
            </div>

            <label htmlFor="public_port" className="setting-label">
              *本地端口:
            </label>
            <input
              id="public_port"
              type="number"
              value={String(settingData.public_port ?? 58090)}
              onChange={(e) => updateSettingField("public_port", Number(e.target.value || 0))}
            />

            <label htmlFor="jellyfin_enabled">启用 Jellyfin 客户端:</label>
            <select
              id="jellyfin_enabled"
              value={String(Boolean(settingData.jellyfin_enabled))}
              onChange={(e) => updateSettingField("jellyfin_enabled", e.target.value === "true")}
            >
              <option value="true">开启</option>
              <option value="false">关闭</option>
            </select>

            <label htmlFor="jellyfin_base_url">Jellyfin 地址:</label>
            <input
              id="jellyfin_base_url"
              type="text"
              value={String(settingData.jellyfin_base_url || "")}
              onChange={(e) => updateSettingField("jellyfin_base_url", e.target.value)}
            />

            <label htmlFor="jellyfin_api_key">Jellyfin API Key:</label>
            <input
              id="jellyfin_api_key"
              type="password"
              value={String(settingData.jellyfin_api_key || "")}
              onChange={(e) => updateSettingField("jellyfin_api_key", e.target.value)}
            />

            <label htmlFor="jellyfin_user_id">Jellyfin User ID(可选):</label>
            <input
              id="jellyfin_user_id"
              type="text"
              value={String(settingData.jellyfin_user_id || "")}
              onChange={(e) => updateSettingField("jellyfin_user_id", e.target.value)}
            />

            <div className="component-button-group">
              <button onClick={() => void saveSettings()}>保存配置</button>
              <button onClick={() => void loadSettingData()}>重载配置</button>
            </div>
          </div>
        </div>

        <div className="advanced-config-toggle" onClick={() => setAdvancedOpen((v) => !v)} role="button" tabIndex={0}>
          <span>⚠️</span>
          <span className="advanced-config-toggle-text">以下为高级配置，请谨慎修改</span>
          <span className="advanced-config-toggle-icon">{advancedOpen ? "▲" : "▼"}</span>
        </div>

        {advancedOpen ? (
          <div className="advanced-config-content">
            <div className="config-tabs" role="tablist" aria-label="高级配置分类">
              {ADVANCED_TABS.map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  className={`config-tab-button ${activeAdvancedTab === tab.key ? "active" : ""}`}
                  onClick={() => setActiveAdvancedTab(tab.key)}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <div className="config-tab-panels">
              {ADVANCED_TABS.map((tab) => (
                <div
                  key={`panel-${tab.key}`}
                  className={`config-tab-content setting-panel ${activeAdvancedTab === tab.key ? "active" : ""}`}
                  style={{ display: activeAdvancedTab === tab.key ? "block" : "none" }}
                >
                  <div className="card-content">
                    <div className="rows">
                      {tab.fields.map((field) => (
                        <div key={`${tab.key}-${field.key}`}>{renderSettingField(field)}</div>
                      ))}
                      {tab.key === "playlist" ? (
                        <div className="component-button-group">
                          <button onClick={() => void fetchMusicListJson()}>获取歌单</button>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <div className="setting-card setting-panel">
              <h3 className="card-title">高级配置(JSON)</h3>
              <textarea
                id="setting-json"
                className="search-input"
                style={{ minHeight: 260, fontFamily: "monospace" }}
                value={settingJsonText}
                onChange={(e) => {
                  const next = e.target.value;
                  setSettingJsonText(next);
                  try {
                    const parsed = JSON.parse(next) as Record<string, unknown>;
                    setSettingData(parsed);
                  } catch {
                    // keep free editing until valid JSON
                  }
                }}
              />
              <div className="component-button-group">
                <button onClick={() => void saveSettings()}>保存配置</button>
              </div>
            </div>
          </div>
        ) : null}

        <div className="setting-footer webui-setting-footer">
          <div className="button-section">
            <div className={`section-header ${operationOpen ? "" : "collapsed"}`} onClick={() => setOperationOpen((v) => !v)}>
              <h3 className="button-section-title">功能操作</h3>
              <span className="section-toggle-icon">{operationOpen ? "▲" : "▼"}</span>
            </div>
            <div className={`section-content ${operationOpen ? "" : "collapsed"}`} style={{ display: operationOpen ? "block" : "none" }}>
              <div className="button-grid">
                <button onClick={() => void refreshMusicTag()}>
                  <span className="material-icons">refresh</span>
                  <span>刷新tag</span>
                </button>
                <button onClick={() => clearCache()}>
                  <span className="material-icons">delete_sweep</span>
                  <span>清空缓存</span>
                </button>
                <button onClick={() => void cleanTempDir()}>
                  <span className="material-icons">folder_delete</span>
                  <span>清空临时目录</span>
                </button>
                <a href="/downloadlog" target="_blank" rel="noreferrer">
                  <button>
                    <span className="material-icons">download</span>
                    <span>下载日志文件</span>
                  </button>
                </a>
              </div>
            </div>
          </div>

          <div className="button-section">
            <div className={`section-header ${toolsOpen ? "" : "collapsed"}`} onClick={() => setToolsOpen((v) => !v)}>
              <h3 className="button-section-title">工具链接</h3>
              <span className="section-toggle-icon">{toolsOpen ? "▲" : "▼"}</span>
            </div>
            <div className={`section-content ${toolsOpen ? "" : "collapsed"}`} style={{ display: toolsOpen ? "block" : "none" }}>
              <div className="link-grid">
                <a href="/docs" target="_blank" className="link-card" rel="noreferrer">
                  <div className="link-card-icon"><span className="material-icons">description</span></div>
                  <div className="link-card-content"><div className="link-card-title">接口文档</div><div className="link-card-description">查看 API 接口文档</div></div>
                </a>
                <a href="#" className="link-card disabled" onClick={(e) => e.preventDefault()}>
                  <div className="link-card-icon"><span className="material-icons">swap_horiz</span></div>
                  <div className="link-card-content"><div className="link-card-title">m3u转换</div><div className="link-card-description">待迁移到 WebUI 工具页</div></div>
                </a>
                <a href="#" className="link-card disabled" onClick={(e) => e.preventDefault()}>
                  <div className="link-card-icon"><span className="material-icons">cloud_download</span></div>
                  <div className="link-card-content"><div className="link-card-title">歌曲下载工具</div><div className="link-card-description">待迁移到 WebUI 工具页</div></div>
                </a>
                <a href="#" className="link-card disabled" onClick={(e) => e.preventDefault()}>
                  <div className="link-card-icon"><span className="material-icons">merge_type</span></div>
                  <div className="link-card-content"><div className="link-card-title">歌单合并工具</div><div className="link-card-description">待迁移到 WebUI 工具页</div></div>
                </a>
              </div>
            </div>
          </div>
        </div>

        <div className="component-button-one">
          <button onClick={() => setShowSettings(false)} className="close-button">
            关闭
          </button>
        </div>
      </div>

      <div className="footer">Powered by XiaoMusic</div>
      <div id="sr-announcer" className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {message}
      </div>
    </>
  );
}
