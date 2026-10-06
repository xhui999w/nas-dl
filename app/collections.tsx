"use client";

import { useEffect, useState, type MouseEvent } from "react";
import Link from "next/link";
import type { ApiTask } from "./home";
import "./collections.css";

export type Collection = {
  id: string; title: string; url: string; state: string; status: string; error?: string;
  total: number; selected: number; pending: number; running: number; queued: number;
  completed: number; failed: number; cancelled: number; paused: number; progress: number; speed_bytes: number;
};
type Props = {
  collections: Collection[]; mode: "download" | "library"; filter: string;
  focus?: { id: string; request: number } | null;
  onUpdate: (collection: Collection) => void;
  onShare: (task: ApiTask) => void;
  onSave: (event: MouseEvent<HTMLAnchorElement>, task: ApiTask) => void;
};
const labels: Record<string, string> = { resolving: "读取目录中", ready: "待选择", pending: "未开始", running: "下载中", queued: "等待中", completed: "已完成", failed: "失败", cancelled: "已取消", paused: "已暂停" };

export function collectionMatches(group: Collection, mode: Props["mode"], filter: string) {
  if (mode === "library") return filter === "completed" ? group.completed > 0 : filter === "failed" ? group.failed > 0 || group.status === "failed" : filter === "cancelled" ? group.cancelled > 0 || group.status === "cancelled" : group.completed + group.failed + group.cancelled > 0 || ["failed", "cancelled"].includes(group.status);
  if (filter === "running") return group.running > 0;
  if (filter === "queued") return group.queued > 0 || ["resolving", "ready"].includes(group.status);
  if (filter === "failed") return group.failed > 0 || ["failed", "cancelled"].includes(group.status);
  return group.status !== "completed";
}

