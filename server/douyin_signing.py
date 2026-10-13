"""Sign Douyin detail queries using the site's published web checksum scheme.

Reference: https://github.com/yt-dlp/yt-dlp/issues/9667 (2026-10-11).
Implemented here without copying a third-party downloader or sending tokens to it.
"""
import hashlib
from urllib.parse import quote_plus

SIGNATURE_KEY = "x-secsdk-web-signature"
SALT = "A96D855A08C0A9707F8BEF0D9A527E4E"


def form_query(query: dict[str, str]) -> str:
    # Match URLSearchParams, including its encoding of '~' and preservation of '*'.
    def encode(value: str) -> str:
        return quote_plus(str(value), safe="*").replace("~", "%7E")
    return "&".join(f"{encode(key)}={encode(value)}" for key, value in query.items())


def signed_detail_query(query: dict, uifid: str) -> dict[str, str]:
    if not uifid or len(uifid) > 8192 or any(ord(char) < 32 for char in uifid):
        raise ValueError("Fresh cookies are needed：缺少有效的网站请求标识，请更新 Cookie")
    values = {str(key): str(value) for key, value in query.items() if key != SIGNATURE_KEY}
    values["uifid"] = uifid
    material = f"{uifid}_{values.get('timestamp', '')}_{SALT}_{form_query(values)}"
    values[SIGNATURE_KEY] = hashlib.md5(material.encode("utf-8")).hexdigest()
    return values
