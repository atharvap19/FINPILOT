"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, api, type FileStatus } from "@/lib/api";

export function PasswordDialog({
  jobId,
  file,
  onClose,
  onUnlocked,
}: {
  jobId: string;
  file: FileStatus | null;
  onClose: () => void;
  onUnlocked: () => void;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!file) return null;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await api.submitPassword(jobId, file.doc_id, password);
      const stillLocked = next.files.find((f) => f.doc_id === file.doc_id)?.needs_password;
      if (stillLocked) {
        setError("That password did not unlock the file. Try again.");
        return;
      }
      onUnlocked();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>This PDF is protected</DialogTitle>
          <DialogDescription>
            {file.filename} needs a password to open. It is used to unlock the
            file for this run and is never stored.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="pdf-password">Password</Label>
          <Input
            id="pdf-password"
            type="password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && password && !busy) void submit();
            }}
            placeholder="Often your date of birth or PAN"
          />
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!password || busy}>
            {busy ? "Unlocking…" : "Unlock"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
