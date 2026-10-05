import MediaPlayer from "./player";
import { requireAdministrator } from "../../../admin-auth";

export const dynamic = "force-dynamic";

export default async function PlayPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  await requireAdministrator(`/media/play/${id}`);
  return <MediaPlayer key={id} id={id} />;
}
