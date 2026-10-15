"use client";

import { Crown, LogOut, Sparkles, UserRound } from "lucide-react";
import { useState } from "react";

import { useAccount } from "@/components/account/account-context";
import { AccountDialog } from "@/components/account/account-dialog";
import { ThemeToggle } from "@/components/account/theme-toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { UsageMeter } from "@/lib/api";
import { cn } from "@/lib/utils";

function Meter({ label, meter }: { label: string; meter: UsageMeter }) {
  const share = meter.limit ? Math.min(meter.used / meter.limit, 1) : 0;
  return (
    <div className="hidden w-28 md:block" title={`${meter.used} of ${meter.limit} this month`}>
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>{label}</span>
        <span className="tabular-nums">
          {meter.used}/{meter.limit}
        </span>
      </div>
      <div className="mt-0.5 h-1 overflow-hidden rounded-full bg-muted">
        <div
          className={cn("h-full rounded-full bg-primary", share >= 1 && "bg-destructive", share >= 0.8 && share < 1 && "bg-amber-500")}
          style={{ width: `${share * 100}%` }}
        />
      </div>
    </div>
  );
}

export function AppHeader() {
  const { account, logout, openUpgrade } = useAccount();
  const [accountOpen, setAccountOpen] = useState(false);
  const pro = account.plan === "pro";

  return (
    <header className="sticky top-0 z-30 border-b bg-background/90 backdrop-blur">
      <div className="mx-auto flex h-14 w-full max-w-7xl items-center gap-2 px-4 sm:gap-4 sm:px-6">
        <div className="flex items-center gap-2 font-semibold">
          <span className="flex size-7 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Sparkles className="size-4" aria-hidden />
          </span>
          FinPilot
        </div>
        <Badge variant={pro ? "default" : "outline"} className="gap-1">
          {pro && <Crown className="size-3" aria-hidden />}
          {pro ? "Pro" : "Free"}
        </Badge>

        <div className="ml-auto flex items-center gap-1 sm:gap-4">
          {account.auth_required && (
            <>
              <Meter label="Questions" meter={account.usage.chat} />
              <Meter label="Reports" meter={account.usage.report} />
              <Meter label="Files" meter={account.usage.file} />
            </>
          )}
          {!pro && account.auth_required && (
            <Button size="sm" onClick={() => openUpgrade()} title="Upgrade to Pro" aria-label="Upgrade to Pro">
              <Crown className="size-3.5" aria-hidden />
              <span className="hidden sm:inline">Upgrade</span>
            </Button>
          )}
          {pro && account.auth_required && (
            <Button size="sm" variant="ghost" onClick={() => openUpgrade()} title="Manage plan" aria-label="Manage plan">
              <Crown className="size-3.5 sm:hidden" aria-hidden />
              <span className="hidden sm:inline">Manage plan</span>
            </Button>
          )}
          <div className="hidden text-right text-xs leading-tight sm:block">
            <p className="font-medium">{account.user.name}</p>
            <p className="text-muted-foreground">{account.user.email}</p>
          </div>
          {account.auth_required && (
            <Button size="icon" variant="ghost" onClick={() => setAccountOpen(true)} title="Account and data">
              <UserRound className="size-4" aria-hidden />
              <span className="sr-only">Account and data</span>
            </Button>
          )}
          <ThemeToggle />
          {account.auth_required && (
            <Button size="icon" variant="ghost" onClick={() => void logout()} title="Sign out">
              <LogOut className="size-4" aria-hidden />
              <span className="sr-only">Sign out</span>
            </Button>
          )}
        </div>
      </div>
      <AccountDialog
        open={accountOpen}
        onClose={() => setAccountOpen(false)}
        account={account}
        onDeleted={() => {
          setAccountOpen(false);
          void logout();
        }}
      />
    </header>
  );
}
