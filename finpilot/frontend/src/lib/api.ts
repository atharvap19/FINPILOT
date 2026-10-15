/**
 * The FastAPI client.
 *
 * These types mirror `app/api/schemas.py`. They are written by hand rather
 * than generated so that a backend change that breaks the UI shows up as a
 * TypeScript error here, at the one place worth looking, instead of as an
 * undefined at runtime three components deep.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// types
// ---------------------------------------------------------------------------

export type JobStatus =
  | "queued"
  | "running"
  | "needs_input"
  | "done"
  | "failed";

export type FileState =
  | "queued"
  | "processing"
  | "needs_password"
  | "needs_mapping"
  | "processed"
  | "needs_review"
  | "no_transactions"
  | "failed"
  | "duplicate";

export type Stage =
  | "detect"
  | "extract"
  | "map"
  | "normalize"
  | "validate"
  | "merge"
  | "link"
  | "done";

export interface Issue {
  code: string;
  severity: "error" | "warning";
  message: string;
  source_ref?: string | null;
}

export interface FileStatus {
  doc_id: string;
  filename: string;
  status: FileState;
  document_type: string;
  issuer: string | null;
  account: string | null;
  statement_start: string | null;
  statement_end: string | null;
  extraction_method: string | null;
  rows: number;
  needs_password: boolean;
  needs_mapping: boolean;
  issues: Issue[];
  reconciliation: Record<string, unknown> | null;
}

export interface JobTotals {
  files_processed: number;
  rows: number;
  duplicates_merged: number;
  transfers_found: number;
  card_payments_found: number;
  refunds_found: number;
  emails_linked: number;
  emails_added_as_rows: number;
  spreadsheet_rows_linked: number;
  bills_found: number;
  bills_paid: number;
  links_made: number;
  rows_needing_review: number;
  reconciliation_pass_rate: number | null;
}

export interface JobState {
  job_id: string;
  status: JobStatus;
  stage: Stage;
  progress: number;
  stages: Stage[];
  files: FileStatus[];
  error: string | null;
  created_at: string | null;
  finished_at: string | null;
  totals: JobTotals | null;
  /** when this upload is erased, and whether the user chose to keep it */
  retention: Retention | null;
}

/** One line of a budget plan: a fixed payment or a usual spend. */
export interface BudgetLine {
  key: string;
  label: string;
  amount: number;
  basis: string;
  /** set when a planned item already accounts for this line */
  covered_by: string | null;
  included: boolean;
  /** commitments only: the day it falls due */
  date?: string;
}

export interface TypedExpense {
  txn_id: string;
  date: string;
  amount: number;
  direction: "debit" | "credit";
  label: string;
  mode: string;
}

export interface BudgetPlan {
  period: "day" | "week";
  start: string;
  end: string;
  days: number;
  budget: number;
  planned: { label: string; amount: number }[];
  planned_total: number;
  /** already spent inside the window (typed or from statements) */
  spent: { label: string; amount: number }[];
  spent_total: number;
  commitments: BudgetLine[];
  commitments_total: number;
  usual: BudgetLine[];
  usual_total: number;
  safe_to_spend: number;
  per_day: number;
  status: "ok" | "tight" | "over";
  history: { from: string; to: string; weeks: number } | null;
}

export interface Retention {
  expires_at: string | null;
  days_left: number | null;
  /** null until the user answers "delete now / keep for 30 days" */
  choice: "keep" | null;
  extensions: number;
  retention_days: number;
}

export interface JobCreated {
  job_id: string;
  files: FileStatus[];
  skipped_duplicates: string[];
}

export interface TransactionRow {
  txn_id: string;
  date: string;
  time: string | null;
  amount: string;
  direction: "debit" | "credit";
  currency: string;
  account: string;
  account_type: string;
  source_type: string;
  source_ref: string;
  raw_description: string;
  merchant: string | null;
  counterparty_type: string;
  payment_mode: string;
  channel: string | null;
  balance_after: string | null;
  reference_no: string | null;
  order_id: string | null;
  item_details: string | null;
  card_last4: string | null;
  is_transfer: boolean;
  is_refund: boolean;
  is_fee: boolean;
  is_cash_withdrawal: boolean;
  emi_flag: boolean;
  needs_review: boolean;
  linked_sources: string;
  extraction_method: string;
  extraction_confidence: number;
}

