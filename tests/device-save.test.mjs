import assert from "node:assert/strict";
import test from "node:test";
import { writeDeviceFile } from "../app/device-save.ts";

function destination({ failWrite = false } = {}) {
  const state = { opened: false, committed: false, aborted: false, bytes: [] };
  const handle = { async createWritable() {
    state.opened = true;
    return new WritableStream({
      write(chunk) { if (failWrite) throw new Error("disk full"); state.bytes.push(...chunk); },
      close() { state.committed = true; },
      abort() { state.aborted = true; },
    });
  } };
  return { handle, state };
}

test("commits the original bytes after the entire file arrives", async () => {
  const { handle, state } = destination();
  const updates = [];
  const result = await writeDeviceFile(new Response(new Uint8Array([1, 2, 3]), { headers: { "content-length": "3" } }), handle, p => updates.push(p));
  assert.equal(result, 3);
  assert.deepEqual(state.bytes, [1, 2, 3]);
  assert.equal(state.committed, true);
  assert.deepEqual(updates.at(-1), { received: 3, total: 3 });
});

test("empty, truncated and oversized responses never commit a file", async () => {
  for (const [bytes, length] of [[[], "0"], [[], null], [[1], "3"], [[1, 2, 3], "1"]]) {
    const { handle, state } = destination();
    await assert.rejects(writeDeviceFile(new Response(new Uint8Array(bytes), { headers: length ? { "content-length": length } : {} }), handle, () => {}));
    assert.equal(state.committed, false);
    if (state.opened) assert.equal(state.aborted, true);
  }
});

test("network interruption aborts the temporary write", async () => {
  const { handle, state } = destination();
  const body = new ReadableStream({ start(c) { c.enqueue(new Uint8Array([1])); }, pull(c) { c.error(new Error("connection lost")); } });
  await assert.rejects(writeDeviceFile(new Response(body), handle, () => {}));
  assert.equal(state.committed, false);
  assert.equal(state.aborted, true);
});

test("rejects login pages, errors and partial responses before opening destination", async () => {
  for (const response of [new Response("login", { headers: { "content-type": "text/html" } }), new Response("{}", { headers: { "content-type": "application/json" } }), new Response("denied", { status: 401 }), new Response("part", { status: 206 })]) {
    const { handle, state } = destination();
    await assert.rejects(writeDeviceFile(response, handle, () => {}));
    assert.equal(state.opened, false);
  }
});

test("streaming without Content-Length works, and disk errors cannot report success", async () => {
  const good = destination();
  assert.equal(await writeDeviceFile(new Response(new Uint8Array([1, 2])), good.handle, () => {}), 2);
  const bad = destination({ failWrite: true });
  await assert.rejects(writeDeviceFile(new Response(new Uint8Array([1, 2])), bad.handle, () => {}));
  assert.equal(bad.state.committed, false);
});
