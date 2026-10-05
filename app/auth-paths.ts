export function safeAdminReturnPath(value: string | null) {
  return value && /^\/media\/play\/[a-f0-9]{32}$/.test(value) ? value : "/";
}
