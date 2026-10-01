export const dynamic = "force-dynamic";

export async function GET() {
  return Response.json(
    { apiBase: "/nas-api" },
    { headers: { "Cache-Control": "no-store" } },
  );
}
