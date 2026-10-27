import { afterEach, expect, it, vi } from "vitest";
import { beginResetGrant, pendingResetGrants, resolveResetGrant } from "@/lib/resetGrantIntents";

function storage() {
  const rows = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    get length() { return rows.size; },
    key: (index: number) => [...rows.keys()][index] ?? null,
    getItem: (key: string) => rows.get(key) ?? null,
    setItem: (key: string, value: string) => void rows.set(key, value),
    removeItem: (key: string) => void rows.delete(key),
  });
  return rows;
}
afterEach(() => vi.unstubAllGlobals());

it("keeps a committed but lost response pending and retries the original operation, then allows a new grant", async () => {
  storage();
  const applied = new Map<string, number>();
  let loseResponse = true;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url === "/api/account") return Response.json({ id: "admin" });
    const id = new Headers(init?.headers).get("Idempotency-Key");
    expect(id).toBeTruthy();
    if (!applied.has(id!)) applied.set(id!, applied.size + 1);
    if (loseResponse) { loseResponse = false; throw new TypeError("connection lost after commit"); }
    return Response.json({ granted_at: "2026-10-27T00:00:00+00:00", count: 3 });
  });
  const first = beginResetGrant("admin");
  await expect(resolveResetGrant("admin", first.operationId)).rejects.toThrow("connection lost");
  expect(pendingResetGrants("admin")).toEqual([first]);
  expect(pendingResetGrants("other")).toEqual([]);
  expect(await resolveResetGrant("admin", pendingResetGrants("admin")[0].operationId)).toEqual({ granted_at: "2026-10-27T00:00:00+00:00", count: 3 });
  expect(applied.size).toBe(1);
  expect(pendingResetGrants("admin")).toEqual([]);
  const second = beginResetGrant("admin");
  expect(second.operationId).not.toBe(first.operationId);
  await resolveResetGrant("admin", second.operationId);
  expect(applied.size).toBe(2);
});

it("refuses to send when storage is unavailable or the signed-in account changed", async () => {
  const rows = storage();
  const pending = beginResetGrant("admin");
  vi.stubGlobal("fetch", async (url: string) => {
    if (url !== "/api/account") throw new Error("must not send a grant");
    return Response.json({ id: "other" });
  });
  await expect(resolveResetGrant("admin", pending.operationId)).rejects.toThrow();
  expect(rows.size).toBe(1);
  vi.stubGlobal("localStorage", { setItem() { throw new Error("storage blocked"); }, length: 0 });
  expect(() => beginResetGrant("admin")).toThrow("storage blocked");
});

it("resolving one tab's pending operation leaves another tab's operation intact", async () => {
  const rows = storage();
  const first = beginResetGrant("admin");
  const second = { operationId: crypto.randomUUID(), createdAt: "2026-10-27T12:00:00.000Z" };
  rows.set(`eidolon.resetGrant.admin:${second.operationId}`, JSON.stringify({ version: 1, ...second }));
  expect(pendingResetGrants("admin")).toHaveLength(2);
  vi.stubGlobal("fetch", async (url: string) => Response.json(url === "/api/account" ? { id: "admin" } : {
    granted_at: "2026-10-27T00:00:00+00:00", count: 2,
  }));
  await resolveResetGrant("admin", first.operationId);
  expect(pendingResetGrants("admin")).toEqual([second]);
});
