"""Settings. Everything tunable comes from the environment.

There is nothing to configure for extraction: the pipeline reads every document
with code. The API key, model id and per-call budget below belong to the
segmentation stage, whose only model call turns a cluster's aggregate profile
into a name -- they are never consulted while a document is being read. The
rest is storage, the optional local embedding model, and the thresholds linking
uses to decide whether two records describe the same transaction.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- storage ---------------------------------------------------------
    data_dir: Path = Field(default=BACKEND_DIR / "data")
    database_url: str = Field(default="")

    # --- embeddings ------------------------------------------------------
    embedding_model: str = Field(default="all-MiniLM-L6-v2")
    #: Header-mapping auto-accept threshold. A column the embedding tier is
    #: less sure than this about is shown to the user to confirm.
    embedding_auto_map_threshold: float = Field(default=0.72)
    merchant_embedding_threshold: float = Field(default=0.80)

    # --- matching thresholds (link.py) -----------------------------------
    transfer_day_window: int = Field(default=1)
    receipt_day_window: int = Field(default=3)
    receipt_amount_tolerance: float = Field(default=1.0)  # rupees
    merchant_fuzzy_threshold: int = Field(default=70)
    refund_day_window: int = Field(default=90)

    # --- segmentation ----------------------------------------------------
    #: Smallest group HDBSCAN is allowed to call a segment. Treated as a floor;
    #: cluster.py scales it with the ledger's size.
    segment_min_cluster_size: int = Field(default=5)
    segment_min_samples: int | None = Field(default=None)

    # --- the segment-naming model ----------------------------------------
    #: Extraction uses no model at all. Gemini is used to name clusters from
    #: aggregate profiles. It never sees raw statement rows.
    gemini_api_key: str = Field(default="")
    gemini_naming_model: str = Field(default="gemini-3.5-flash-lite")
    llm_enabled: bool = Field(default=True)
    llm_timeout_seconds: int = Field(default=30)
    llm_max_calls_per_job: int = Field(default=50)

    # --- the query agent and the financial report --------------------------
    #: The agent plans, calls tools over the ledger, the segments and the
    #: document index, and writes the answer. A full Flash model rather than
    #: Flash-Lite, because it has to choose tools well, not just phrase a name.
    gemini_agent_model: str = Field(default="gemini-3.5-flash")
    #: Separate from LLM_ENABLED, which governs segment *naming*: the agent is
    #: the product's chat and report, and has no rules fallback to degrade to.
    agent_enabled: bool = Field(default=True)
    #: Tool-calling rounds before the agent must answer with what it has.
    agent_max_steps: int = Field(default=8)
    report_max_steps: int = Field(default=16)
    agent_temperature: float = Field(default=0.7)
    agent_timeout_seconds: int = Field(default=90)

    # --- document index (vector store) -------------------------------------
    #: Passages returned per document search.
    rag_top_k: int = Field(default=6)
    #: Characters per passage cut from free text (PDF pages, email bodies).
    rag_chunk_chars: int = Field(default=1200)
    #: Rows per passage cut from a statement or sheet.
    rag_rows_per_chunk: int = Field(default=20)

    # --- accounts --------------------------------------------------------
    #: Off only for tests and single-user local runs: every request is then
    #: the built-in "local" user and plan limits are not enforced.
    auth_required: bool = Field(default=True)
    session_days: int = Field(default=30)
    #: true behind HTTPS in production; false for http://localhost
    cookie_secure: bool = Field(default=False)

    #: the version of the privacy policy + terms a sign-up agrees to; bump it
    #: when either changes materially
    consent_version: str = Field(default="2026-10-15")
    #: public address of the web app, for links in emails
    public_url: str = Field(default="http://localhost:3000")

    # --- data protection -------------------------------------------------------
    #: a whole upload (files, ledger, habits, reports, index) is erased this
    #: many days after its analysis, unless a Pro user extends it. 0 = never.
    upload_retention_days: int = Field(default=30)
    #: write rotating daily log files here (CERT-In asks for 180 days of logs)
    log_dir: str = Field(default="")
    log_retention_days: int = Field(default=180)

    # --- email (password reset) -------------------------------------------------
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_from: str = Field(default="")
    smtp_starttls: bool = Field(default=True)

    # --- plans ---------------------------------------------------------------
    free_chat_per_month: int = Field(default=10)
    free_reports_per_month: int = Field(default=1)
    #: emails are files too, so this is generous: one sample upload is ~36
    free_files_per_month: int = Field(default=50)
    #: Pro is "unlimited" with a fair-use ceiling that protects model costs
    pro_chat_per_month: int = Field(default=300)
    pro_reports_per_month: int = Field(default=20)
    pro_files_per_month: int = Field(default=2000)
    price_pro_monthly_paise: int = Field(default=14900)
    price_pro_yearly_paise: int = Field(default=99900)

    # --- payments (Razorpay) -------------------------------------------------
    razorpay_key_id: str = Field(default="")
    razorpay_key_secret: str = Field(default="")
    #: Lets the upgrade flow complete without a real payment. Never in production.
    payments_dev_mode: bool = Field(default=False)

    # --- api -------------------------------------------------------------
    #: Serve /docs and /openapi.json. Handy in development; production
    #: (docker-compose.yml) turns it off so the API surface isn't advertised.
    api_docs: bool = Field(default=True)
    cors_origins: str = Field(default="http://localhost:3000")
    max_upload_mb: int = Field(default=50)

    @property
    def naming_available(self) -> bool:
        """Whether a segment can be named by the model rather than by rules."""
        return bool(self.llm_enabled and self.gemini_api_key)

    @property
    def agent_available(self) -> bool:
        """The agent has no rules fallback: without a model there is no agent."""
        return bool(self.agent_enabled and self.gemini_api_key)

    @property
    def razorpay_enabled(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def vectors_dir(self) -> Path:
        return self.data_dir / "vectors"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def model_post_init(self, _ctx) -> None:
        if not self.database_url:
            object.__setattr__(
                self, "database_url", f"sqlite:///{(self.data_dir / 'finpilot.db').as_posix()}"
            )

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.uploads_dir, self.exports_dir, self.vectors_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Tests change env vars then call this."""
    get_settings.cache_clear()
    return get_settings()
