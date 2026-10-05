"use client";

import { useState, type FormEvent } from "react";
import { safeAdminReturnPath } from "../auth-paths";
import "../auth-ui.css";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/nas-api/api/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "登录失败，请重试。");
      const destination = safeAdminReturnPath(new URLSearchParams(window.location.search).get("returnTo"));
      window.location.replace(destination);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法连接 NASFlow，请稍后重试。");
      setBusy(false);
    }
  }

  return <main className="auth-page"><section className="auth-card">
    <div className="auth-brand">NAS<span>Flow</span></div>
    <h1>登录管理后台</h1><p>使用管理员账号管理下载、媒体库和设置。</p>
    <form onSubmit={(event) => void login(event)}>
      <label>账号<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required maxLength={64} /></label>
      <label>密码<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required maxLength={128} /></label>
      {message && <p className="auth-message" role="alert">{message}</p>}
      <button type="submit" disabled={busy}>{busy ? "正在登录…" : "登录"}</button>
    </form>
    <p className="auth-note">首次登录的账号信息由 NAS 管理员在部署时设置。</p>
  </section></main>;
}
