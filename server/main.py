from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import inspect
from sqlmodel import Field as DBField
from sqlmodel import Session, SQLModel, create_engine, select

from server.download_errors import classify_download_error

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
processes: dict[str, subprocess.Popen[str]] = {}
process_lock = threading.Lock()


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
    save_to_obsidian: bool = False
    obsidian_note_path: str | None = None
    obsidian_error: str | None = None
    created_at: datetime = DBField(default_factory=utcnow)
    updated_at: datetime = DBField(default_factory=utcnow)


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


class CreateTask(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    kind: Literal["auto", "video", "gallery"] = "auto"
    quality: Literal["best", "4k", "1080p", "audio"] = "best"
    folder: str = "自动分类"
    save_to_obsidian: bool = False


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


app = FastAPI(title="NASFlow API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("NASFLOW_CORS", "*").split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    with Session(engine) as session:
        row = session.get(Setting, "cookies")
    if not row:
        return None
    try:
        payload = CookiesPayload.model_validate_json(row.value)
    except Exception:
        return None
    match = next((rule for rule in payload.rules if host == rule.domain or host.endswith(f".{rule.domain}")), None)
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


def parse_progress(line: str) -> tuple[float | None, str | None, str | None]:
    percent_match = re.search(r"(\d{1,3}(?:\.\d+)?)%", line)
    speed_match = re.search(r"\bat\s+([^\s]+/s)", line)
    eta_match = re.search(r"\bETA\s+([^\s]+)", line)
    percent = float(percent_match.group(1)) if percent_match else None
    return percent, speed_match.group(1) if speed_match else None, eta_match.group(1) if eta_match else None


def build_command(task: Task) -> tuple[list[str], Path]:
    target = DOWNLOAD_DIR / safe_folder(task.folder)
    target.mkdir(parents=True, exist_ok=True)
    cookie_file = cookie_file_for_url(task.url)
    proxy = configured_proxy()
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
    formats = {
        "best": "bv*+ba/b",
        "4k": "bv*[height<=2160]+ba/b[height<=2160]",
        "1080p": "bv*[height<=1080]+ba/b[height<=1080]",
        "audio": "ba/b",
    }
    command = [
        sys.executable, "-m", "yt_dlp", "--newline", "--write-info-json",
        "--write-thumbnail",
        "--print", "before_dl:__NASFLOW_TITLE__%(title)s",
        "--print", "after_move:__NASFLOW_FILE__%(filepath)s",
        "-o", template,
        "-f", formats.get(task.quality, formats["best"]),
    ]
    if task.subscription_id:
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
        if not task or task.status == "cancelled":
            return
        command, target = build_command(task)

    update_task(task_id, status="running", error=None, error_type=None, output_path=str(target))
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        with process_lock:
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
        with Session(engine) as session:
            current = session.get(Task, task_id)
            was_cancelled = current is not None and current.status == "cancelled"
        if not was_cancelled:
            if code == 0:
                update_task(task_id, status="completed", progress=100, eta=None, error_type=None)
                write_obsidian_note(task_id)
            else:
                with Session(engine) as session:
                    failed_task = session.get(Task, task_id)
                    log_tail = failed_task.log_tail if failed_task else ""
                update_task(
                    task_id,
                    status="failed",
                    error=f"{task.engine} 退出码 {code}",
                    error_type=classify_download_error(log_tail),
                )
    except Exception as exc:
        update_task(task_id, status="failed", error=str(exc), error_type=classify_download_error(str(exc)))
    finally:
        with process_lock:
            processes.pop(task_id, None)


def dispatch(task_id: str) -> None:
    executor.submit(run_download, task_id)


def migrate_schema() -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("task")}
    additions = {
        "quality": "TEXT NOT NULL DEFAULT 'best'",
        "folder": "TEXT NOT NULL DEFAULT '自动分类'",
        "retry_count": "INTEGER NOT NULL DEFAULT 0",
        "log_tail": "TEXT NOT NULL DEFAULT ''",
        "subscription_id": "TEXT",
        "error_type": "TEXT",
        "save_to_obsidian": "BOOLEAN NOT NULL DEFAULT 0",
        "obsidian_note_path": "TEXT",
        "obsidian_error": "TEXT",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.exec_driver_sql(f"ALTER TABLE task ADD COLUMN {name} {definition}")


@app.on_event("startup")
def on_startup() -> None:
    SQLModel.metadata.create_all(engine)
    migrate_schema()
    with Session(engine) as session:
        interrupted = session.exec(select(Task).where(Task.status.in_(["running", "queued"]))).all()
        for task in interrupted:
            task.status = "queued"
            task.error = "服务重启后已自动恢复"
            session.add(task)
        session.commit()
        ids = [task.id for task in interrupted]
    for task_id in ids:
        dispatch(task_id)


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


@app.get("/api/tasks", response_model=list[Task])
def list_tasks(status: str | None = None) -> list[Task]:
    with Session(engine) as session:
        statement = select(Task).order_by(Task.created_at.desc())
        if status:
            statement = statement.where(Task.status == status)
        return list(session.exec(statement).all())


@app.get("/api/tasks/{task_id}", response_model=Task)
def get_task(task_id: str) -> Task:
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        return task


@app.post("/api/tasks", response_model=Task, status_code=201)
def create_task(payload: CreateTask) -> Task:
    if not valid_url(payload.url):
        raise HTTPException(422, "请输入有效的 HTTP/HTTPS 链接")
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


@app.post("/api/tasks/{task_id}/cancel", response_model=Task)
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


@app.post("/api/tasks/{task_id}/retry", response_model=Task)
def retry_task(task_id: str) -> Task:
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        if task.status not in {"failed", "cancelled"}:
            raise HTTPException(409, "只有失败或取消的任务可以重试")
        task.status = "queued"
        task.progress = 0
        task.error = None
        task.error_type = None
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
