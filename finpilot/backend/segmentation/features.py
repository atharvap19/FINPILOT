"""Turn ledger rows into the matrix the clusterer sees.

Segmentation runs on the ledger ingestion produced, so every field here is one
the ingestion contract guarantees. Nothing is re-derived from raw documents.

Three families of feature, and the reason each one earns its place:

* **when** -- hour of day, day of week, day of month. All three are cyclical,
  so they are encoded as sin/cos pairs: without that, 23:00 and 00:00 sit at
  opposite ends of the axis and a late-night ordering habit splits in two. Day
  of month is what separates salary, rent and EMI from discretionary spending,
  because those land on the same date every month.
* **what** -- log amount, direction, payment mode, channel, account type and
  the ingestion flags. Amount is logged because spending is heavy-tailed: a
  single 85,000 salary credit would otherwise dominate the distance metric and
  flatten every 200-rupee difference that actually distinguishes habits.
* **how often** -- per-merchant count, amount variability and interval
  regularity. A subscription is not distinguished from a takeaway order by
  amount or hour; it is distinguished by arriving every month for the same
  figure. Those three columns are what let a cluster mean "subscription".

``time`` is frequently absent -- a bank statement rarely prints it, while an
order email does. Rather than impute an hour that was never observed, the hour
columns are zeroed and ``has_time`` carries the fact, so rows without a time do
not cluster as if they all happened at midnight.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

#: Columns fed to the clusterer as plain 0/1 indicators.
FLAG_COLUMNS: tuple[str, ...] = (
    "is_transfer",
    "is_refund",
    "is_fee",
    "is_cash_withdrawal",
    "emi_flag",
)

#: How many distinct ``mcc`` values get a column of their own.
TOP_CATEGORIES = 12

#: How much the category block counts for, relative to every other 0/1 column.
#:
#: Measured on an 806-row export carrying 22 categories, scoring each setting by
#: how consistently a segment's rows share a category:
#:
#:     weight 0 (ignored)  13 segments, category consistency 0.30
#:     weight 1            3 segments,  consistency 0.24
#:     weight 2            13 segments, consistency 0.87
#:     weight 3+           13 segments, consistency 0.87, noise 0
#:
#: At 1 the block is drowned by the 40-odd other binary columns. At 3 and above
#: the fit degenerates into grouping by the category column alone -- nothing a
#: GROUP BY could not do, and it stops noticing that groceries bought weekly in
#: small amounts differ from a single large monthly stock-up. 2 is where
#: category leads and the rest of the profile still shapes the result.
CATEGORY_WEIGHT = 2.0

#: Categorical columns one-hot encoded against a fixed vocabulary. The
#: vocabulary is fixed rather than learned so that two jobs over different
#: files still produce the same columns in the same order.
CATEGORICAL_VOCAB: dict[str, tuple[str, ...]] = {
    "direction": ("debit", "credit"),
    "account_type": ("bank", "credit_card", "cash", "wallet"),
    "payment_mode": (
        "UPI",
        "CARD",
        "NEFT",
        "IMPS",
        "RTGS",
        "ACH",
        "ATM",
        "CASH",
        "CHEQUE",
        "OTHER",
    ),
    "channel": ("app", "pos", "atm", "online", "other"),
    "counterparty_type": ("merchant", "person", "self", "unknown"),
}


@dataclass
class FeatureMatrix:
    """The scaled matrix plus everything needed to explain it afterwards."""

    txn_ids: list[str]
    matrix: np.ndarray
    columns: list[str] = field(default_factory=list)
    #: the source rows, carried through so profiling reads real values rather
    #: than trying to invert the scaling
    frame: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def n_rows(self) -> int:
        return int(self.matrix.shape[0])

    @property
    def n_features(self) -> int:
        return int(self.matrix.shape[1]) if self.matrix.ndim == 2 else 0


# --------------------------------------------------------------------------
# cyclical encoding
# --------------------------------------------------------------------------


def _cyclical(values: pd.Series, period: float) -> tuple[np.ndarray, np.ndarray]:
    radians = 2.0 * np.pi * values.to_numpy(dtype=float) / period
    return np.sin(radians), np.cos(radians)


def _hours(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Hour of day, and whether one was observed at all."""
    index = frame.index
    if "time" not in frame.columns:
        return pd.Series(0.0, index=index), pd.Series(0.0, index=index)
    raw = frame["time"].astype("object").where(frame["time"].notna(), None)
    parsed = pd.to_datetime(raw, format="mixed", errors="coerce")
    has_time = parsed.notna()
    hour = parsed.dt.hour.fillna(0).astype(float)
    return hour, has_time.astype(float)


# --------------------------------------------------------------------------
# per-merchant recurrence
# --------------------------------------------------------------------------


