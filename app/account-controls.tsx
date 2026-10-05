"use client";

import { useState, type FormEvent } from "react";

export function LogoutButton() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function logout() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/nas-api/api/auth/logout", { method: "POST" });
      if (!response.ok && response.status !== 401) throw new Error("退出失败，请重试。");
      window.location.replace("/login");
    } catch {
      setError("退出失败，请检查网络后重试。");
      setBusy(false);
    }
  }
  return <div className="logout-control"><button className="logout-button" type="button" disabled={busy} onClick={() => void logout()}>{busy ? "正在退出…" : "退出登录"}</button>{error && <span role="alert">{error}</span>}</div>;
}

export function AccountSecurity({ username }: { username: string }) {
  const [account, setAccount] = useState(username);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (newPassword !== confirmation) { setMessage("两次输入的新密码不一致。"); return; }
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/nas-api/api/auth/credentials", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: account, current_password: currentPassword, new_password: newPassword }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "保存失败，请检查账号和密码。");
      window.location.replace("/login");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法连接 NASFlow。");
      setBusy(false);
    }
  }
  return <section className="account-security"><h3>登录安全</h3><p>修改账号或密码后，所有设备都需要重新登录。</p><form onSubmit={(event) => void save(event)}>
    <label>管理员账号<input autoComplete="username" value={account} onChange={(event) => setAccount(event.target.value)} required maxLength={64} /></label>
    <label>当前密码<input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required maxLength={128} /></label>
    <label>新密码<input type="password" autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required minLength={8} maxLength={128} /></label>
    <label>确认新密码<input type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required minLength={8} maxLength={128} /></label>
    {message && <p className="auth-message" role="alert">{message}</p>}
    <button type="submit" disabled={busy}>{busy ? "正在保存…" : "保存并重新登录"}</button>
  </form></section>;
}
