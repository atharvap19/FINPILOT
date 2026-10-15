"use client";

/**
 * Chat with the FinPilot agent.
 *
 * Every question is a fresh agent run: Gemini plans, calls tools over the
 * ledger, the segments, the bills and the indexed documents, and writes the
 * answer. Its steps stream in as it works and stay folded under the answer, so
 * the user can see what it looked at; document passages it cited are listed
 * as sources.
 */

import { Bot, FileText, Loader2, Send, Sparkles, Square, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";

import { useAccount } from "@/components/account/account-context";
import { addStep, AgentSteps, type Step } from "@/components/sources/agent-steps";
import { Markdown } from "@/components/sources/markdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardTitle } from "@/components/ui/card";
import { ApiError, api, type AgentSource, type ChatTurn } from "@/lib/api";

const STARTERS = [
  "How did my spending change from month to month, and why?",
  "Which subscriptions and recurring payments am I paying for?",
  "What did my bills say, and are any still unpaid?",
  "Where could I realistically save ₹5,000 a month?",
];

interface Message {
  role: "user" | "assistant";
  content: string;
  steps?: Step[];
  sources?: AgentSource[];
  meta?: string;
  failed?: boolean;
}

export function AgentChat({
  jobId,
  ready,
  seed,
}: {
  jobId: string;
  ready: boolean;
  /** A question to send as soon as the chat is idle (from "Ask FinPilot" chips). */
  seed?: { id: number; text: string } | null;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [liveSteps, setLiveSteps] = useState<Step[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const { account, handleLimit, refresh: refreshAccount } = useAccount();
  const endRef = useRef<HTMLDivElement | null>(null);

  const { data: index } = useSWR(ready ? ["index", jobId] : null, () =>
    api.indexStatus(jobId),
  );

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [messages, liveSteps]);

  // a suggested question from elsewhere on the page: send it once
  const seededRef = useRef<number | null>(null);
  useEffect(() => {
    if (!ready || !seed || busy || seededRef.current === seed.id) return;
    seededRef.current = seed.id;
    void send(seed.text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed, ready, busy]);

  // a different job is a different conversation
  useEffect(() => {
    abortRef.current?.abort();
    setMessages([]);
    setLiveSteps([]);
  }, [jobId]);

  if (!ready) return null;

  async function send(text: string) {
    const question = text.trim();
    if (!question || busy) return;
    const history: ChatTurn[] = messages
      .filter((m) => !m.failed)
      .map((m) => ({ role: m.role, content: m.content }));
    setMessages((prev) => [...prev, { role: "user", content: question }]);
    setDraft("");
    setBusy(true);
    setLiveSteps([]);

    const controller = new AbortController();
    abortRef.current = controller;
    let steps: Step[] = [];
    let finished = false;
    try {
      await api.chat(
        jobId,
        question,
        history,
        (event) => {
          steps = addStep(steps, event);
          setLiveSteps(steps);
          if (event.type === "answer") {
            finished = true;
            void refreshAccount();
            setMessages((prev) => [
              ...prev,
              {
                role: "assistant",
                content: event.text,
                steps,
                sources: event.sources,
                meta: `${event.model} · ${event.steps} tool call${event.steps === 1 ? "" : "s"} · ${event.seconds}s`,
              },
            ]);
          } else if (event.type === "error") {
            finished = true;
            setMessages((prev) => [
              ...prev,
              { role: "assistant", content: event.message, steps, failed: true },
            ]);
          }
        },
        controller.signal,
      );
      if (!finished) {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: "The answer was cut off. Please try again.",
            steps,
            failed: true,
          },
        ]);
      }
    } catch (err) {
      const stopped = controller.signal.aborted;
      if (!stopped && handleLimit(err)) {
        setMessages((prev) => prev.slice(0, -1));
        setDraft(question);
        return;
      }
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: stopped
            ? "Stopped."
            : err instanceof ApiError
              ? err.message
              : String(err),
          steps,
          failed: true,
        },
      ]);
    } finally {
      setBusy(false);
      setLiveSteps([]);
      abortRef.current = null;
    }
  }

  return (
    <Card>
      <div className="flex items-start justify-between gap-4 px-6">
        <div>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="size-4 text-violet-500" aria-hidden />
            Ask FinPilot
          </CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            A financial-analysis agent. It plans, queries your ledger and segments,
            searches the documents you uploaded, and cites what it used.
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {index && (
            <Badge variant="outline" className="gap-1 font-normal">
              <FileText className="size-3" aria-hidden />
              {index.documents}/{index.documents_total} docs · {index.passages} passages indexed
            </Badge>
          )}
          {account.auth_required && (
            <span className="text-[11px] text-muted-foreground">
              {Math.max(account.usage.chat.limit - account.usage.chat.used, 0)} questions left this month
            </span>
          )}
        </div>
      </div>
      <CardContent className="space-y-4">
        {messages.length === 0 && !busy && (
          <div className="grid gap-2 sm:grid-cols-2">
            {STARTERS.map((prompt) => (
              <button
                key={prompt}
                type="button"
                onClick={() => send(prompt)}
                className="rounded-lg border px-3 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
              >
                {prompt}
              </button>
            ))}
          </div>
        )}

        {(messages.length > 0 || busy) && (
          <div className="max-h-[75vh] space-y-5 overflow-y-auto pr-1">
            {messages.map((message, index) =>
              message.role === "user" ? (
                <UserBubble key={index} text={message.content} />
              ) : (
                <AssistantBlock key={index} message={message} jobId={jobId} />
              ),
            )}
            {busy && (
              <div className="flex gap-3">
                <Avatar bot />
                <div className="min-w-0 flex-1 space-y-2">
                  <AgentSteps steps={liveSteps} running defaultOpen />
                  {liveSteps.length === 0 && (
                    <p className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Loader2 className="size-3 animate-spin" aria-hidden />
                      Planning…
                    </p>
                  )}
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>
        )}

        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void send(draft);
          }}
        >
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send(draft);
              }
            }}
            rows={1}
            placeholder="Ask anything about your money…"
            disabled={busy}
            maxLength={2000}
            aria-label="Message FinPilot"
            className="min-h-9 flex-1 resize-none rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50"
          />
          {busy ? (
            <Button
              type="button"
              size="icon"
              variant="outline"
              onClick={() => abortRef.current?.abort()}
            >
              <Square className="size-3.5" aria-hidden />
              <span className="sr-only">Stop</span>
            </Button>
          ) : (
            <Button type="submit" size="icon" disabled={!draft.trim()}>
              <Send className="size-4" aria-hidden />
              <span className="sr-only">Send</span>
            </Button>
          )}
        </form>
        <p className="text-[11px] leading-snug text-muted-foreground">
          FinPilot&rsquo;s answers are AI-generated from your documents and can contain mistakes. They are
          information, not financial, tax or investment advice.
        </p>
      </CardContent>
    </Card>
  );
}

