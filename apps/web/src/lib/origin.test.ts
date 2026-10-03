import assert from "node:assert/strict";
import { test } from "node:test";
import { isSameOriginWrite } from "./origin";
test("allows loopback Host when Next normalizes its URL", () => {
  assert.equal(
    isSameOriginWrite(
      new Request("http://localhost:3000/api/backend/projects", {
        headers: {
          host: "127.0.0.1:3000",
          origin: "http://127.0.0.1:3000",
          "sec-fetch-site": "same-origin",
        },
      }),
    ),
    true,
  );
});
test("rejects cross-site and mismatched origins", () => {
  const cases: Record<string, string>[] = [
    { host: "127.0.0.1:3000", origin: "https://attacker.test" },
    {
      host: "127.0.0.1:3000",
      origin: "http://127.0.0.1:3000",
      "sec-fetch-site": "cross-site",
    },
    { host: "127.0.0.1:3000", origin: "null" },
  ];
  for (const headers of cases)
    assert.equal(
      isSameOriginWrite(new Request("http://localhost:3000", { headers })),
      false,
    );
});
