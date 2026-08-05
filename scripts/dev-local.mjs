import http from "node:http";
import net from "node:net";
import { spawn } from "node:child_process";

const publicHost = process.env.NASFLOW_WEB_HOST || "127.0.0.1";
const publicPort = Number(process.env.NASFLOW_WEB_PORT || "3003");
const internalPort = Number(process.env.NASFLOW_INTERNAL_WEB_PORT || "3001");

const vinext = spawn(
  process.execPath,
  ["node_modules/vinext/dist/cli.js", "dev", "--port", String(internalPort)],
  { stdio: "inherit", env: process.env },
);

const server = http.createServer((request, response) => {
  const upstream = http.request({
    hostname: "::1",
    port: internalPort,
    path: request.url,
    method: request.method,
    headers: { ...request.headers, host: `localhost:${internalPort}` },
  }, (upstreamResponse) => {
    response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
    upstreamResponse.pipe(response);
  });
  upstream.on("error", () => {
    if (!response.headersSent) response.writeHead(503, { "content-type": "text/plain; charset=utf-8" });
    response.end("NASFlow frontend is starting. Please refresh shortly.");
  });
  request.pipe(upstream);
});

server.on("upgrade", (request, socket, head) => {
  const upstream = net.createConnection({ host: "::1", port: internalPort }, () => {
    const headers = Object.entries(request.headers)
      .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : value}`)
      .join("\r\n");
    upstream.write(`${request.method} ${request.url} HTTP/${request.httpVersion}\r\n${headers}\r\n\r\n`);
    if (head.length) upstream.write(head);
    socket.pipe(upstream).pipe(socket);
  });
  upstream.on("error", () => socket.destroy());
});

server.listen(publicPort, publicHost, () => {
  console.log(`NASFlow local web: http://${publicHost}:${publicPort}`);
});

function shutdown() {
  server.close();
  vinext.kill();
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
vinext.on("exit", (code) => {
  server.close(() => process.exit(code ?? 0));
});
