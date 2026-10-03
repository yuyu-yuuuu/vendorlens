import { ApiError } from "./api";
import type { RunEvent } from "./types";
export function parseEvent(block: string): RunEvent | null {
  let id = "",
    type = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!id || !data.length) return null;
  const sequence = Number(id);
  if (!Number.isSafeInteger(sequence) || sequence < 0) return null;
  return { sequence, type, payload: JSON.parse(data.join("\n")) };
}
export async function readEvents(
  taskId: string,
  after: number,
  signal: AbortSignal,
  onEvent: (event: RunEvent) => void,
) {
  const response = await fetch(
    `/api/backend/tasks/${encodeURIComponent(taskId)}/events?after=${after}`,
    { signal, headers: { accept: "text/event-stream" } },
  );
  if (!response.ok) throw new ApiError(response.status, "任务事件暂时无法读取");
  if (!response.body) throw new Error("事件流为空");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let cut: number;
      while ((cut = buffer.indexOf("\n\n")) !== -1) {
        const event = parseEvent(buffer.slice(0, cut));
        buffer = buffer.slice(cut + 2);
        if (event) onEvent(event);
      }
      if (done) break;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
