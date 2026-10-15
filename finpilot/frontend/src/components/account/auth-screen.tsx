"use client";

/**
 * The signed-out landing page: what FinPilot is, how it works, what it reads,
 * and the sign-up / sign-in form.
 *
 * The hero illustration is plain HTML + CSS animation: document chips drift
 * in from the edges, a ledger card fills row by row, and a chat bubble from
 * the agent pops in -- the product's whole story in one loop. Everything
 * respects prefers-reduced-motion (see globals.css).
 */

import {
  ArrowRight,
  Bot,
  FileSpreadsheet,
  FileText,
  Loader2,
  LockKeyhole,
  Mail,
  Receipt,
  ScanSearch,
  Sparkles,
  Wand2,
} from "lucide-react";
import { useRef, useState } from "react";

import { ThemeToggle } from "@/components/account/theme-toggle";
import { SiteFooter } from "@/components/legal/site-footer";
import { FORMATS } from "@/components/onboarding/formats";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, api, type Account } from "@/lib/api";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// hero illustration
// ---------------------------------------------------------------------------

// placed above and below the ledger card, never over its rows
const CHIPS = [
  { label: "hdfc_statement.csv", icon: FileSpreadsheet, className: "left-0 top-0", tilt: "-6deg", delay: "0s" },
  { label: "airtel_bill.pdf", icon: Receipt, className: "left-[42%] -top-3", tilt: "3deg", delay: "1.8s" },
  { label: "card_july.pdf", icon: FileText, className: "right-0 top-2", tilt: "5deg", delay: "1.2s" },
  { label: "swiggy_order.eml", icon: Mail, className: "left-2 bottom-0", tilt: "4deg", delay: "0.6s" },
];

const ROWS = [
  { name: "Salary · Acme Tech", amount: "+₹1,25,000", tone: "text-emerald-600", dot: "bg-emerald-500" },
  { name: "Swiggy", amount: "−₹432", tone: "", dot: "bg-orange-500" },
  { name: "House rent", amount: "−₹22,000", tone: "", dot: "bg-sky-600" },
  { name: "Airtel postpaid", amount: "−₹799", tone: "", dot: "bg-violet-500" },
  { name: "Blinkit", amount: "−₹333", tone: "", dot: "bg-orange-500" },
];

