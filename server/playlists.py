"""Read YouTube playlist catalogs without downloading their video files."""
import re
from urllib.parse import parse_qs, urlencode, urlparse


def youtube_playlist_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"https", "http"} or host not in {
            "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be",
        }:
            return None
        playlist = parse_qs(parsed.query).get("list", [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", playlist):
            return None
        return "https://www.youtube.com/playlist?" + urlencode({"list": playlist})
    except ValueError:
        return None


def playlist_entry(entry: dict, index: int) -> dict | None:
    video_id = str(entry.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return None
    title = str(entry.get("title") or "未命名视频").strip()
    return {"video_id": video_id, "title": title, "index": entry.get("playlist_index") or index,
            "url": "https://www.youtube.com/watch?" + urlencode({"v": video_id})}


def read_playlist(url: str, cookie_file=None, proxy=None):
    # Lazy flat extraction fetches catalog pages, not each video's media formats.
    from yt_dlp import YoutubeDL
    options = {"extract_flat": "in_playlist", "lazy_playlist": True, "skip_download": True,
               "quiet": True, "no_warnings": True, "socket_timeout": 20,
               "retries": 3, "extractor_retries": 3}
    if cookie_file:
        options["cookiefile"] = str(cookie_file)
    if proxy:
        options["proxy"] = proxy
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
        if not info or info.get("_type") not in {"playlist", "multi_video"}:
            raise ValueError("没有读取到播放列表")
        yield {"title": info.get("title") or "YouTube 合集"}
        for index, entry in enumerate(info.get("entries") or (), 1):
            if entry:
                item = playlist_entry(entry, index)
                if item:
                    yield item


def speed_bytes(value: str | None) -> float:
    match = re.fullmatch(r"\s*([\d.]+)\s*([KMGT]?)(i?B)/s\s*", value or "", re.I)
    if not match:
        return 0
    try:
        power = "KMGT".find(match[2].upper()) + 1 if match[2] else 0
        return float(match[1]) * (1024 if match[3].lower() == "ib" else 1000) ** power
    except ValueError:
        return 0
