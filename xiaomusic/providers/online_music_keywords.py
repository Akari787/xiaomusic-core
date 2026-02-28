"""Keyword parsing helpers for online music search."""


def build_keyword(song_name, artist):
    if song_name and artist:
        return f"{song_name}-{artist}"
    if song_name:
        return song_name
    if artist:
        return artist
    return ""


def parse_keyword_by_dash(keyword):
    if "-" in keyword:
        parts = keyword.split("-", 1)
        return parts[0].strip(), parts[1].strip()
    return keyword, ""
