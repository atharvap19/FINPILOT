"use client";

/**
 * Free vs Pro, and the checkout.
 *
 * With Razorpay keys configured on the server, the order is paid in Razorpay
 * Checkout (loaded on demand) and the signature it returns is verified by the
 * API before the plan changes. In dev mode the API completes the order itself,
 * so the flow can be exercised without a payment.
 */

import { Check, Crown, Loader2, Minus } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError, api, type Account, type Checkout } from "@/lib/api";
import { cn } from "@/lib/utils";

const RAZORPAY_SRC = "https://checkout.razorpay.com/v1/checkout.js";

interface RazorpayResponse {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
}

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => { open: () => void };
  }
}

function loadRazorpay(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = RAZORPAY_SRC;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Could not load Razorpay Checkout."));
    document.body.appendChild(script);
  });
}

function payWithRazorpay(order: Checkout): Promise<RazorpayResponse> {
  return new Promise((resolve, reject) => {
    const Razorpay = window.Razorpay;
    if (!Razorpay) return reject(new Error("Razorpay is unavailable."));
    new Razorpay({
      key: order.key_id,
      order_id: order.order_id,
      amount: order.amount_paise,
      currency: order.currency,
      name: "FinPilot",
      description: order.label,
      prefill: { email: order.email, name: order.name },
      theme: { color: "#1f3a5f" },
      handler: resolve,
      modal: { ondismiss: () => reject(new Error("Payment cancelled.")) },
    }).open();
  });
}

const rupees = (paise: number) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(
    paise / 100,
  );

const FEATURES: { label: string; free: string | boolean; pro: string | boolean }[] = [
  { label: "Clean ledger, deduplication & CSV export", free: true, pro: true },
  { label: "Spending segments", free: true, pro: true },
  { label: "Questions to FinPilot", free: "per month", pro: "per month" },
  { label: "Financial reports (PDF)", free: "per month", pro: "per month" },
  { label: "Uploaded files", free: "per month", pro: "per month" },
  { label: "Keep your data longer than 30 days", free: false, pro: true },
  { label: "Alerts, budgets & goals", free: false, pro: "coming soon" },
  { label: "Monthly report by email", free: false, pro: "coming soon" },
];

export function UpgradeDialog({
  open,
  reason,
  account,
  onClose,
  onUpgraded,
}: {
  open: boolean;
  reason?: string;
  account: Account;
  onClose: () => void;
  onUpgraded: (account: Account) => void;
}) {
  const { data: plans } = useSWR(open ? "plans" : null, () => api.plans());
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isPro = account.plan === "pro";

  const limitFor = (plan: "free" | "pro", label: string) => {
    if (!plans) return "…";
    const caps = plans.limits[plan];
    if (label.startsWith("Questions")) return String(caps.chat);
    if (label.startsWith("Financial")) return String(caps.report);
    return String(caps.file);
  };

  async function buy(code: string) {
    setBusy(code);
    setError(null);
    try {
      const order = await api.checkout(code);
      let updated: Account;
      if (order.provider === "razorpay") {
        await loadRazorpay();
        const paid = await payWithRazorpay(order);
        updated = await api.verifyPayment(
          paid.razorpay_order_id,
          paid.razorpay_payment_id,
          paid.razorpay_signature,
        );
      } else {
        updated = await api.completeDevPayment(order.order_id);
      }
      onUpgraded(updated);
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Crown className="size-4 text-amber-500" aria-hidden />
            {isPro ? "You're on Pro" : "Upgrade to FinPilot Pro"}
          </DialogTitle>
          <DialogDescription>
            {reason ??
              (isPro
                ? `Pro is active until ${account.pro_until ? new Date(account.pro_until).toLocaleDateString("en-IN") : "—"}. You can extend it below.`
                : "More questions, more reports, more history — and the agent watching your money for you.")}
          </DialogDescription>
        </DialogHeader>

        <div className="overflow-hidden rounded-lg border text-sm">
          <table className="w-full">
            <thead className="bg-muted/60 text-xs">
              <tr>
                <th className="px-3 py-2 text-left font-medium" />
                <th className="px-3 py-2 text-center font-medium">Free</th>
                <th className="px-3 py-2 text-center font-medium">Pro</th>
              </tr>
            </thead>
            <tbody>
              {FEATURES.map((f) => (
                <tr key={f.label} className="border-t">
                  <td className="px-3 py-1.5">{f.label}</td>
                  {(["free", "pro"] as const).map((plan) => {
                    const v = f[plan];
                    return (
                      <td key={plan} className="px-3 py-1.5 text-center text-muted-foreground">
                        {v === true ? (
                          <Check className="mx-auto size-4 text-emerald-600" aria-label="included" />
                        ) : v === false ? (
                          <Minus className="mx-auto size-4" aria-label="not included" />
                        ) : v === "per month" ? (
                          `${limitFor(plan, f.label)}/mo`
                        ) : (
                          <span className="text-xs">{v}</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {plans?.provider === null && (
          <p className="text-xs text-amber-600">
            Payments are not configured on this server yet.
          </p>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          {(plans?.prices ?? []).map((price) => {
            const yearly = price.months === 12;
            return (
              <button
                key={price.code}
                type="button"
                disabled={busy !== null || plans?.provider === null}
                onClick={() => buy(price.code)}
                className={cn(
                  "relative rounded-lg border p-4 text-left transition-colors hover:bg-muted/50 disabled:opacity-60",
                  yearly && "border-primary ring-1 ring-primary",
                )}
              >
                {yearly && (
                  <Badge className="absolute -top-2 right-3">Best value</Badge>
                )}
                <p className="text-sm font-medium">{yearly ? "Yearly" : "Monthly"}</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums">
                  {rupees(price.amount_paise)}
                  <span className="text-sm font-normal text-muted-foreground">
                    /{yearly ? "year" : "month"}
                  </span>
                </p>
                {yearly && (
                  <p className="text-xs text-muted-foreground">
                    ≈ {rupees(price.amount_paise / 12)} a month
                  </p>
                )}
                {busy === price.code && (
                  <Loader2 className="absolute right-3 bottom-3 size-4 animate-spin" aria-hidden />
                )}
              </button>
            );
          })}
        </div>

        {plans?.provider === "dev" && (
          <p className="text-xs text-muted-foreground">
            Dev mode: the purchase completes without a real payment.
          </p>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button variant="ghost" onClick={onClose}>
          Not now
        </Button>
      </DialogContent>
    </Dialog>
  );
}
