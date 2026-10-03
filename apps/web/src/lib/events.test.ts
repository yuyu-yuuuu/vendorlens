import assert from "node:assert/strict";
import { test } from "node:test";
import { parseEvent, readEvents } from "./events";
test("SSE parser ignores keepalive and preserves multiline payload", () => {
  assert.equal(parseEvent(": keep-alive"), null);
  assert.deepEqual(
    parseEvent(
      'id: 7\nevent: node.completed\ndata: {"node":\ndata: "compose_report"}',
    ),
    {
      sequence: 7,
      type: "node.completed",
      payload: { node: "compose_report" },
    },
  );
  assert.equal(parseEvent("id: NaN\ndata: {}"), null);
});
test("streaming handles split UTF-8 and event boundaries", async () => {
  const original = globalThis.fetch;
  const encoder = new TextEncoder();
  const bytes = encoder.encode(
    'id: 1\nevent: node.completed\ndata: {"node":"评审"}\n\nid: 2\nevent: run.finished\ndata: {"status":"completed"}\n\n',
  );
  const received: unknown[] = [];
  globalThis.fetch = async () =>
    new Response(
      new ReadableStream({
        start(controller) {
          for (let i = 0; i < bytes.length; i += 5)
            controller.enqueue(bytes.slice(i, i + 5));
          controller.close();
        },
      }),
    );
  try {
    await readEvents("task", 0, new AbortController().signal, (e) =>
      received.push(e),
    );
    assert.equal(received.length, 2);
    assert.equal(
      (received[0] as { payload: { node: string } }).payload.node,
      "评审",
    );
  } finally {
    globalThis.fetch = original;
  }
});
