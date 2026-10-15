"use client";

/**
 * Who is signed in, their plan and usage, and the upgrade dialog.
 *
 * `AccountGate` shows the sign-in screen until there is a session, then
 * provides the account to everything below it. Any component that hits a plan
 * limit (a 402 from the API) calls `openUpgrade` with the reason, and any that
 * spends usage calls `refresh` so the meters in the header stay current.
 */

import { Loader2 } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import useSWR from "swr";

import { AuthScreen } from "@/components/account/auth-screen";
import { UpgradeDialog } from "@/components/account/upgrade-dialog";
import { ApiError, api, type Account } from "@/lib/api";

interface AccountState {
  account: Account;
  refresh: () => Promise<unknown>;
  logout: () => Promise<void>;
  openUpgrade: (reason?: string) => void;
  /** If `err` is a plan-limit 402, open the upgrade dialog and return true. */
  handleLimit: (err: unknown) => boolean;
}

const AccountContext = createContext<AccountState | null>(null);

export function useAccount(): AccountState {
  const value = useContext(AccountContext);
  if (!value) throw new Error("useAccount outside AccountGate");
  return value;
}

export function AccountGate({ children }: { children: ReactNode }) {
  const { data, error, isLoading, mutate } = useSWR<Account | null>(
    "account",
    async () => {
      try {
        return await api.me();
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
    { revalidateOnFocus: true, shouldRetryOnError: false },
  );
  const [upgrade, setUpgrade] = useState<{ open: boolean; reason?: string }>({ open: false });

  const openUpgrade = useCallback((reason?: string) => setUpgrade({ open: true, reason }), []);
  const handleLimit = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 402) {
        openUpgrade(err.message);
        return true;
      }
      return false;
    },
    [openUpgrade],
  );
  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      window.localStorage.removeItem("finpilot.jobId");
      await mutate(null, { revalidate: false });
    }
  }, [mutate]);

  const value = useMemo(
    () =>
      data
        ? { account: data, refresh: () => mutate(), logout, openUpgrade, handleLimit }
        : null,
    [data, mutate, logout, openUpgrade, handleLimit],
  );

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        <Loader2 className="mr-2 size-4 animate-spin" aria-hidden />
        Loading…
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center px-6 text-center text-sm text-destructive">
        {error instanceof Error ? error.message : String(error)}
      </div>
    );
  }
  if (!value) return <AuthScreen onSignedIn={(account) => mutate(account, { revalidate: false })} />;

  return (
    <AccountContext.Provider value={value}>
      {children}
      <UpgradeDialog
        open={upgrade.open}
        reason={upgrade.reason}
        account={value.account}
        onClose={() => setUpgrade({ open: false })}
        onUpgraded={(account) => {
          void mutate(account, { revalidate: false });
          setUpgrade({ open: false });
        }}
      />
    </AccountContext.Provider>
  );
}
