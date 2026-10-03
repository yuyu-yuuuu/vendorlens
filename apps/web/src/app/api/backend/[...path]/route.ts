import { NextRequest } from "next/server";
import { isSameOriginWrite } from "@/lib/origin";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  if (request.method !== "GET" && !isSameOriginWrite(request))
    return Response.json({ detail: "跨站写入请求已拒绝" }, { status: 403 });
  const allowed = ["health", "projects", "tasks", "skills", "routing"];
  if (
    !allowed.includes(path[0]) ||
    path.some(
      (segment) => [".", ".."].includes(segment) || /[\\/]/.test(segment),
    )
  )
    return Response.json({ detail: "invalid API path" }, { status: 400 });
  const base = (
    process.env.VENDORLENS_API_URL ?? "http://127.0.0.1:8000"
  ).replace(/\/$/, "");
  const target = new URL(base + "/" + path.map(encodeURIComponent).join("/"));
  target.search = request.nextUrl.search;
  const headers = new Headers();
  for (const name of ["content-type", "last-event-id", "accept"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const key = process.env.VENDORLENS_API_ACCESS_KEY;
  if (key) headers.set("authorization", `Bearer ${key}`);
  try {
    const response = await fetch(target, {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method)
        ? undefined
        : await request.arrayBuffer(),
      signal: request.signal,
      cache: "no-store",
      redirect: "error",
    });
    const resultHeaders = new Headers({ "cache-control": "no-store" });
    for (const name of ["content-type", "content-disposition"]) {
      const value = response.headers.get(name);
      if (value) resultHeaders.set(name, value);
    }
    if (response.headers.get("content-type")?.includes("text/event-stream"))
      resultHeaders.set("x-accel-buffering", "no");
    return new Response(response.body, {
      status: response.status,
      headers: resultHeaders,
    });
  } catch {
    return Response.json(
      { detail: "后端暂时无法连接，请检查 API 服务。" },
      { status: 502 },
    );
  }
}
export { proxy as GET, proxy as POST, proxy as PUT };
