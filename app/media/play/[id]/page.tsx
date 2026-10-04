import MediaPlayer from "./player";

export default async function PlayPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <MediaPlayer key={id} id={id} />;
}
