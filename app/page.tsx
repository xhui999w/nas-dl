"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type Task = {
  id: number | string;
  title: string;
  source: string;
  status: "下载中" | "排队中" | "已完成" | "失败" | "已取消";
  progress: number;
  meta: string;
  tone: string;
  backendStatus?: string;
};

function SourceMark({ tone, label }: { tone: string; label: string }) {
  return <span className={`source-mark ${tone}`}>{label.slice(0, 1)}</span>;
}

type ApiTask = {
  id: string;
  url: string;
  title: string;
  engine: string;
  status: string;
  progress: number;
  speed?: string;
  eta?: string;
  error?: string;
  retry_count: number;
};

type StorageInfo = {
  path: string;
  total: number;
  used: number;
  free: number;
  percent: number;
};

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "0 GB";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index >= 4 ? 1 : 0)} ${units[index]}`;
}

const statusLabels: Record<string, Task["status"]> = {
  queued: "排队中",
  running: "下载中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

let apiBasePromise: Promise<string> | undefined;

function getApiBase() {
  apiBasePromise ??= fetch("/api-config", { cache: "no-store" })
    .then(async (response) => {
      if (!response.ok) throw new Error("config unavailable");
      const config = (await response.json()) as { apiPort?: string };
      const port = /^\d{1,5}$/.test(config.apiPort || "") ? config.apiPort : "8888";
      return `${window.location.protocol}//${window.location.hostname}:${port}`;
    })
    .catch(() => `${window.location.protocol}//${window.location.hostname}:8888`);
  return apiBasePromise;
}

function fromApiTask(item: ApiTask): Task {
  const host = new URL(item.url).hostname.replace("www.", "");
  const details = item.error || [item.speed, item.eta ? `剩余 ${item.eta}` : ""].filter(Boolean).join(" · ") || item.engine;
  return {
    id: item.id,
    title: item.title || "等待解析",
    source: host,
    status: statusLabels[item.status] || "排队中",
    progress: item.progress,
    meta: details,
    tone: item.status === "failed" ? "red" : item.engine === "gallery-dl" ? "orange" : "violet",
    backendStatus: item.status,
  };
}

