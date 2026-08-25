from __future__ import annotations

import re


ERROR_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DRM", ("drm protected", "protected by drm", "this video is drm")),
    ("REGION_LOCKED", ("not available in your country", "geo restricted", "region restriction", "not available in your region")),
    ("COOKIE_REQUIRED", ("fresh cookies", "cookies are needed", "cookies (not necessarily logged in) are needed", "sign in to confirm", "use --cookies", "cookies are no longer valid")),
    ("LOGIN_REQUIRED", ("login required", "log in to", "sign in to", "authentication required", "members-only")),
    ("BLOCKED", ("captcha", "verify you are human", "too many requests", "http error 429", "http error 403", "403: forbidden", "blocked by", "ip address is blocked")),
    ("NETWORK_ERROR", ("network is unreachable", "connection timed out", "timed out", "temporary failure in name resolution", "failed to establish a new connection", "connection reset", "sslerror", "certificate", "trust anchors")),
    ("FFMPEG_ERROR", ("ffmpeg exited with code", "ffprobe exited with code", "error opening output files", "postprocessing: error")),
    ("VIDEO_UNAVAILABLE", ("video unavailable", "this video has been removed", "private video", "video is no longer available", "tweet unavailable")),
    ("UNSUPPORTED", ("unsupported url", "no suitable extractor", "no extractor")),
    ("PARSE_ERROR", ("unable to extract", "failed to parse", "cannot parse data", "can't find any video", "no video formats found", "requested format is not available", "unable to download api page")),
    ("DOWNLOADER_ERROR", ("fixed output name but more than one file", "traceback (most recent call last)", "nonetype' object has no attribute")),
)


def classify_download_error(message: str) -> str:
    """Map downloader output to a stable, user-facing machine status."""
    normalized = re.sub(r"\s+", " ", message).lower()
    for error_type, needles in ERROR_PATTERNS:
        if any(needle in normalized for needle in needles):
            return error_type
    return "UNKNOWN"
