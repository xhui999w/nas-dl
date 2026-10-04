"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import "plyr/dist/plyr.css";
import "./player.css";

const UNSUPPORTED = "当前视频格式暂不支持网页直接播放，可下载后使用本地播放器观看。";
type Media = { id: string; title: string; source: string; format: string; mime_type: string | null; supported: boolean; message: string | null };

export default function MediaPlayer({ id }: { id: string }) {
  const [media, setMedia] = useState<Media | null>(null);
  const [error, setError] = useState("");
  const [resumeNotice, setResumeNotice] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const apiPath = `/nas-api/api/media/${encodeURIComponent(id)}`;

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(apiPath, { signal: controller.signal, cache: "no-store" });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "视频暂时无法读取，请返回媒体库重试。");
        if (!controller.signal.aborted) setMedia(payload as Media);
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "无法连接下载服务，请稍后重试。");
      }
    }
    void load();
    return () => controller.abort();
  }, [apiPath]);

  useEffect(() => {
    const container = containerRef.current;
    if (!media?.supported || !media.mime_type || !container) return;
    let disposed = false;
    let cleanup = () => {};
    async function setup() {
      // Keep Plyr out of the media-library bundle and away from server rendering.
      const { default: Plyr } = await import("plyr");
      if (disposed || !media || !container) return;
      const video = document.createElement("video");
      if (!video.canPlayType(media.mime_type!)) { setError(UNSUPPORTED); return; }
      video.controls = true;
      video.playsInline = true;
      video.preload = "metadata";
      video.setAttribute("aria-label", media.title);
      container.replaceChildren(video);
      const player = new Plyr(video, {
        iconUrl: "/plyr.svg",
        controls: ["play-large", "play", "progress", "current-time", "duration", "mute", "volume", "settings", "pip", "fullscreen"],
        settings: ["speed"],
        speed: { selected: 1, options: [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2] },
        fullscreen: { enabled: true, fallback: true, iosNative: true },
        i18n: { play: "播放", pause: "暂停", mute: "静音", unmute: "取消静音", volume: "音量", settings: "设置", speed: "倍速", normal: "正常", enterFullscreen: "全屏", exitFullscreen: "退出全屏", pip: "画中画", seek: "进度", currentTime: "当前时间", duration: "时长" },
      });
      const storageKey = `nasflow:playback:v1:${id}`;
      let restored = false;
      const restore = () => {
        if (restored || !Number.isFinite(video.duration) || video.duration <= 0) return;
        restored = true;
        try {
          const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
          const position = saved?.position;
          if (typeof position === "number" && Number.isFinite(position) && position > 0 && position < video.duration - 3) {
            video.currentTime = position;
            const minutes = Math.floor(position / 60);
            const seconds = Math.floor(position % 60).toString().padStart(2, "0");
            setResumeNotice(`已恢复到上次观看位置 ${minutes}:${seconds}`);
          }
        } catch { /* Playback still works if storage is disabled or corrupted. */ }
      };
      const save = () => {
        if (!restored || video.error || !Number.isFinite(video.currentTime)) return;
        try {
          const finished = video.ended || video.currentTime >= video.duration - 3;
          if (finished) localStorage.removeItem(storageKey);
          else localStorage.setItem(storageKey, JSON.stringify({ position: video.currentTime }));
        } catch { /* Private mode/storage quotas must not interrupt playback. */ }
      };
      const onError = () => {
        setError(video.error?.code === 2 ? "视频读取中断，请检查网络后刷新页面重试。" : UNSUPPORTED);
      };
      const onVisibility = () => { if (document.hidden) save(); };
      video.addEventListener("loadedmetadata", restore);
      video.addEventListener("durationchange", restore);
      video.addEventListener("pause", save);
      video.addEventListener("seeked", save);
      video.addEventListener("ended", save);
      video.addEventListener("error", onError);
      window.addEventListener("pagehide", save);
      document.addEventListener("visibilitychange", onVisibility);
      const timer = window.setInterval(save, 5000);
      // No blob download or buffering of the entire file in JavaScript.
      video.src = `${apiPath}/stream`;
      video.load();
      cleanup = () => {
        save();
        window.clearInterval(timer);
        window.removeEventListener("pagehide", save);
        document.removeEventListener("visibilitychange", onVisibility);
        video.removeEventListener("loadedmetadata", restore);
        video.removeEventListener("durationchange", restore);
        video.removeEventListener("pause", save);
        video.removeEventListener("seeked", save);
        video.removeEventListener("ended", save);
        video.removeEventListener("error", onError);
        video.pause();
        player.destroy();
        video.removeAttribute("src");
        video.replaceChildren();
        video.load();
        container.replaceChildren();
      };
    }
    void setup().catch(() => { if (!disposed) setError("播放器加载失败，请刷新页面重试。"); });
    return () => { disposed = true; cleanup(); };
  }, [apiPath, id, media]);

  const message = error || (media && !media.supported ? media.message || UNSUPPORTED : "");
  return (
    <main className="media-page">
      <header className="media-page-header">
        <Link className="media-brand" href="/#library" prefetch={false}>NAS<span>Flow</span></Link>
        <Link className="media-back" href="/#library" prefetch={false}>← 返回媒体库</Link>
      </header>
      <section className="media-panel">
        <h1>{media?.title || "视频播放"}</h1>
        {media && <p className="media-info">{media.source} · {media.format.toUpperCase()}</p>}
        {!media && !error && <p role="status">正在读取视频信息…</p>}
        {message && <div className="media-message" role="alert"><p>{message}</p>{media && <a href={`/nas-api/api/tasks/${encodeURIComponent(id)}/file`} download>下载后观看 ⇩</a>}</div>}
        <div ref={containerRef} className="media-video" hidden={Boolean(message) || !media?.supported} />
        {resumeNotice && !message && <p className="media-resume" role="status">{resumeNotice}</p>}
      </section>
    </main>
  );
}