function Avatar({ bot }: { bot?: boolean }) {
  return (
    <div
      className={
        bot
          ? "flex size-7 shrink-0 items-center justify-center rounded-full bg-violet-500/10 text-violet-600"
          : "flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground"
      }
    >
      {bot ? <Bot className="size-4" aria-hidden /> : <User className="size-4" aria-hidden />}
    </div>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex gap-3">
      <Avatar />
      <p className="pt-1 text-sm font-medium whitespace-pre-wrap">{text}</p>
    </div>
  );
}

function AssistantBlock({ message, jobId }: { message: Message; jobId: string }) {
  return (
    <div className="flex gap-3">
      <Avatar bot />
      <div className="min-w-0 flex-1 space-y-2">
        {message.steps && message.steps.length > 0 && (
          <AgentSteps steps={message.steps} running={false} />
        )}
        {message.failed ? (
          <p className="text-sm text-destructive">{message.content}</p>
        ) : (
          <Markdown text={message.content} jobId={jobId} />
        )}
        {message.sources && message.sources.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-muted-foreground">Sources:</span>
            {message.sources.map((source) => (
              <span
                key={`${source.filename}-${source.locator}`}
                title={source.snippet}
                className="inline-flex items-center gap-1 rounded-md border bg-muted/40 px-1.5 py-0.5 text-[11px] text-muted-foreground"
              >
                <FileText className="size-3" aria-hidden />
                {source.filename}
                {source.locator ? ` · ${source.locator}` : ""}
              </span>
            ))}
          </div>
        )}
        {message.meta && <p className="text-[11px] text-muted-foreground/70">{message.meta}</p>}
      </div>
    </div>
  );
}
