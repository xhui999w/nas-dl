"""Small adapter for Douyin image posts; ordinary video downloads stay in yt-dlp."""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests

OFFICIAL_HOST = "www.douyin.com"
CDN_DOMAINS = ("douyinpic.com", "byteimg.com", "ibytedtos.com", "pstatp.com")


def note_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"douyin.com", "www.douyin.com", "iesdouyin.com", "www.iesdouyin.com"}:
        return None
    match = re.fullmatch(r"/(?:share/)?note/(\d{10,24})/?", parsed.path)
    return match.group(1) if match else None


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value.encode("utf-8")[:160].decode("utf-8", errors="ignore").strip(" .") or "图文作品"


def cookie_header(cookie_file: str | None) -> str:
    if not cookie_file:
        return ""
    jar = http.cookiejar.MozillaCookieJar(cookie_file)
    jar.load(ignore_discard=True)
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in jar
                     if cookie.domain.lstrip(".") in {"douyin.com", OFFICIAL_HOST} and not cookie.is_expired())


def read_note(post_id: str, cookie_file: str | None, proxy: str | None) -> dict:
    headers = {"Referer": f"https://{OFFICIAL_HOST}/note/{post_id}"}
    cookie = cookie_header(cookie_file)
    if cookie:
        headers["Cookie"] = cookie
    # Never follow a redirect while carrying a configured login credential.
    response = requests.get(f"https://{OFFICIAL_HOST}/aweme/v1/web/aweme/detail/",
                      params={"aweme_id": post_id, "aid": "6383", "device_platform": "webapp",
                              "version_code": "190600", "pc_client_type": "1"},
                      headers=headers, proxy=proxy, impersonate="chrome", timeout=25,
                      allow_redirects=False)
    try:
        if response.status_code in {403, 429}:
            raise ValueError("网站风控拦截（HTTP Error 403/429），请稍后重试")
        if response.status_code != 200 or not response.content.strip():
            raise ValueError("Fresh cookies are needed：网站未返回作品信息，请更新网站 Cookie 后重试")
        try:
            data = response.json()
        except ValueError:
            raise ValueError("Fresh cookies are needed：网站未返回作品信息，请更新网站 Cookie 后重试") from None
    finally:
        response.close()
    detail = data.get("aweme_detail")
    if not isinstance(detail, dict):
        reason = (data.get("filter_detail") or {}).get("filter_reason")
        if reason == "images_base":
            raise ValueError("Unable to extract image post：网站未返回图文详情")
        raise ValueError("Video unavailable：作品不存在、已删除或当前账号无权访问")
    if str(detail.get("aweme_id")) != post_id:
        raise ValueError("Unable to extract image post：作品信息不匹配")
    return detail


def image_urls(image: dict) -> list[str]:
    values = [*(image.get("download_url_list") or []), *(image.get("url_list") or [])]
    return list(dict.fromkeys(url for url in values if isinstance(url, str)))


def valid_image_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (parsed.scheme == "https" and not parsed.username and not parsed.password
            and parsed.port in {None, 443}
            and any(host == domain or host.endswith("." + domain) for domain in CDN_DOMAINS))


def image_extension(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}:
        return "avif"
    return None


def fetch_image(urls: list[str], proxy: str | None) -> tuple[bytes, str]:
    for url in urls:
        if not valid_image_url(url):
            continue
        try:
            # Public CDN requests never receive the owner's login Cookie.
            response = requests.get(url, proxy=proxy, headers={"Referer": "https://www.douyin.com/"},
                                    impersonate="chrome", stream=True, timeout=40, allow_redirects=False)
            try:
                if response.status_code != 200:
                    continue
                chunks, size = [], 0
                for chunk in response.iter_content(chunk_size=65536):
                    size += len(chunk)
                    if size > 50 * 1024 * 1024:
                        raise ValueError("图片文件过大")
                    chunks.append(chunk)
                data = b"".join(chunks)
            finally:
                response.close()
            ext = image_extension(data)
            if ext:
                return data, ext
        except requests.RequestsError:
            continue
    raise ValueError("Unable to extract image post：图片地址失效或下载失败，请重试")


def download_note(url: str, target: Path, cookie_file: str | None, proxy: str | None) -> Path:
    post_id = note_id(url)
    if not post_id:
        raise ValueError("Unsupported URL：不是支持的图文分享链接")
    detail = read_note(post_id, cookie_file, proxy)
    images = detail.get("images") or []
    if not images or not all(isinstance(image, dict) for image in images) or len(images) > 200:
        raise ValueError("Unable to extract image post：作品没有可下载的图片")
    title = detail.get("desc") or f"图文作品 {post_id}"
    print("__NASFLOW_TITLE__" + str(title).replace("\n", " ").replace("\r", " "), flush=True)
    author = safe_name((detail.get("author") or {}).get("nickname") or "未知作者")
    destination = target / author / f"{safe_name(str(title))} [{post_id}].zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".zip.part")
    # Atomic completion: a failed or cancelled job never exposes a partial ZIP.
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED) as archive:
            for index, image in enumerate(images, 1):
                data, ext = fetch_image(image_urls(image), proxy)
                archive.writestr(f"{index:03d}.{ext}", data)
                print(f"[download] {index / len(images) * 100:.1f}% ({index}/{len(images)} 图片)", flush=True)
            archive.writestr("作品信息.json", json.dumps({"title": title, "uploader": author,
                                                       "source_url": url, "image_count": len(images)}, ensure_ascii=False, indent=2))
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    info = {"title": title, "uploader": author, "id": post_id, "original_url": url, "ext": "zip"}
    destination.with_suffix(".info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    print("__NASFLOW_FILE__" + str(destination), flush=True)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--cookies")
    parser.add_argument("--proxy")
    args = parser.parse_args()
    try:
        download_note(args.url, Path(args.target), args.cookies, args.proxy)
        return 0
    except requests.RequestsError:
        print("ERROR: Network connection timed out or failed while fetching image post", file=sys.stderr)
    except Exception as error:
        # Do not log upstream exceptions with request URLs, headers, or credentials.
        message = str(error) if isinstance(error, ValueError) else "Unable to extract image post：图文下载失败"
        print("ERROR: " + message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
