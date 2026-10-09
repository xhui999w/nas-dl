from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import uuid
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, computed_field, field_serializer
from sqlalchemy import delete, func, inspect, update
from sqlmodel import Field as DBField
from sqlmodel import Session, SQLModel, create_engine, select

from server.download_errors import classify_download_error
from server.media import file_available, resolve_task_file, video_available, video_metadata, UNSUPPORTED_MESSAGE
from server import auth
from server.playlists import read_playlist, speed_bytes, youtube_playlist_url
from server.douyin_notes import note_id

DATA_DIR = Path(os.getenv("NASFLOW_DATA", "/data"))
DOWNLOAD_DIR = Path(os.getenv("NASFLOW_DOWNLOADS", "/downloads"))
OBSIDIAN_VAULT_DIR = Path(os.getenv("NASFLOW_OBSIDIAN_VAULT", "/obsidian"))
OBSIDIAN_NOTES_DIR = os.getenv("NASFLOW_OBSIDIAN_NOTES_DIR", "视频收藏").strip("/\\") or "视频收藏"
PUBLIC_DOWNLOAD_DIR = Path(os.getenv("NASFLOW_PUBLIC_DOWNLOADS", "/volume2/盘2/media/nas-dl"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_WORKERS = max(1, min(int(os.getenv("NASFLOW_CONCURRENCY", "2")), 8))
engine = create_engine(
    f"sqlite:///{DATA_DIR / 'nasflow.db'}",
    connect_args={"check_same_thread": False},
)
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="nasflow")
catalog_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nasflow-catalog")
processes: dict[str, subprocess.Popen[str]] = {}
process_lock = threading.Lock()
collection_lock = threading.RLock()
catalog_jobs: set[str] = set()
PLACEHOLDER_TITLE = "等待解析"
COOKIE_HOST_ALIASES = {
    "iesdouyin.com": "douyin.com",
    "v.douyin.com": "douyin.com",
    "b23.tv": "bilibili.com",
    "youtu.be": "youtube.com",
    "vm.tiktok.com": "tiktok.com",
    "vt.tiktok.com": "tiktok.com",
    "fb.watch": "facebook.com",
    "twitter.com": "x.com",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Task(SQLModel, table=True):
    id: str = DBField(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    url: str
    title: str = "等待解析"
    engine: str = "yt-dlp"
    status: str = "queued"
    progress: float = 0
    speed: str | None = None
    eta: str | None = None
    error: str | None = None
    error_type: str | None = None
    output_path: str | None = None
    quality: str = "best"
    folder: str = "自动分类"
    retry_count: int = 0
    log_tail: str = ""
    subscription_id: str | None = None
    collection_id: str | None = DBField(default=None, index=True)
    collection_index: int | None = None
    collection_video_id: str | None = None
    save_to_obsidian: bool = False
    obsidian_note_path: str | None = None
    obsidian_error: str | None = None
    created_at: datetime = DBField(default_factory=utcnow)
    updated_at: datetime = DBField(default_factory=utcnow)


class TaskView(Task):
    """API-only fields; no database migration or new stored paths."""

    @computed_field
    @property
    def file_available(self) -> bool:
        return file_available(self.status, self.output_path, DOWNLOAD_DIR)

    @computed_field
    @property
    def media_available(self) -> bool:
        return video_available(self.status, self.output_path, DOWNLOAD_DIR)

    @field_serializer("output_path")
    def public_filename(self, value: str | None) -> str | None:
        return Path(value).name if value else None

    @field_serializer("log_tail")
    def private_download_logs(self, value: str) -> str:
        # Downloader logs can contain absolute filesystem paths and credentials.
        return ""


class Subscription(SQLModel, table=True):
    id: str = DBField(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    name: str
    url: str
    enabled: bool = True
    interval_minutes: int = 360
    quality: str = "best"
    folder: str = "订阅"
    last_checked_at: datetime | None = None
    created_at: datetime = DBField(default_factory=utcnow)


class Setting(SQLModel, table=True):
    key: str = DBField(primary_key=True)
    value: str
    updated_at: datetime = DBField(default_factory=utcnow)


class MediaShare(SQLModel, table=True):
    id: str = DBField(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    task_id: str = DBField(index=True)
    token_hash: str = DBField(index=True, unique=True)
    max_plays: int
    play_count: int = 0
    byte_limit: int = 0
    transferred_bytes: int = 0
    revoked: bool = False
    created_at: datetime = DBField(default_factory=utcnow)


class Collection(SQLModel, table=True):
    id: str = DBField(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    url: str = DBField(index=True, unique=True)
    title: str = "正在读取合集目录"
    state: str = "resolving"
    quality: str = "1080p"
    folder_name: str | None = None
    save_to_obsidian: bool = False
    error: str | None = None
    created_at: datetime = DBField(default_factory=utcnow)


class MediaShareSession(SQLModel, table=True):
    token_hash: str = DBField(primary_key=True)
    share_id: str = DBField(index=True)
    expires_at: float


class MediaShareExternalToken(SQLModel, table=True):
    token_hash: str = DBField(primary_key=True)
    share_id: str = DBField(index=True)
    expires_at: float


class CreateMediaShare(BaseModel):
    plays: int = Field(ge=1, le=1000)


class CreateTask(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    kind: Literal["auto", "video", "gallery"] = "auto"
    quality: Literal["best", "4k", "1080p", "audio"] = "best"
    folder: str = "自动分类"
    save_to_obsidian: bool = False


class StartCollection(BaseModel):
    task_ids: list[str] | None = Field(default=None, max_length=10000)


class CreateSubscription(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=4096)
    interval_minutes: int = Field(default=360, ge=15, le=43200)
    quality: Literal["best", "4k", "1080p", "audio"] = "best"
    folder: str = "订阅"


class SettingsPayload(BaseModel):
    concurrency: int = Field(default=2, ge=1, le=8)
    download_dir: str = "/downloads"
    filename_template: str = "%(uploader)s/%(title)s [%(id)s].%(ext)s"
    proxy: str = ""


class CookieRule(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    cookie: str = Field(min_length=1, max_length=32768)


class CookiesPayload(BaseModel):
    rules: list[CookieRule] = Field(default_factory=list, max_length=100)


class PlatformSyncPayload(BaseModel):
    enabled: dict[str, bool] = Field(default_factory=dict)


class SubscriptionEntry(BaseModel):
    id: str
    title: str
    url: str
    duration: int | None = None
    thumbnail: str | None = None


class DownloadEntriesPayload(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=200)


app = FastAPI(title="NASFlow API", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("NASFLOW_CORS", "*").split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

login_attempts: dict[str, deque[float]] = defaultdict(deque)
login_lock = threading.Lock()
PUBLIC_SHARE_PATH = re.compile(r"/api/shares/[A-Za-z0-9_-]{1,128}(?:/(play|stream))?")


def public_api_request(path: str, method: str) -> bool:
    if path == "/api/health" and method in {"GET", "HEAD"}:
        return True
    if path == "/api/auth/login" and method == "POST":
        return True
    if re.fullmatch(r"/api/media/[a-f0-9]{32}/external-stream", path) and method in {"GET", "HEAD"}:
        return True  # The endpoint validates a separate, expiring owner token.
    match = PUBLIC_SHARE_PATH.fullmatch(path)
    if not match:
        return False
    action = match.group(1)
    return (action is None and method == "GET" or action == "play" and method == "POST"
            or action == "stream" and method in {"GET", "HEAD"})


@app.middleware("http")
async def protect_management(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected_host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
        expected_scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        if origin and origin != f"{expected_scheme}://{expected_host}":
            return JSONResponse({"detail": "请求来源不匹配，请在 NASFlow 页面操作"}, status_code=403)
    if not public_api_request(request.url.path, request.method):
        username = auth.session_username(engine, request.cookies.get(auth.COOKIE_NAME))
        if not username:
            return JSONResponse({"detail": "请先登录管理员账号"}, status_code=401,
                                headers={"Cache-Control": "no-store"})
        request.state.admin_username = username
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChangeCredentialsPayload(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


def limit_login_attempts(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with login_lock:
        attempts = login_attempts[client]
        while attempts and attempts[0] < now - 300:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429, "尝试次数过多，请 5 分钟后重试")
        attempts.append(now)


@app.post("/api/auth/login")
def admin_login(payload: LoginPayload, request: Request, response: Response) -> dict[str, str]:
    limit_login_attempts(request)
    username = auth.verify_login(engine, payload.username, payload.password)
    if not username:
        raise HTTPException(401, "账号或密码不正确")
    token = auth.create_session(engine)
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(auth.COOKIE_NAME, token, max_age=auth.SESSION_SECONDS, httponly=True,
                        secure=secure, samesite="lax", path="/")
    return {"username": username}


@app.get("/api/auth/session")
def admin_session(request: Request) -> dict[str, str]:
    return {"username": request.state.admin_username}


@app.post("/api/auth/logout")
def admin_logout(request: Request, response: Response) -> dict[str, bool]:
    auth.logout(engine, request.cookies.get(auth.COOKIE_NAME, ""))
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"logged_out": True}


@app.post("/api/auth/credentials")
def update_admin_credentials(payload: ChangeCredentialsPayload, request: Request, response: Response) -> dict[str, bool]:
    limit_login_attempts(request)
    username = payload.username.strip()
    if not username:
        raise HTTPException(422, "账号不能为空")
    if not auth.change_credentials(engine, DATA_DIR, username, payload.current_password, payload.new_password):
        raise HTTPException(401, "当前密码不正确")
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"updated": True, "login_required": True}


def valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def safe_folder(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "-_ ").strip()
    return cleaned[:80] or "自动分类"


def choose_engine(url: str, kind: str) -> str:
    if kind == "gallery":
        return "gallery-dl"
    if kind == "video":
        return "yt-dlp"
    host = (urlparse(url).hostname or "").lower()
    path = urlparse(url).path.lower()
    if ("instagram.com" in host and any(marker in path for marker in ("/reel/", "/reels/", "/p/", "/tv/"))) or ((host == "x.com" or host.endswith(".x.com") or "twitter.com" in host) and "/status/" in path):
        return "yt-dlp"
    gallery_hosts = ("instagram.com", "x.com", "twitter.com", "pixiv.net", "flickr.com")
    return "gallery-dl" if any(item in host for item in gallery_hosts) else "yt-dlp"


def platform_for_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "bilibili.com" in host or "b23.tv" in host:
        return "bilibili"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    if host == "x.com" or host.endswith(".x.com") or "twitter.com" in host:
        return "x"
    return host.removeprefix("www.")


def cookie_file_for_url(url: str) -> Path | None:
    host = (urlparse(url).hostname or "").lower().strip(".")
    candidate_hosts = {host}
    for short_host, canonical_host in COOKIE_HOST_ALIASES.items():
        if host == short_host or host.endswith(f".{short_host}"):
            candidate_hosts.add(canonical_host)
    with Session(engine) as session:
        row = session.get(Setting, "cookies")
    if not row:
        return None
    try:
        payload = CookiesPayload.model_validate_json(row.value)
    except Exception:
        return None
    match = next((rule for rule in payload.rules if any(candidate == rule.domain or candidate.endswith(f".{rule.domain}") for candidate in candidate_hosts)), None)
    if not match:
        return None
    cookie_dir = DATA_DIR / "cookies"
    cookie_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    cookie_path = cookie_dir / f"{hashlib.sha256(match.domain.encode()).hexdigest()[:16]}.txt"
    lines = ["# Netscape HTTP Cookie File"]
    for part in match.cookie.replace("\r", "").replace("\n", "").split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        if name:
            lines.append(f".{match.domain}\tTRUE\t/\tTRUE\t2147483647\t{name}\t{value}")
    if len(lines) == 1:
        return None
    cookie_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cookie_path.chmod(0o600)
    return cookie_path


def configured_proxy() -> str | None:
    """Return the persisted downloader proxy, while keeping env proxies working."""
    with Session(engine) as session:
        row = session.get(Setting, "system")
    if not row:
        return None
    try:
        proxy = SettingsPayload.model_validate_json(row.value).proxy.strip()
    except Exception:
        return None
    return proxy or None


def update_task(task_id: str, **values: object) -> None:
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            return
        for key, value in values.items():
            setattr(task, key, value)
        task.updated_at = utcnow()
        session.add(task)
        session.commit()


def append_log(task_id: str, line: str) -> None:
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            return
        lines = (task.log_tail + "\n" + line).strip().splitlines()[-40:]
        task.log_tail = "\n".join(lines)
        task.updated_at = utcnow()
        session.add(task)
        session.commit()


def finish_running_task(task_id: str, **values: object) -> bool:
    """Keep cancellation from being overwritten by a downloader finishing."""
    with Session(engine) as session:
        changed = session.exec(update(Task).where(Task.id == task_id, Task.status == "running")
                               .values(**values, updated_at=utcnow()).returning(Task.id)).scalars().first()
        session.commit()
        return changed is not None


def is_placeholder_title(title: str | None) -> bool:
    return not title or not title.strip() or title.strip() == PLACEHOLDER_TITLE


def source_path_key(value: object) -> str:
    try:
        parsed = urlparse(str(value).strip())
    except (TypeError, ValueError):
        return ""
    host = (parsed.hostname or "").lower().strip(".")
    path = parsed.path.rstrip("/") or "/"
    return f"{host}{path}" if host else ""


def read_info_title(path: Path) -> tuple[str | None, list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, []
    if not isinstance(payload, dict):
        return None, []
    title = str(payload.get("title") or "").strip() or None
    sources = [
        str(payload.get("original_url") or "").strip(),
        str(payload.get("webpage_url") or "").strip(),
    ]
    return title, [source for source in sources if source]


def build_info_title_indexes() -> tuple[dict[str, str], dict[str, str]]:
    exact: dict[str, str] = {}
    by_path: dict[str, str] = {}
    try:
        info_paths = DOWNLOAD_DIR.rglob("*.info.json")
        for info_path in info_paths:
            title, sources = read_info_title(info_path)
            if not title:
                continue
            for source in sources:
                exact[source] = title
                key = source_path_key(source)
                if key:
                    previous = by_path.get(key)
                    by_path[key] = title if previous in (None, title) else ""
    except OSError:
        return exact, by_path
    return exact, by_path


def title_from_output_path(output_path: str | None) -> str | None:
    if not output_path:
        return None
    path = Path(output_path)
    if not path.is_file() and (not path.suffix or path.suffix.lower() in {".json", ".info"}):
        return None
    for info_path in (Path(f"{path}.info.json"), path.with_suffix(".info.json")):
        if info_path.is_file():
            title, _ = read_info_title(info_path)
            if title:
                return title
    name = path.name
    name = re.sub(r"\.[A-Za-z0-9]+$", "", name)
    name = re.sub(r"\s*\[[^\]]+\]$", "", name).strip()
    return name or None


def title_from_task_logs(log_tail: str) -> str | None:
    for line in reversed(log_tail.splitlines()):
        match = re.search(r"(?:Adding metadata to|Destination:|__NASFLOW_FILE__)\s*:?\s*\"?(.+?)\"?$", line)
        if match:
            title = title_from_output_path(match.group(1).strip().strip('"'))
            if title:
                return title
    return None


def recover_task_title(task: Task, exact: dict[str, str], by_path: dict[str, str]) -> str | None:
    if not is_placeholder_title(task.title):
        return task.title.strip()
    if task.url in exact:
        return exact[task.url]
    key = source_path_key(task.url)
    if key and by_path.get(key):
        return by_path[key]
    return title_from_output_path(task.output_path) or title_from_task_logs(task.log_tail)


def backfill_task_titles() -> None:
    with Session(engine) as session:
        pending = list(session.exec(select(Task).where(Task.title == PLACEHOLDER_TITLE)).all())
    if not pending:
        return
    exact, by_path = build_info_title_indexes()
    updates = [(task.id, title) for task in pending if (title := recover_task_title(task, exact, by_path))]
    if not updates:
        return
    with Session(engine) as session:
        for task_id, title in updates:
            task = session.get(Task, task_id)
            if task and is_placeholder_title(task.title):
                task.title = title
                task.updated_at = utcnow()
                session.add(task)
        session.commit()


def parse_progress(line: str) -> tuple[float | None, str | None, str | None]:
    percent_match = re.search(r"(\d{1,3}(?:\.\d+)?)%", line)
    speed_match = re.search(r"\bat\s+([^\s]+/s)", line)
    eta_match = re.search(r"\bETA\s+([^\s]+)", line)
    percent = float(percent_match.group(1)) if percent_match else None
    return percent, speed_match.group(1) if speed_match else None, eta_match.group(1) if eta_match else None


def build_command(task: Task) -> tuple[list[str], Path]:
    target = DOWNLOAD_DIR / safe_folder(task.folder)
    if task.collection_id:
        with Session(engine) as session:
            collection = session.get(Collection, task.collection_id)
        if not collection:
            raise ValueError("合集不存在")
        target = DOWNLOAD_DIR / "合集" / (collection.folder_name or f"{safe_folder(collection.title)[:100]} [{collection.id[:8]}]")
    target.mkdir(parents=True, exist_ok=True)
    cookie_file = cookie_file_for_url(task.url)
    proxy = configured_proxy()
    if task.engine == "yt-dlp" and note_id(task.url):
        command = [sys.executable, "-m", "server.douyin_notes", "--url", task.url, "--target", str(target)]
        if cookie_file:
            command += ["--cookies", str(cookie_file)]
        if proxy:
            command += ["--proxy", proxy]
        return command, target
    if task.engine == "gallery-dl":
        command = [sys.executable, "-m", "gallery_dl", "--dest", str(target), "--write-metadata"]
        if proxy:
            command += ["--proxy", proxy]
        if task.subscription_id:
            archive_dir = DATA_DIR / "archives"
            archive_dir.mkdir(parents=True, exist_ok=True)
            command += ["--download-archive", str(archive_dir / f"{task.subscription_id}.txt")]
        if cookie_file:
            command += ["--cookies", str(cookie_file)]
        command.append(task.url)
        return command, target

    template = str(target / "%(uploader|未知作者)s/%(title)s [%(id)s].%(ext)s")
    infojson_template = str(target / "%(uploader|未知作者)s/%(id)s.info.json")
    if task.collection_id:
        template = str(target / f"{task.collection_index or 0:04d} - %(title)s [%(id)s].%(ext)s")
        infojson_template = str(target / "%(id)s.info.json")
    formats = {
        "best": "bv*+ba/b",
        "4k": "bv*[height<=2160]+ba/b[height<=2160]",
        "1080p": "bv*[height<=1080]+ba/b[height<=1080]",
        "audio": "ba/b",
    }
    host = (urlparse(task.url).hostname or "").lower()
    if task.quality != "audio" and any(host == domain or host.endswith("." + domain)
                                       for domain in ("douyin.com", "iesdouyin.com")):
        # MP4 is a container: Douyin's best playback stream can still be HEVC.
        # Prefer the source's direct H.264/AAC stream, then its download stream;
        # keep the existing fallback when no compatible source is offered.
        for quality in ("best", "4k", "1080p"):
            height = {"4k": "[height<=2160]", "1080p": "[height<=1080]"}.get(quality, "")
            compatible = f"b[ext=mp4][vcodec~='^(avc1|h264)'][acodec~='^(mp4a|aac)']{height}"
            formats[quality] = f"{compatible}[format_id!^=download_addr]/{compatible}/{formats[quality]}"
    if task.collection_id and task.quality != "audio":
        height = {"4k": "[height<=2160]", "1080p": "[height<=1080]"}.get(task.quality, "")
        formats[task.quality] = f"bv[ext=mp4][vcodec^=avc1]{height}+ba[ext=m4a]/b[ext=mp4][vcodec^=avc1]{height}"
    command = [
        sys.executable, "-m", "yt_dlp", "--newline", "--progress", "--write-info-json",
        "--write-thumbnail",
        "--print", "before_dl:__NASFLOW_TITLE__%(title)s",
        "--print", "after_move:__NASFLOW_FILE__%(filepath)s",
        "-o", template,
        "-o", f"infojson:{infojson_template}",
        "-f", formats.get(task.quality, formats["best"]),
    ]
    if task.collection_id:
        command += ["--no-playlist", "--merge-output-format", "mp4"]
    elif task.subscription_id:
        archive_dir = DATA_DIR / "archives"
        archive_dir.mkdir(parents=True, exist_ok=True)
        command += ["--download-archive", str(archive_dir / f"{task.subscription_id}.txt"), "--lazy-playlist"]
    else:
        command.append("--no-playlist")
    if shutil.which("ffmpeg"):
        command.append("--embed-metadata")
    if task.quality == "audio":
        command += ["-x", "--audio-format", "m4a"]
    if cookie_file:
        command += ["--cookies", str(cookie_file)]
    if proxy:
        command += ["--proxy", proxy]
    command.append(task.url)
    return command, target


def safe_note_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:120] or "未命名视频"


def write_obsidian_note(task_id: str) -> None:
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task or not task.save_to_obsidian or not task.output_path:
            return
        session.expunge(task)
    try:
        video_path = Path(task.output_path)
        if not video_path.exists():
            raise FileNotFoundError(f"下载文件不存在: {video_path}")
        try:
            relative_video = video_path.resolve().relative_to(DOWNLOAD_DIR.resolve())
            public_video_path = PUBLIC_DOWNLOAD_DIR / relative_video
        except ValueError:
            public_video_path = video_path
        notes_dir = OBSIDIAN_VAULT_DIR / OBSIDIAN_NOTES_DIR
        notes_dir.mkdir(parents=True, exist_ok=True)
        note_path = notes_dir / f"{safe_note_name(task.title)} [{task.id[:8]}].md"
        downloaded_at = utcnow().astimezone().isoformat(timespec="seconds")
        size = video_path.stat().st_size
        frontmatter = {
            "title": task.title,
            "source": platform_for_url(task.url),
            "source_url": task.url,
            "video_path": str(public_video_path),
            "file_size": size,
            "downloaded_at": downloaded_at,
            "nasflow_task_id": task.id,
            "tags": ["NASFlow", "视频收藏"],
        }
        yaml_lines = ["---"]
        for key, value in frontmatter.items():
            yaml_lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        yaml_lines += ["---", "", f"# {task.title}", "", f"- 来源平台：{platform_for_url(task.url)}", f"- 原始链接：[打开原页面]({task.url})", f"- NAS 视频：`{public_video_path}`", f"- 文件大小：{round(size / 1024 / 1024, 1)} MB", f"- 下载时间：{downloaded_at}", "", "> 视频文件由 NASFlow 单独保存在 NAS 媒体库中，本笔记不复制视频本体。", ""]
        note_path.write_text("\n".join(yaml_lines), encoding="utf-8")
        update_task(task_id, obsidian_note_path=str(note_path), obsidian_error=None)
    except Exception as exc:
        update_task(task_id, obsidian_error=str(exc))


def run_download(task_id: str) -> None:
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task or task.status != "queued":
            return
        claimed = session.exec(update(Task).where(Task.id == task_id, Task.status == "queued")
                               .values(status="running", updated_at=utcnow()).returning(Task.id)).scalars().first()
        session.commit()
        if not claimed:
            return
        session.refresh(task)
    try:
        command, target = build_command(task)
        update_task(task_id, speed=None, eta=None, error=None, error_type=None, log_tail="", output_path=str(target))
        with process_lock:
            with Session(engine) as session:
                current = session.get(Task, task_id)
                if not current or current.status != "running":
                    return
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace",
                                       env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
            processes[task_id] = process
        assert process.stdout
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            append_log(task_id, line)
            if line.startswith("__NASFLOW_TITLE__"):
                update_task(task_id, title=line.removeprefix("__NASFLOW_TITLE__").strip())
                continue
            if line.startswith("__NASFLOW_FILE__"):
                update_task(task_id, output_path=line.removeprefix("__NASFLOW_FILE__").strip())
                continue
            percent, speed, eta = parse_progress(line)
            values: dict[str, object] = {}
            if percent is not None:
                values["progress"] = max(0, min(percent, 100))
            if speed:
                values["speed"] = speed
            if eta:
                values["eta"] = eta
            if line.startswith("[download] Destination:"):
                values["output_path"] = line.split(":", 1)[1].strip()
            if values:
                update_task(task_id, **values)

        code = process.wait()
        if code == 0:
            if finish_running_task(task_id, status="completed", progress=100, speed=None, eta=None, error_type=None):
                write_obsidian_note(task_id)
        else:
            with Session(engine) as session:
                failed_task = session.get(Task, task_id)
                log_tail = failed_task.log_tail if failed_task else ""
            finish_running_task(task_id, status="failed", speed=None, eta=None,
                                error=f"{task.engine} 退出码 {code}",
                                error_type=classify_download_error(log_tail))
    except Exception as exc:
        finish_running_task(task_id, status="failed", speed=None, eta=None,
                            error=str(exc), error_type=classify_download_error(str(exc)))
    finally:
        with process_lock:
            processes.pop(task_id, None)


def dispatch(task_id: str) -> None:
    executor.submit(run_download, task_id)


def migrate_schema() -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("task")}
    share_columns = {column["name"] for column in inspect(engine).get_columns("mediashare")}
    additions = {
        "quality": "TEXT NOT NULL DEFAULT 'best'",
        "folder": "TEXT NOT NULL DEFAULT '自动分类'",
        "retry_count": "INTEGER NOT NULL DEFAULT 0",
        "log_tail": "TEXT NOT NULL DEFAULT ''",
        "subscription_id": "TEXT",
        "collection_id": "TEXT",
        "collection_index": "INTEGER",
        "collection_video_id": "TEXT",
        "error_type": "TEXT",
        "save_to_obsidian": "BOOLEAN NOT NULL DEFAULT 0",
        "obsidian_note_path": "TEXT",
        "obsidian_error": "TEXT",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.exec_driver_sql(f"ALTER TABLE task ADD COLUMN {name} {definition}")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_task_collection_id ON task (collection_id)")
        connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ix_collection_video ON task (collection_id, collection_video_id)")
        share_additions = {
            "byte_limit": "INTEGER NOT NULL DEFAULT 0",
            "transferred_bytes": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in share_additions.items():
            if name not in share_columns:
                connection.exec_driver_sql(f"ALTER TABLE mediashare ADD COLUMN {name} {definition}")


@app.on_event("startup")
def on_startup() -> None:
    SQLModel.metadata.create_all(engine)
    migrate_schema()
    auth.initialize_admin(engine, DATA_DIR)
    backfill_task_titles()
    with Session(engine) as session:
        interrupted = session.exec(select(Task).where(Task.status.in_(["running", "queued"]))).all()
        for task in interrupted:
            collection = session.get(Collection, task.collection_id) if task.collection_id else None
            task.status = "paused" if collection and collection.state == "paused" else "cancelled" if collection and collection.state == "cancelled" else "queued"
            task.error = "服务重启后已自动恢复"
            session.add(task)
        session.commit()
        ids = [task.id for task in interrupted if task.status == "queued"]
        catalog_ids = list(session.exec(select(Collection.id).where(Collection.state == "resolving")).all())
    for task_id in ids:
        dispatch(task_id)
    for collection_id in catalog_ids:
        dispatch_catalog(collection_id)


@app.on_event("shutdown")
def on_shutdown() -> None:
    with process_lock:
        running = list(processes.values())
    for process in running:
        process.terminate()


@app.get("/api/health")
def health() -> dict[str, object]:
    with process_lock:
        running = len(processes)
    return {"status": "ok", "version": app.version, "running": running, "concurrency": MAX_WORKERS}


@app.get("/api/storage")
def storage() -> dict[str, int | float | str]:
    """Return real filesystem usage for the mounted downloads directory."""
    usage = shutil.disk_usage(DOWNLOAD_DIR)
    percent = round((usage.used / usage.total) * 100, 1) if usage.total else 0
    return {
        "path": str(DOWNLOAD_DIR),
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "percent": percent,
    }


@app.get("/api/tasks", response_model=list[TaskView])
def list_tasks(status: str | None = None, include_collections: bool = False) -> list[Task]:
    with Session(engine) as session:
        statement = select(Task).order_by(Task.created_at.desc())
        if not include_collections:
            statement = statement.where(Task.collection_id == None)
        if status:
            statement = statement.where(Task.status == status)
        return list(session.exec(statement).all())


def collection_summaries(collection_id: str | None = None) -> list[dict]:
    with Session(engine) as session:
        statement = select(Collection).order_by(Collection.created_at.desc())
        if collection_id:
            statement = statement.where(Collection.id == collection_id)
        groups = session.exec(statement).all()
        if collection_id and not groups:
            raise HTTPException(404, "合集不存在")
        ids = [group.id for group in groups]
        counts: dict[str, dict[str, int]] = {group.id: {} for group in groups}
        if ids:
            rows = session.exec(select(Task.collection_id, Task.status, func.count(Task.id))
                                .where(Task.collection_id.in_(ids)).group_by(Task.collection_id, Task.status)).all()
            for key, status, count in rows:
                counts[key][status] = count
        speeds: dict[str, float] = {}
        for key, value in session.exec(select(Task.collection_id, Task.speed)
                                       .where(Task.collection_id.in_(ids), Task.status == "running")).all() if ids else []:
            speeds[key] = speeds.get(key, 0) + speed_bytes(value)
        result = []
        for group in groups:
            values = {key: counts[group.id].get(key, 0) for key in ("pending", "queued", "running", "paused", "completed", "failed", "cancelled")}
            total = sum(values.values())
            selected = total - values["pending"]
            status = group.state
            if status == "active":
                status = "running" if values["running"] else "queued" if values["queued"] else "paused" if values["paused"] else "failed" if values["failed"] else "cancelled" if values["cancelled"] else "completed" if selected else "ready"
            result.append({"id": group.id, "title": group.title, "url": group.url,
                           "state": group.state, "status": status, "error": group.error,
                           "quality": group.quality, "total": total, "selected": selected, **values,
                           "progress": round(values["completed"] / selected * 100, 1) if selected else 0,
                           "speed_bytes": speeds.get(group.id, 0), "created_at": group.created_at})
        return result


def resolve_collection(collection_id: str) -> None:
    try:
        with Session(engine) as session:
            collection = session.get(Collection, collection_id)
            if not collection or collection.state != "resolving":
                return
            url = collection.url
            existing = set(session.exec(select(Task.collection_video_id).where(Task.collection_id == collection_id)).all())
        batch = []

        def save_batch():
            with collection_lock, Session(engine) as session:
                current = session.get(Collection, collection_id)
                if not current or current.state != "resolving":
                    return False
                for entry in batch:
                    session.add(Task(url=entry["url"], title=entry["title"], status="pending", engine="yt-dlp",
                                     collection_id=collection_id, collection_index=int(entry["index"]),
                                     collection_video_id=entry["video_id"], quality=current.quality,
                                     save_to_obsidian=current.save_to_obsidian, folder="合集"))
                session.commit()
                batch.clear()
                return True

        started = time.monotonic()
        for entry in read_playlist(url, cookie_file_for_url(url), configured_proxy()):
            if time.monotonic() - started > 900:
                raise TimeoutError("读取目录超时")
            if "video_id" not in entry:
                with collection_lock, Session(engine) as session:
                    current = session.get(Collection, collection_id)
                    if not current or current.state != "resolving":
                        return
                    current.title = str(entry["title"])[:300]
                    if not current.folder_name:
                        current.folder_name = f"{safe_folder(current.title)[:100]} [{current.id[:8]}]"
                    session.add(current)
                    session.commit()
                continue
            if entry["video_id"] in existing:
                continue
            existing.add(entry["video_id"])
            batch.append(entry)
            if len(batch) >= 25 and not save_batch():
                return
        if not save_batch():
            return
        with collection_lock, Session(engine) as session:
            current = session.get(Collection, collection_id)
            if current and current.state == "resolving":
                if not existing:
                    raise ValueError("播放列表没有可读取的视频")
                current.state = "ready"
                current.error = None
                session.add(current)
                session.commit()
    except Exception as exc:
        with collection_lock, Session(engine) as session:
            current = session.get(Collection, collection_id)
            if current and current.state == "resolving":
                category = classify_download_error(str(exc))
                messages = {"COOKIE_REQUIRED": "读取合集需要 YouTube Cookie，请在账号与 Cookies 中配置。",
                            "LOGIN_REQUIRED": "该合集需要登录，请配置 YouTube Cookie。",
                            "NETWORK_ERROR": "读取合集网络连接失败，请检查代理后重新读取。"}
                current.state = "failed"
                current.error = messages.get(category, "合集目录读取失败，请确认播放列表可访问后重新读取。")
                session.add(current)
                session.commit()


def dispatch_catalog(collection_id: str) -> None:
    with collection_lock:
        if collection_id in catalog_jobs:
            return
        catalog_jobs.add(collection_id)
    def work():
        try:
            resolve_collection(collection_id)
        finally:
            with collection_lock:
                catalog_jobs.discard(collection_id)
    catalog_executor.submit(work)


@app.get("/api/collections")
def list_collections() -> list[dict]:
    return collection_summaries()


@app.post("/api/collections", status_code=201)
def create_collection(payload: CreateTask) -> dict:
    url = youtube_playlist_url(payload.url)
    if not url:
        raise HTTPException(422, "请粘贴含 list= 的 YouTube 播放列表链接")
    with collection_lock, Session(engine) as session:
        collection = session.exec(select(Collection).where(Collection.url == url)).first()
        if collection:
            return collection_summaries(collection.id)[0]
        collection = Collection(url=url, quality=payload.quality, save_to_obsidian=payload.save_to_obsidian)
        session.add(collection)
        session.commit()
        session.refresh(collection)
        collection_id = collection.id
    dispatch_catalog(collection_id)
    return collection_summaries(collection_id)[0]


@app.get("/api/collections/{collection_id}/entries")
def collection_entries(collection_id: str, page: int = Query(default=1, ge=1),
                       page_size: int = Query(default=50, ge=1, le=100),
                       view: Literal["download", "library"] = "download",
                       status: Literal["all", "pending", "completed", "failed", "cancelled"] = "all") -> dict:
    summary = collection_summaries(collection_id)[0]
    with Session(engine) as session:
        filters = [Task.collection_id == collection_id]
        if view == "library":
            filters.append(Task.status.in_(["completed", "failed", "cancelled"]))
        if status != "all":
            filters.append(Task.status == status)
        total = session.exec(select(func.count(Task.id)).where(*filters)).one()
        entries = session.exec(select(Task).where(*filters).order_by(Task.collection_index, Task.id)
                               .offset((page - 1) * page_size).limit(page_size)).all()
        return {"collection": summary, "page": page, "page_size": page_size, "total": total,
                "entries": [TaskView.model_validate(entry).model_dump() for entry in entries]}


@app.post("/api/collections/{collection_id}/start")
def start_collection(collection_id: str, payload: StartCollection) -> dict:
    with collection_lock, Session(engine) as session:
        collection = session.get(Collection, collection_id)
        if not collection:
            raise HTTPException(404, "合集不存在")
        if collection.state == "resolving" or collection.error:
            raise HTTPException(409, "请先完成合集目录读取")
        statement = select(Task).where(Task.collection_id == collection_id, Task.status == "pending")
        if payload.task_ids is not None:
            if not payload.task_ids:
                raise HTTPException(422, "请先选择视频")
            statement = statement.where(Task.id.in_(payload.task_ids))
            valid_ids = set(session.exec(select(Task.id).where(Task.collection_id == collection_id, Task.id.in_(payload.task_ids))).all())
            if valid_ids != set(payload.task_ids):
                raise HTTPException(422, "选中的视频不属于该合集")
        tasks = session.exec(statement).all()
        ids = []
        for task in tasks:
            claimed = session.exec(update(Task).where(Task.id == task.id, Task.status == "pending")
                                   .values(status="queued", updated_at=utcnow()).returning(Task.id)).scalars().first()
            if claimed:
                ids.append(claimed)
        collection.state = "active"
        session.add(collection)
        session.commit()
    for task_id in ids:
        dispatch(task_id)
    return collection_summaries(collection_id)[0]


@app.post("/api/collections/{collection_id}/{action}")
def control_collection(collection_id: str, action: Literal["pause", "resume", "cancel", "retry", "rescan"]) -> dict:
    with collection_lock, Session(engine) as session:
        collection = session.get(Collection, collection_id)
        if not collection:
            raise HTTPException(404, "合集不存在")
        if action == "rescan":
            busy = session.exec(select(Task.id).where(Task.collection_id == collection_id, Task.status.in_(["running", "queued"]))).first()
            if busy or collection_id in catalog_jobs:
                raise HTTPException(409, "请在合集停止下载或读取后重新读取目录")
            collection.state = "resolving"
            collection.error = None
            session.add(collection)
            session.commit()
            ids = []
        else:
            if collection.state == "resolving" and action != "cancel":
                raise HTTPException(409, "合集目录正在读取")
            if collection.error and action in {"resume", "retry"}:
                raise HTTPException(409, "请先重新读取合集目录")
            source = {"pause": ["queued"], "resume": ["paused", "cancelled"],
                      "cancel": ["paused", "queued", "running"], "retry": ["failed"]}[action]
            tasks = session.exec(select(Task).where(Task.collection_id == collection_id, Task.status.in_(source))).all()
            with process_lock:
                if action in {"resume", "retry"} and any(task.id in processes for task in tasks):
                    raise HTTPException(409, "下载进程正在结束，请稍后重试")
            ids = []
            for task in tasks:
                values = {"status": {"pause": "paused", "cancel": "cancelled"}.get(action, "queued"),
                          "speed": None, "eta": None, "error": "用户取消" if action == "cancel" else None,
                          "error_type": None, "updated_at": utcnow()}
                if action == "retry":
                    values.update(retry_count=Task.retry_count + 1, progress=0)
                changed = session.exec(update(Task).where(Task.id == task.id, Task.status.in_(source))
                                       .values(**values).returning(Task.id)).scalars().first()
                if changed:
                    ids.append(changed)
            collection.state = {"pause": "paused", "cancel": "cancelled"}.get(action, "active")
            session.add(collection)
            session.commit()
    if action == "rescan":
        dispatch_catalog(collection_id)
    elif action in {"resume", "retry"}:
        for task_id in ids:
            dispatch(task_id)
    elif action == "cancel":
        with process_lock:
            running = [processes[task_id] for task_id in ids if task_id in processes]
        for process in running:
            process.terminate()
    return collection_summaries(collection_id)[0]


@app.get("/api/tasks/{task_id}", response_model=TaskView)
def get_task(task_id: str) -> Task:
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        return task


@app.get("/api/tasks/{task_id}/file", response_class=FileResponse)
def download_task_file(task_id: str) -> FileResponse:
    """Download a completed task result without exposing arbitrary NAS paths."""
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        if task.status != "completed":
            raise HTTPException(409, "任务尚未完成")
        output_path = task.output_path

    if not output_path:
        raise HTTPException(404, "任务没有可下载的文件")

    download_root = DOWNLOAD_DIR.resolve()
    candidate = Path(output_path).resolve()
    if not candidate.is_relative_to(download_root):
        raise HTTPException(403, "文件不在下载目录中")
    if not candidate.is_file():
        raise HTTPException(404, "下载文件不存在")

    return FileResponse(
        candidate,
        filename=candidate.name,
        media_type="application/octet-stream",
        content_disposition_type="attachment",
    )


@app.get("/api/media/{task_id}")
def get_media(task_id: str) -> dict[str, object]:
    task = get_task(task_id)
    path = resolve_task_file(task.status, task.output_path, DOWNLOAD_DIR)
    if not video_available(task.status, task.output_path, DOWNLOAD_DIR):
        raise HTTPException(415, UNSUPPORTED_MESSAGE)
    return {"id": task.id, "title": task.title, "source": platform_for_url(task.url), **video_metadata(path)}


@app.api_route("/api/media/{task_id}/stream", methods=["GET", "HEAD"], response_class=FileResponse)
def stream_media(task_id: str) -> FileResponse:
    task = get_task(task_id)
    path = resolve_task_file(task.status, task.output_path, DOWNLOAD_DIR)
    if not video_available(task.status, task.output_path, DOWNLOAD_DIR):
        raise HTTPException(415, UNSUPPORTED_MESSAGE)
    media = video_metadata(path)
    if not media["supported"]:
        raise HTTPException(415, UNSUPPORTED_MESSAGE)
    # Starlette FileResponse reads bounded chunks and implements Range/If-Range,
    # 206 Content-Range, 416 for unsatisfiable ranges, and bodyless HEAD responses.
    media_type = media["mime_type"] or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=str(media_type), filename=path.name,
                        content_disposition_type="inline", headers={"X-Content-Type-Options": "nosniff"})


@app.post("/api/media/{task_id}/external-token")
def create_owner_media_token(task_id: str, request: Request) -> dict[str, str]:
    metadata = get_media(task_id)
    if not metadata["supported"]:
        raise HTTPException(415, UNSUPPORTED_MESSAGE)
    token = secrets.token_urlsafe(32)
    with Session(engine) as session:
        session.exec(delete(auth.OwnerMediaToken).where(auth.OwnerMediaToken.expires_at <= time.time()))
        session.add(auth.OwnerMediaToken(token_hash=auth.token_hash(token), task_id=task_id,
                                        admin_session_hash=auth.token_hash(request.cookies[auth.COOKIE_NAME]),
                                        expires_at=time.time() + 8 * 60 * 60))
        session.commit()
    return {"token": token}


@app.api_route("/api/media/{task_id}/external-stream", methods=["GET", "HEAD"], response_class=FileResponse)
def stream_owner_external_media(task_id: str, token: str = "") -> FileResponse:
    if not token or len(token) > 128:
        raise HTTPException(403, "播放器授权已失效")
    with Session(engine) as session:
        saved = session.get(auth.OwnerMediaToken, auth.token_hash(token))
        admin = session.get(auth.AdminSession, saved.admin_session_hash) if saved else None
        if not saved or saved.task_id != task_id or saved.expires_at <= time.time() or not admin or admin.expires_at <= time.time():
            raise HTTPException(403, "播放器授权已失效")
    return stream_media(task_id)


def _share_by_token(token: str) -> MediaShare:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with Session(engine) as session:
        share = session.exec(select(MediaShare).where(MediaShare.token_hash == token_hash)).first()
        if not share or share.revoked:
            raise HTTPException(404, "分享链接已失效或不存在")
        return share


def _share_media(token: str) -> tuple[MediaShare, Task, Path, dict[str, object]]:
    share = _share_by_token(token)
    task = get_task(share.task_id)
    path = resolve_task_file(task.status, task.output_path, DOWNLOAD_DIR)
    media = video_metadata(path)
    _ensure_share_quota(share, path)
    return share, task, path, media


def _ensure_share_quota(share: MediaShare, path: Path) -> None:
    if share.byte_limit > 0:
        return
    byte_limit = max(1, int(path.stat().st_size * share.max_plays * 1.2))
    with Session(engine) as session:
        session.exec(update(MediaShare).where(MediaShare.id == share.id, MediaShare.byte_limit == 0)
                     .values(byte_limit=byte_limit))
        session.commit()
        current = session.get(MediaShare, share.id)
        if current:
            share.byte_limit = current.byte_limit
            share.transferred_bytes = current.transferred_bytes


def _share_quota_fields(share: MediaShare) -> dict[str, int]:
    return {
        "byte_limit": share.byte_limit,
        "transferred_bytes": share.transferred_bytes,
        "remaining_bytes": max(0, share.byte_limit - share.transferred_bytes),
    }


def _reserve_share_bytes(share_id: str, amount: int) -> int:
    if amount <= 0:
        return 0
    for _ in range(3):
        with Session(engine) as session:
            share = session.get(MediaShare, share_id)
            if not share or share.revoked:
                return 0
            available = max(0, share.byte_limit - share.transferred_bytes)
            if available == 0:
                return 0
            reserved_bytes = min(amount, available)
            reserved = session.exec(
                update(MediaShare)
                .where(MediaShare.id == share_id, MediaShare.revoked == False,
                       MediaShare.transferred_bytes + reserved_bytes <= MediaShare.byte_limit)
                .values(transferred_bytes=MediaShare.transferred_bytes + reserved_bytes)
                .returning(MediaShare.id)
            ).first()
            session.commit()
            if reserved:
                return reserved_bytes
    return 0


class ShareQuotaFileResponse(FileResponse):
    def __init__(self, *args: object, share_id: str, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.share_id = share_id
        self.chunk_size = 256 * 1024

    async def __call__(self, scope: dict[str, object], receive: object, send: object) -> None:
        closed = False
        count_body = False

        async def quota_send(message: dict[str, object]) -> None:
            nonlocal closed, count_body
            if message.get("type") == "http.response.start":
                count_body = scope.get("method") != "HEAD" and message.get("status") in (200, 206)
                if count_body:
                    # The final body may be cut short at the exact quota boundary.
                    # Without Content-Length, the server can close it cleanly instead
                    # of reporting a mismatched fixed-length response.
                    headers = message.get("headers", [])
                    message = {**message, "headers": [(key, value) for key, value in headers
                                                        if key.lower() != b"content-length"]}
            if message.get("type") == "http.response.body":
                if closed:
                    return
                body = message.get("body", b"")
                if count_body and isinstance(body, bytes) and body:
                    reserved = _reserve_share_bytes(self.share_id, len(body))
                    if reserved < len(body):
                        closed = True
                        await send({**message, "body": body[:reserved], "more_body": False})  # type: ignore[operator]
                        return
            await send(message)  # type: ignore[operator]

        # Disable ASGI's zero-copy path-send extension so every video chunk
        # passes through quota_send, including under servers that support sendfile.
        quota_scope = dict(scope)
        extensions = dict(scope.get("extensions", {}))  # type: ignore[arg-type]
        extensions.pop("http.response.pathsend", None)
        quota_scope["extensions"] = extensions
        await super().__call__(quota_scope, receive, quota_send)  # type: ignore[arg-type]


@app.get("/api/tasks/{task_id}/shares")
def list_media_shares(task_id: str) -> list[dict[str, object]]:
    task = get_task(task_id)
    with Session(engine) as session:
        shares = session.exec(select(MediaShare).where(MediaShare.task_id == task_id).order_by(MediaShare.created_at.desc())).all()
    if shares and video_available(task.status, task.output_path, DOWNLOAD_DIR):
        path = resolve_task_file(task.status, task.output_path, DOWNLOAD_DIR)
        for item in shares:
            _ensure_share_quota(item, path)
    return [{"id": item.id, "max_plays": item.max_plays, "play_count": item.play_count,
             "revoked": item.revoked, **_share_quota_fields(item),
             "created_at": item.created_at.isoformat()} for item in shares]


@app.post("/api/tasks/{task_id}/shares", status_code=201)
def create_media_share(task_id: str, payload: CreateMediaShare) -> dict[str, object]:
    task = get_task(task_id)
    if not video_available(task.status, task.output_path, DOWNLOAD_DIR):
        raise HTTPException(415, UNSUPPORTED_MESSAGE)
    path = resolve_task_file(task.status, task.output_path, DOWNLOAD_DIR)
    token = secrets.token_urlsafe(32)
    byte_limit = max(1, int(path.stat().st_size * payload.plays * 1.2))
    share = MediaShare(task_id=task_id, token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                       max_plays=payload.plays, byte_limit=byte_limit)
    with Session(engine) as session:
        session.add(share)
        session.commit()
        session.refresh(share)
    return {"id": share.id, "token": token, "max_plays": share.max_plays, "play_count": 0,
            "revoked": False, **_share_quota_fields(share), "created_at": share.created_at.isoformat()}


@app.delete("/api/shares/{share_id}")
def revoke_media_share(share_id: str) -> dict[str, bool]:
    with Session(engine) as session:
        share = session.get(MediaShare, share_id)
        if not share:
            raise HTTPException(404, "分享链接不存在")
        share.revoked = True
        session.add(share)
        session.exec(delete(MediaShareSession).where(MediaShareSession.share_id == share_id))
        session.exec(delete(MediaShareExternalToken).where(MediaShareExternalToken.share_id == share_id))
        session.commit()
    return {"revoked": True}


@app.get("/api/shares/{token}")
def get_shared_media(token: str) -> dict[str, object]:
    share, task, _path, media = _share_media(token)
    return {"title": task.title, "source": platform_for_url(task.url), **media,
            "max_plays": share.max_plays, "play_count": share.play_count,
            "remaining_plays": max(0, share.max_plays - share.play_count), **_share_quota_fields(share)}


@app.post("/api/shares/{token}/play")
def start_shared_playback(token: str, request: Request, response: Response) -> dict[str, object]:
    share, task, _path, media = _share_media(token)
    if not media["supported"]:
        raise HTTPException(415, UNSUPPORTED_MESSAGE)
    if share.transferred_bytes >= share.byte_limit:
        raise HTTPException(410, "此分享链接的流量额度已用完")
    share_cookie = f"nasflow_share_{token[:12]}"
    existing_cookie = request.cookies.get(share_cookie)
    current_time = utcnow().timestamp()
    with Session(engine) as session:
        session.exec(delete(MediaShareSession).where(MediaShareSession.expires_at <= current_time))
        session.exec(delete(MediaShareExternalToken).where(MediaShareExternalToken.expires_at <= current_time))
        if existing_cookie:
            existing_hash = hashlib.sha256(existing_cookie.encode("utf-8")).hexdigest()
            active_session = session.get(MediaShareSession, existing_hash)
            if active_session and active_session.share_id == share.id and active_session.expires_at > current_time:
                return {"started": True, "already_counted": True,
                        "remaining_plays": max(0, share.max_plays - share.play_count),
                        "title": task.title, **_share_quota_fields(share),
                        "source": platform_for_url(task.url), **media}

        reserved_id = session.exec(
            update(MediaShare)
            .where(MediaShare.id == share.id, MediaShare.revoked == False, MediaShare.play_count < MediaShare.max_plays)
            .values(play_count=MediaShare.play_count + 1)
            .returning(MediaShare.id)
        ).first()
        if not reserved_id:
            raise HTTPException(410, "此分享链接的播放次数已经用完")
        current_share = session.get(MediaShare, share.id)
        new_cookie = secrets.token_urlsafe(32)
        session.add(MediaShareSession(token_hash=hashlib.sha256(new_cookie.encode("utf-8")).hexdigest(),
                                      share_id=share.id, expires_at=current_time + 8 * 60 * 60))
        session.commit()
        remaining = max(0, current_share.max_plays - current_share.play_count)
        quota = _share_quota_fields(current_share)
    response.set_cookie(share_cookie, new_cookie, max_age=8 * 60 * 60,
                        httponly=True, secure=True, samesite="lax",
                        path=f"/nas-api/api/shares/{token}/")
    return {"started": True, "already_counted": False, "remaining_plays": remaining,
            "title": task.title, "source": platform_for_url(task.url), **quota, **media}


@app.api_route("/api/shares/{token}/stream", methods=["GET", "HEAD"], response_class=FileResponse)
def stream_shared_media(token: str, request: Request) -> FileResponse:
    share, task, path, media = _share_media(token)
    if not media["supported"]:
        raise HTTPException(415, UNSUPPORTED_MESSAGE)
    session_cookie = request.cookies.get(f"nasflow_share_{token[:12]}")
    if not session_cookie:
        raise HTTPException(403, "请先点击开始播放")
    session_hash = hashlib.sha256(session_cookie.encode("utf-8")).hexdigest()
    with Session(engine) as session:
        active_session = session.get(MediaShareSession, session_hash)
        if not active_session or active_session.share_id != share.id or active_session.expires_at <= utcnow().timestamp():
            raise HTTPException(403, "播放会话已过期，请重新点击开始播放")
        current_share = session.get(MediaShare, share.id)
        if not current_share or current_share.transferred_bytes >= current_share.byte_limit:
            raise HTTPException(410, "此分享链接的流量额度已用完")
    media_type = media["mime_type"] or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return ShareQuotaFileResponse(path, share_id=share.id, media_type=str(media_type), filename=path.name,
                                  content_disposition_type="inline", headers={"X-Content-Type-Options": "nosniff"})


@app.post("/api/tasks", response_model=TaskView, status_code=201)
def create_task(payload: CreateTask) -> Task:
    if not valid_url(payload.url):
        raise HTTPException(422, "请输入有效的 HTTP/HTTPS 链接")
    if youtube_playlist_url(payload.url):
        raise HTTPException(422, "这是 YouTube 合集链接，请通过新版下载中心读取合集目录")
    task = Task(
        url=payload.url,
        engine=choose_engine(payload.url, payload.kind),
        quality=payload.quality,
        folder=safe_folder(payload.folder),
        save_to_obsidian=payload.save_to_obsidian,
    )
    with Session(engine) as session:
        session.add(task)
        session.commit()
        session.refresh(task)
    dispatch(task.id)
    return task


@app.post("/api/tasks/{task_id}/cancel", response_model=TaskView)
def cancel_task(task_id: str) -> Task:
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        if task.status in {"completed", "failed", "cancelled"}:
            raise HTTPException(409, "该任务当前无法取消")
        task.status = "cancelled"
        task.error = "用户取消"
        task.updated_at = utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)
    with process_lock:
        process = processes.get(task_id)
    if process:
        process.terminate()
    return task


@app.post("/api/tasks/{task_id}/retry", response_model=TaskView)
def retry_task(task_id: str) -> Task:
    with process_lock:
        if task_id in processes:
            raise HTTPException(409, "下载进程正在结束，请稍后重试")
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        missing_file = task.status == "completed" and not file_available(task.status, task.output_path, DOWNLOAD_DIR)
        if task.status not in {"failed", "cancelled"} and not missing_file:
            raise HTTPException(409, "只有失败、取消或文件不存在的任务可以重试")
        if task.collection_id:
            collection = session.get(Collection, task.collection_id)
            if collection and collection.state in {"paused", "resolving"}:
                raise HTTPException(409, "请先继续合集下载")
            if collection:
                collection.state = "active"
                session.add(collection)
        task.status = "queued"
        task.progress = 0
        task.speed = None
        task.eta = None
        task.error = None
        task.error_type = None
        if missing_file:
            task.output_path = None
        task.retry_count += 1
        task.updated_at = utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)
    dispatch(task.id)
    return task


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str) -> dict[str, bool]:
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        if task.status in {"running", "queued"}:
            raise HTTPException(409, "请先取消任务再删除")
        session.delete(task)
        session.commit()
    return {"deleted": True}


@app.get("/api/subscriptions", response_model=list[Subscription])
def list_subscriptions() -> list[Subscription]:
    with Session(engine) as session:
        return list(session.exec(select(Subscription).order_by(Subscription.created_at.desc())).all())


@app.post("/api/subscriptions", response_model=Subscription, status_code=201)
def create_subscription(payload: CreateSubscription) -> Subscription:
    if not valid_url(payload.url):
        raise HTTPException(422, "请输入有效的订阅链接")
    item = Subscription(**payload.model_dump(exclude={"folder"}), folder=safe_folder(payload.folder))
    with Session(engine) as session:
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


def create_subscription_sync_task(item: Subscription) -> Task:
    task = Task(
        url=item.url,
        title=f"同步订阅 · {item.name}",
        engine=choose_engine(item.url, "auto"),
        quality=item.quality,
        folder=item.folder,
        subscription_id=item.id,
    )
    with Session(engine) as session:
        session.add(task)
        stored = session.get(Subscription, item.id)
        if stored:
            stored.last_checked_at = utcnow()
            session.add(stored)
        session.commit()
        session.refresh(task)
    dispatch(task.id)
    return task


@app.post("/api/subscriptions/sync", response_model=list[Task])
def sync_subscriptions(platform: str | None = None) -> list[Task]:
    with Session(engine) as session:
        items = list(session.exec(select(Subscription).where(Subscription.enabled == True)).all())  # noqa: E712
    if platform:
        items = [item for item in items if platform_for_url(item.url) == platform]
    with Session(engine) as session:
        platform_row = session.get(Setting, "subscription_platforms")
    if platform_row:
        try:
            switches = PlatformSyncPayload.model_validate_json(platform_row.value).enabled
            items = [item for item in items if switches.get(platform_for_url(item.url), True)]
        except Exception:
            pass
    if not items:
        raise HTTPException(404, "没有符合条件的已启用订阅")
    return [create_subscription_sync_task(item) for item in items]


@app.post("/api/subscriptions/{subscription_id}/sync", response_model=Task)
def sync_subscription(subscription_id: str) -> Task:
    with Session(engine) as session:
        item = session.get(Subscription, subscription_id)
        if not item:
            raise HTTPException(404, "订阅不存在")
        session.expunge(item)
    return create_subscription_sync_task(item)


@app.patch("/api/subscriptions/{subscription_id}/toggle", response_model=Subscription)
def toggle_subscription(subscription_id: str) -> Subscription:
    with Session(engine) as session:
        item = session.get(Subscription, subscription_id)
        if not item:
            raise HTTPException(404, "订阅不存在")
        item.enabled = not item.enabled
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


@app.get("/api/subscription-platforms", response_model=PlatformSyncPayload)
def get_subscription_platforms() -> PlatformSyncPayload:
    with Session(engine) as session:
        row = session.get(Setting, "subscription_platforms")
    return PlatformSyncPayload.model_validate_json(row.value) if row else PlatformSyncPayload()


@app.put("/api/subscription-platforms", response_model=PlatformSyncPayload)
def save_subscription_platforms(payload: PlatformSyncPayload) -> PlatformSyncPayload:
    allowed = {str(key)[:60]: bool(value) for key, value in payload.enabled.items()}
    saved = PlatformSyncPayload(enabled=allowed)
    with Session(engine) as session:
        row = session.get(Setting, "subscription_platforms") or Setting(key="subscription_platforms", value="")
        row.value = saved.model_dump_json()
        row.updated_at = utcnow()
        session.add(row)
        session.commit()
    return saved


@app.get("/api/subscriptions/{subscription_id}/entries", response_model=list[SubscriptionEntry])
def list_subscription_entries(subscription_id: str) -> list[SubscriptionEntry]:
    with Session(engine) as session:
        item = session.get(Subscription, subscription_id)
        if not item:
            raise HTTPException(404, "订阅不存在")
        session.expunge(item)
    command = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "--dump-single-json", "--playlist-end", "100", item.url]
    cookie_file = cookie_file_for_url(item.url)
    if cookie_file:
        command[3:3] = ["--cookies", str(cookie_file)]
    proxy = configured_proxy()
    if proxy:
        command[3:3] = ["--proxy", proxy]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=90,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    if result.returncode != 0:
        raise HTTPException(502, (result.stderr or "读取订阅目录失败")[-600:])
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(502, "订阅目录解析失败") from exc
    entries: list[SubscriptionEntry] = []
    for entry in data.get("entries") or []:
        entry_url = entry.get("webpage_url") or entry.get("url")
        if not entry_url or not str(entry_url).startswith(("http://", "https://")):
            continue
        entries.append(SubscriptionEntry(id=str(entry.get("id") or entry_url), title=str(entry.get("title") or "未命名内容"), url=str(entry_url), duration=entry.get("duration"), thumbnail=entry.get("thumbnail")))
    return entries


@app.post("/api/subscriptions/{subscription_id}/entries", response_model=list[Task], status_code=201)
def download_subscription_entries(subscription_id: str, payload: DownloadEntriesPayload) -> list[Task]:
    with Session(engine) as session:
        item = session.get(Subscription, subscription_id)
        if not item:
            raise HTTPException(404, "订阅不存在")
        tasks = [Task(url=url, title=f"订阅选取 · {item.name}", engine=choose_engine(url, "auto"), quality=item.quality, folder=item.folder, subscription_id=item.id) for url in payload.urls if valid_url(url)]
        if not tasks:
            raise HTTPException(422, "没有有效的内容链接")
        for task in tasks:
            session.add(task)
        session.commit()
        for task in tasks:
            session.refresh(task)
    for task in tasks:
        dispatch(task.id)
    return tasks


@app.get("/api/settings", response_model=SettingsPayload)
def get_settings() -> SettingsPayload:
    with Session(engine) as session:
        row = session.get(Setting, "system")
        if not row:
            return SettingsPayload(concurrency=MAX_WORKERS, download_dir=str(DOWNLOAD_DIR))
        return SettingsPayload.model_validate_json(row.value)


@app.put("/api/settings", response_model=SettingsPayload)
def save_settings(payload: SettingsPayload) -> SettingsPayload:
    with Session(engine) as session:
        row = session.get(Setting, "system") or Setting(key="system", value="")
        row.value = payload.model_dump_json()
        row.updated_at = utcnow()
        session.add(row)
        session.commit()
    return payload


@app.get("/api/cookies", response_model=CookiesPayload)
def get_cookies() -> CookiesPayload:
    with Session(engine) as session:
        row = session.get(Setting, "cookies")
        return CookiesPayload.model_validate_json(row.value) if row else CookiesPayload()


@app.put("/api/cookies", response_model=CookiesPayload)
def save_cookies(payload: CookiesPayload) -> CookiesPayload:
    normalized: list[CookieRule] = []
    seen: set[str] = set()
    for rule in payload.rules:
        domain = rule.domain.lower().strip().removeprefix("https://").removeprefix("http://").split("/", 1)[0].strip(".")
        if not re.fullmatch(r"[a-z0-9.-]+", domain) or "." not in domain:
            raise HTTPException(422, f"无效域名: {rule.domain}")
        if domain in seen:
            raise HTTPException(422, f"域名重复: {domain}")
        seen.add(domain)
        normalized.append(CookieRule(domain=domain, cookie=rule.cookie.strip()))
    result = CookiesPayload(rules=normalized)
    with Session(engine) as session:
        row = session.get(Setting, "cookies") or Setting(key="cookies", value="")
        row.value = result.model_dump_json()
        row.updated_at = utcnow()
        session.add(row)
        session.commit()
    return result
