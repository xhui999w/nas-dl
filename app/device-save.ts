export type SaveFileHandle = {
  createWritable: () => Promise<WritableStream<Uint8Array>>;
};

export type SaveProgress = { received: number; total: number | null };

// Keep large files streaming. Commit the selected file only after all bytes
// arrive; an empty or interrupted response must never report a successful save.
export async function writeDeviceFile(
  response: Response,
  handle: SaveFileHandle,
  onProgress: (progress: SaveProgress) => void,
) {
  if (response.status !== 200 || !response.body || /(?:text\/html|application\/json)/i.test(response.headers.get("content-type") || "")) {
    await response.body?.cancel();
    throw new Error("文件获取失败，请检查登录或网络后重试");
  }
  const length = response.headers.get("content-length");
  const total = length !== null && /^\d+$/.test(length) ? Number(length) : null;
  if (total === 0) {
    await response.body.cancel();
    throw new Error("收到的文件为空，请重试");
  }
  const reader = response.body.getReader();
  let writer: WritableStreamDefaultWriter<Uint8Array> | undefined;
  let received = 0;
  let lastReport = 0;
  try {
    writer = (await handle.createWritable()).getWriter();
    onProgress({ received, total });
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      received += value.byteLength;
      if (total !== null && received > total) throw new Error("文件大小不一致，请重试");
      await writer.write(value);
      if (Date.now() - lastReport >= 200 || received === total) {
        onProgress({ received, total });
        lastReport = Date.now();
      }
    }
    if (!received || (total !== null && received !== total)) {
      throw new Error("文件未传输完整，请重试");
    }
    await writer.close();
    return received;
  } catch (error) {
    await reader.cancel().catch(() => {});
    await writer?.abort().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
    writer?.releaseLock();
  }
}
