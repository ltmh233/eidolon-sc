// Forwards /api/* to the backend at runtime, streaming both bodies: the request's, so that a
// data package being imported is never held whole in memory here, and the response's, so that
// server-sent events reach the browser as they arrive. The browser only ever talks to
// this origin: the session cookie, the Origin the backend checks, and the redirects of
// signing in with Google or GitHub all pass through here.

import { BACKEND_URL } from "@/lib/backend";

async function forward(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const { search } = new URL(request.url);
  const url = `${BACKEND_URL}/api/${path.map(encodeURIComponent).join("/")}${search}`;
  const headers = new Headers();
  // The Go gateway must see explicit work scope to reject DLC requests on legacy SC routes.
  for (const name of ["content-type", "cookie", "origin", "x-timezone", "x-work-id", "if-none-match", "idempotency-key"]) {
    const value = request.headers.get(name);
    if (value !== null && (value !== "" || name === "idempotency-key")) headers.set(name, value);
  }

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    duplex: "half", // a stream as the body, sent while it is still being read
    // Next aborts this signal on disconnect, including before upstream headers arrive.
    // Passing it also opts out of Next's GET deduplication and its tee'd response body.
    signal: request.signal,
    cache: "no-store",
    redirect: "manual", // the browser follows the backend's redirects itself
  };
  const res = await fetch(url, init);

  const out = new Headers();
  // the etag lets the browser revalidate an avatar instead of downloading it again; a data
  // package downloads under its name, with its size for the browser's progress
  for (const name of [
    "content-type",
    "cache-control",
    "etag",
    "location",
    "retry-after",
    "content-disposition",
    "content-length",
  ]) {
    const value = res.headers.get(name);
    if (value) out.set(name, value);
  }
  // fetch hands on a compressed body decompressed, no longer the length the backend gave
  if (res.headers.has("content-encoding")) out.delete("content-length");
  for (const cookie of res.headers.getSetCookie()) out.append("set-cookie", cookie);
  // a 304 has no body, and Response refuses one for it
  return new Response(res.status === 304 ? null : res.body, { status: res.status, headers: out });
}

export const dynamic = "force-dynamic";
export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
