"use client";

import { useEffect, useId, useState } from "react";

import { Notice } from "@/components/app/admin/Notice";
import { Timestamp } from "@/components/app/admin/Timestamp";
import { AppLink } from "@/components/app/AppLink";
import { FailureNotice } from "@/components/app/FailureNotice";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { copy } from "@/i18n";
import { getAdminGifts, type AdminGifts } from "@/lib/adminGifts";
import { getAccount } from "@/lib/api";
import { beginResetGrant, pendingResetGrants, resolveResetGrant, type PendingResetGrant } from "@/lib/resetGrantIntents";
import { ANNOUNCEMENTS_HREF } from "@/lib/announcements";
import { toFailure, type Failure } from "@/lib/failure";

const t = copy.admin.config.resetCardGrants;

/**
 * 「好感度和礼物」卡里开关下面那一块（DESIGN.md「后台 · 配置」）：一句说明、上次发放，「发放」（outline）。
 * 点了先弹 `AlertDialog` 写明人数和后果；发完的提示写实际张数，再提一句可以去写公告，不自动配公告。
 * 好感度和礼物关着时「发放」不可用；开关一变（`isOn`）就重新读一遍是否开放，发放被拒时也是。
 */
export function ResetCardGrants({ isOn }: { isOn: boolean }) {
  const [gifts, setGifts] = useState<AdminGifts | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [isConfirming, setConfirming] = useState(false);
  const [isGranting, setGranting] = useState(false);
  const [granted, setGranted] = useState<number | null>(null);
  const [userId, setUserId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingResetGrant[]>([]);
  const [storageReady, setStorageReady] = useState(false);
  const titleId = useId();
  const users = gifts?.users ?? 0;

  useEffect(() => {
    getAdminGifts().then(setGifts, (e) => setFailure(toFailure(e)));
  }, [isOn]);

  useEffect(() => {
    let active = true;
    getAccount().then((account) => {
      if (active) setUserId(account.id);
    }, (e) => { if (active) setFailure(toFailure(e)); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!userId) return;
    function refreshPending() {
      try {
        setPending(pendingResetGrants(userId!));
        setStorageReady(true);
      } catch (e) {
        setStorageReady(false);
        setFailure(toFailure(e));
      }
    }
    refreshPending();
    window.addEventListener("storage", refreshPending);
    return () => window.removeEventListener("storage", refreshPending);
  }, [userId]);

  async function grant(operationId?: string) {
    if (!userId) return;
    setGranting(true);
    setFailure(null);
    setGranted(null);
    try {
      const intent = operationId ?? beginResetGrant(userId).operationId;
      setPending(pendingResetGrants(userId));
      const { count } = await resolveResetGrant(userId, intent);
      setGranted(count);
      // The POST has succeeded. A display refresh failure cannot create a new grant.
      getAdminGifts().then(setGifts, (e) => setFailure(toFailure(e)));
    } catch (e) {
      setFailure(toFailure(e));
      // 多半是别处刚关了开关：重读一遍，「发放」随之不可用。
      getAdminGifts().then(setGifts, () => {});
    } finally {
      try {
        setPending(pendingResetGrants(userId));
      } catch (e) {
        setStorageReady(false);
        setFailure(toFailure(e));
      }
      setGranting(false);
      setConfirming(false);
    }
  }

  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-3 border-t pt-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h3 id={titleId} className="font-medium">
            {t.title}
          </h3>
          <p className="text-muted-foreground">{t.hint}</p>
          {gifts && (
            <p className="text-muted-foreground">
              {gifts.last_grant ? (
                <>
                  {t.last}
                  <Timestamp value={gifts.last_grant.granted_at} />
                  {t.lastCount(gifts.last_grant.count)}
                </>
              ) : (
                t.never
              )}
            </p>
          )}
        </div>
        <Button variant="outline" size="sm" disabled={!gifts?.open || isGranting || !storageReady || pending.length > 0} onClick={() => setConfirming(true)}>
          {t.grant}
        </Button>
      </div>
      {pending.length > 0 && (
        <Alert>
          <AlertDescription className="flex flex-col items-start gap-3">
            <p>{t.pending(pending.length)}</p>
            <Button variant="outline" size="sm" disabled={isGranting || !storageReady} onClick={() => void grant(pending[0].operationId)}>
              {isGranting && <Spinner data-icon="inline-start" />}
              {t.resolve}
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {granted !== null && (
        <Notice text={t.granted(granted)} onDismiss={() => setGranted(null)}>
          {t.announce.before}
          <AppLink href={ANNOUNCEMENTS_HREF}>{t.announce.link}</AppLink>
          {t.announce.after}
        </Notice>
      )}
      {failure && <FailureNotice failure={failure} />}
      <AlertDialog open={isConfirming} onOpenChange={(open) => !isGranting && setConfirming(open)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t.dialog.title}</AlertDialogTitle>
            <AlertDialogDescription>{t.dialog.description(users)}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isGranting}>{copy.common.cancel}</AlertDialogCancel>
            <AlertDialogAction disabled={isGranting || !storageReady || pending.length > 0} onClick={() => void grant()}>
              {isGranting && <Spinner data-icon="inline-start" />}
              {t.dialog.confirm(users)}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
