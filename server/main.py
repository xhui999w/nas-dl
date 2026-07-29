from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlmodel import Field as DBField
from sqlmodel import Session, SQLModel, create_engine, select

DATA_DIR = Path(os.getenv("NASFLOW_DATA", "/data"))
DOWNLOAD_DIR = Path(os.getenv("NASFLOW_DOWNLOADS", "/downloads"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{DATA_DIR / 'nasflow.db'}", connect_args={"check_same_thread": False})


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
    created_at: datetime = DBField(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = DBField(default_factory=lambda: datetime.now(timezone.utc))


class CreateTask(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    kind: Literal["auto", "video", "gallery"] = "auto"
    quality: Literal["best", "4k", "1080p", "audio"] = "best"
    folder: str = "自动分类"


app = FastAPI(title="NASFlow API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("NASFLOW_CORS", "*").split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    SQLModel.metadata.create_all(engine)


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
        task.updated_at = datetime.now(timezone.utc)
        session.add(task)
        session.commit()


def run_download(task_id: str, quality: str, folder: str) -> None:
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            return
        url, selected_engine = task.url, task.engine

    target = DOWNLOAD_DIR / safe_folder(folder)
    target.mkdir(parents=True, exist_ok=True)
    update_task(task_id, status="running")

    if selected_engine == "gallery-dl":
        command = ["gallery-dl", "--dest", str(target), "--write-metadata", url]
    else:
        template = str(target / "%(uploader|未知作者)s/%(title)s [%(id)s].%(ext)s")
        command = [
            "yt-dlp", "--newline", "--no-playlist", "--write-info-json",
            "--write-thumbnail", "--embed-metadata", "-o", template,
        ]
        formats = {
            "best": "bv*+ba/b",
            "4k": "bv*[height<=2160]+ba/b[height<=2160]",
            "1080p": "bv*[height<=1080]+ba/b[height<=1080]",
            "audio": "ba/b",
        }
        command += ["-f", formats[quality]]
        if quality == "audio":
            command += ["-x", "--audio-format", "m4a"]
        command.append(url)

    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        assert process.stdout
        for line in process.stdout:
            line = line.strip()
            if "[download]" in line and "%" in line:
                try:
                    percent = float(line.split("%", 1)[0].split()[-1])
                    update_task(task_id, progress=max(0, min(percent, 100)))
                except (ValueError, IndexError):
                    pass
            if line.startswith("[download] Destination:"):
                update_task(task_id, output_path=line.split(":", 1)[1].strip())
        code = process.wait()
        if code == 0:
            update_task(task_id, status="completed", progress=100)
        else:
            update_task(task_id, status="failed", error=f"{selected_engine} 退出码 {code}")
    except Exception as exc:
        update_task(task_id, status="failed", error=str(exc))


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/api/tasks", response_model=list[Task])
def list_tasks() -> list[Task]:
    with Session(engine) as session:
        return list(session.exec(select(Task).order_by(Task.created_at.desc())).all())


@app.post("/api/tasks", response_model=Task, status_code=201)
def create_task(payload: CreateTask) -> Task:
    parsed = urlparse(payload.url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(422, "请输入有效的 HTTP/HTTPS 链接")
    task = Task(url=payload.url, engine=choose_engine(payload.url, payload.kind))
    with Session(engine) as session:
        session.add(task)
        session.commit()
        session.refresh(task)
    threading.Thread(target=run_download, args=(task.id, payload.quality, payload.folder), daemon=True).start()
    return task


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str) -> dict[str, bool]:
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        if task.status == "running":
            raise HTTPException(409, "正在下载的任务暂不能删除")
        session.delete(task)
        session.commit()
    return {"deleted": True}
