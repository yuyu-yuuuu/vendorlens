export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData))
    headers.set("content-type", "application/json");
  const response = await fetch("/api/backend" + path, {
    ...init,
    headers,
    cache: "no-store",
  });
  const body = await response
    .json()
    .catch(() => ({ detail: "服务返回了无法读取的内容" }));
  if (!response.ok) throw new ApiError(response.status, body.detail ?? body);
  return body as T;
}
export async function downloadSource(
  projectId: string,
  versionId: string,
  name: string,
) {
  const response = await fetch(
    `/api/backend/projects/${encodeURIComponent(projectId)}/versions/${encodeURIComponent(versionId)}/file`,
  );
  if (!response.ok) throw new Error("原始文件暂时无法下载");
  downloadBlob(await response.blob(), name);
}
export function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function exportJson(value: unknown, name: string) {
  downloadBlob(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
    name,
  );
}
