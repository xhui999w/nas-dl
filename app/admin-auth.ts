import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { safeAdminReturnPath } from "./auth-paths";

export async function requireAdministrator(returnTo: string): Promise<{ username: string }> {
  const token = (await cookies()).get("nasflow_admin")?.value;
  const loginPath = `/login?returnTo=${encodeURIComponent(safeAdminReturnPath(returnTo))}`;
  if (!token) redirect(loginPath);
  const base = (process.env.NASFLOW_INTERNAL_API_URL || "http://api:8888").replace(/\/+$/, "");
  const response = await fetch(`${base}/api/auth/session`, {
    headers: { Cookie: `nasflow_admin=${encodeURIComponent(token)}` },
    cache: "no-store",
  }).catch(() => null);
  if (!response?.ok) redirect(loginPath);
  return await response.json() as { username: string };
}