export interface PreviewPage {
  rows: TransactionRow[];
  total: number;
  limit: number;
  offset: number;
  accounts: string[];
  payment_modes: string[];
}

export interface LinkedSource {
  source_ref: string;
  link_type: "primary" | "email" | "receipt" | "spreadsheet" | "bill";
  match_score: number;
  doc_id: string;
  filename: string | null;
  document_type: string | null;
}

export interface LineItem {
  name: string;
  quantity: string | null;
  unit_price: string | null;
  amount: string | null;
}

export interface TransactionDetail {
  transaction: TransactionRow;
  links: LinkedSource[];
  items: LineItem[];
  raw_values: Record<string, string> | null;
}

export interface Obligation {
  bill_id: string;
  biller: string;
  category_hint: string | null;
  amount_due: string;
  bill_date: string | null;
  due_date: string | null;
  billing_period: string | null;
  consumer_no: string | null;
  status: "due" | "paid" | "overdue";
  paid_txn_id: string | null;
  source_ref: string;
}

export interface Reconciliation {
  filename: string;
  account: string | null;
  ok: boolean;
  checked: boolean;
  expected_closing: string | null;
  stated_closing: string | null;
  difference: string | null;
  row_count: number;
  running_balance_ok: boolean | null;
}

export interface Summary extends JobTotals {
  reconciliations: Reconciliation[];
}

export interface ColumnSuggestion {
  header: string;
  field_name: string;
  confidence: number;
  reason: string;
}

export interface PendingMapping {
  doc_id: string;
  filename: string;
  headers: string[];
  sample_rows: string[][];
  suggestions: ColumnSuggestion[];
  amount_style: string;
  uncertain: string[];
  available_fields: { name: string; description: string }[];
  amount_styles: string[];
}

export interface SegmentView {
  segment_id: number;
  name: string | null;
  rationale: string;
  named_by: string | null;
  size: number;
  share_of_rows: number;
  median_amount: number;
  total_amount: number;
  mean_membership: number;
  profile: Record<string, unknown> | null;
}

export interface Segmentation {
  rows: number;
  segments: number;
  unsegmented_rows: number;
  named_by_model: number;
  named_by_rules: number;
  detail: SegmentView[];
}

export interface SegmentSlice {
  segment_id: number;
  name: string;
  size: number;
  total_amount: number;
}

export interface SegmentMonth {
  /** `YYYY-MM` */
  month: string;
  /** segment_id (as a string key) -> row count in that month */
  counts: Record<string, number>;
}

export interface SegmentPoint {
  date: string;
  amount: number;
  segment_id: number | null;
}

