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
};

const initialTasks: Task[] = [
  { id: 1, title: "东京雨夜散步 · 4K", source: "YouTube", status: "下载中", progress: 72, meta: "18.4 MB/s · 剩余 1分12秒", tone: "violet" },
  { id: 2, title: "夏日岛屿摄影集", source: "Instagram", status: "下载中", progress: 38, meta: "12 / 31 张 · 原图", tone: "orange" },
  { id: 3, title: "年度科技纪录片", source: "哔哩哔哩", status: "排队中", progress: 0, meta: "等待空闲下载槽位", tone: "blue" },
];

const finished = [
  ["山野露营的第七天", "YouTube · 4K · 2.8 GB", "今天 14:28"],
  ["城市建筑灵感收藏", "小红书 · 46 张 · 384 MB", "今天 12:04"],
  ["胶片感人像作品集", "Instagram · 22 张 · 176 MB", "昨天 23:17"],
];

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

const statusLabels: Record<string, Task["status"]> = {
  queued: "排队中",
  running: "下载中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

function getApiBase() {
  return `${window.location.protocol}//${window.location.hostname}:8888`;
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
  };
}

export default function Home() {
  const [url, setUrl] = useState("");
  const [tasks, setTasks] = useState(initialTasks);
  const [quality, setQuality] = useState("自动选择最佳画质");
  const [notice, setNotice] = useState("");
  const [connected, setConnected] = useState(false);
  const active = useMemo(() => tasks.filter((task) => task.status === "下载中" || task.status === "排队中").length, [tasks]);

  useEffect(() => {
    let disposed = false;
    async function syncTasks() {
      try {
        const response = await fetch(`${getApiBase()}/api/tasks`);
        if (!response.ok) throw new Error("offline");
        const items = (await response.json()) as ApiTask[];
        if (!disposed) {
          setTasks(items.map(fromApiTask));
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
    const apiBase = getApiBase();
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

  async function taskAction(task: Task) {
    const isRemote = typeof task.id === "string";
    if (!isRemote) {
      setTasks((current) => current.filter((item) => item.id !== task.id));
      return;
    }
    const action = task.status === "失败" || task.status === "已取消" ? "retry" : "cancel";
    try {
      const response = await fetch(`${getApiBase()}/api/tasks/${task.id}/${action}`, { method: "POST" });
      if (!response.ok) throw new Error("action failed");
      const updated = fromApiTask((await response.json()) as ApiTask);
      setTasks((current) => current.map((item) => item.id === task.id ? updated : item));
      setNotice(action === "retry" ? "任务已重新加入队列" : "任务已取消");
    } catch {
      setNotice("操作未完成，请检查下载服务是否在线");
    }
  }

  return (
    <main>
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">N</span><span>NAS<span>Flow</span></span></div>
        <nav aria-label="主要导航">
          <a className="active" href="#overview"><span>⌂</span>概览</a>
          <a href="#tasks"><span>↓</span>下载任务 <b>{active}</b></a>
          <a href="#library"><span>▦</span>媒体库</a>
          <a href="#subscriptions"><span>◎</span>订阅中心</a>
          <p>管理</p>
          <a href="#cookies"><span>◇</span>账号与 Cookies</a>
          <a href="#notifications"><span>♢</span>通知推送</a>
          <a href="#settings"><span>⚙</span>系统设置</a>
        </nav>
        <div className="storage-card">
          <div><span>NAS 存储空间</span><strong>68%</strong></div>
          <div className="storage-bar"><i /></div>
          <small>3.4 TB / 5 TB</small>
        </div>
        <div className="system-state"><i className={connected ? "" : "offline"} /> {connected ? "下载服务已连接" : "界面预览模式"} <span>v0.2.0</span></div>
      </aside>

      <section className="workspace" id="overview">
        <header>
          <button className="mobile-menu" aria-label="打开导航">☰</button>
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
          <article><div><p>今日已下载</p><strong>24 <small>项</small></strong><em>↗ 12% 较昨日</em></div><span className="stat-icon purple">↓</span></article>
          <article><div><p>本月新增</p><strong>186 <small>项</small></strong><em>视频 128 · 图片 58</em></div><span className="stat-icon orange">▦</span></article>
          <article><div><p>节省空间</p><strong>12.8 <small>GB</small></strong><em>智能去重与压缩</em></div><span className="stat-icon green">♧</span></article>
        </section>

        <section className="content-grid">
          <div className="panel task-panel" id="tasks">
            <div className="panel-title"><div><h3>正在进行</h3><span>{active} 个任务</span></div><a href="#tasks">查看全部 →</a></div>
            <div className="task-list">
              {tasks.map((task) => (
                <article className="task" key={task.id}>
                  <SourceMark tone={task.tone} label={task.source} />
                  <div className="task-main">
                    <div className="task-top"><div><h4>{task.title}</h4><p>{task.source} · {task.meta}</p></div><strong className={task.status === "排队中" ? "queued" : task.status === "失败" ? "failed" : ""}>{task.status === "排队中" || task.status === "失败" || task.status === "已取消" ? task.status : `${task.progress}%`}</strong></div>
                    <div className="progress"><i style={{ width: `${task.progress || 3}%` }} /></div>
                  </div>
                  <button
                    className={task.status === "失败" || task.status === "已取消" ? "retry-button" : ""}
                    onClick={() => taskAction(task)}
                    aria-label={`${task.status === "失败" || task.status === "已取消" ? "重试" : "取消"} ${task.title}`}
                  >
                    {task.status === "失败" || task.status === "已取消" ? "↻" : "×"}
                  </button>
                </article>
              ))}
              {!tasks.length && <div className="empty">队列空空的，去添加一个喜欢的链接吧。</div>}
            </div>
          </div>

          <div className="panel recent-panel" id="library">
            <div className="panel-title"><div><h3>最近完成</h3><span>自动归档至媒体库</span></div><a href="#library">全部记录 →</a></div>
            <div className="finished-list">
              {finished.map(([title, meta, time], index) => (
                <article key={title}><span className={`finished-cover cover-${index + 1}`}>{index === 0 ? "▶" : index === 1 ? "▤" : "◎"}</span><div><h4>{title}</h4><p>{meta}</p></div><time>{time}</time><b>✓</b></article>
              ))}
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