export default function Home() {
  const [url, setUrl] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [quality, setQuality] = useState("自动选择最佳画质");
  const [notice, setNotice] = useState("");
  const [connected, setConnected] = useState(false);
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  const [activeNav, setActiveNav] = useState("overview");
  const [menuOpen, setMenuOpen] = useState(false);
  const activeTasks = useMemo(() => tasks.filter((task) => task.status === "下载中" || task.status === "排队中"), [tasks]);
  const historyTasks = useMemo(() => tasks.filter((task) => task.status !== "下载中" && task.status !== "排队中"), [tasks]);
  const active = activeTasks.length;
  const completed = historyTasks.filter((task) => task.status === "已完成").length;
  const needsAttention = historyTasks.filter((task) => task.status === "失败" || task.status === "已取消").length;

  useEffect(() => {
    let disposed = false;
    async function syncTasks() {
      try {
        const apiBase = await getApiBase();
        const [tasksResponse, storageResponse] = await Promise.all([
          fetch(`${apiBase}/api/tasks`),
          fetch(`${apiBase}/api/storage`),
        ]);
        if (!tasksResponse.ok) throw new Error("offline");
        const items = (await tasksResponse.json()) as ApiTask[];
        if (!disposed) {
          setTasks(items.map(fromApiTask));
          if (storageResponse.ok) setStorage((await storageResponse.json()) as StorageInfo);
          setConnected(true);
        }
      } catch {
        if (!disposed) setConnected(false);
      }
    }
    void syncTasks();
    const timer = window.setInterval(syncTasks, 3000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, []);

  function navigateTo(id: string, message?: string) {
    setActiveNav(id);
    setMenuOpen(false);
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
    if (message) setNotice(message);
  }

  async function createTask(event: FormEvent) {
    event.preventDefault();
    const value = url.trim();
    if (!value) {
      setNotice("先粘贴一个视频、图集或作品集链接");
      return;
    }
    try {
      new URL(value);
    } catch {
      setNotice("这个链接看起来不完整，请检查后重试");
      return;
    }
    const optimisticTask: Task = {
      id: Date.now(),
      title: "正在解析新链接",
      source: new URL(value).hostname.replace("www.", ""),
      status: "排队中",
      progress: 0,
      meta: `${quality} · 等待解析`,
      tone: "green",
    };
    setTasks((current) => [...current, optimisticTask]);
    setUrl("");
    setNotice("正在提交任务，NASFlow 会自动识别内容类型");
    const apiBase = await getApiBase();
    const qualityMap: Record<string, string> = {
      "自动选择最佳画质": "best",
      "最高 4K": "4k",
      "最高 1080P": "1080p",
      "仅音频": "audio",
    };
    try {
      const response = await fetch(`${apiBase}/api/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: value, kind: "auto", quality: qualityMap[quality], folder: "自动分类" }),
      });
      if (!response.ok) throw new Error("提交失败");
      setNotice("任务已进入真实下载队列，可在 NAS 下载目录查看结果");
    } catch {
      setNotice("界面演示任务已创建；启动 NASFlow 下载服务后即可执行真实下载");
    }
  }

  async function cancelTask(task: Task) {
    const isRemote = typeof task.id === "string";
    if (!isRemote) {
      setTasks((current) => current.filter((item) => item.id !== task.id));
      return;
    }
    try {
      const apiBase = await getApiBase();
      const response = await fetch(`${apiBase}/api/tasks/${task.id}/cancel`, { method: "POST" });
      if (!response.ok) throw new Error("action failed");
      const updated = fromApiTask((await response.json()) as ApiTask);
      setTasks((current) => current.map((item) => item.id === task.id ? updated : item));
      setNotice("任务已取消，可在历史记录中重试或删除");
    } catch {
      setNotice("操作未完成，请检查下载服务是否在线");
    }
  }

  async function retryTask(task: Task) {
    try {
      const apiBase = await getApiBase();
      const response = await fetch(`${apiBase}/api/tasks/${task.id}/retry`, { method: "POST" });
      if (!response.ok) throw new Error("retry failed");
      const updated = fromApiTask((await response.json()) as ApiTask);
      setTasks((current) => current.map((item) => item.id === task.id ? updated : item));
      setNotice("任务已重新加入队列");
    } catch {
      setNotice("重试失败，请检查下载服务是否在线");
    }
  }

  async function deleteTask(task: Task) {
    if (typeof task.id !== "string") {
      setTasks((current) => current.filter((item) => item.id !== task.id));
      return;
    }
    if (!window.confirm(`确定删除“${task.title}”的任务记录吗？已下载的文件不会被删除。`)) return;
    try {
      const apiBase = await getApiBase();
      const response = await fetch(`${apiBase}/api/tasks/${task.id}`, { method: "DELETE" });
      if (!response.ok) throw new Error("delete failed");
      setTasks((current) => current.filter((item) => item.id !== task.id));
      setNotice("任务记录已删除，下载文件仍保留在 NAS 中");
    } catch {
      setNotice("删除失败，请刷新后重试");
    }
  }

  return (
    <main>
      {menuOpen && <button className="menu-backdrop" aria-label="关闭导航" onClick={() => setMenuOpen(false)} />}
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <div className="brand"><span className="brand-mark">N</span><span>NAS<span>Flow</span></span></div>
        <nav aria-label="主要导航">
          <button className={activeNav === "overview" ? "active" : ""} onClick={() => navigateTo("overview")}><span>⌂</span>概览</button>
          <button className={activeNav === "tasks" ? "active" : ""} onClick={() => navigateTo("tasks")}><span>↓</span>下载任务 <b>{active}</b></button>
          <button className={activeNav === "library" ? "active" : ""} onClick={() => navigateTo("library")}><span>▦</span>媒体库</button>
          <button className={activeNav === "subscriptions" ? "active" : ""} onClick={() => navigateTo("subscriptions")}><span>◎</span>订阅中心</button>
          <p>管理</p>
          <button onClick={() => navigateTo("overview", "账号与 Cookies 管理功能正在接入，当前可在 Compose 中配置 Cookies 文件")}><span>◇</span>账号与 Cookies</button>
          <button onClick={() => navigateTo("overview", "通知推送功能即将开放")}><span>♢</span>通知推送</button>
          <button onClick={() => navigateTo("overview", "系统设置 API 已就绪，图形化设置页即将开放")}><span>⚙</span>系统设置</button>
        </nav>
        <div className="storage-card">
          <div><span>下载盘空间</span><strong>{storage ? `${storage.percent}%` : "--"}</strong></div>
          <div className="storage-bar"><i style={{ width: `${storage?.percent || 0}%` }} /></div>
          <small>{storage ? `${formatBytes(storage.used)} / ${formatBytes(storage.total)}` : "连接服务后显示真实容量"}</small>
          {storage && <small className="storage-free">剩余 {formatBytes(storage.free)}</small>}
        </div>
        <div className="system-state"><i className={connected ? "" : "offline"} /> {connected ? "下载服务已连接" : "界面预览模式"} <span>v0.2.0</span></div>
      </aside>

      <section className="workspace" id="overview">
        <header>
          <button className="mobile-menu" aria-label="打开导航" onClick={() => setMenuOpen(true)}>☰</button>
          <div><h1>下午好，欢迎回来</h1><p>想收藏点什么？交给 NASFlow 就好。</p></div>
          <div className="header-actions"><button aria-label="查看通知">♢<i /></button><span className="avatar">X</span></div>
        </header>

        <section className="capture-card">
          <div className="capture-copy"><span className="spark">✦</span><div><h2>把喜欢的内容，带回家。</h2><p>粘贴视频、图集或作品集链接，剩下的交给我们。</p></div></div>
          <form onSubmit={createTask}>
            <label><span>↗</span><input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="在这里粘贴链接..." aria-label="媒体链接" /></label>
            <button type="submit">开始下载 <span>→</span></button>
          </form>
          <div className="capture-options">
            <div className="supported"><span className="mini yt">▶</span><span className="mini bilibili">B</span><span className="mini insta">◎</span><span className="mini x">𝕏</span><span className="mini note">R</span><span>支持 1000+ 网站</span></div>
            <label>保存至 <select aria-label="保存目录"><option>/downloads/自动分类</option><option>/downloads/视频</option><option>/downloads/图片</option></select></label>
            <label>画质 <select value={quality} onChange={(e) => setQuality(e.target.value)} aria-label="下载画质"><option>自动选择最佳画质</option><option>最高 4K</option><option>最高 1080P</option><option>仅音频</option></select></label>
          </div>
          {notice && <p className="notice" role="status">{notice}</p>}
        </section>

        <section className="stats" aria-label="系统统计">
          <article><div><p>正在处理</p><strong>{active} <small>项</small></strong><em>{connected ? "实时同步下载队列" : "等待连接下载服务"}</em></div><span className="stat-icon purple">↓</span></article>
          <article><div><p>已完成</p><strong>{completed} <small>项</small></strong><em>来自真实任务记录</em></div><span className="stat-icon orange">▦</span></article>
          <article><div><p>需要处理</p><strong>{needsAttention} <small>项</small></strong><em>失败或已取消任务</em></div><span className="stat-icon green">!</span></article>
        </section>

        <section className="content-grid">
          <div className="panel task-panel" id="tasks">
            <div className="panel-title"><div><h3>正在进行</h3><span>{active} 个任务</span></div><a href="#tasks">查看全部 →</a></div>
            <div className="task-list">
              {activeTasks.map((task) => (
                <article className="task" key={task.id}>
                  <SourceMark tone={task.tone} label={task.source} />
                  <div className="task-main">
                    <div className="task-top"><div><h4>{task.title}</h4><p>{task.source} · {task.meta}</p></div><strong className={task.status === "排队中" ? "queued" : task.status === "失败" ? "failed" : ""}>{task.status === "排队中" || task.status === "失败" || task.status === "已取消" ? task.status : `${task.progress}%`}</strong></div>
                    <div className="progress"><i style={{ width: `${task.progress || 3}%` }} /></div>
                  </div>
                  <button
                    onClick={() => cancelTask(task)}
                    aria-label={`取消 ${task.title}`}
                  >
                    ×
                  </button>
                </article>
              ))}
              {!activeTasks.length && <div className="empty">当前没有正在进行的任务。</div>}
            </div>
          </div>

          <div className="panel recent-panel" id="library">
            <div className="panel-title"><div><h3>历史记录</h3><span>{historyTasks.length} 条记录</span></div><a href="#library">已完成 / 失败 / 已取消</a></div>
            <div className="finished-list">
              {historyTasks.map((task, index) => (
                <article key={task.id}>
                  <span className={`finished-cover cover-${index % 3 + 1}`}>{task.source.slice(0, 1)}</span>
                  <div><h4>{task.title}</h4><p>{task.source} · {task.meta}</p></div>
                  <time className={`history-status ${task.backendStatus || ""}`}>{task.status}</time>
                  <div className="history-actions">
                    {(task.status === "失败" || task.status === "已取消") && <button onClick={() => retryTask(task)} aria-label={`重试 ${task.title}`}>↻</button>}
                    <button className="delete-button" onClick={() => deleteTask(task)} aria-label={`删除 ${task.title}`}>×</button>
                  </div>
                </article>
              ))}
              {!historyTasks.length && <div className="empty">还没有历史任务。</div>}
            </div>
          </div>
        </section>

        <section className="automation-strip" id="subscriptions">
          <div><span className="automation-icon">◎</span><div><h3>订阅自动更新</h3><p>按频道、作者或收藏夹定时检查，只下载新增内容。</p></div></div>
          <div className="automation-pills"><span>更新间隔可调</span><span>自动去重</span><span>独立画质策略</span></div>
          <button onClick={() => setNotice("订阅 API 已就绪，下一步将接入完整订阅管理页面")}>管理订阅 →</button>
        </section>

        <footer><p><i /> NASFlow 服务运行中 · 已连续运行 12 天 8 小时</p><div><span>yt-dlp <b>最新版</b></span><span>gallery-dl <b>最新版</b></span><a href="#help">需要帮助？</a></div></footer>
      </section>
    </main>
  );
}
