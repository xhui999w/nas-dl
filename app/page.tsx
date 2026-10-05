import Home from "./home";
import { requireAdministrator } from "./admin-auth";

export const dynamic = "force-dynamic";

export default async function Page() {
  const administrator = await requireAdministrator("/");
  return <Home username={administrator.username} />;
}
