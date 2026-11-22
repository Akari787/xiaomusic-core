SUPPORT_MUSIC_TYPE = [
    ".mp3",
    ".flac",
    ".wav",
    ".ape",
    ".ogg",
    ".m4a",
    ".wma",
]

# audio_id sent with play_by_music_url; the speaker plays the stream URL, the id
# only has to be a valid catalogue id.
DEFAULT_AUDIO_ID = "1582971365183456177"

COOKIE_TEMPLATE = "deviceId={device_id}; serviceToken={service_token}; userId={user_id}"

PLAY_TYPE_ONE = 0  # 单曲循环
PLAY_TYPE_ALL = 1  # 全部循环
PLAY_TYPE_RND = 2  # 随机播放
PLAY_TYPE_SIN = 3  # 单曲播放
PLAY_TYPE_SEQ = 4  # 顺序播放

# 需要采用 mina 获取对话记录的设备型号
# 需要使用 play_musci 接口的设备型号
NEED_USE_PLAY_MUSIC_API = [
    "X08C",
    "X08E",
    "X8F",
    "X4B",
    "LX05",
    "OH2",
    "OH2P",
    "X6A",
]