function speedLabel(bytes: number) {
  if (!bytes) return "等待速度信息";
  return bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MB/s` : `${(bytes / 1024).toFixed(1)} KB/s`;
}

export function CollectionGroups(props: Props) {
  return <div className="collection-groups">{props.collections.filter((group) => collectionMatches(group, props.mode, props.filter)).map((group) =>
    <CollectionGroup {...props} group={group} key={`${group.id}:${props.focus?.id === group.id ? props.focus.request : 0}:${props.mode}:${props.filter}`} />
  )}</div>;
}

function CollectionGroup({ group, mode, filter, focus, onUpdate, onShare, onSave }: Props & { group: Collection }) {
  const [expanded, setExpanded] = useState(mode === "download" && focus?.id === group.id);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [data, setData] = useState<{ entries: ApiTask[]; total: number; page: number; key: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [revision, setRevision] = useState(0);
  const status = mode === "library" ? filter : "all";
  const queryKey = `${mode}:${status}`;
  const api = `/nas-api/api/collections/${encodeURIComponent(group.id)}`;

  useEffect(() => {
    if (!expanded) return;
    const controller = new AbortController();
    async function refresh() {
      try {
        const response = await fetch(`${api}/entries?page=${page}&page_size=50&view=${mode}&status=${status}`, { signal: controller.signal, cache: "no-store" });
        if (response.status === 401) { window.location.replace("/login"); return; }
        const payload = await response.json();
        if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "读取合集明细失败");
        if (!controller.signal.aborted) setData({ ...payload, key: queryKey });
      } catch (cause) {
        if (!controller.signal.aborted) setMessage(cause instanceof Error ? cause.message : "无法连接 NASFlow");
      }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [api, expanded, mode, page, queryKey, status, revision]);

  async function control(action: string, taskIds?: string[] | null) {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${api}/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: action === "start" ? JSON.stringify({ task_ids: taskIds ?? null }) : undefined,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "操作失败");
      onUpdate(payload as Collection);
      if (action === "start") setSelected(new Set());
      setRevision((value) => value + 1);
      if (action === "pause") setMessage("已暂停后续排队，正在下载的集会继续完成。");
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : "操作失败"); }
    finally { setBusy(false); }
  }

  async function episodeAction(task: ApiTask, action: string) {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`/nas-api/api/tasks/${encodeURIComponent(task.id)}/${action}`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "操作失败");
      setRevision((value) => value + 1);
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : "操作失败"); }
    finally { setBusy(false); }
  }

  const currentData = data?.page === page && data.key === queryKey ? data : null;
  const entries = currentData?.entries || [];
  const totalPages = Math.max(1, Math.ceil((currentData?.total || 0) / 50));
  const selectable = entries.filter((entry) => entry.status === "pending");
  const canStart = group.state !== "resolving" && !group.error && group.pending > 0;
  return <section className="collection-group" aria-label={`合集 ${group.title}`}>
    <div className="collection-header">
      <button type="button" className="collection-folder" onClick={() => setExpanded((value) => !value)} aria-label={`${expanded ? "收起" : "展开"} ${group.title}`} aria-expanded={expanded}>▰</button>
      <div className="collection-main"><button className="collection-title" type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>{group.title}</button>
        <p>{group.total} 集 · 已完成 {group.completed} / {group.selected || group.total} 集{group.selected > 0 && group.selected < group.total ? `（已选 ${group.selected} 集）` : ""}{group.running > 0 ? ` · 下载中 ${group.running} 集 · ${speedLabel(group.speed_bytes)}` : ""}{group.failed > 0 ? ` · 失败 ${group.failed} 集` : ""}</p>
        <div className="progress collection-progress"><i style={{ width: `${group.progress}%` }} /></div>
      </div><span className={`collection-status ${group.status}`}>{labels[group.status] || group.status}{group.selected > 0 && group.status !== "resolving" ? ` · ${group.progress}%` : ""}</span>
    </div>
    <div className="collection-controls">
      <button type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>{expanded ? "收起明细" : mode === "library" ? "打开合集" : "展开明细"}</button>
      {canStart && <button type="button" className="collection-primary" disabled={busy} onClick={() => void control("start")}>下载全部{group.selected ? "未开始内容" : ""}</button>}
      {(group.queued > 0 || group.running > 0) && group.state !== "paused" && <button type="button" disabled={busy} onClick={() => void control("pause")}>暂停合集</button>}
      {(group.paused + group.cancelled > 0 || group.state === "paused") && <button type="button" disabled={busy} onClick={() => void control("resume")}>继续下载</button>}
      {group.failed > 0 && <button type="button" disabled={busy} onClick={() => void control("retry")}>重试失败</button>}
      {!["completed", "cancelled"].includes(group.status) && <button type="button" disabled={busy} onClick={() => void control("cancel")}>取消合集</button>}
      {(group.error || group.status === "completed" || group.state === "cancelled" && !group.total) && <button type="button" disabled={busy} onClick={() => void control("rescan")}>{group.error ? "重新读取" : "检查更新"}</button>}
    </div>
    {(message || group.error) && <p className="collection-message" role="status">{message || group.error}</p>}
    {expanded && <div className="collection-details">
      {mode === "download" && canStart && <div className="collection-selection"><button type="button" onClick={() => setSelected((current) => { const next = new Set(current); selectable.forEach((entry) => next.add(entry.id)); return next; })}>选择本页</button><button type="button" onClick={() => setSelected(new Set())}>清空选择</button><span>已选择 {selected.size} 集</span><button className="collection-primary" type="button" disabled={busy || !selected.size} onClick={() => void control("start", [...selected])}>下载选中内容</button></div>}
      {entries.map((entry) => {
        const missingFile = entry.status === "completed" && entry.file_available === false;
        return <div className="collection-episode" key={entry.id}>
        <span className="collection-index">{mode === "download" && entry.status === "pending" && canStart ? <input type="checkbox" aria-label={`选择 ${entry.title}`} checked={selected.has(entry.id)} onChange={(event) => setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(entry.id); else next.delete(entry.id); return next; })} /> : entry.collection_index}</span>
        <div className="collection-episode-main"><h4>{entry.collection_index}. {entry.title}</h4><p>{missingFile ? "文件已删除或移走，可重新下载" : entry.error || [entry.speed, entry.eta ? `剩余 ${entry.eta}` : ""].filter(Boolean).join(" · ") || labels[entry.status]}</p>{entry.status === "running" && <div className="progress"><i style={{ width: `${entry.progress}%` }} /></div>}</div>
        <span className={`collection-episode-status ${missingFile ? "missing" : entry.status}`}>{missingFile ? "文件不存在" : entry.status === "running" ? `${entry.progress}%` : labels[entry.status]}</span>
        <div className="history-actions">
          {entry.status === "completed" && entry.media_available && <><Link className="media-play" href={`/media/play/${encodeURIComponent(entry.id)}`} prefetch={false} aria-label={`播放 ${entry.title}`} title="播放">▶</Link><button type="button" className="media-share" onClick={() => onShare(entry)} aria-label={`分享 ${entry.title}`} title="分享">↗</button></>}
          {entry.status === "completed" && !missingFile && <a className="device-download" href={`/nas-api/api/tasks/${encodeURIComponent(entry.id)}/file`} download onClick={(event) => onSave(event, entry)} aria-label={`保存 ${entry.title} 到当前设备`} title="保存到此设备">⇩</a>}
          {(["failed", "cancelled"].includes(entry.status) || missingFile) && <button type="button" disabled={busy} onClick={() => void episodeAction(entry, "retry")} aria-label={`${missingFile ? "重新下载" : "重试"} ${entry.title}`} title={missingFile ? "重新下载" : "重试"}>↻</button>}
          {["queued", "running"].includes(entry.status) && <button type="button" disabled={busy} onClick={() => void episodeAction(entry, "cancel")} aria-label={`取消 ${entry.title}`} title="取消">×</button>}
        </div>
      </div>;
      })}
      {!entries.length && <p className="collection-empty">{group.state === "resolving" ? "正在读取合集目录，读取完成后可以选择下载。" : currentData ? "当前筛选条件下没有视频。" : "正在读取明细…"}</p>}
      <div className="collection-pagination"><button type="button" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button><span>第 {page} / {totalPages} 页 · {currentData?.total || 0} 集</span><button type="button" disabled={!currentData || page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button></div>
    </div>}
  </section>;
}
