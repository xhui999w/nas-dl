"use client";

import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";

type Task = {
  id: number | string;
  title: string;
  source: string;
  status: "下载中" | "排队中" | "已完成" | "失败" | "已取消";
  progress: number;
  meta: string;
  tone: string;
  backendStatus?: string;
  speed?: string;
  eta?: string;
  saveToObsidian?: boolean;
  obsidianNotePath?: string;
  obsidianError?: string;
};

const demoTasks: Task[] = [
  { id: 101, title: "东京雨夜散步 · 4K", source: "YouTube", status: "下载中", progress: 72, meta: "18.4 MB/s · 剩余 1分12秒", tone: "violet", backendStatus: "running" },
  { id: 102, title: "夏日岛屿摄影集", source: "Instagram", status: "下载中", progress: 38, meta: "12 / 31 张 · 原图", tone: "orange", backendStatus: "running" },
  { id: 103, title: "年度科技纪录片", source: "哔哩哔哩", status: "排队中", progress: 0, meta: "等待空闲下载槽位", tone: "blue", backendStatus: "queued" },
  { id: 104, title: "城市延时摄影合集", source: "YouTube", status: "已完成", progress: 100, meta: "4K · 2.8 GB", tone: "green", backendStatus: "completed" },
  { id: 105, title: "独立音乐现场", source: "YouTube", status: "失败", progress: 46, meta: "网络连接中断，可重新尝试", tone: "red", backendStatus: "failed" },
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
  save_to_obsidian?: boolean;
  obsidian_note_path?: string;
  obsidian_error?: string;
};

type StorageInfo = {
  path: string;
  total: number;
  used: number;
  free: number;
  percent: number;
};

type Subscription = {
  id: string | number;
  name: string;
  url: string;
  enabled: boolean;
  interval_minutes: number;
  quality: string;
  folder: string;
  last_checked_at?: string | null;
  created_at?: string | null;
};

type SubscriptionEntry = { id: string; title: string; url: string; duration?: number | null; thumbnail?: string | null };

type CookieRow = { id: number; domain: string; cookie: string };

