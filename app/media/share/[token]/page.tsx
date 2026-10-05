import MediaPlayer from "../../play/[id]/player";

export default async function SharePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <MediaPlayer key={token} shareToken={token} />;
}