function HeroIllustration() {
  return (
    <div className="relative mx-auto h-[420px] w-full max-w-[520px]" aria-hidden>
      <div className="fp-drift absolute top-10 left-16 size-56 rounded-full bg-indigo-400/25 blur-3xl" />
      <div className="fp-drift absolute right-10 bottom-10 size-56 rounded-full bg-fuchsia-400/20 blur-3xl" style={{ animationDelay: "-6s" }} />

      {/* the ledger card */}
      <div className="fp-rise absolute inset-x-10 top-16 rounded-2xl border bg-card/90 p-5 shadow-2xl shadow-indigo-500/10 backdrop-blur" style={{ animationDelay: "200ms" }}>
        <div className="mb-3 flex items-center justify-between">
          <div>
            <p className="text-xs text-muted-foreground">One clean ledger</p>
            <p className="text-lg font-semibold">₹2,07,406 spent</p>
          </div>
          <div className="flex items-end gap-1">
            {[38, 52, 30, 44, 26].map((h, i) => (
              <span key={i} className="fp-grow-y w-2 rounded-t-sm bg-indigo-500/80" style={{ height: h, animationDelay: `${600 + i * 90}ms` }} />
            ))}
          </div>
        </div>
        <div className="space-y-1.5">
          {ROWS.map((row, i) => (
            <div key={row.name} className="fp-rise flex items-center justify-between rounded-lg bg-muted/60 px-3 py-1.5 text-sm" style={{ animationDelay: `${700 + i * 160}ms` }}>
              <span className="flex items-center gap-2">
                <span className={cn("size-2 rounded-full", row.dot)} />
                {row.name}
              </span>
              <span className={cn("font-medium tabular-nums", row.tone)}>{row.amount}</span>
            </div>
          ))}
        </div>
      </div>

      {/* floating source documents */}
      {CHIPS.map(({ label, icon: Icon, className, tilt, delay }) => (
        <div
          key={label}
          className={cn("fp-float absolute flex items-center gap-2 rounded-xl border bg-background px-3 py-2 text-xs font-medium shadow-lg", className)}
          style={{ ["--fp-tilt" as string]: tilt, animationDelay: delay }}
        >
          <Icon className="size-4 text-indigo-600 dark:text-indigo-400" />
          {label}
        </div>
      ))}

      {/* the agent */}
      <div className="fp-rise absolute right-4 -bottom-2 max-w-[250px] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-3 text-xs text-white shadow-xl" style={{ animationDelay: "1.8s" }}>
        <p className="mb-1 flex items-center gap-1.5 font-semibold"><Bot className="size-3.5" />FinPilot</p>
        Late-night food orders cost you <b>₹4,458</b> this quarter — about <b>₹1,500/month</b> you could save.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// the page
// ---------------------------------------------------------------------------

const STEPS = [
  { icon: FileSpreadsheet, title: "Add your sources", body: "Bank and card statements, bills, order emails and your own spreadsheets — several at once." },
  { icon: Wand2, title: "We clean & connect", body: "Duplicates merged, transfers spotted, receipts matched to payments, every balance reconciled." },
  { icon: ScanSearch, title: "See your habits", body: "Spending grouped into the habits it really is, with a money-flow map and a calendar of every rupee." },
  { icon: Bot, title: "Ask your AI analyst", body: "Ask anything about your money and get a downloadable report — backed by your own documents." },
];

export function AuthScreen({ onSignedIn }: { onSignedIn: (account: Account) => void }) {
  const [mode, setMode] = useState<"login" | "register">("register");
  const formRef = useRef<HTMLDivElement | null>(null);

  const goTo = (next: "login" | "register") => {
    setMode(next);
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => formRef.current?.querySelector<HTMLInputElement>("input")?.focus(), 400);
  };

  return (
    <div className="fp-hero-bg min-h-screen overflow-x-hidden">
      <nav className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
        <div className="flex items-center gap-2 font-semibold">
          <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground"><Sparkles className="size-4" /></span>
          FinPilot
        </div>
        <div className="flex items-center gap-1">
          <ThemeToggle />
          <Button variant="ghost" size="sm" onClick={() => goTo("login")}>Sign in</Button>
        </div>
      </nav>

      {/* hero */}
      <section className="relative mx-auto grid max-w-7xl items-center gap-10 px-6 pt-8 pb-16 lg:grid-cols-2 lg:pt-16">
        <div className="fp-grid-bg pointer-events-none absolute inset-0 -z-10" />
        <div className="space-y-6">
          <span className="fp-rise inline-flex items-center gap-2 rounded-full border bg-background/70 px-3 py-1 text-xs font-medium text-muted-foreground backdrop-blur">
            <span className="size-1.5 rounded-full bg-emerald-500 fp-pulse" />
            AI expense guide, built for Indian bank data
          </span>
          <h1 className="fp-rise text-4xl font-semibold tracking-tight sm:text-5xl lg:text-6xl" style={{ animationDelay: "80ms" }}>
            Welcome to your <span className="fp-gradient-text">personal expense guide</span>
          </h1>
          <p className="fp-rise max-w-xl text-lg text-muted-foreground" style={{ animationDelay: "160ms" }}>
            Drop in your statements, bills and receipts. FinPilot turns them into one clean ledger, shows you where every rupee goes, and answers your questions like a personal analyst.
          </p>
          <div className="fp-rise flex flex-wrap gap-3" style={{ animationDelay: "240ms" }}>
            <Button size="lg" onClick={() => goTo("register")}>
              Get started free <ArrowRight className="size-4" />
            </Button>
            <Button size="lg" variant="outline" onClick={() => goTo("login")}>I have an account</Button>
          </div>
          <p className="fp-rise flex items-center gap-1.5 text-xs text-muted-foreground" style={{ animationDelay: "320ms" }}>
            <LockKeyhole className="size-3.5" />
            Account, card and UPI numbers are masked before anything is stored.
          </p>
        </div>
        <HeroIllustration />
      </section>

      {/* how it works */}
      <section className="mx-auto max-w-7xl px-6 pb-16">
        <h2 className="mb-6 text-center text-sm font-semibold tracking-widest text-muted-foreground uppercase">How it works</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map(({ icon: Icon, title, body }, i) => (
            <Card key={title} className="fp-rise relative p-5" style={{ animationDelay: `${i * 90}ms` }}>
              <span className="absolute top-4 right-4 text-xs font-semibold text-muted-foreground/60">0{i + 1}</span>
              <span className="mb-3 flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary"><Icon className="size-5" /></span>
              <p className="font-semibold">{title}</p>
              <p className="mt-1 text-sm text-muted-foreground">{body}</p>
            </Card>
          ))}
        </div>
      </section>

      {/* formats */}
      <section className="mx-auto max-w-7xl px-6 pb-16">
        <h2 className="mb-2 text-center text-2xl font-semibold tracking-tight">Reads what your bank actually gives you</h2>
        <p className="mb-6 text-center text-sm text-muted-foreground">HDFC, SBI, ICICI, Axis, Kotak and more — new layouts are learned the first time you confirm them.</p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {FORMATS.map(({ icon: Icon, title, exts, body }) => (
            <div key={title} className="flex gap-3 rounded-xl border bg-background/70 p-4 backdrop-blur">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted"><Icon className="size-4.5 text-indigo-600 dark:text-indigo-400" /></span>
              <div>
                <p className="text-sm font-semibold">{title}</p>
                <p className="text-xs text-muted-foreground">{body}</p>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {exts.map((e) => <span key={e} className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">{e}</span>)}
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* form */}
      <section className="mx-auto max-w-md px-6 pb-24" ref={formRef}>
        <AuthForm mode={mode} setMode={setMode} onSignedIn={onSignedIn} />
      </section>
      <SiteFooter />
    </div>
  );
}

function AuthForm({
  mode,
  setMode,
  onSignedIn,
}: {
  mode: "login" | "register";
  setMode: (m: "login" | "register") => void;
  onSignedIn: (account: Account) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [acceptTerms, setAcceptTerms] = useState(false);
  const [adult, setAdult] = useState(false);
  const [forgot, setForgot] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (forgot) {
        const r = await api.forgotPassword(email);
        setNotice(r.message);
        return;
      }
      const account =
        mode === "login"
          ? await api.login(email, password)
          : await api.register(email, password, name, { accept_terms: acceptTerms, confirm_age: adult });
      onSignedIn(account);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const switchMode = (next: "login" | "register") => {
    setMode(next);
    setForgot(false);
    setError(null);
    setNotice(null);
  };

  const title = forgot ? "Reset your password" : mode === "login" ? "Welcome back" : "Create your free account";
  const subtitle = forgot
    ? "Enter your email and we\u2019ll send you a link to choose a new password."
    : mode === "login"
      ? "Sign in to see your expense guide."
      : "Free forever for the basics. No card needed.";
  const canSubmit = forgot || mode === "login" || (acceptTerms && adult);

  return (
    <Card className="shadow-xl shadow-indigo-500/5">
      <CardContent className="pt-2">
        <h2 className="text-xl font-semibold">{title}</h2>
        <p className="mb-5 text-sm text-muted-foreground">{subtitle}</p>
        <form className="space-y-4" onSubmit={submit}>
          {mode === "register" && !forgot && (
            <div className="space-y-1.5">
              <Label htmlFor="name">Your name</Label>
              <Input id="name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          </div>
          {!forgot && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="password">Password</Label>
                {mode === "login" && (
                  <button
                    type="button"
                    className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                    onClick={() => {
                      setForgot(true);
                      setError(null);
                    }}
                  >
                    Forgot password?
                  </button>
                )}
              </div>
              <Input
                id="password"
                type="password"
                required
                minLength={mode === "register" ? 8 : undefined}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
              {mode === "register" && <p className="text-xs text-muted-foreground">At least 8 characters.</p>}
            </div>
          )}
          {mode === "register" && !forgot && (
            <div className="space-y-2.5 rounded-lg border bg-muted/30 p-3">
              <label className="flex items-start gap-2.5 text-sm">
                <Checkbox
                  checked={acceptTerms}
                  onCheckedChange={(v) => setAcceptTerms(v === true)}
                  className="mt-0.5"
                  aria-label="Accept the Terms and Privacy Policy"
                />
                <span className="text-muted-foreground">
                  I agree to the{" "}
                  <a href="/terms" target="_blank" className="text-foreground underline underline-offset-2">Terms</a> and{" "}
                  <a href="/privacy" target="_blank" className="text-foreground underline underline-offset-2">Privacy Policy</a>,
                  and consent to FinPilot processing my statements and documents &mdash; including sending masked
                  extracts to Google Gemini &mdash; to provide the service.
                </span>
              </label>
              <label className="flex items-start gap-2.5 text-sm">
                <Checkbox
                  checked={adult}
                  onCheckedChange={(v) => setAdult(v === true)}
                  className="mt-0.5"
                  aria-label="I am 18 or older"
                />
                <span className="text-muted-foreground">I am 18 years of age or older.</span>
              </label>
            </div>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
          {notice && <p className="text-sm text-emerald-600 dark:text-emerald-400">{notice}</p>}
          <Button type="submit" className="w-full" size="lg" disabled={busy || !canSubmit}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {forgot ? "Email me a reset link" : mode === "login" ? "Sign in" : "Create account"}
          </Button>
        </form>
        <p className="mt-4 text-center text-sm text-muted-foreground">
          {forgot ? (
            <button
              type="button"
              className="font-medium text-foreground underline-offset-4 hover:underline"
              onClick={() => switchMode("login")}
            >
              Back to sign in
            </button>
          ) : (
            <>
              {mode === "login" ? "New to FinPilot? " : "Already have an account? "}
              <button
                type="button"
                className="font-medium text-foreground underline-offset-4 hover:underline"
                onClick={() => switchMode(mode === "login" ? "register" : "login")}
              >
                {mode === "login" ? "Create an account" : "Sign in"}
              </button>
            </>
          )}
        </p>
      </CardContent>
    </Card>
  );
}