export interface SegmentChartData {
  slices: SegmentSlice[];
  months: SegmentMonth[];
  points: SegmentPoint[];
  unsegmented_rows: number;
  points_sampled: boolean;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface Recommendation {
  title: string;
  detail: string;
  monthly_saving_rupees: number | null;
  segment_id: number | null;
  segment_name: string | null;
}

export interface AdviceReply {
  answer: string;
  recommendations: Recommendation[];
  source: "model" | "rules";
  segmented: boolean;
  period_start: string | null;
  period_end: string | null;
}

/** A document passage the agent cited. */
export interface AgentSource {
  filename: string;
  locator: string;
  document_type: string | null;
  snippet: string;
}

/**
 * One line of the agent's stream. The agent plans (`thought`), calls tools
 * (`tool_call` / `tool_result`) as many times as it decides to, then ends with
 * `answer` (chat), `report` (financial report) or `error`.
 */
export type AgentEvent =
  | { type: "start"; model: string; max_steps: number }
  | { type: "thought"; text: string }
  | {
      type: "tool_call";
      id: number;
      step: number;
      name: string;
      args: Record<string, unknown>;
      label: string;
    }
  | { type: "tool_result"; id: number; name: string; summary: string }
  | {
      type: "answer";
      text: string;
      sources: AgentSource[];
      tools_used: string[];
      steps: number;
      seconds: number;
      model: string;
    }
  | { type: "report"; markdown: string; meta: ReportMeta }
  | { type: "error"; message: string };

export interface ReportMeta {
  generated_at: string;
  model: string;
  steps: number;
  tools_used: string[];
  seconds: number;
  sources: AgentSource[];
  pdf: boolean;
}

export interface FinancialReport {
  markdown: string;
  meta: ReportMeta;
}

export interface IndexStatus {
  passages: number;
  documents: number;
  documents_total: number;
  by_document: { filename: string; passages: number }[];
}

export interface Kpi {
  value: number;
  series?: number[];
  delta?: number | null;
  rate?: number | null;
}

/** Everything the dashboard draws, computed by code from the ledger. */
export interface Insights {
  empty: boolean;
  /** `months` is every month seen; `trend_months` drops partial ones. */
  period: { from: string; to: string; months: string[]; trend_months: string[] };
  kpis: {
    money_in: Kpi;
    money_out: Kpi;
    saved: Kpi;
    bills_due: { count: number; value: number; total_bills: number };
    transactions: number;
  };
  monthly: { month: string; in: number; out: number; net: number; partial: boolean }[];
  calendar: { date: string; out: number }[];
  week_hour: { grid: number[][]; weekday_totals: number[]; timed_share: number };
  merchants: { name: string; total: number; count: number; share: number }[];
  flow: {
    sources: { name: string; value: number }[];
    targets: { name: string; value: number; kind: "spend" | "saved" }[];
    grouped_by: "segment" | "merchant";
  };
  segment_trends: Habit[];
  segmented: boolean;
}

/** One spending habit (segment), told as a story. */
export interface Habit {
  segment_id: number;
  name: string;
  why: string | null;
  total: number;
  count: number;
  share: number;
  median: number;
  per_month: number;
  /** spend per month over `period.trend_months` */
  series: number[];
  change: number | null;
  merchants: { name: string; total: number }[];
  busiest_day: string | null;
  busiest_hour: number | null;
}

export type Plan = "free" | "pro";

export interface UsageMeter {
  used: number;
  limit: number;
}

export interface Account {
  user: { user_id: string; email: string; name: string };
  plan: Plan;
  pro_until: string | null;
  usage: { chat: UsageMeter; report: UsageMeter; file: UsageMeter };
  auth_required: boolean;
}

export interface JobListItem {
  job_id: string;
  status: JobStatus;
  created_at: string | null;
  files: number;
  period_start: string | null;
  period_end: string | null;
  expires_at: string | null;
}

export interface PlansInfo {
  prices: { code: string; label: string; amount_paise: number; months: number }[];
  limits: Record<Plan, { chat: number; report: number; file: number }>;
  provider: "razorpay" | "dev" | null;
}

export interface Checkout {
  provider: "razorpay" | "dev";
  order_id: string;
  key_id: string | null;
  amount_paise: number;
  currency: string;
  label: string;
  email: string;
  name: string;
}

export interface PreviewFilters {
  limit?: number;
  offset?: number;
  account?: string;
  direction?: string;
  payment_mode?: string;
  needs_review?: boolean;
  date_from?: string;
  date_to?: string;
  search?: string;
  sort?: "date" | "amount" | "merchant" | "account";
  order?: "asc" | "desc";
}

// ---------------------------------------------------------------------------
// transport
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** The structured body of a 402, when a plan limit was hit. */
    readonly limit: LimitReached | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** What a 402 says: which monthly limit was reached, and on which plan. */
export interface LimitReached {
  code: "limit_reached";
  feature: "chat" | "report" | "file";
  limit: number;
  used: number;
  plan: Plan;
  message: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { credentials: "include", ...init });
  } catch {
    // A network failure here almost always means the API is not running, and
    // saying so is far more useful than "Failed to fetch".
    throw new ApiError(
      0,
      `Could not reach the FinPilot API at ${API_BASE}. Is the backend running?`,
    );
  }
  if (!response.ok) throw await apiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function apiError(response: Response): Promise<ApiError> {
  let detail: unknown = null;
  try {
    detail = (await response.clone().json())?.detail;
  } catch {
    /* not json */
  }
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const limit = detail as LimitReached;
    if (limit.code === "limit_reached") return new ApiError(response.status, limit.message, limit);
  }
  return new ApiError(response.status, await errorMessage(response));
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail.message === "string") return detail.message;
    if (Array.isArray(detail) && detail.length) {
      return detail.map((d) => d.msg ?? JSON.stringify(d)).join("; ");
    }
  } catch {
    /* fall through to the status text */
  }
  return response.statusText || `Request failed with ${response.status}`;
}

