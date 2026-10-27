// One pending record per confirmed grant; a lost response must never create a new intent.
import { getAccount } from "@/lib/api";
import { grantResetCards, type ResetCardGrant } from "@/lib/adminGifts";

export type PendingResetGrant = { operationId: string; createdAt: string };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const prefix = (userId: string) => `eidolon.resetGrant.${encodeURIComponent(userId)}:`;
const key = (userId: string, id: string) => prefix(userId) + id;

export function pendingResetGrants(userId: string): PendingResetGrant[] {
  const found: PendingResetGrant[] = [];
  for (let index = 0; index < localStorage.length; index++) {
    const name = localStorage.key(index);
    if (!name?.startsWith(prefix(userId))) continue;
    const item = JSON.parse(localStorage.getItem(name) ?? "null");
    if (!item || item.version !== 1 || typeof item.operationId !== "string" ||
        !UUID.test(item.operationId) || name !== key(userId, item.operationId) ||
        typeof item.createdAt !== "string" || !Number.isFinite(Date.parse(item.createdAt))) {
      throw new Error("上次发放的记录无法读取，请先核对后再发放。");
    }
    found.push({ operationId: item.operationId, createdAt: item.createdAt });
  }
  return found.sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.operationId.localeCompare(b.operationId));
}

export function beginResetGrant(userId: string): PendingResetGrant {
  if (pendingResetGrants(userId).length) throw new Error("请先核对上次发放的结果。");
  const item = { operationId: crypto.randomUUID(), createdAt: new Date().toISOString() };
  localStorage.setItem(key(userId, item.operationId), JSON.stringify({ version: 1, ...item }));
  return item;
}

export async function resolveResetGrant(userId: string, operationId: string): Promise<ResetCardGrant> {
  if (!pendingResetGrants(userId).some((item) => item.operationId === operationId)) {
    throw new Error("没有找到这次发放的待确认记录。");
  }
  if ((await getAccount()).id !== userId) throw new Error("登录的账号已改变，请刷新后再核对。");
  const result = await grantResetCards(operationId);
  localStorage.removeItem(key(userId, operationId));
  return result;
}
