export const dynamic = "force-dynamic";

type RouteContext = {
  params: Promise<{ path?: string[] }> | { path?: string[] };
};

const hopByHopHeaders = [
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
];

async function proxy(request: Request, context: RouteContext) {
  const params = await context.params;
  const path = (params.path || []).map(encodeURIComponent).join("/");
  const base = (process.env.NASFLOW_INTERNAL_API_URL || "http://api:8888").replace(/\/+$/, "");
  const target = new URL(`${base}/${path}`);
  target.search = new URL(request.url).search;

  const headers = new Headers(request.headers);
  headers.delete("host");
  hopByHopHeaders.forEach((header) => headers.delete(header));

  const canHaveBody = !["GET", "HEAD"].includes(request.method);
  const body = canHaveBody ? await request.arrayBuffer() : undefined;
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body: body?.byteLength ? body : undefined,
    redirect: "manual",
    cache: "no-store",
  });

  const responseHeaders = new Headers(upstream.headers);
  hopByHopHeaders.forEach((header) => responseHeaders.delete(header));
  responseHeaders.set("Cache-Control", "no-store");

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export { proxy as DELETE, proxy as GET, proxy as HEAD, proxy as PATCH, proxy as POST, proxy as PUT };