const demoSubscriptions: Subscription[] = [
  { id: 201, name: "科技频道每周更新", url: "https://www.youtube.com/@demo", enabled: true, interval_minutes: 360, quality: "1080p", folder: "订阅/科技" },
  { id: 202, name: "旅行影像收藏", url: "https://www.bilibili.com/space/demo", enabled: true, interval_minutes: 720, quality: "best", folder: "订阅/旅行" },
];

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "0 GB";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index >= 4 ? 1 : 0)} ${units[index]}`;
}

function subscriptionPlatform(url: string) {
  try {
    const host = new URL(url).hostname.toLowerCase();
    if (host.includes("bilibili.com") || host.includes("b23.tv")) return "bilibili";
    if (host.includes("youtube.com") || host.includes("youtu.be")) return "youtube";
    if (host.includes("instagram.com")) return "instagram";
    if (host === "x.com" || host.endsWith(".x.com") || host.includes("twitter.com")) return "x";
    return host.replace("www.", "");
  } catch {
    return "other";
  }
}

const platformLabels: Record<string, string> = { bilibili: "哔哩哔哩", youtube: "YouTube", instagram: "Instagram", x: "X" };

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
    speed: item.speed,
    eta: item.eta,
    saveToObsidian: item.save_to_obsidian,
    obsidianNotePath: item.obsidian_note_path,
    obsidianError: item.obsidian_error,
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
  const [subscriptionOpen, setSubscriptionOpen] = useState(false);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [subscriptionName, setSubscriptionName] = useState("");
  const [subscriptionUrl, setSubscriptionUrl] = useState("");
  const [cookieOpen, setCookieOpen] = useState(false);
  const [cookieRows, setCookieRows] = useState<CookieRow[]>([]);
  const [platformSwitches, setPlatformSwitches] = useState<Record<string, boolean>>({ youtube: true, bilibili: true, instagram: true, x: true });
  const [browsingSubscription, setBrowsingSubscription] = useState<Subscription | null>(null);
  const [subscriptionEntries, setSubscriptionEntries] = useState<SubscriptionEntry[]>([]);
  const [selectedEntries, setSelectedEntries] = useState<Set<string>>(new Set());
  const [entriesLoading, setEntriesLoading] = useState(false);
  const [showSubscriptionForm, setShowSubscriptionForm] = useState(false);
  const [downloadDevice, setDownloadDevice] = useState<"computer" | "nas">("computer");
  const [computerApiUrl, setComputerApiUrl] = useState("http://127.0.0.1:8888");
  const [nasApiUrl, setNasApiUrl] = useState("http://192.168.31.126:18888");
  const [deviceRevision, setDeviceRevision] = useState(0);
  const [taskFilter, setTaskFilter] = useState<"active" | "running" | "queued" | "failed">("active");
  const [saveToObsidian, setSaveToObsidian] = useState(false);
  const activeTasks = useMemo(() => tasks.filter((task) => task.status === "下载中" || task.status === "排队中"), [tasks]);
  const historyTasks = useMemo(() => tasks.filter((task) => task.status !== "下载中" && task.status !== "排队中"), [tasks]);
  const active = activeTasks.length;
  const homeTasks = tasks.filter((task) => task.status !== "已完成" && (taskFilter === "active" || (taskFilter === "running" && task.status === "下载中") || (taskFilter === "queued" && task.status === "排队中") || (taskFilter === "failed" && (task.status === "失败" || task.status === "已取消"))));
  const subscriptionsAddedToday = subscriptions.filter((item) => item.created_at && new Date(item.created_at).toDateString() === new Date().toDateString()).length;
  const pendingSubscriptions = subscriptions.filter((item) => item.enabled && !item.last_checked_at).length;
  const latestSubscriptionSync = subscriptions.map((item) => item.last_checked_at).filter(Boolean).sort().at(-1);

  function getSelectedApiBase() {
    return Promise.resolve((downloadDevice === "nas" ? nasApiUrl : computerApiUrl).replace(/\/$/, ""));
  }

  useEffect(() => {
    const savedDevice = window.localStorage.getItem("nasflow-download-device");
    const savedComputerApi = window.localStorage.getItem("nasflow-computer-api");
    const savedNasApi = window.localStorage.getItem("nasflow-nas-api");
    if (savedDevice === "nas" || savedDevice === "computer") setDownloadDevice(savedDevice);
    else if (!['127.0.0.1', 'localhost', '::1'].includes(window.location.hostname)) setDownloadDevice("nas");
    if (savedComputerApi) setComputerApiUrl(savedComputerApi);
    if (savedNasApi) setNasApiUrl(savedNasApi);
    else if (!['127.0.0.1', 'localhost', '::1'].includes(window.location.hostname)) setNasApiUrl(`${window.location.protocol}//${window.location.hostname}:18888`);
  }, []);

  function changeDownloadDevice(device: "computer" | "nas") {
    setDownloadDevice(device);
    if (device === "computer") setSaveToObsidian(false);
    setTasks([]);
    setSubscriptions([]);
    setStorage(null);
    setConnected(false);
    window.localStorage.setItem("nasflow-download-device", device);
  }

  function saveDeviceAddresses() {
    try {
      new URL(computerApiUrl);
      new URL(nasApiUrl);
      window.localStorage.setItem("nasflow-computer-api", computerApiUrl.replace(/\/$/, ""));
      window.localStorage.setItem("nasflow-nas-api", nasApiUrl.replace(/\/$/, ""));
      setDeviceRevision((value) => value + 1);
      setConnected(false);
      setNotice("下载设备地址已保存在当前浏览器");
    } catch {
      setNotice("请输入完整地址，例如 http://192.168.31.126:18888");
    }
  }

  useEffect(() => {
    const views = new Set(["overview", "library", "subscriptions", "cookies", "notifications", "settings"]);
    const openHashView = () => {
      const requested = window.location.hash.slice(1);
      setActiveNav(requested === "tasks" ? "overview" : views.has(requested) ? requested : "overview");
    };
    openHashView();
    window.addEventListener("hashchange", openHashView);
    return () => window.removeEventListener("hashchange", openHashView);
  }, []);

  useEffect(() => {
    let disposed = false;
    const demoMode = new URLSearchParams(window.location.search).get("demo") === "1";
    if (demoMode) {
      setTasks(demoTasks);
      setSubscriptions(demoSubscriptions);
      setCookieRows([
        { id: 301, domain: "youtube.com", cookie: "LOGIN_INFO=••••••; SID=••••••" },
        { id: 302, domain: "bilibili.com", cookie: "SESSDATA=••••••; bili_jct=••••••" },
      ]);
      setStorage({ path: "/downloads", total: 5 * 1024 ** 4, used: 3.4 * 1024 ** 4, free: 1.6 * 1024 ** 4, percent: 68 });
      setConnected(true);
      return;
    }
    async function syncTasks() {
      try {
        const apiBase = await getSelectedApiBase();
        const [tasksResponse, storageResponse, subscriptionsResponse, cookiesResponse, platformsResponse] = await Promise.all([
          fetch(`${apiBase}/api/tasks`),
          fetch(`${apiBase}/api/storage`),
          fetch(`${apiBase}/api/subscriptions`),
          fetch(`${apiBase}/api/cookies`),
          fetch(`${apiBase}/api/subscription-platforms`),
        ]);
        if (!tasksResponse.ok) throw new Error("offline");
        const items = (await tasksResponse.json()) as ApiTask[];
        if (!disposed) {
          setTasks(items.map(fromApiTask));
          if (storageResponse.ok) setStorage((await storageResponse.json()) as StorageInfo);
          if (subscriptionsResponse.ok) setSubscriptions((await subscriptionsResponse.json()) as Subscription[]);
          if (cookiesResponse.ok) {
            const payload = (await cookiesResponse.json()) as { rules: Array<{ domain: string; cookie: string }> };
            setCookieRows(payload.rules.map((rule, index) => ({ id: index + 1, ...rule })));
          }
          if (platformsResponse.ok) {
            const payload = await platformsResponse.json() as { enabled: Record<string, boolean> };
            setPlatformSwitches((current) => ({ ...current, ...payload.enabled }));
          }
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
  }, [downloadDevice, deviceRevision]);

  function navigateTo(id: string, message?: string) {
    setActiveNav(id);
    setMenuOpen(false);
    window.history.replaceState(null, "", `#${id}`);
    window.scrollTo({ top: 0, behavior: "smooth" });
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
      saveToObsidian,
    };
    setTasks((current) => [...current, optimisticTask]);
    setUrl("");
    setNotice("正在提交任务，NASFlow 会自动识别内容类型");
    const apiBase = await getSelectedApiBase();
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
        body: JSON.stringify({ url: value, kind: "auto", quality: qualityMap[quality], folder: saveToObsidian ? "Obsidian视频" : "自动分类", save_to_obsidian: saveToObsidian }),
      });
      if (!response.ok) throw new Error("提交失败");
      setNotice("任务已进入真实下载队列，可在 NAS 下载目录查看结果");
    } catch {
      setNotice("界面演示任务已创建；启动 NASFlow 下载服务后即可执行真实下载");
    }
  }

  async function createSubscription(event: FormEvent) {
    event.preventDefault();
    const name = subscriptionName.trim();
    const targetUrl = subscriptionUrl.trim();
    if (!name || !targetUrl) {
      setNotice("请填写订阅名称和频道、作者或收藏夹链接");
      return;
    }
    try {
      new URL(targetUrl);
      const demoMode = new URLSearchParams(window.location.search).get("demo") === "1";
      if (demoMode) {
        setSubscriptions((current) => [...current, { id: Date.now(), name, url: targetUrl, enabled: true, interval_minutes: 360, quality: "best", folder: "订阅" }]);
      } else {
        const apiBase = await getSelectedApiBase();
        const response = await fetch(`${apiBase}/api/subscriptions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, url: targetUrl, interval_minutes: 360, quality: "best", folder: "订阅" }),
        });
        if (!response.ok) throw new Error("create failed");
        const created = (await response.json()) as Subscription;
        setSubscriptions((current) => [...current, created]);
      }
      setSubscriptionName("");
      setSubscriptionUrl("");
      setShowSubscriptionForm(false);
      setNotice("订阅已添加，将按设定间隔检查新增内容");
    } catch {
      setNotice("订阅添加失败，请检查链接和下载服务");
    }
  }

  async function toggleSubscription(item: Subscription) {
    if (typeof item.id === "number") {
      setSubscriptions((current) => current.map((entry) => entry.id === item.id ? { ...entry, enabled: !entry.enabled } : entry));
      return;
    }
    try {
      const apiBase = await getSelectedApiBase();
      const response = await fetch(`${apiBase}/api/subscriptions/${item.id}/toggle`, { method: "PATCH" });
      if (!response.ok) throw new Error("toggle failed");
      const updated = (await response.json()) as Subscription;
      setSubscriptions((current) => current.map((entry) => entry.id === item.id ? updated : entry));
    } catch {
      setNotice("订阅状态修改失败，请检查下载服务");
    }
  }

  function addDemoSyncTask(title: string, source: string) {
    setTasks((current) => [{ id: Date.now() + Math.random(), title, source, status: "排队中", progress: 0, meta: "订阅同步 · 自动跳过已下载内容", tone: "violet", backendStatus: "queued" }, ...current]);
  }

  async function syncSubscription(item: Subscription) {
    if (typeof item.id === "number") {
      addDemoSyncTask(`同步订阅 · ${item.name}`, platformLabels[subscriptionPlatform(item.url)] || subscriptionPlatform(item.url));
      setNotice(`已开始同步“${item.name}”，已下载内容会自动跳过`);
      setSubscriptionOpen(false);
      return;
    }
    try {
      const apiBase = await getSelectedApiBase();
      const response = await fetch(`${apiBase}/api/subscriptions/${item.id}/sync`, { method: "POST" });
      if (!response.ok) throw new Error("sync failed");
      const task = fromApiTask((await response.json()) as ApiTask);
      setTasks((current) => [task, ...current.filter((entry) => entry.id !== task.id)]);
      setNotice(`已开始同步“${item.name}”，已下载内容会自动跳过`);
      setSubscriptionOpen(false);
    } catch {
      setNotice("订阅同步失败，请检查 Cookie、订阅链接和下载服务");
    }
  }

  async function syncSubscriptions(platform?: string) {
    const selected = subscriptions.filter((item) => item.enabled && platformSwitches[subscriptionPlatform(item.url)] !== false && (!platform || subscriptionPlatform(item.url) === platform));
    if (!selected.length) {
      setNotice("没有符合条件的已启用订阅");
      return;
    }
    if (selected.every((item) => typeof item.id === "number")) {
      selected.forEach((item, index) => addDemoSyncTask(`同步订阅 · ${item.name}`, platformLabels[subscriptionPlatform(item.url)] || subscriptionPlatform(item.url)));
      setNotice(`已创建 ${selected.length} 个${platform ? platformLabels[platform] || platform : ""}订阅同步任务`);
      setSubscriptionOpen(false);
      return;
    }
    try {
      const apiBase = await getSelectedApiBase();
      const suffix = platform ? `?platform=${encodeURIComponent(platform)}` : "";
      const response = await fetch(`${apiBase}/api/subscriptions/sync${suffix}`, { method: "POST" });
      if (!response.ok) throw new Error("sync failed");
      const created = ((await response.json()) as ApiTask[]).map(fromApiTask);
      setTasks((current) => [...created, ...current.filter((entry) => !created.some((task) => task.id === entry.id))]);
      setNotice(`已创建 ${created.length} 个订阅同步任务，重复内容会自动跳过`);
      setSubscriptionOpen(false);
    } catch {
      setNotice("批量同步失败，请检查已启用订阅和下载服务");
    }
  }

  async function togglePlatform(platform: string) {
    const next = { ...platformSwitches, [platform]: !platformSwitches[platform] };
    setPlatformSwitches(next);
    if (new URLSearchParams(window.location.search).get("demo") === "1") return;
    try {
      const apiBase = await getSelectedApiBase();
      await fetch(`${apiBase}/api/subscription-platforms`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: next }) });
    } catch { setNotice("网站同步开关保存失败"); }
  }

  async function browseSubscription(item: Subscription) {
    setBrowsingSubscription(item);
    setSelectedEntries(new Set());
    setEntriesLoading(true);
    if (typeof item.id === "number") {
      setSubscriptionEntries([
        { id: "demo-1", title: "NAS 家庭影音整理与自动化", url: "https://example.com/video/1", duration: 742 },
        { id: "demo-2", title: "本周值得收藏的数码内容", url: "https://example.com/video/2", duration: 518 },
        { id: "demo-3", title: "旅行影像：海边日落完整记录", url: "https://example.com/video/3", duration: 965 },
      ]);
      setEntriesLoading(false);
      return;
    }
    try {
      const apiBase = await getSelectedApiBase();
      const response = await fetch(`${apiBase}/api/subscriptions/${item.id}/entries`);
      if (!response.ok) throw new Error("load failed");
      setSubscriptionEntries(await response.json());
    } catch { setNotice("读取收藏目录失败，请检查 Cookie 和订阅地址"); setSubscriptionEntries([]); }
    finally { setEntriesLoading(false); }
  }

  async function downloadSelectedEntries() {
    if (!browsingSubscription || !selectedEntries.size) { setNotice("请先勾选要下载的内容"); return; }
    const urls = subscriptionEntries.filter((entry) => selectedEntries.has(entry.id)).map((entry) => entry.url);
    if (typeof browsingSubscription.id === "number") {
      urls.forEach((_, index) => addDemoSyncTask(`手动选择 · ${subscriptionEntries.filter((entry) => selectedEntries.has(entry.id))[index].title}`, platformLabels[subscriptionPlatform(browsingSubscription.url)] || "订阅"));
      setNotice(`已创建 ${urls.length} 个选中内容的下载任务`);
      navigateTo("tasks");
      return;
    }
    try {
      const apiBase = await getSelectedApiBase();
      const response = await fetch(`${apiBase}/api/subscriptions/${browsingSubscription.id}/entries`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ urls }) });
      if (!response.ok) throw new Error("download failed");
      const created = await response.json() as ApiTask[];
      setTasks((current) => [...created.map(fromApiTask), ...current]);
      setNotice(`已创建 ${created.length} 个下载任务`);
      navigateTo("tasks");
    } catch { setNotice("创建选中内容的下载任务失败"); }
  }

  function updateCookieRow(id: number, field: "domain" | "cookie", value: string) {
    setCookieRows((current) => current.map((row) => row.id === id ? { ...row, [field]: value } : row));
  }

  async function saveCookies() {
    const rules = cookieRows
      .map(({ domain, cookie }) => ({ domain: domain.trim(), cookie: cookie.trim() }))
      .filter((rule) => rule.domain || rule.cookie);
    if (rules.some((rule) => !rule.domain || !rule.cookie)) {
      setNotice("每一行都需要同时填写网站域名和 Cookie");
      return;
    }
    if (new URLSearchParams(window.location.search).get("demo") === "1") {
      setNotice("演示模式：Cookie 配置已模拟保存，不会写入电脑或 NAS");
      setCookieOpen(false);
      return;
    }
    try {
      const apiBase = await getSelectedApiBase();
      const response = await fetch(`${apiBase}/api/cookies`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rules }),
      });
      if (!response.ok) throw new Error("save failed");
      setNotice("Cookie 已保存在 NASFlow 本地数据目录，下载时会按域名自动使用");
      setCookieOpen(false);
    } catch {
      setNotice("Cookie 保存失败，请检查域名格式和下载服务");
    }
  }

  async function importCookieFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const text = await file.text();
      const grouped = new Map<string, string[]>();
      for (const line of text.split(/\r?\n/)) {
        if (!line.trim() || line.startsWith("#")) continue;
        const fields = line.split("\t");
        if (fields.length < 7) continue;
        const domain = fields[0].replace(/^\./, "").toLowerCase();
        const name = fields[5];
        const value = fields.slice(6).join("\t");
        if (!domain || !name) continue;
        grouped.set(domain, [...(grouped.get(domain) || []), `${name}=${value}`]);
      }
      if (!grouped.size) throw new Error("invalid cookies file");
      setCookieRows(Array.from(grouped.entries()).map(([domain, pairs], index) => ({ id: Date.now() + index, domain, cookie: pairs.join("; ") })));
      setNotice(`已从插件导出的文件中识别 ${grouped.size} 个网站，请检查后保存`);
    } catch {
      setNotice("没有识别出 Netscape cookies.txt，请确认文件由 Cookie 导出插件生成");
    }
  }

  async function cancelTask(task: Task) {
    const isRemote = typeof task.id === "string";
    if (!isRemote) {
      setTasks((current) => current.filter((item) => item.id !== task.id));
      return;
    }
    try {
      const apiBase = await getSelectedApiBase();
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
      const apiBase = await getSelectedApiBase();
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
      const apiBase = await getSelectedApiBase();
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
        <div className="brand"><img className="brand-mark" src="/nasflow-icon.png" alt="" /><span>NAS<span>Flow</span></span></div>
        <nav aria-label="主要导航">
          <button className={activeNav === "overview" ? "active" : ""} onClick={() => navigateTo("overview")}><span>↓</span>首页（下载中心） <b>{active}</b></button>
          <button className={activeNav === "library" ? "active" : ""} onClick={() => navigateTo("library")}><span>▦</span>媒体库</button>
          <button className={activeNav === "subscriptions" ? "active" : ""} onClick={() => navigateTo("subscriptions")}><span>◎</span>订阅中心</button>
          <p>管理</p>
          <button className={activeNav === "cookies" ? "active" : ""} onClick={() => navigateTo("cookies")}><span>◇</span>账号与 Cookies</button>
          <button className={activeNav === "notifications" ? "active" : ""} onClick={() => navigateTo("notifications")}><span>♢</span>通知推送</button>
          <button className={activeNav === "settings" ? "active" : ""} onClick={() => navigateTo("settings")}><span>⚙</span>系统设置</button>
        </nav>
        <div className="storage-card">
          <div><span>下载盘空间</span><strong>{storage ? `${storage.percent}%` : "--"}</strong></div>
          <div className="storage-bar"><i style={{ width: `${storage?.percent || 0}%` }} /></div>
          <small>{storage ? `${formatBytes(storage.used)} / ${formatBytes(storage.total)}` : "连接服务后显示真实容量"}</small>
          {storage && <small className="storage-free">剩余 {formatBytes(storage.free)}</small>}
        </div>
        <div className="system-state"><i className={connected ? "" : "offline"} /> {connected ? `${downloadDevice === "nas" ? "NAS" : "当前电脑"}已连接` : `${downloadDevice === "nas" ? "NAS" : "当前电脑"}未连接`} <span>v0.2.0</span></div>
      </aside>

      <section className="workspace" id="overview">
        <header>
          <button className="mobile-menu" aria-label="打开导航" onClick={() => setMenuOpen(true)}>☰</button>
          <div><h1>{{ overview: "下载中心", library: "媒体库", subscriptions: "订阅中心", cookies: "账号与 Cookies", notifications: "通知推送", settings: "系统设置" }[activeNav] || "NASFlow"}</h1><p>{{ overview: "添加链接、查看进度并管理最近下载。", library: "管理已经完成、失败或取消的任务记录。", subscriptions: "统一管理平台、订阅源和同步状态。", cookies: "按网站独立管理登录凭据。", notifications: "集中配置任务和订阅消息提醒。", settings: "调整下载服务与系统偏好。" }[activeNav]}</p></div>
          <div className="header-actions"><button aria-label="查看通知">♢<i /></button><span className="avatar">X</span></div>
        </header>

        {activeNav === "overview" && <><section className="capture-card">
          <div className="capture-copy"><span className="spark">✦</span><div><h2>把喜欢的内容，带回家。</h2><p>粘贴视频、图集或作品集链接，剩下的交给我们。</p></div></div>
          <form onSubmit={createTask}>
            <label><span>↗</span><input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="在这里粘贴链接..." aria-label="媒体链接" /></label>
            <button type="submit">开始下载 <span>→</span></button>
          </form>
          <div className="capture-options">
            <div className="device-picker" aria-label="下载设备"><span>下载到</span><button className={downloadDevice === "computer" ? "active" : ""} onClick={() => changeDownloadDevice("computer")} type="button">当前电脑</button><button className={downloadDevice === "nas" ? "active" : ""} onClick={() => changeDownloadDevice("nas")} type="button">NAS</button></div>
            <label className={`obsidian-option ${downloadDevice !== "nas" ? "disabled" : ""}`} title={downloadDevice !== "nas" ? "请先选择下载到 NAS" : "视频仍保存在媒体目录，只在 Obsidian 创建索引笔记"}><span className="obsidian-logo">O</span><span><b>同时收藏到 Obsidian</b><small>{downloadDevice === "nas" ? "创建视频笔记，视频仍单独保存" : "请先选择“下载到 NAS”"}</small></span><input type="checkbox" checked={saveToObsidian} disabled={downloadDevice !== "nas"} onChange={(event) => setSaveToObsidian(event.target.checked)} aria-label="同时收藏到 Obsidian" /></label>
            <div className="supported"><span className="mini yt">▶</span><span className="mini bilibili">B</span><span className="mini insta">◎</span><span className="mini x">𝕏</span><span className="mini note">R</span><span>支持 1000+ 网站</span></div>
            <label>保存至 <select aria-label="保存目录"><option>{saveToObsidian ? "/downloads/Obsidian视频" : "/downloads/自动分类"}</option><option>/downloads/视频</option><option>/downloads/图片</option></select></label>
            <label>画质 <select value={quality} onChange={(e) => setQuality(e.target.value)} aria-label="下载画质"><option>自动选择最佳画质</option><option>最高 4K</option><option>最高 1080P</option><option>仅音频</option></select></label>
          </div>
          {notice && <p className="notice" role="status">{notice}</p>}
        </section>

        <section className="panel task-panel download-center-tasks" id="tasks">
            <div className="panel-title"><div><h3>当前下载</h3><span>{active} 个进行中 · {tasks.length} 个全部任务</span></div><span className={`connection-badge ${connected ? "online" : ""}`}>{connected ? "服务已连接" : "服务未连接"}</span></div>
            <div className="task-filter-tabs" aria-label="任务状态筛选">{([['active', '进行中', tasks.filter((task) => task.status !== '已完成').length], ['running', '下载中', tasks.filter((task) => task.status === '下载中').length], ['queued', '等待中', tasks.filter((task) => task.status === '排队中').length], ['failed', '失败或取消', tasks.filter((task) => task.status === '失败' || task.status === '已取消').length]] as const).map(([value, label, count]) => <button key={value} className={taskFilter === value ? "active" : ""} onClick={() => setTaskFilter(value)}>{label}<span>{count}</span></button>)}</div>
            <div className="task-list">
              {homeTasks.map((task) => (
                <article className="task" key={task.id}>
                  <SourceMark tone={task.tone} label={task.source} />
                  <div className="task-main">
                    <div className="task-top"><div><h4>{task.title}</h4><p>{task.source}</p></div><strong className={task.status === "排队中" ? "queued" : task.status === "失败" ? "failed" : ""}>{task.status === "排队中" || task.status === "失败" || task.status === "已取消" ? task.status : `${task.progress}%`}</strong></div>
                    <div className="progress"><i style={{ width: `${task.progress || 3}%` }} /></div>
                    <div className="task-details"><span>{task.speed || (task.status === "排队中" ? "等待空闲任务槽" : task.meta)}</span><span>{task.eta ? `剩余 ${task.eta}` : task.status}</span></div>
                  </div>
                  <div className="task-row-actions">{(task.status === "失败" || task.status === "已取消") && <button onClick={() => retryTask(task)} aria-label={`重试 ${task.title}`}>↻</button>}{(task.status === "下载中" || task.status === "排队中") && <button onClick={() => cancelTask(task)} aria-label={`取消 ${task.title}`}>×</button>}</div>
                </article>
              ))}
              {!homeTasks.length && <div className="empty">当前筛选条件下没有任务。</div>}
            </div>
        </section>

        </>}

        {activeNav === "library" && <section className="content-grid single-view">

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
        </section>}

        {activeNav === "subscriptions" && (
          <div className="standalone-view subscription-standalone">
            <section className="subscription-dashboard">
              <div className="subscription-heading subscription-center-heading">
                <div><span>订阅中心</span><p>按平台管理频道、作者和收藏夹，自动发现新增内容。</p></div>
                <div className="subscription-heading-actions"><button onClick={() => setShowSubscriptionForm((value) => !value)}>＋ 添加订阅</button><button className="heading-action" onClick={() => syncSubscriptions()}>↻ 同步全部</button></div>
              </div>
              <section className="automation-strip subscription-automation">
                <div><span className="automation-icon">◎</span><div><h3>订阅自动更新</h3><p>按设定周期检查收藏夹与频道，只下载新增内容。</p></div></div>
                <div className="automation-pills"><span>自动去重</span><span>按平台启停</span><span>{subscriptions.filter((item) => item.enabled).length} 个订阅运行中</span></div>
                <button type="button" onClick={() => syncSubscriptions()}>立即同步全部</button>
              </section>
              <section className="platform-settings">
                <div><h3>更新网站</h3><p>关闭某个平台后，自动同步和“同步全部”都会跳过该网站；仍可进入单个订阅手动挑选内容。</p></div>
                <div className="platform-switch-list">
                  {["youtube", "bilibili", "instagram", "x"].map((platform) => <article key={platform}><span className={`platform-logo ${platform}`}>{platform === "youtube" ? "▶" : platform === "bilibili" ? "B" : platform === "instagram" ? "◎" : "X"}</span><div><strong>{platformLabels[platform]}</strong><small>{platformSwitches[platform] ? "已启用" : "已关闭"} · {subscriptions.filter((item) => subscriptionPlatform(item.url) === platform).length} 个订阅</small></div><button className={`subscription-switch ${platformSwitches[platform] ? "on" : ""}`} onClick={() => togglePlatform(platform)} aria-label={`${platformSwitches[platform] ? "关闭" : "开启"}${platformLabels[platform]}同步`} aria-pressed={platformSwitches[platform]}><i /></button></article>)}
                </div>
              </section>
              <div className="subscription-overview">
                <article><span>订阅总数</span><strong>{subscriptions.length}</strong><small>个订阅源</small></article>
                <article><span>今日新增</span><strong>{subscriptionsAddedToday}</strong><small>今天添加</small></article>
                <article><span>待同步</span><strong>{pendingSubscriptions}</strong><small>尚未运行</small></article>
                <article><span>最近同步</span><strong className="sync-time">{latestSubscriptionSync ? new Date(latestSubscriptionSync).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" }) : "—"}</strong><small>{latestSubscriptionSync ? new Date(latestSubscriptionSync).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : "暂无记录"}</small></article>
              </div>
              {showSubscriptionForm && <form className="subscription-form" onSubmit={createSubscription}>
                <label>订阅名称<input value={subscriptionName} onChange={(event) => setSubscriptionName(event.target.value)} placeholder="例如：喜欢的科技频道" /></label>
                <label>频道或收藏夹链接<input value={subscriptionUrl} onChange={(event) => setSubscriptionUrl(event.target.value)} placeholder="https://..." /></label>
                <button type="submit">添加订阅</button>
              </form>}
              <div className="subscription-list">
                <div className="subscription-list-title"><h3>订阅列表</h3><span>{subscriptions.length} 个</span></div>
                <div className="subscription-table-head"><span>名称</span><span>平台</span><span>链接</span><span>更新周期</span><span>状态</span><span>操作</span></div>
                {subscriptions.map((item) => (
                  <article key={item.id}>
                    <div className="subscription-name"><span className="subscription-source">{(platformLabels[subscriptionPlatform(item.url)] || "站").slice(0, 1)}</span><div><h4>{item.name}</h4><p>{item.last_checked_at ? `最近 ${new Date(item.last_checked_at).toLocaleString("zh-CN")}` : "尚未同步"}</p></div></div>
                    <span className="platform-cell">{platformLabels[subscriptionPlatform(item.url)] || subscriptionPlatform(item.url)}</span>
                    <a className="subscription-url" href={item.url} target="_blank" rel="noreferrer">{item.url}</a>
                    <span className="interval-cell">每 {item.interval_minutes / 60} 小时</span>
                    <span className={`subscription-status ${item.enabled ? "enabled" : ""}`}>{item.enabled ? "已启用" : "已暂停"}</span>
                    <div className="subscription-item-actions">
                      <button className="browse-one" onClick={() => browseSubscription(item)}>浏览内容</button>
                      <button className="sync-one" onClick={() => syncSubscription(item)}>同步</button>
                      <button className={`subscription-switch ${item.enabled ? "on" : ""}`} onClick={() => toggleSubscription(item)} aria-label={`${item.enabled ? "暂停" : "启用"} ${item.name}`} aria-pressed={item.enabled}><i /></button>
                    </div>
                  </article>
                ))}
                {!subscriptions.length && <div className="empty">还没有订阅，添加一个试试。</div>}
              </div>
              {browsingSubscription && <section className="entry-browser">
                <div className="entry-browser-heading"><div><span>收藏目录</span><h3>{browsingSubscription.name}</h3><p>勾选真正想保存的内容，再创建下载任务。</p></div><button onClick={() => setBrowsingSubscription(null)}>×</button></div>
                {entriesLoading ? <div className="empty">正在读取目录内容…</div> : <div className="entry-list">
                  {subscriptionEntries.map((entry) => <label key={entry.id}><input type="checkbox" checked={selectedEntries.has(entry.id)} onChange={() => setSelectedEntries((current) => { const next = new Set(current); next.has(entry.id) ? next.delete(entry.id) : next.add(entry.id); return next; })} /><span className="entry-cover">▶</span><div><strong>{entry.title}</strong><small>{entry.duration ? `${Math.floor(entry.duration / 60)} 分 ${entry.duration % 60} 秒` : "时长未知"}</small></div><a href={entry.url} target="_blank" rel="noreferrer">原页面</a></label>)}
                  {!subscriptionEntries.length && <div className="empty">没有读取到可下载内容。</div>}
                </div>}
                <div className="entry-actions"><button onClick={() => setSelectedEntries(new Set(subscriptionEntries.map((entry) => entry.id)))}>全选</button><span>已选择 {selectedEntries.size} 项</span><button className="primary" onClick={downloadSelectedEntries}>下载选中内容</button></div>
              </section>}
            </section>
          </div>
        )}

        {activeNav === "cookies" && (
          <div className="standalone-view">
            <section className="cookie-drawer cookie-page">
              <div className="subscription-heading">
                <div><span>本地凭据</span><h2 id="cookie-title">账号与 Cookies</h2><p>每个网站单独一行，下载时按链接域名自动匹配。</p></div>
              </div>
              <div className="cookie-security">Cookie 等同于登录凭据，只保存在 NASFlow 的本地数据目录。不要从陌生设备访问或截图分享。</div>
              <div className="cookie-import-card">
                <div><strong>通过浏览器插件获取</strong><p>推荐使用 Get cookies.txt LOCALLY 等本地导出插件，在已登录的网站导出 Netscape cookies.txt 后直接导入。</p></div>
                <label>导入 cookies.txt<input type="file" accept=".txt,text/plain" onChange={importCookieFile} /></label>
              </div>
              <div className="cookie-table">
                <div className="cookie-table-head"><span>网站域名</span><span>Cookie 内容</span><span /></div>
                {cookieRows.map((row) => (
                  <div className="cookie-row" key={row.id}>
                    <input value={row.domain} onChange={(event) => updateCookieRow(row.id, "domain", event.target.value)} placeholder="例如 youtube.com" aria-label="网站域名" />
                    <input type="password" value={row.cookie} onChange={(event) => updateCookieRow(row.id, "cookie", event.target.value)} placeholder="粘贴 Cookie 字符串" aria-label={`${row.domain || "网站"} Cookie`} />
                    <button onClick={() => setCookieRows((current) => current.filter((item) => item.id !== row.id))} aria-label={`删除 ${row.domain || "Cookie 行"}`}>×</button>
                  </div>
                ))}
              </div>
              <button className="add-cookie-row" onClick={() => setCookieRows((current) => [...current, { id: Date.now(), domain: "", cookie: "" }])}>＋ 添加一个网站</button>
              <div className="cookie-actions"><button onClick={() => setCookieRows([])}>清空</button><button className="primary" onClick={saveCookies}>保存 Cookie 配置</button></div>
            </section>
          </div>
        )}

        {activeNav === "notifications" && <section className="standalone-view simple-page"><h2>通知推送</h2><p>下载完成、失败以及订阅发现新内容时，都可以在这里统一配置提醒。</p><div className="simple-card"><strong>推送渠道</strong><span>该功能正在接入，后续可独立启用，不会挤在下载页面中。</span></div></section>}
        {activeNav === "settings" && <section className="standalone-view simple-page"><h2>系统设置</h2><p>配置当前电脑与 NAS 下载节点。地址只保存在当前浏览器。</p><div className="device-settings"><label><span>当前电脑 API</span><input value={computerApiUrl} onChange={(event) => setComputerApiUrl(event.target.value)} placeholder="http://127.0.0.1:8888" /></label><label><span>NAS API</span><input value={nasApiUrl} onChange={(event) => setNasApiUrl(event.target.value)} placeholder="http://192.168.31.126:18888" /></label><div className="device-settings-footer"><span><i className={connected ? "online" : ""} />当前选择：{downloadDevice === "nas" ? "NAS" : "当前电脑"} · {connected ? "连接正常" : "无法连接"}</span><button onClick={saveDeviceAddresses}>保存设备地址</button></div></div></section>}

        <footer><p><i /> NASFlow 服务运行中 · 已连续运行 12 天 8 小时</p><div><span>yt-dlp <b>最新版</b></span><span>gallery-dl <b>最新版</b></span><a href="#help">需要帮助？</a></div></footer>
      </section>
    </main>
  );
}
