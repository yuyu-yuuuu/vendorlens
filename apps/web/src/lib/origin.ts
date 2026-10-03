/** Next may normalize request.url to localhost; the browser-facing Host is authoritative. */
export function isSameOriginWrite(request: Request): boolean {
  if (request.headers.get("sec-fetch-site") === "cross-site") return false;
  const origin = request.headers.get("origin");
  if (!origin) return true;
  try {
    const externalHost =
      request.headers.get("host") ?? new URL(request.url).host;
    const parsed = new URL(origin);
    return (
      parsed.host === externalHost &&
      parsed.protocol === new URL(request.url).protocol
    );
  } catch {
    return false;
  }
}
