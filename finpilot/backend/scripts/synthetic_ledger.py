"""The synthetic story, and the ground truth derived from it.

One fictional user, three months, three accounts. Everything else in the
generator -- the CSVs, the card PDF, the emails, the bill images, the
spreadsheet -- is a *rendering* of the ledger built here, and
``expected_transactions.csv`` is the same ledger written out in the canonical
schema. That is the point: the ground truth cannot drift from the fixtures,
because both come from these objects.

Determinism: a fixed seed drives only the filler discretionary spends. Every
transaction the tests actually assert on (salary, rent, transfer, card bill
payment, ATM, refund, EMI, fees) is hand-written below.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field
from decimal import Decimal

SEED = 20260101

PERIOD_START = dt.date(2025, 6, 1)
PERIOD_END = dt.date(2025, 8, 31)

USER_NAME = "Aarav Sharma"
USER_EMAIL = "aarav.sharma@example.com"

# --------------------------------------------------------------------------
# accounts
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Account:
    key: str
    masked: str  # what ingestion must produce
    number: str  # what appears in the raw files
    kind: str  # AccountType value
    issuer: str
    opening_balance: Decimal | None = None

    @property
    def last4(self) -> str:
        return self.number[-4:]


HDFC = Account(
    key="hdfc",
    masked="HDFC-XX7890",
    number="50100234567890",
    kind="bank",
    issuer="HDFC Bank",
    opening_balance=Decimal("184250.00"),
)
SBI = Account(
    key="sbi",
    masked="SBI-XX8901",
    number="20012345678901",
    kind="bank",
    issuer="State Bank of India",
    opening_balance=Decimal("42180.00"),
)
CARD = Account(
    key="card",
    masked="CARD-XX4417",
    number="4477123456784417",
    kind="credit_card",
    issuer="HDFC Bank",
)
CASH = Account(key="cash", masked="CASH", number="", kind="cash", issuer="")

ACCOUNTS = {a.key: a for a in (HDFC, SBI, CARD, CASH)}

#: The user's own VPA, used for self-transfers and as the payer in emails.
USER_VPA = "aarav.sharma@okhdfcbank"


# --------------------------------------------------------------------------
# counterparties
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Party:
    """Who the money went to or came from.

    ``name`` is the display name the statement carries; ``merchant`` is what
    merchant resolution must canonicalise it to (None for self-dealings).
    """

    name: str
    merchant: str | None
    kind: str  # CounterpartyType value
    vpa: str | None = None
    ifsc: str = "HDFC0000060"
    city: str = "MUMBAI"
    mcc: str | None = None


P = Party
SWIGGY = P("SWIGGY", "Swiggy", "merchant", "swiggy.payu@hdfcbank", "HDFC0000060", "BANGALORE", "5812")
ZOMATO = P("ZOMATO LTD", "Zomato", "merchant", "zomato@ybl", "YESB0000001", "GURGAON", "5812")
BIGBASKET = P("BIGBASKET", "BigBasket", "merchant", "bigbasket@icici", "ICIC0000001", "BANGALORE", "5411")
BLINKIT = P("BLINKIT", "Blinkit", "merchant", "blinkit.rzp@axisbank", "UTIB0000123", "GURGAON", "5411")
DMART = P("DMART AVENUE SUPERMART", "DMart", "merchant", "dmart@paytm", "PYTM0123456", "MUMBAI", "5411")
HPCL = P("HPCL PETROL PUMP", "HPCL", "merchant", "hpcl.fuel@ybl", "YESB0000001", "MUMBAI", "5541")
NETFLIX = P("NETFLIX ENTERTAINMENT SER", "Netflix", "merchant", None, "HDFC0000060", "MUMBAI", "4899")
SPOTIFY = P("SPOTIFY INDIA", "Spotify", "merchant", "spotify.bd@icici", "ICIC0000001", "MUMBAI", "5815")
CULTFIT = P("CULT FIT", "Cult.fit", "merchant", "cultfit@ybl", "YESB0000001", "BANGALORE", "7997")
AMAZON = P("AMAZON IN", "Amazon", "merchant", "amazon@apl", "ICIC0000001", "BANGALORE", "5942")
MYNTRA = P("MYNTRA DESIGNS", "Myntra", "merchant", "myntra.rzp@hdfcbank", "HDFC0000060", "BANGALORE", "5651")
UBER = P("UBER INDIA", "Uber", "merchant", "uber.rzp@icici", "ICIC0000001", "MUMBAI", "4121")
SAMSUNG = P("SAMSUNG INDIA ELECTRONICS", "Samsung", "merchant", None, "HDFC0000060", "GURGAON", "5732")
AWS = P("AMAZON WEB SERVICES", "Amazon Web Services", "merchant", None, "HDFC0000060", "SEATTLE", "7372")
ACME = P("ACME TECHNOLOGIES PVT LTD", "Acme Technologies", "merchant", None, "ICIC0000001", "MUMBAI")
HDFC_BANK = P("HDFC BANK", "HDFC Bank", "merchant", None, "HDFC0000060", "MUMBAI")
SBI_BANK = P("STATE BANK OF INDIA", "State Bank of India", "merchant", None, "SBIN0000001", "MUMBAI")

# people -- phone-number and name VPAs, which is how the parser tells a person
# from a merchant
RAMESH = P("RAMESH KUMAR", "Ramesh Kumar", "person", "ramesh.kumar@okaxis", "UTIB0000123", "MUMBAI")
PRIYA = P("PRIYA NAIR", "Priya Nair", "person", "9820045612@ybl", "YESB0000001", "MUMBAI")
VIKRAM = P("VIKRAM SINGH", "Vikram Singh", "person", "8765432109@paytm", "PYTM0123456", "DELHI")
SELF = P(USER_NAME.upper(), None, "self", USER_VPA, "HDFC0000060", "MUMBAI")


# --------------------------------------------------------------------------
# ledger entries
# --------------------------------------------------------------------------


@dataclass
class OrderDetail:
    """Extra facts that only an order email or receipt knows."""

    order_id: str
    items: list[tuple[str, int, Decimal]] = field(default_factory=list)
    gst: Decimal | None = None
    discount: Decimal | None = None
    delivery_fee: Decimal | None = None
    item_details: str | None = None


@dataclass
class Entry:
    """One real-world transaction, plus how each file should render it."""

    date: dt.date
    amount: Decimal
    direction: str  # debit | credit
    account: Account
    party: Party
    payment_mode: str
    ref: str | None = None
    remark: str | None = None
    channel: str | None = None
    time: dt.time | None = None
    tags: set[str] = field(default_factory=set)
    order: OrderDetail | None = None
    fx: tuple[str, Decimal] | None = None  # (currency, foreign amount)
    # --- rendering flags -------------------------------------------------
    email: str | None = None  # email kind: order_swiggy/order_zomato/alert/...
    in_sheet: bool = False  # also appears in personal_expenses.xlsx
    sheet_note: str | None = None
    #: key of the Entry this one reverses, for a refund
    refund_of_key: str | None = None
    # --- filled during build --------------------------------------------
    balance_after: Decimal | None = None
    key: str = ""

    @property
    def is_fee(self) -> bool:
        return "fee" in self.tags

    @property
    def is_cash_withdrawal(self) -> bool:
        return "atm" in self.tags

    @property
    def is_transfer(self) -> bool:
        return "transfer" in self.tags

    @property
    def is_refund(self) -> bool:
        return "refund" in self.tags

    @property
    def emi_flag(self) -> bool:
        return "emi" in self.tags


def _ref(n: int) -> str:
    """Deterministic 12-digit UPI reference."""
    return f"5{n:011d}"


def _utr(n: int) -> str:
    return f"N{n:014d}"


# --------------------------------------------------------------------------
# the story
# --------------------------------------------------------------------------

_SALARY_DATES = [dt.date(2025, 6, 2), dt.date(2025, 7, 1), dt.date(2025, 8, 1)]
_RENT_DATES = [dt.date(2025, 6, 5), dt.date(2025, 7, 5), dt.date(2025, 8, 5)]
_NETFLIX_DATES = [dt.date(2025, 6, 15), dt.date(2025, 7, 15), dt.date(2025, 8, 15)]
_GYM_DATES = [dt.date(2025, 6, 8), dt.date(2025, 7, 8), dt.date(2025, 8, 8)]
_SMS_FEE_DATES = [dt.date(2025, 6, 30), dt.date(2025, 7, 31), dt.date(2025, 8, 31)]

#: Discretionary spends the RNG draws from: (party, low, high, account, late_night?)
_FILLER_MENU: list[tuple[Party, int, int, str, bool]] = [
    (SWIGGY, 240, 780, "hdfc", True),
    (ZOMATO, 210, 690, "sbi", True),
    (BLINKIT, 180, 950, "hdfc", False),
    (BIGBASKET, 640, 2400, "hdfc", False),
    (DMART, 850, 3200, "hdfc", False),
    (HPCL, 1000, 2600, "hdfc", False),
    (AMAZON, 299, 3499, "sbi", False),
    (UBER, 90, 480, "hdfc", False),
]


def _build_anchors() -> list[Entry]:
    """Every transaction a test asserts on."""
    e: list[Entry] = []
    n = 1

    # --- salary ---------------------------------------------------------
    for i, d in enumerate(_SALARY_DATES):
        e.append(
            Entry(
                date=d,
                amount=Decimal("125000.00"),
                direction="credit",
                account=HDFC,
                party=ACME,
                payment_mode="NEFT",
                ref=_utr(100000000 + i),
                remark=f"SALARY {d.strftime('%b').upper()} {d.year}",
                channel="online",
                key=f"salary_{i}",
            )
        )

    # --- rent to a person ------------------------------------------------
    for i, d in enumerate(_RENT_DATES):
        e.append(
            Entry(
                date=d,
                amount=Decimal("22000.00"),
                direction="debit",
                account=HDFC,
                party=RAMESH,
                payment_mode="UPI",
                ref=_ref(n := n + 1),
                remark=f"RENT {d.strftime('%B').upper()}",
                channel="app",
                email="alert_hdfc",
                time=dt.time(9, 14),
                key=f"rent_{i}",
            )
        )

    # --- subscriptions ---------------------------------------------------
    for i, d in enumerate(_NETFLIX_DATES):
        e.append(
            Entry(
                date=d,
                amount=Decimal("649.00"),
                direction="debit",
                account=HDFC,
                party=NETFLIX,
                payment_mode="ACH",
                ref=f"98765432{i:02d}",
                channel="online",
                email="receipt_netflix" if i == 1 else None,
                time=dt.time(4, 12) if i == 1 else None,
                key=f"netflix_{i}",
            )
        )
    for i, d in enumerate(_GYM_DATES):
        e.append(
            Entry(
                date=d,
                amount=Decimal("1999.00"),
                direction="debit",
                account=HDFC,
                party=CULTFIT,
                payment_mode="UPI",
                ref=_ref(n := n + 1),
                remark="CULT PASS MONTHLY",
                channel="app",
                key=f"gym_{i}",
            )
        )

    # --- bank charges ----------------------------------------------------
    for i, d in enumerate(_SMS_FEE_DATES):
        e.append(
            Entry(
                date=d,
                amount=Decimal("17.70"),
                direction="debit",
                account=HDFC,
                party=HDFC_BANK,
                payment_mode="OTHER",
                tags={"fee"},
                key=f"smsfee_{i}",
            )
        )
    e.append(
        Entry(
            date=dt.date(2025, 7, 20),
            amount=Decimal("590.00"),
            direction="debit",
            account=SBI,
            party=SBI_BANK,
            payment_mode="OTHER",
            tags={"fee"},
            key="sbi_amb_fee",
        )
    )

    # --- self transfer HDFC -> SBI --------------------------------------
    transfer_ref = _ref(n := n + 1)
    e.append(
        Entry(
            date=dt.date(2025, 6, 10),
            amount=Decimal("15000.00"),
            direction="debit",
            account=HDFC,
            party=SELF,
            payment_mode="IMPS",
            ref=transfer_ref,
            remark="SELF TRANSFER",
            channel="online",
            tags={"transfer"},
            key="transfer_out",
        )
    )
    e.append(
        Entry(
            date=dt.date(2025, 6, 10),
            amount=Decimal("15000.00"),
            direction="credit",
            account=SBI,
            party=SELF,
            payment_mode="IMPS",
            ref=transfer_ref,
            remark="SELF TRANSFER",
            channel="online",
            tags={"transfer"},
            key="transfer_in",
        )
    )

    # --- credit card bill payment ---------------------------------------
    e.append(
        Entry(
            date=dt.date(2025, 7, 2),
            amount=Decimal("18450.75"),
            direction="debit",
            account=HDFC,
            party=SELF,
            payment_mode="OTHER",
            ref=None,  # HDFC prints no reference for an autopay standing instruction
            remark="CREDIT CARD AUTOPAY",
            channel="online",
            tags={"transfer"},
            key="cc_payment_bank",
        )
    )
    e.append(
        Entry(
            date=dt.date(2025, 7, 2),
            amount=Decimal("18450.75"),
            direction="credit",
            account=CARD,
            party=SELF,
            payment_mode="OTHER",
            channel="online",
            tags={"transfer"},
            key="cc_payment_card",
        )
    )

    # --- ATM withdrawal --------------------------------------------------
    e.append(
        Entry(
            date=dt.date(2025, 6, 18),
            amount=Decimal("5000.00"),
            direction="debit",
            account=HDFC,
            party=SELF,
            payment_mode="ATM",
            ref=None,  # ATM withdrawals carry no reference in the statement
            channel="atm",
            tags={"atm"},
            key="atm",
        )
    )

    # --- refund story: buy on 04 Jul, refunded 12 Jul --------------------
    myntra_ref = _ref(n := n + 1)
    e.append(
        Entry(
            date=dt.date(2025, 7, 4),
            amount=Decimal("2499.00"),
            direction="debit",
            account=HDFC,
            party=MYNTRA,
            payment_mode="UPI",
            ref=myntra_ref,
            remark="MYNTRA ORDER",
            channel="online",
            time=dt.time(21, 6),
            email="order_myntra",
            order=OrderDetail(
                order_id="MYN-1180042317",
                items=[("Roadster Slim Fit Shirt", 1, Decimal("1499.00")),
                       ("HRX Running Socks (3 pack)", 1, Decimal("899.00"))],
                gst=Decimal("381.20"),
                discount=Decimal("0.00"),
                delivery_fee=Decimal("101.00"),
            ),
            key="myntra_buy",
        )
    )
    e.append(
        Entry(
            date=dt.date(2025, 7, 12),
            amount=Decimal("2499.00"),
            direction="credit",
            account=HDFC,
            party=MYNTRA,
            payment_mode="UPI",
            ref=_ref(n := n + 1),
            remark="MYNTRA REFUND MYN-1180042317",
            channel="online",
            time=dt.time(16, 41),
            email="refund_myntra",
            order=OrderDetail(order_id="MYN-1180042317"),
            tags={"refund"},
            refund_of_key="myntra_buy",
            key="myntra_refund",
        )
    )

    # --- payments to individuals -----------------------------------------
    e.append(
        Entry(
            date=dt.date(2025, 6, 21),
            amount=Decimal("1200.00"),
            direction="debit",
            account=HDFC,
            party=PRIYA,
            payment_mode="UPI",
            ref=_ref(n := n + 1),
            remark="DINNER SPLIT",
            channel="app",
            time=dt.time(22, 47),
            email="alert_hdfc",
            key="person_priya",
        )
    )
    e.append(
        Entry(
            date=dt.date(2025, 7, 19),
            amount=Decimal("3500.00"),
            direction="debit",
            account=SBI,
            party=VIKRAM,
            payment_mode="UPI",
            ref=_ref(n := n + 1),
            remark="TREK SHARE",
            channel="app",
            key="person_vikram",
        )
    )
    e.append(
        Entry(
            date=dt.date(2025, 8, 9),
            amount=Decimal("800.00"),
            direction="credit",
            account=HDFC,
            party=PRIYA,
            payment_mode="UPI",
            ref=_ref(n := n + 1),
            remark="RETURNING DINNER",
            channel="app",
            key="person_priya_back",
        )
    )

    # --- SBI interest ----------------------------------------------------
    e.append(
        Entry(
            date=dt.date(2025, 6, 30),
            amount=Decimal("412.00"),
            direction="credit",
            account=SBI,
            party=SBI_BANK,
            payment_mode="OTHER",
            remark="CREDIT INTEREST",
            key="sbi_interest",
        )
    )

    # --- credit card statement transactions ------------------------------
    card_spends = [
        (dt.date(2025, 6, 21), Decimal("432.00"), SWIGGY, set(), None),
        (dt.date(2025, 6, 23), Decimal("2500.00"), HPCL, set(), None),
        (dt.date(2025, 6, 25), Decimal("4166.00"), SAMSUNG, {"emi"}, None),
        (dt.date(2025, 6, 28), Decimal("1032.24"), AWS, set(), ("USD", Decimal("12.00"))),
        (dt.date(2025, 7, 4), Decimal("119.00"), SPOTIFY, set(), None),
        (dt.date(2025, 7, 7), Decimal("1845.50"), BIGBASKET, set(), None),
        (dt.date(2025, 7, 11), Decimal("287.00"), UBER, set(), None),
        (dt.date(2025, 7, 16), Decimal("3210.00"), DMART, set(), None),
        (dt.date(2025, 7, 18), Decimal("648.00"), ZOMATO, set(), None),
    ]
    for i, (d, amt, party, tags, fx) in enumerate(card_spends):
        e.append(
            Entry(
                date=d,
                amount=amt,
                direction="debit",
                account=CARD,
                party=party,
                payment_mode="CARD",
                channel="online" if party in (AWS, SPOTIFY, UBER) else "pos",
                tags=set(tags),
                fx=fx,
                key=f"card_{i}",
            )
        )

    return e


def _build_filler(rng: random.Random) -> list[Entry]:
    """Everyday food / grocery / fuel / ride spends.

    Seeded, so the fixture files and the ground truth stay byte-stable, but not
    hand-written -- these exist to give the segmentation stage something to
    chew on later and to make dedupe and linking work on realistic volume.
    """
    out: list[Entry] = []
    n = 5000
    day = PERIOD_START
    while day <= PERIOD_END:
        # 0-2 discretionary spends a day, more at the weekend
        weekend = day.weekday() >= 5
        count = rng.choices([0, 1, 2], weights=[30, 50, 20] if not weekend else [15, 45, 40])[0]
        for _ in range(count):
            party, lo, hi, acct_key, late = rng.choice(_FILLER_MENU)
            amount = Decimal(rng.randint(lo * 100, hi * 100)) / 100
            amount = amount.quantize(Decimal("0.01"))
            if late and rng.random() < 0.28:
                hour = rng.choice([22, 23, 0, 1])
            else:
                hour = rng.randint(8, 21)
            has_email = party in (SWIGGY, ZOMATO, AMAZON) and rng.random() < 0.55
            n += 1
            entry = Entry(
                date=day,
                amount=amount,
                direction="debit",
                account=ACCOUNTS[acct_key],
                party=party,
                payment_mode="UPI",
                ref=_ref(n),
                remark=f"{party.merchant.upper()} ORDER" if party.merchant else None,
                channel="app",
                time=dt.time(hour, rng.randint(0, 59)) if has_email else None,
                key=f"filler_{n}",
            )
            if has_email:
                entry.email = {
                    SWIGGY: "order_swiggy",
                    ZOMATO: "order_zomato",
                    AMAZON: "order_amazon",
                }[party]
                entry.order = _make_order(party, amount, n, rng)
            out.append(entry)
        day += dt.timedelta(days=1)
    return out


_SWIGGY_ITEMS = [
    ("Paneer Butter Masala", Decimal("249.00")),
    ("Butter Naan (2)", Decimal("90.00")),
    ("Veg Biryani", Decimal("229.00")),
    ("Masala Dosa", Decimal("139.00")),
    ("Filter Coffee", Decimal("69.00")),
    ("Chicken Roll", Decimal("179.00")),
]
_AMAZON_ITEMS = [
    ("Boat Rockerz 255 Earphones", Decimal("1299.00")),
    ("Kitchen Storage Containers Set", Decimal("649.00")),
    ("USB-C Cable 1m", Decimal("299.00")),
    ("Notebook A5 Ruled", Decimal("199.00")),
]


def inclusive_gst(total: Decimal, rate_pct: Decimal) -> Decimal:
    """GST already contained in ``total``, not added on top.

    Indian food-delivery and marketplace receipts print tax as a component of
    the amount charged, so the only arithmetic that has to hold is
    ``item total - discount + delivery = grand total``. Keeping the fixture
    internally consistent matters: the pipeline validates receipt totals in
    code, and a fixture whose own sums do not close would fail that check for
    the wrong reason.
    """
    return (total * rate_pct / (Decimal("100") + rate_pct)).quantize(Decimal("0.01"))


def _make_order(party: Party, amount: Decimal, n: int, rng: random.Random) -> OrderDetail:
    """Split an order total into plausible items + fees.

    Arithmetic is done here, in code, on purpose: the LLM that later reads the
    email must never be the thing that decides a total (principle 2). The email
    renders these numbers; the pipeline must reproduce them.
    """
    if party is AMAZON:
        name, price = rng.choice(_AMAZON_ITEMS)
        return OrderDetail(
            order_id=f"402-{n % 10000000:07d}-{rng.randint(1000000, 9999999)}",
            items=[(name, 1, amount)],
            gst=inclusive_gst(amount, Decimal("18")),
            item_details=name,
        )
    pool = _SWIGGY_ITEMS
    chosen = rng.sample(pool, k=rng.randint(1, 2))
    delivery = Decimal(rng.choice([0, 25, 35, 45]))
    discount = Decimal(rng.choice([0, 0, 50, 75]))
    subtotal = amount - delivery + discount
    # scale the menu prices so the items really do add up to the subtotal
    menu_total = sum(p for _, p in chosen) or Decimal("1")
    items: list[tuple[str, int, Decimal]] = []
    running = Decimal("0.00")
    for i, (nm, price) in enumerate(chosen):
        if i == len(chosen) - 1:
            line = (subtotal - running).quantize(Decimal("0.01"))
        else:
            line = (subtotal * price / menu_total).quantize(Decimal("0.01"))
            running += line
        items.append((nm, 1, line))
    prefix = "SWG" if party is SWIGGY else "ZOM"
    return OrderDetail(
        order_id=f"{prefix}{n:08d}",
        items=items,
        gst=inclusive_gst(amount, Decimal("5")),
        discount=discount,
        delivery_fee=delivery,
        item_details=", ".join(nm for nm, _, _ in items),
    )


# --------------------------------------------------------------------------
# cash + spreadsheet rows
# --------------------------------------------------------------------------


@dataclass
class SheetRow:
    date: dt.date
    description: str
    amount: Decimal
    mode: str  # Cash | UPI
    note: str = ""
    #: when set, this sheet row describes a transaction that already exists in
    #: a statement -- link.py must match it rather than create a new row
    duplicate_of_key: str | None = None
    party: Party | None = None


def build_sheet_rows(entries: list[Entry], rng: random.Random) -> list[SheetRow]:
    """The user's own expense sheet: genuine cash spends plus a few rows that
    re-describe statement transactions."""
    rows: list[SheetRow] = [
        SheetRow(dt.date(2025, 6, 7), "Chai and vada pav", Decimal("120.00"), "Cash", "office"),
        SheetRow(dt.date(2025, 6, 24), "Auto to station", Decimal("180.00"), "Cash", ""),
        SheetRow(dt.date(2025, 7, 9), "Haircut", Decimal("300.00"), "Cash", "salon"),
        SheetRow(dt.date(2025, 7, 26), "Vegetables - local market", Decimal("450.00"), "Cash", ""),
        SheetRow(dt.date(2025, 8, 14), "Movie tickets", Decimal("700.00"), "Cash", "with Priya"),
        SheetRow(dt.date(2025, 8, 23), "Parking", Decimal("60.00"), "Cash", ""),
    ]
    # three rows that duplicate real statement transactions
    by_key = {e.key: e for e in entries}
    for key, label in (
        ("rent_1", "Rent July"),
        ("gym_0", "Gym membership"),
        ("myntra_buy", "Myntra shirt"),
    ):
        src = by_key[key]
        rows.append(
            SheetRow(
                date=src.date,
                description=label,
                amount=src.amount,
                mode="UPI",
                note="from bank",
                duplicate_of_key=key,
                party=src.party,
            )
        )
    rows.sort(key=lambda r: (r.date, r.description))
    return rows


# --------------------------------------------------------------------------
# bills
# --------------------------------------------------------------------------


@dataclass
class Bill:
    biller: str
    category_hint: str
    amount_due: Decimal
    bill_date: dt.date
    due_date: dt.date
    billing_period: str
    consumer_no: str  # unmasked; the pipeline must store it masked
    masked_consumer_no: str
    filename: str
    #: key of the Entry that paid it, if any
    paid_by_key: str | None = None


def build_bills() -> list[Bill]:
    return [
        Bill(
            biller="Tata Power",
            category_hint="electricity",
            amount_due=Decimal("2340.00"),
            bill_date=dt.date(2025, 7, 5),
            due_date=dt.date(2025, 7, 20),
            billing_period="Jun 2025",
            consumer_no="900012345678",
            masked_consumer_no="XXXXXXXX5678",
            filename="bill_tatapower_jul2025.pdf",
            paid_by_key="bill_pay_tatapower",
        ),
        Bill(
            biller="Airtel",
            category_hint="mobile",
            amount_due=Decimal("799.00"),
            bill_date=dt.date(2025, 7, 8),
            due_date=dt.date(2025, 7, 25),
            billing_period="08 Jun 2025 - 07 Jul 2025",
            consumer_no="9820045612",
            masked_consumer_no="XXXXXX5612",
            filename="bill_airtel_jul2025.pdf",
            paid_by_key="bill_pay_airtel",
        ),
        Bill(
            biller="ACT Fibernet",
            category_hint="broadband",
            amount_due=Decimal("1180.00"),
            bill_date=dt.date(2025, 8, 3),
            due_date=dt.date(2025, 8, 18),
            billing_period="Aug 2025",
            consumer_no="ACT7710022",
            masked_consumer_no="XXXXXX0022",
            filename="bill_act_aug2025.pdf",
            paid_by_key=None,  # left unpaid on purpose -> stays `due`
        ),
    ]


TATA_POWER = P("TATA POWER", "Tata Power", "merchant", "tatapower.bbps@icici", "ICIC0000001", "MUMBAI")
AIRTEL = P("AIRTEL POSTPAID", "Airtel", "merchant", "airtel.bbps@axisbank", "UTIB0000123", "DELHI")


def _bill_payments() -> list[Entry]:
    return [
        Entry(
            date=dt.date(2025, 7, 18),
            amount=Decimal("2340.00"),
            direction="debit",
            account=HDFC,
            party=TATA_POWER,
            payment_mode="UPI",
            ref=_ref(9000001),
            remark="ELECTRICITY BILL",
            channel="app",
            key="bill_pay_tatapower",
        ),
        Entry(
            date=dt.date(2025, 7, 22),
            amount=Decimal("799.00"),
            direction="debit",
            account=HDFC,
            party=AIRTEL,
            payment_mode="UPI",
            ref=_ref(9000002),
            remark="AIRTEL POSTPAID BILL",
            channel="app",
            key="bill_pay_airtel",
        ),
    ]


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------


def build_ledger() -> list[Entry]:
    """Every transaction that really happened, in date order."""
    rng = random.Random(SEED)
    entries = _build_anchors() + _bill_payments()
    entries += _build_filler(rng)
    entries.sort(key=lambda e: (e.date, e.account.key, e.key))
    _assign_balances(entries)
    return entries


def _assign_balances(entries: list[Entry]) -> None:
    """Running balance per bank account, in date order.

    Card accounts get no running balance -- card statements do not print one,
    so ``balance_after`` must stay empty for them.
    """
    for account in (HDFC, SBI):
        balance = account.opening_balance or Decimal("0.00")
        for e in [x for x in entries if x.account is account]:
            balance += e.amount if e.direction == "credit" else -e.amount
            e.balance_after = balance.quantize(Decimal("0.01"))


def account_opening_balance(entries: list[Entry], account: Account, start: dt.date) -> Decimal:
    prior = [e for e in entries if e.account is account and e.date < start]
    if not prior:
        return account.opening_balance or Decimal("0.00")
    return prior[-1].balance_after
