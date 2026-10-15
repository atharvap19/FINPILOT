"use client";

import { CheckCircle2, Loader2, Sparkles } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, api } from "@/lib/api";

/** Choose a new password from an emailed, single-use link. */
export function ResetForm({ token }: { token: string }) {
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (password !== again) {
      setError("The two passwords don’t match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.resetPassword(token, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="w-full max-w-sm shadow-xl shadow-indigo-500/5">
      <CardContent className="space-y-5 pt-2">
        <div className="flex items-center gap-2 font-semibold">
          <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Sparkles className="size-4" aria-hidden />
          </span>
          FinPilot
        </div>
        {!token ? (
          <p className="text-sm text-destructive">
            This link is incomplete. Open the link from your email again, or ask for a new one from the sign-in page.
          </p>
        ) : done ? (
          <div className="space-y-4">
            <p className="flex items-center gap-2 font-medium">
              <CheckCircle2 className="size-5 text-emerald-600" aria-hidden />
              Your password has been changed.
            </p>
            <p className="text-sm text-muted-foreground">For your security you have been signed out everywhere.</p>
            <Button asChild className="w-full">
              <Link href="/">Sign in</Link>
            </Button>
          </div>
        ) : (
          <form className="space-y-4" onSubmit={submit}>
            <div>
              <h1 className="text-xl font-semibold">Choose a new password</h1>
              <p className="text-sm text-muted-foreground">At least 8 characters.</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-password">New password</Label>
              <Input id="new-password" type="password" required minLength={8} value={password}
                onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-password-again">Repeat it</Label>
              <Input id="new-password-again" type="password" required minLength={8} value={again}
                onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={busy}>
              {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Set new password
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
