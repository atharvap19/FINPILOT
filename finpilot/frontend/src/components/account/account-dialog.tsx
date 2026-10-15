"use client";

/**
 * Account: download everything, or delete everything.
 *
 * Deleting needs the password and the word DELETE -- it is immediate and
 * irreversible, so it should never be one mis-click away.
 */

import { Download, Loader2, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { ApiError, api, type Account } from "@/lib/api";
import { LEGAL } from "@/lib/legal";

export function AccountDialog({
  open,
  onClose,
  account,
  onDeleted,
}: {
  open: boolean;
  onClose: () => void;
  account: Account;
  onDeleted: () => void;
}) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.deleteAccount(password, confirm);
      window.localStorage.removeItem("finpilot.jobId");
      onDeleted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Your account</DialogTitle>
          <DialogDescription>
            {account.user.name} · {account.user.email} · {account.plan === "pro" ? "Pro" : "Free"} plan
          </DialogDescription>
        </DialogHeader>

        <section className="space-y-2">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <ShieldCheck className="size-4 text-emerald-600" aria-hidden /> Your data
          </h3>
          <p className="text-sm text-muted-foreground">
            Each upload &mdash; files, ledger, habits and reports &mdash; is deleted from our servers
            {" "}{LEGAL.uploadRetentionDays} days after its analysis, unless you extend it with Pro. We email you 7 days
            and 1 day before. Read the{" "}
            <a href="/privacy" target="_blank" className="underline">Privacy Policy</a>.
          </p>
          <Button asChild variant="outline" size="sm">
            <a href={api.accountExportUrl()} download>
              <Download className="size-4" aria-hidden /> Download all my data (.zip)
            </a>
          </Button>
        </section>

        <Separator />

        <form className="space-y-3" onSubmit={remove}>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-destructive">
            <Trash2 className="size-4" aria-hidden /> Delete my account
          </h3>
          <p className="text-sm text-muted-foreground">
            Permanently deletes your account, every upload, your ledger, habits and reports — immediately, and
            it can&rsquo;t be undone. Payment records are kept only as tax law requires.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="delete-password">Your password</Label>
              <Input id="delete-password" type="password" required value={password}
                onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="delete-confirm">Type DELETE</Label>
              <Input id="delete-confirm" required value={confirm} onChange={(e) => setConfirm(e.target.value)}
                autoComplete="off" />
            </div>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button type="submit" variant="destructive" disabled={busy || confirm.trim().toUpperCase() !== "DELETE" || !password}>
            {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
            Delete my account and all data
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