/**
 * POST and read a newline-delimited JSON stream, one event per line, calling
 * `onEvent` as each arrives. Resolves when the server closes the stream.
 */
async function streamEvents(
  path: string,
  body: unknown,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "include",
      signal,
    });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new ApiError(
      0,
      `Could not reach the FinPilot API at ${API_BASE}. Is the backend running?`,
    );
  }
  if (!response.ok || !response.body) throw await apiError(response);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buffer += decoder.decode(value, { stream: !done });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line) as AgentEvent);
      newline = buffer.indexOf("\n");
    }
    if (done) break;
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as AgentEvent);
}

function query(filters: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

// ---------------------------------------------------------------------------
// endpoints
// ---------------------------------------------------------------------------

export const api = {
  me: () => request<Account>("/api/auth/me"),

  register: (
    email: string,
    password: string,
    name: string,
    consent: { accept_terms: boolean; confirm_age: boolean },
  ) =>
    request<Account>("/api/auth/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password, name, ...consent }) }),

  login: (email: string, password: string) =>
    request<Account>("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) }),

  logout: () => request<void>("/api/auth/logout", { method: "POST" }),

  forgotPassword: (email: string) =>
    request<{ ok: boolean; message: string }>("/api/auth/forgot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    }),

  resetPassword: (token: string, password: string) =>
    request<{ ok: boolean }>("/api/auth/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, password }),
    }),

  deleteAccount: (password: string, confirm: string) =>
    request<{ ok: boolean; uploads_deleted: number }>("/api/account", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password, confirm }),
    }),

  accountExportUrl: () => `${API_BASE}/api/account/export`,

  jobs: () => request<JobListItem[]>("/api/jobs"),

  budget: (
    jobId: string,
    body: { period: "day" | "week"; budget: number; text: string; start: string; exclude: string[] },
  ) =>
    request<BudgetPlan>(`/api/jobs/${jobId}/budget`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  logExpenses: (jobId: string, text: string) =>
    request<{ added: TypedExpense[]; skipped: string[] }>(`/api/jobs/${jobId}/expenses`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }),

  typedExpenses: (jobId: string) =>
    request<{ date: string; entries: TypedExpense[]; spent: number }>(`/api/jobs/${jobId}/expenses`),

  undoExpense: (jobId: string, txnId: string) =>
    request<void>(`/api/jobs/${jobId}/expenses/${txnId}`, { method: "DELETE" }),

  startManualLedger: () => request<{ job_id: string }>("/api/manual-ledger", { method: "POST" }),

  keepUpload: (jobId: string) =>
    request<Retention>(`/api/jobs/${jobId}/retention/keep`, { method: "POST" }),

  extendUpload: (jobId: string) =>
    request<Retention>(`/api/jobs/${jobId}/retention/extend`, { method: "POST" }),

  deleteJob: (jobId: string) => request<void>(`/api/jobs/${jobId}`, { method: "DELETE" }),

  insights: (jobId: string) => request<Insights>(`/api/jobs/${jobId}/insights`),

  demo: () => request<{ job_id: string }>("/api/demo", { method: "POST" }),

  plans: () => request<PlansInfo>("/api/billing/plans"),

  checkout: (plan_code: string) =>
    request<Checkout>("/api/billing/checkout", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan_code }) }),

  verifyPayment: (order_id: string, payment_id: string, signature: string) =>
    request<Account>("/api/billing/verify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ order_id, payment_id, signature }) }),

  completeDevPayment: (order_id: string) =>
    request<Account>("/api/billing/dev-complete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ order_id }) }),

  health: () =>
    request<{
      status: string;
      llm: { configured: boolean; enabled: boolean };
      agent: { available: boolean; model: string };
      embeddings: boolean;
      templates: string[];
    }>("/api/health"),

  createJob: (files: File[]) => {
    const body = new FormData();
    for (const file of files) body.append("files", file, file.name);
    return request<JobCreated>("/api/jobs", { method: "POST", body });
  },

  job: (jobId: string) => request<JobState>(`/api/jobs/${jobId}`),

  summary: (jobId: string) => request<Summary>(`/api/jobs/${jobId}/summary`),

  preview: (jobId: string, filters: PreviewFilters = {}) =>
    request<PreviewPage>(`/api/jobs/${jobId}/preview${query({ ...filters })}`),

  transaction: (jobId: string, txnId: string) =>
    request<TransactionDetail>(`/api/jobs/${jobId}/transactions/${txnId}`),

  obligations: (jobId: string) =>
    request<Obligation[]>(`/api/jobs/${jobId}/obligations`),

  pendingMappings: (jobId: string) =>
    request<PendingMapping[]>(`/api/jobs/${jobId}/mappings/pending`),

  confirmMapping: (
    jobId: string,
    docId: string,
    payload: {
      columns: Record<string, string>;
      amount_style: string;
      save_as_template: boolean;
      template_name?: string;
    },
  ) =>
    request<JobState>(`/api/jobs/${jobId}/mappings/${docId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  submitPassword: (jobId: string, docId: string, password: string) =>
    request<JobState>(`/api/jobs/${jobId}/files/${docId}/password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    }),

  segmentation: (jobId: string) =>
    request<Segmentation>(`/api/jobs/${jobId}/segments`),

  runSegmentation: (jobId: string) =>
    request<Segmentation>(`/api/jobs/${jobId}/segments`, { method: "POST" }),

  segmentChart: (jobId: string) =>
    request<SegmentChartData>(`/api/jobs/${jobId}/segments/chart`),

  ask: (jobId: string, question: string, history: ChatTurn[] = []) =>
    request<AdviceReply>(`/api/jobs/${jobId}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history }),
    }),

  exportUrl: (jobId: string, name: string) =>
    `${API_BASE}/api/jobs/${jobId}/export/${name}`,

  chat: (
    jobId: string,
    message: string,
    history: ChatTurn[],
    onEvent: (event: AgentEvent) => void,
    signal?: AbortSignal,
  ) => streamEvents(`/api/jobs/${jobId}/chat`, { message, history }, onEvent, signal),

  generateReport: (
    jobId: string,
    onEvent: (event: AgentEvent) => void,
    signal?: AbortSignal,
  ) => streamEvents(`/api/jobs/${jobId}/financial-report`, undefined, onEvent, signal),

  financialReport: (jobId: string) =>
    request<FinancialReport>(`/api/jobs/${jobId}/financial-report`),

  financialReportUrl: (jobId: string, format: "pdf" | "md") =>
    `${API_BASE}/api/jobs/${jobId}/financial-report/${format}`,

  indexStatus: (jobId: string) => request<IndexStatus>(`/api/jobs/${jobId}/index`),
};

// ---------------------------------------------------------------------------
// formatting
// ---------------------------------------------------------------------------

/** `123456.5` -> `1,23,456.50`. Indian grouping, which Intl already knows. */
export function formatRupees(value: string | number | null): string {
  if (value === null || value === "") return "";
  const amount = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(amount)) return String(value);
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

export function formatDate(value: string | null): string {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(date);
}

export function formatTime(value: string | null): string {
  if (!value) return "";
  return value.slice(0, 5);
}

export const STAGE_LABELS: Record<Stage, string> = {
  detect: "Detect",
  extract: "Extract",
  map: "Map",
  normalize: "Normalize",
  validate: "Validate",
  merge: "Merge",
  link: "Link",
  done: "Done",
};
