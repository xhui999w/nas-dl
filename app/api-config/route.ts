export const dynamic = "force-dynamic";

export async function GET() {
  const configuredPort = process.env.NASFLOW_API_PORT || "8888";
  const apiPort = /^\d{1,5}$/.test(configuredPort) ? configuredPort : "8888";

  return Response.json(
    { apiPort },
    { headers: { "Cache-Control": "no-store" } },
  );
}
