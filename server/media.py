"""Read existing task files; never accept a client-supplied filesystem path."""

import json
import subprocess
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

UNSUPPORTED_MESSAGE = "当前视频格式暂不支持网页直接播放，可下载后使用本地播放器观看。"
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".webm", ".ogv", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".ts", ".m2ts"}
BROWSER_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg"}


def resolve_task_file(status: str, output_path: str | None, root: Path) -> Path:
    if status != "completed":
        raise HTTPException(409, "任务尚未完成")
    if not output_path:
        raise HTTPException(404, "任务没有可用的文件")
    try:
        candidate = Path(output_path).resolve()
        if not candidate.is_relative_to(root.resolve()):
            raise HTTPException(403, "文件不在下载目录中")
        if not candidate.is_file():
            raise HTTPException(404, "下载文件不存在")
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(404, "下载文件不可用") from None
    return candidate


def file_available(status: str, output_path: str | None, root: Path) -> bool:
    try:
        resolve_task_file(status, output_path, root)
        return True
    except HTTPException:
        return False


def video_available(status: str, output_path: str | None, root: Path) -> bool:
    try:
        return resolve_task_file(status, output_path, root).suffix.lower() in VIDEO_EXTENSIONS
    except HTTPException:
        return False


@lru_cache(maxsize=128)
def _probe_codecs(path: str, size: int, modified: int) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    # size/mtime invalidate the small in-memory cache when a task file changes.
    # ffprobe is already shipped with the downloader; it only reads metadata.
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "json", path],
            capture_output=True, text=True, timeout=8, check=True,
        )
        streams = json.loads(result.stdout).get("streams", [])
        return (
            tuple(s.get("codec_name", "unknown") for s in streams if s.get("codec_type") == "video"),
            tuple(s.get("codec_name", "unknown") for s in streams if s.get("codec_type") == "audio"),
        )
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        # When probing is unavailable, let the browser detect unsupported codecs.
        return None


def video_metadata(path: Path) -> dict[str, object]:
    suffix = path.suffix.lower()
    mime = BROWSER_TYPES.get(suffix)
    supported = mime is not None
    if supported:
        try:
            stat = path.stat()
        except OSError:
            raise HTTPException(404, "下载文件不可用") from None
        codecs = _probe_codecs(str(path), stat.st_size, stat.st_mtime_ns)
        if codecs is not None:
            video, audio = codecs
            allowed_video, allowed_audio = (
                ({"h264"}, {"aac", "mp3"}) if mime == "video/mp4" else
                ({"vp8", "vp9", "av1"}, {"opus", "vorbis"}) if mime == "video/webm" else
                ({"theora"}, {"vorbis", "opus"})
            )
            supported = bool(video) and all(c in allowed_video for c in video) and all(c in allowed_audio for c in audio)
    return {"format": suffix.lstrip("."), "mime_type": mime, "supported": supported,
            "message": None if supported else UNSUPPORTED_MESSAGE}