def merchant_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Count, amount variability and interval regularity, per merchant.

    Returned aligned to ``frame``'s index. Rows with no merchant get neutral
    values: a count of one and no regularity, which is what "seen once, no
    rhythm" should look like, rather than being pooled into a single enormous
    "unknown merchant" group that would invent a cluster of its own.
    """
    out = pd.DataFrame(index=frame.index)
    if "merchant" in frame.columns:
        merchant = frame["merchant"]
    else:
        merchant = pd.Series([None] * len(frame), index=frame.index)
    key = merchant.fillna("").astype(str).str.strip().str.lower()

    out["has_merchant"] = (key != "").astype(float)
    out["merchant_count"] = 1.0
    out["merchant_amount_cv"] = 0.0
    out["merchant_interval_regularity"] = 0.0

    amount = pd.to_numeric(frame["amount"], errors="coerce")
    date = pd.to_datetime(frame["date"], errors="coerce")

    for name, rows in key.groupby(key).groups.items():
        if not name:
            continue
        rows = list(rows)
        out.loc[rows, "merchant_count"] = float(len(rows))

        amounts = amount.loc[rows].dropna()
        if len(amounts) >= 2 and amounts.mean():
            # coefficient of variation: ~0 for a fixed subscription, high for
            # discretionary spending at the same merchant
            out.loc[rows, "merchant_amount_cv"] = float(
                amounts.std(ddof=0) / abs(amounts.mean())
            )

        days = date.loc[rows].dropna().sort_values()
        if len(days) >= 3:
            gaps = days.diff().dt.days.dropna().to_numpy(dtype=float)
            if gaps.size and gaps.mean() > 0:
                # 1 for a perfectly regular rhythm, decaying as gaps scatter
                out.loc[rows, "merchant_interval_regularity"] = float(
                    1.0 / (1.0 + gaps.std() / gaps.mean())
                )
    return out


# --------------------------------------------------------------------------
# the matrix
# --------------------------------------------------------------------------


def build_features(frame: pd.DataFrame) -> FeatureMatrix:
    """Canonical transaction frame -> scaled feature matrix.

    Scaling is z-score over the numeric block only; the one-hot and flag
    columns are already on a 0/1 scale and standardising them would inflate
    whichever category happens to be rare in this particular job.
    """
    from sklearn.preprocessing import StandardScaler

    frame = frame.reset_index(drop=True)
    if frame.empty:
        return FeatureMatrix(
            txn_ids=[], matrix=np.empty((0, 0)), columns=[], frame=frame
        )

    date = pd.to_datetime(frame["date"], errors="coerce")
    hour, has_time = _hours(frame)
    mask = has_time.to_numpy(dtype=float)

    numeric: dict[str, np.ndarray] = {}
    amount = pd.to_numeric(frame["amount"], errors="coerce").fillna(0.0)
    numeric["log_amount"] = np.log1p(amount.clip(lower=0).to_numpy(dtype=float))

    hour_sin, hour_cos = _cyclical(hour, 24.0)
    numeric["hour_sin"] = hour_sin * mask
    numeric["hour_cos"] = hour_cos * mask

    numeric["dow_sin"], numeric["dow_cos"] = _cyclical(date.dt.dayofweek.fillna(0), 7.0)
    numeric["dom_sin"], numeric["dom_cos"] = _cyclical(date.dt.day.fillna(1), 31.0)

    merch = merchant_features(frame)
    numeric["merchant_count"] = np.log1p(merch["merchant_count"].to_numpy(dtype=float))
    numeric["merchant_amount_cv"] = merch["merchant_amount_cv"].to_numpy(dtype=float)
    numeric["merchant_interval_regularity"] = merch[
        "merchant_interval_regularity"
    ].to_numpy(dtype=float)

    numeric_names = list(numeric)
    numeric_block = np.column_stack([numeric[n] for n in numeric_names])
    numeric_block = np.nan_to_num(numeric_block, nan=0.0, posinf=0.0, neginf=0.0)
    numeric_block = StandardScaler().fit_transform(numeric_block)

    binary: dict[str, np.ndarray] = {
        "has_time": mask,
        "is_weekend": (date.dt.dayofweek.fillna(0) >= 5).to_numpy(dtype=float),
        "has_merchant": merch["has_merchant"].to_numpy(dtype=float),
    }
    for column, vocab in CATEGORICAL_VOCAB.items():
        if column in frame.columns:
            values = frame[column].fillna("").astype(str)
        else:
            values = pd.Series([""] * len(frame))
        for option in vocab:
            binary[f"{column}={option}"] = (values == option).to_numpy(dtype=float)
    for column in FLAG_COLUMNS:
        if column in frame.columns:
            binary[column] = frame[column].fillna(False).astype(bool).to_numpy(dtype=float)
        else:
            binary[column] = np.zeros(len(frame), dtype=float)

    # ``mcc`` carries whatever category the source file already knew -- an
    # export that ships a Category column lands here, and it is by far the
    # strongest single signal about what a row is *for*. Unlike the columns
    # above its vocabulary cannot be fixed in advance, so the most common
    # values in this ledger get a column each and the tail shares one. Without
    # this, a file whose only distinguishing feature is its category clusters
    # on dates alone and splits into near-identical groups.
    if "mcc" in frame.columns:
        mcc = frame["mcc"].fillna("").astype(str).str.strip()
        binary["has_mcc"] = (mcc != "").to_numpy(dtype=float) * CATEGORY_WEIGHT
        top = [value for value, _ in Counter(v for v in mcc if v).most_common(TOP_CATEGORIES)]
        for value in top:
            binary[f"mcc={value}"] = (mcc == value).to_numpy(dtype=float) * CATEGORY_WEIGHT
        if top:
            binary["mcc=<other>"] = (
                ((mcc != "") & (~mcc.isin(top))).to_numpy(dtype=float) * CATEGORY_WEIGHT
            )

    binary_names = list(binary)
    binary_block = np.column_stack([binary[n] for n in binary_names])

    return FeatureMatrix(
        txn_ids=[str(t) for t in frame["txn_id"].tolist()],
        matrix=np.hstack([numeric_block, binary_block]),
        columns=numeric_names + binary_names,
        frame=frame,
    )
