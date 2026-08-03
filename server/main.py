from __future__ import annotations

import json
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

DATA_DIR = Path(os.getenv("NASFLOW_DATA", "/data"))
DOWNLOAD_DIR = Path(os.getenv("NASFLOW_DOWNLOADS", "/downloads"))
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
    output_path: str | None = None
    quality: str = "best"
    folder: str = "自动分类"
    retry_count: int = 0
    log_tail: str = ""
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
    gallery_hosts = ("instagram.com", "x.com", "twitter.com", "pixiv.net", "flickr.com")
    return "gallery-dl" if any(item in host for item in gallery_hosts) else "yt-dlp"


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
    if task.engine == "gallery-dl":
        return [sys.executable, "-m", "gallery_dl", "--dest", str(target), "--write-metadata", task.url], target

    template = str(target / "%(uploader|未知作者)s/%(title)s [%(id)s].%(ext)s")
    formats = {
        "best": "bv*+ba/b",
        "4k": "bv*[height<=2160]+ba/b[height<=2160]",
        "1080p": "bv*[height<=1080]+ba/b[height<=1080]",
        "audio": "ba/b",
    }
    command = [
        sys.executable, "-m", "yt_dlp", "--newline", "--no-playlist", "--write-info-json",
        "--write-thumbnail",
        "--print", "before_dl:__NASFLOW_TITLE__%(title)s",
        "--print", "after_move:__NASFLOW_FILE__%(filepath)s",
        "-o", template,
        "-f", formats.get(task.quality, formats["best"]),
    ]
    if shutil.which("ffmpeg"):
        command.insert(-4, "--embed-metadata")
    if task.quality == "audio":
        command += ["-x", "--audio-format", "m4a"]
    command.append(task.url)
    return command, target


def run_download(task_id: str) -> None:
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task or task.status == "cancelled":
            return
        command, target = build_command(task)

    update_task(task_id, status="running", error=None, output_path=str(target))
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
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
                update_task(task_id, status="completed", progress=100, eta=None)
            else:
                update_task(task_id, status="failed", error=f"{task.engine} 退出码 {code}")
    except Exception as exc:
        update_task(task_id, status="failed", error=str(exc))
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
