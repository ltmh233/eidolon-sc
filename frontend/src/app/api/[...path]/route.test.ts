import { afterEach, describe, expect, it, vi } from "vitest";

import { GET, POST } from "./route";

function backendAnswers(response: () => Response) {
  const fetch = vi.fn(async (_url: string, _init: RequestInit) => response());
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function get(path: string, headers: Record<string, string> = {}) {
  return GET(new Request(`http://localhost:3000/api/${path}`, { headers }), {
    params: Promise.resolve({ path: path.split("/") }),
  });
}

describe("forwarding /api/* to the backend", () => {
  it("preserves grant keys including an explicit empty value for backend rejection", async () => {
    for (const value of ["12345678-1234-4234-8234-123456789abc", ""]) {
      const fetch = backendAnswers(() => Response.json({ count: 1 }));
      const response = await POST(new Request("http://localhost/api/admin/gifts/reset-card-grants", {
        method: "POST", headers: { "Idempotency-Key": value },
      }), { params: Promise.resolve({ path: ["admin", "gifts", "reset-card-grants"] }) });
      expect(new Headers(fetch.mock.calls[0][1].headers).get("Idempotency-Key"), value).toBe(value);
      await response.text();
    }
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("cancels a pending upstream fetch when the downstream request aborts", async () => {
    const controller = new AbortController();
    const entered = Promise.withResolvers<void>();
    vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => {
      entered.resolve();
      return new Promise<Response>((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
      });
    }));
    const request = new Request("http://localhost/api/stream", { signal: controller.signal });
    const response = GET(request, { params: Promise.resolve({ path: ["stream"] }) });
    const rejected = expect(response).rejects.toMatchObject({ name: "AbortError" });
    await entered.promise;
    controller.abort();
    await rejected;
  }, 1000);

  it("uses the downstream signal without disturbing a normally completed response", async () => {
    const request = new Request("http://localhost/api/stream");
    const fetch = backendAnswers(() => new Response("data: first\n\ndata: done\n\n"));
    const response = await GET(request, { params: Promise.resolve({ path: ["stream"] }) });
    expect(fetch.mock.calls[0][1].signal).toBe(request.signal);
    expect(await response.text()).toBe("data: first\n\ndata: done\n\n");
    expect(request.signal.aborted).toBe(false);
  });

  it("passes on what the browser needs to cache an avatar", async () => {
    backendAnswers(
      () =>
        new Response("jpeg", {
          headers: {
            "content-type": "image/jpeg",
            "cache-control": "public, max-age=86400",
            etag: '"abc"',
          },
        }),
    );

    const res = await get("characters/mano/avatar");

    expect(res.headers.get("cache-control")).toBe("public, max-age=86400");
    expect(res.headers.get("etag")).toBe('"abc"');
  });

  it("asks the backend whether the browser's copy is still current, and passes on its 304", async () => {
    const fetch = backendAnswers(() => new Response(null, { status: 304, headers: { etag: '"abc"' } }));

    const res = await get("characters/mano/avatar", { "if-none-match": '"abc"' });

    const sent = new Headers(fetch.mock.calls[0][1].headers);
    expect(sent.get("if-none-match")).toBe('"abc"');
    expect(res.status).toBe(304);
    expect(res.body).toBeNull();
  });

  it("streams a request's body to the backend, and passes on a download's name and size", async () => {
    const fetch = backendAnswers(
      () =>
        new Response("zip", {
          headers: {
            "content-type": "application/zip",
            "content-disposition": 'attachment; filename="eidolon-data-package-2026-10-19.zip"',
            "content-length": "3",
          },
        }),
    );
    const body = new Blob([new Uint8Array([0x50, 0x4b, 0x03, 0x04])]).stream();

    const res = await POST(
      new Request("http://localhost:3000/api/account/data-package?include=profile", {
        method: "POST",
        body,
        duplex: "half",
        headers: { "content-type": "application/zip" },
      } as RequestInit),
      { params: Promise.resolve({ path: ["account", "data-package"] }) },
    );

    const sent = fetch.mock.calls[0][1] as RequestInit & { duplex?: string };
    expect(sent.body).toBeInstanceOf(ReadableStream);
    expect(sent.duplex).toBe("half");
    expect(new Uint8Array(await new Response(sent.body).arrayBuffer())).toEqual(new Uint8Array([0x50, 0x4b, 0x03, 0x04]));
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="eidolon-data-package-2026-10-19.zip"');
    expect(res.headers.get("content-length")).toBe("3");
  });

  it("keeps an explicit DLC scope so the Go gateway can refuse legacy SC writes", async () => {
    const upstream = vi.fn(async (_url: string, init: RequestInit) => {
      const scope = new Headers(init.headers).get("x-work-id");
      return Response.json({}, { status: scope === "demo" ? 409 : 201 });
    });
    vi.stubGlobal("fetch", upstream);
    const response = await POST(
      new Request("http://localhost/api/conversations", {
        method: "POST",
        headers: { "content-type": "application/json", "x-work-id": "demo" },
        body: JSON.stringify({ character_id: "mano" }),
      }),
      { params: Promise.resolve({ path: ["conversations"] }) },
    );
    expect(response.status).toBe(409);
    expect(upstream).toHaveBeenCalledOnce();
  });
});
