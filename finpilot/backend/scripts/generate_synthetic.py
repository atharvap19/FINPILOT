"""Render the synthetic story into realistic files, and write the ground truth.

    uv run python scripts/generate_synthetic.py [--out DIR]

Produces, with a fixed seed:

    hdfc_savings_jun_aug15.csv   HDFC layout, 01/06/2025 - 15/08/2025
    hdfc_savings_aug.csv         HDFC layout, 01/08/2025 - 31/08/2025  (overlaps)
    sbi_savings_jun_aug.csv      SBI layout, different dates + Indian digit grouping
    hdfc_card_jul2025.pdf        text PDF card statement, amount + Dr/Cr suffix
    emails/*.eml                 ~25 order/alert emails with times, plus noise
    bills/*.pdf                  3 bill PDFs with a real text layer
    personal_expenses.xlsx       signed-amount sheet with cash spends
    expected_transactions.csv    ground truth
    expected_obligations.csv     ground truth

The three amount styles the mapper has to cope with are each represented:
separate debit/credit columns (the bank CSVs), a signed amount (the sheet), and
an amount with a Dr/Cr suffix (the card PDF).
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import random
import re
from dataclasses import dataclass, field
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

from openpyxl import Workbook

from ingestion.normalize import mask_text
from ingestion.schema import (
    ALL_TRANSACTION_COLUMNS,
    BOOL_COLUMNS,
    METHOD_CONFIDENCE,
    OBLIGATION_COLUMNS,
    make_txn_id,
)
from scripts.synthetic_ledger import (
    CARD,
    CASH,
    HDFC,
    PERIOD_END,
    PERIOD_START,
    SBI,
    SEED,
    USER_EMAIL,
    USER_NAME,
    Account,
    Bill,
    Entry,
    OrderDetail,
    Party,
    SheetRow,
    account_opening_balance,
    build_bills,
    build_ledger,
    build_sheet_rows,
    inclusive_gst,
)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

HDFC_FILE_A = "hdfc_savings_jun_aug15.csv"
HDFC_FILE_B = "hdfc_savings_aug.csv"
SBI_FILE = "sbi_savings_jun_aug.csv"
CARD_FILE = "hdfc_card_jul2025.pdf"
SHEET_FILE = "personal_expenses.xlsx"

HDFC_A_RANGE = (dt.date(2025, 6, 1), dt.date(2025, 8, 15))
HDFC_B_RANGE = (dt.date(2025, 8, 1), dt.date(2025, 8, 31))
SBI_RANGE = (PERIOD_START, PERIOD_END)
CARD_RANGE = (dt.date(2025, 6, 20), dt.date(2025, 7, 19))

#: Everything the pipeline can know about is bounded by this date. Obligation
#: status and the "is an unmatched email in the future?" rule are both resolved
#: against it rather than wall-clock time, so the golden test is stable.
AS_OF = PERIOD_END


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------


def indian_grouping(value: Decimal) -> str:
    """1234567.5 -> '12,34,567.50' (last three digits, then pairs)."""
    sign = "-" if value < 0 else ""
    whole, _, frac = f"{abs(value):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{sign}{whole}.{frac}"


def plain_amount(value: Decimal) -> str:
    return f"{value:.2f}"


def ddmmyy(d: dt.date) -> str:
    return d.strftime("%d/%m/%y")


def ddmmyyyy(d: dt.date) -> str:
    return d.strftime("%d/%m/%Y")


def sbi_date(d: dt.date) -> str:
    return f"{d.day} {d.strftime('%b')} {d.year}"


def masked_card(number: str) -> str:
    return f"{number[:6]}XXXXXX{number[-4:]}"


# --------------------------------------------------------------------------
# narrations
# --------------------------------------------------------------------------


def hdfc_narration(e: Entry) -> str:
    """HDFC's statement dialect."""
    p = e.party
    if e.is_fee:
        return f"SMS CHRG INCL GST {e.date.strftime('%b').upper()}{e.date.year}"
    if e.key == "cc_payment_bank":
        return f"CC 000{masked_card(CARD.number)} AUTOPAY SI-TAD"
    if e.payment_mode == "ATM":
        return f"NWD-{masked_card(CARD.number)}-HDFC BANK ATM-ANDHERI W {p.city}"
    if e.payment_mode == "UPI":
        vpa = (p.vpa or "").upper()
        return f"UPI-{p.name}-{vpa}-{p.ifsc}-{e.ref}-{e.remark or 'PAYMENT'}"
    if e.payment_mode == "NEFT":
        if e.direction == "credit":
            return f"NEFT CR-{p.ifsc}-{p.name}-{USER_NAME.upper()}-{e.ref}"
        return f"NEFT DR-{p.ifsc}-{p.name}-{e.remark or ''}-{e.ref}"
    if e.payment_mode == "IMPS":
        other = SBI if e.direction == "debit" else HDFC
        return (
            f"IMPS-{e.ref}-{p.name}-{other.issuer[:4].upper()}-"
            f"XXXXXXXX{other.number[-4:]}-{e.remark or ''}"
        )
    if e.payment_mode == "ACH":
        return f"ACH D- {p.name}-{e.ref}"
    return f"{p.name}-{e.remark or ''}"


def sbi_narration(e: Entry) -> str:
    """SBI's statement dialect -- slash-delimited, trailing double dash."""
    p = e.party
    if e.is_fee:
        return f"DEBIT-AMB CHARGES FOR {e.date.strftime('%b').upper()} {e.date.year}"
    if e.remark == "CREDIT INTEREST":
        return "CREDIT INTEREST"
    bank = p.ifsc[:4]
    if e.payment_mode == "UPI":
        side = "DR" if e.direction == "debit" else "CR"
        verb = "TO TRANSFER" if e.direction == "debit" else "BY TRANSFER"
        name = p.name.title()[:18]
        remark = (e.remark or "Payment").title()[:20]
        return f"{verb}-UPI/{side}/{e.ref}/{name}/{bank}/{p.vpa}/{remark}--"
    if e.payment_mode == "IMPS":
        verb = "TO TRANSFER" if e.direction == "debit" else "BY TRANSFER"
        return f"{verb}-INB IMPS/{e.ref}/{p.name.title()}/{bank}--"
    return f"{'TO' if e.direction == 'debit' else 'BY'} TRANSFER-{p.name}--"


def card_narration(e: Entry) -> str:
    p = e.party
    if e.key == "cc_payment_card":
        return "PAYMENT RECEIVED - THANK YOU"
    if e.emi_flag:
        return f"EMI 03/12 {p.name}"
    country = "US" if e.fx else "IN"
    return f"{p.name} {p.city} {country}"


# --------------------------------------------------------------------------
# bank CSV renderers
# --------------------------------------------------------------------------


def _hdfc_header(account: Account, start: dt.date, end: dt.date, opening: Decimal,
                 closing: Decimal) -> list[str]:
    """The 20-ish metadata rows a real HDFC export puts above the table."""
    pad = ",,,,,,"
    return [
        pad,
        "      HDFC BANK LIMITED,,,,,,",
        "      Account Branch  :ANDHERI WEST,,,,,,",
        "      Address        :S V ROAD ANDHERI WEST MUMBAI 400058,,,,,,",
        "      City           :MUMBAI,,,,,,",
        "      State          :MAHARASHTRA,,,,,,",
        "      Phone no.      :18002026161,,,,,,",
        "      OD Limit       :0,,,,,,",
        "      Currency       :INR,,,,,,",
        f"      Email          :{USER_EMAIL},,,,,,",
        "      Cust ID        :52318877,,,,,,",
        f"      Account No     :{account.number},,,,,,",
        "      A/C Open Date  :12/03/2019,,,,,,",
        "      Account Status :Regular,,,,,,",
        "      RTGS/NEFT IFSC :HDFC0000060,,,,,,",
        "      MICR           :400240012,,,,,,",
        "      Branch Code    :000060,,,,,,",
        "      Product Code   :SAVINGS,,,,,,",
        f"      Statement From :{ddmmyyyy(start)},,,,,,",
        f"      Statement To   :{ddmmyyyy(end)},,,,,,",
        f"      Opening Balance:{plain_amount(opening)},,,,,,",
        f"      Closing Balance:{plain_amount(closing)},,,,,,",
        pad,
    ]


def render_hdfc_csv(entries: list[Entry], path: Path, start: dt.date,
                    end: dt.date) -> dict[str, str]:
    rows = [e for e in entries if e.account is HDFC and start <= e.date <= end]
    opening = account_opening_balance(entries, HDFC, start)
    closing = rows[-1].balance_after if rows else opening
    lines = _hdfc_header(HDFC, start, end, opening, closing)
    lines.append("Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance")
    refs: dict[str, str] = {}
    for i, e in enumerate(rows, start=1):
        narration = hdfc_narration(e)
        withdrawal = plain_amount(e.amount) if e.direction == "debit" else ""
        deposit = plain_amount(e.amount) if e.direction == "credit" else ""
        chq = e.ref or ""
        lines.append(
            ",".join(
                [
                    ddmmyy(e.date),
                    _csv_cell(narration),
                    _csv_cell(chq),
                    ddmmyy(e.date),
                    withdrawal,
                    deposit,
                    plain_amount(e.balance_after or Decimal("0")),
                ]
            )
        )
        refs[e.key] = f"{path.name}#{i}"
    # the footer rows the extractor has to drop
    lines.append(",,,,,,")
    lines.append("*** END OF STATEMENT ***,,,,,,")
    lines.append(
        f"Total,,,,{plain_amount(sum((e.amount for e in rows if e.direction == 'debit'), Decimal('0')))},"
        f"{plain_amount(sum((e.amount for e in rows if e.direction == 'credit'), Decimal('0')))},"
    )
    # newline="" -- write_text would otherwise translate \r\n into CR CR LF on Windows
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")
    return refs


def _sbi_header(account: Account, start: dt.date, end: dt.date,
                opening: Decimal) -> list[str]:
    pad = ",,,,,,"
    return [
        pad,
        f"Account Name,:,MR. {USER_NAME.upper()},,,,",
        "Address,:,FLAT 402 SUNRISE APTS ANDHERI WEST MUMBAI 400058,,,,",
        f"Date,:,{sbi_date(end + dt.timedelta(days=1))},,,,",
        f"Account Number,:,_{account.number},,,,",
        "Branch,:,ANDHERI WEST,,,,",
        "Drawing Power,:,0,,,,",
        "Interest Rate(% p.a.),:,2.70,,,,",
        "MOD Balance,:,0,,,,",
        "CIF No.,:,87654321012,,,,",
        "IFS Code,:,SBIN0000123,,,,",
        "MICR Code,:,400002123,,,,",
        "Nomination Registered,:,Yes,,,,",
        f"Balance as on {sbi_date(start)},:,{_csv_cell(indian_grouping(opening))},,,,",
        f"Start Date,:,{sbi_date(start)},,,,",
        f"End Date,:,{sbi_date(end)},,,,",
        "Account Description,:,SAVINGS,,,,",
        pad,
    ]


def render_sbi_csv(entries: list[Entry], path: Path, start: dt.date,
                   end: dt.date) -> dict[str, str]:
    rows = [e for e in entries if e.account is SBI and start <= e.date <= end]
    opening = account_opening_balance(entries, SBI, start)
    lines = _sbi_header(SBI, start, end, opening)
    lines.append("Txn Date,Value Date,Description,Ref No./Cheque No.,Debit,Credit,Balance")
    refs: dict[str, str] = {}
    for i, e in enumerate(rows, start=1):
        narration = sbi_narration(e)
        debit = indian_grouping(e.amount) if e.direction == "debit" else ""
        credit = indian_grouping(e.amount) if e.direction == "credit" else ""
        lines.append(
            ",".join(
                [
                    sbi_date(e.date),
                    sbi_date(e.date),
                    _csv_cell(narration),
                    _csv_cell(e.ref or ""),
                    _csv_cell(debit),
                    _csv_cell(credit),
                    _csv_cell(indian_grouping(e.balance_after or Decimal("0"))),
                ]
            )
        )
        refs[e.key] = f"{path.name}#{i}"
    lines.append(",,,,,,")
    lines.append("This is a computer generated statement and does not require a signature.,,,,,,")
    # newline="" -- write_text would otherwise translate \r\n into CR CR LF on Windows
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")
    return refs


def _csv_cell(value: str) -> str:
    if any(ch in value for ch in ',"\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


# --------------------------------------------------------------------------
# credit card PDF (text layer)
# --------------------------------------------------------------------------


def render_card_pdf(entries: list[Entry], path: Path, start: dt.date,
                    end: dt.date) -> dict[str, str]:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    rows = sorted(
        [e for e in entries if e.account is CARD and start <= e.date <= end],
        key=lambda e: (e.date, e.key),
    )
    debits = sum((e.amount for e in rows if e.direction == "debit"), Decimal("0"))
    credits = sum((e.amount for e in rows if e.direction == "credit"), Decimal("0"))
    previous = Decimal("18450.75")
    total_due = previous + debits - credits
    min_due = (total_due * Decimal("0.05")).quantize(Decimal("1"))

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(path), pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm,
        leftMargin=14 * mm, rightMargin=14 * mm, title="HDFC Bank Credit Card Statement",
    )
    story = [
        Paragraph("<b>HDFC BANK CREDIT CARD STATEMENT</b>", styles["Title"]),
        Paragraph(f"Name: {USER_NAME.upper()}", styles["Normal"]),
        Paragraph(f"Card No: {CARD.number[:4]} {CARD.number[4:6]}XX XXXX {CARD.number[-4:]}",
                  styles["Normal"]),
        Paragraph(f"Statement Period: {ddmmyyyy(start)} to {ddmmyyyy(end)}", styles["Normal"]),
        Paragraph(f"Statement Date: {ddmmyyyy(end)}", styles["Normal"]),
        Paragraph(f"Payment Due Date: {ddmmyyyy(end + dt.timedelta(days=19))}", styles["Normal"]),
        Paragraph("Credit Limit: 4,00,000.00", styles["Normal"]),
        Spacer(1, 6 * mm),
    ]
    summary = [
        ["Previous Balance", "Payments/Credits", "Purchases/Debits", "Total Dues", "Minimum Due"],
        [
            indian_grouping(previous),
            indian_grouping(credits),
            indian_grouping(debits),
            indian_grouping(total_due),
            indian_grouping(min_due),
        ],
    ]
    summary_table = Table(summary, colWidths=[36 * mm] * 5)
    summary_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 1), (-1, 1), "RIGHT"),
            ]
        )
    )
    story += [summary_table, Spacer(1, 8 * mm),
              Paragraph("<b>TRANSACTION DETAILS</b>", styles["Heading3"])]

    table_rows: list[list[str]] = [
        ["Date", "Transaction Description", "MCC", "Amount (in Rs.)"]
    ]
    refs: dict[str, str] = {}
    data_index = 0
    for e in rows:
        suffix = " Cr" if e.direction == "credit" else " Dr"
        table_rows.append(
            [
                ddmmyyyy(e.date),
                card_narration(e),
                e.party.mcc or "",
                indian_grouping(e.amount) + suffix,
            ]
        )
        data_index += 1
        refs[e.key] = f"{path.name}#1:{data_index}"
        if e.fx:
            # a wrapped continuation row: no date, so the extractor has to fold
            # it into the row above
            currency, foreign = e.fx
            rate = (e.amount / foreign).quantize(Decimal("0.01"))
            table_rows.append(["", f"{currency} {foreign:.2f} @ {rate}", "", ""])
            data_index += 1

    txn_table = Table(table_rows, colWidths=[24 * mm, 96 * mm, 16 * mm, 32 * mm], repeatRows=1)
    txn_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dddddd")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (3, 1), (3, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story += [
        txn_table,
        Spacer(1, 6 * mm),
        Paragraph(
            "This is a system generated statement. Please pay by the due date to avoid "
            "late payment charges and finance charges of 3.6% per month.",
            styles["Normal"],
        ),
    ]
    doc.build(story)
    return refs


# --------------------------------------------------------------------------
# emails
# --------------------------------------------------------------------------


@dataclass
class ExtraEmail:
    """An email with no statement row behind it.

    ``future`` ones land after the last statement date for the account, so the
    pipeline must create a transaction from them; the rest must be dropped
    rather than invented.
    """

    key: str
    kind: str
    date: dt.date
    time: dt.time
    amount: Decimal
    party: Party
    account: Account
    ref: str
    order: OrderDetail
    future: bool


def build_extra_emails() -> list[ExtraEmail]:
    from scripts.synthetic_ledger import SWIGGY, ZOMATO

    return [
        ExtraEmail(
            key="email_future_swiggy",
            kind="order_swiggy",
            date=dt.date(2025, 9, 2),
            time=dt.time(20, 35),
            amount=Decimal("512.00"),
            party=SWIGGY,
            account=HDFC,
            ref="590000000101",
            order=OrderDetail(
                order_id="SWG90000101",
                items=[("Veg Biryani", 1, Decimal("229.00")), ("Paneer Butter Masala", 1, Decimal("248.00"))],
                gst=inclusive_gst(Decimal("512.00"), Decimal("5")),
                discount=Decimal("0.00"),
                delivery_fee=Decimal("35.00"),
                item_details="Veg Biryani, Paneer Butter Masala",
            ),
            future=True,
        ),
        ExtraEmail(
            key="email_orphan_zomato",
            kind="order_zomato",
            date=dt.date(2025, 7, 14),
            time=dt.time(13, 22),
            amount=Decimal("389.00"),
            party=ZOMATO,
            account=HDFC,
            ref="590000000102",
            order=OrderDetail(
                order_id="ZOM90000102",
                items=[("Masala Dosa", 1, Decimal("139.00")), ("Chicken Roll", 1, Decimal("225.00"))],
                gst=inclusive_gst(Decimal("389.00"), Decimal("5")),
                discount=Decimal("0.00"),
                delivery_fee=Decimal("25.00"),
                item_details="Masala Dosa, Chicken Roll",
            ),
            future=False,
        ),
    ]


_NOISE = [
    ("deals@shopping-daily.example", "Your weekly deals roundup - up to 70% off!",
     "<p>Massive savings await. Shop now.</p>"),
    ("noreply@hdfcbank.net", "OTP for your transaction",
     "<p>Your OTP is 448271. Do not share it with anyone.</p>"),
    ("notifications@linkedin.example", "You have 4 new profile views",
     "<p>See who viewed your profile this week.</p>"),
    ("newsletter@techweekly.example", "The Monday Briefing",
     "<p>This week in technology.</p>"),
]


def _money_row(label: str, value: Decimal) -> str:
    return f"<tr><td>{label}</td><td align='right'>Rs. {indian_grouping(value)}</td></tr>"


def _order_html(party: Party, amount: Decimal, order: OrderDetail, when: dt.datetime,
                account: Account, ref: str) -> str:
    items = "".join(
        f"<tr><td>{n}</td><td align='center'>{q}</td>"
        f"<td align='right'>Rs. {indian_grouping(v)}</td></tr>"
        for n, q, v in order.items
    )
    subtotal = sum((v for _, _, v in order.items), Decimal("0"))
    parts = [
        f"<h2>Your {party.merchant} order is confirmed</h2>",
        f"<p>Order ID: <b>{order.order_id}</b></p>",
        f"<p>Placed on {when.strftime('%d %b %Y at %I:%M %p')}</p>",
        "<table border='1' cellpadding='4'>",
        "<tr><th>Item</th><th>Qty</th><th>Amount</th></tr>",
        items,
        "</table>",
        "<table cellpadding='3'>",
        _money_row("Item total", subtotal),
    ]
    if order.discount:
        parts.append(_money_row("Discount", -order.discount))
    if order.delivery_fee:
        parts.append(_money_row("Delivery fee", order.delivery_fee))
    if order.gst:
        parts.append(_money_row("GST (included)", order.gst))
    parts += [
        _money_row("Grand total", amount),
        "</table>",
        f"<p>Paid using {account.issuer} "
        f"{'Credit Card ending ' + account.number[-4:] if account.kind == 'credit_card' else 'A/c XX' + account.number[-4:]}"
        f" &middot; UPI Ref {ref}</p>",
    ]
    return "".join(parts)


def _alert_html(e: Entry) -> str:
    vpa = e.party.vpa or ""
    verb = "debited from" if e.direction == "debit" else "credited to"
    return (
        "<p>Dear Customer,</p>"
        f"<p>Rs. {indian_grouping(e.amount)} has been {verb} "
        f"{e.account.issuer} A/c XX{e.account.number[-4:]} on "
        f"{e.date.strftime('%d-%m-%y')} at {(e.time or dt.time(0, 0)).strftime('%H:%M')} "
        f"to VPA {vpa}. UPI Ref No {e.ref}.</p>"
        "<p>Not you? Call 18002026161 immediately.</p>"
    )


def _netflix_html(e: Entry) -> str:
    return (
        "<h3>Your Netflix payment receipt</h3>"
        f"<p>Amount charged: Rs. {indian_grouping(e.amount)}</p>"
        f"<p>Date: {e.date.strftime('%d %b %Y')}</p>"
        "<p>Plan: Standard</p>"
        f"<p>Reference: {e.ref}</p>"
    )


def _refund_html(e: Entry) -> str:
    order_id = e.order.order_id if e.order else ""
    return (
        "<h3>Your refund has been processed</h3>"
        f"<p>Order ID: <b>{order_id}</b></p>"
        f"<p>Refund amount: Rs. {indian_grouping(e.amount)}</p>"
        f"<p>Credited on {e.date.strftime('%d %b %Y')} to your bank account. "
        f"Reference {e.ref}.</p>"
    )


_SENDERS = {
    "order_swiggy": ("Swiggy", "noreply@swiggy.example", "Order {oid} confirmed - Rs. {amt}"),
    "order_zomato": ("Zomato", "orders@zomato.example", "Your Zomato order {oid}"),
    "order_amazon": ("Amazon.in", "auto-confirm@amazon.example", "Your Amazon.in order {oid}"),
    "order_myntra": ("Myntra", "noreply@myntra.example", "Myntra order {oid} confirmed"),
    "refund_myntra": ("Myntra", "noreply@myntra.example", "Refund processed for order {oid}"),
    "alert_hdfc": ("HDFC Bank Alerts", "alerts@hdfcbank.example", "Update on your HDFC Bank A/c"),
    "receipt_netflix": ("Netflix", "info@mailer.netflix.example", "Your Netflix receipt"),
}


_TAG = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    """A plain-text alternative that mirrors the HTML, the way real mailers
    send one. The parser prefers the HTML part; this exists so the fixtures are
    honest multipart/alternative messages."""
    text = re.sub(r"</(p|tr|h2|h3|table)>", "\n", html)
    text = text.replace("</td>", "\t").replace("<br>", "\n")
    text = _TAG.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def _build_eml(kind: str, when: dt.datetime, subject: str, sender_name: str,
               sender_addr: str, html: str, msg_id: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"{sender_name} <{sender_addr}>"
    msg["To"] = f"{USER_NAME} <{USER_EMAIL}>"
    msg["Subject"] = subject
    msg["Date"] = email.utils.format_datetime(when)
    msg["Message-ID"] = f"<{msg_id}@{sender_addr.split('@')[-1]}>"
    msg.set_content(_html_to_text(html))
    msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")
    # the default boundary is random, which would make the fixtures unstable
    msg.set_boundary(f"----=_FinPilot_{msg_id}")
    return msg.as_bytes()


def render_emails(entries: list[Entry], extras: list[ExtraEmail],
                  out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    refs: dict[str, str] = {}
    seq = 0

    def emit(kind: str, when: dt.datetime, oid: str, amount: Decimal, html: str,
             key: str) -> None:
        nonlocal seq
        seq += 1
        sender_name, sender_addr, subject_tpl = _SENDERS[kind]
        subject = subject_tpl.format(oid=oid, amt=indian_grouping(amount))
        name = f"{kind}_{when.strftime('%Y%m%d_%H%M')}_{seq:03d}.eml"
        blob = _build_eml(kind, when, subject, sender_name, sender_addr, html,
                          f"fp{seq:05d}")
        (out_dir / name).write_bytes(blob)
        refs[key] = f"emails/{name}#1"

    for e in entries:
        if not e.email:
            continue
        when = dt.datetime.combine(e.date, e.time or dt.time(12, 0), tzinfo=IST)
        if e.email.startswith("order_"):
            assert e.order is not None
            emit(e.email, when, e.order.order_id, e.amount,
                 _order_html(e.party, e.amount, e.order, when, e.account, e.ref or ""), e.key)
        elif e.email == "alert_hdfc":
            emit(e.email, when, e.ref or "", e.amount, _alert_html(e), e.key)
        elif e.email == "receipt_netflix":
            emit(e.email, when, e.ref or "", e.amount, _netflix_html(e), e.key)
        elif e.email == "refund_myntra":
            emit(e.email, when, e.order.order_id if e.order else "", e.amount,
                 _refund_html(e), e.key)

    for x in extras:
        when = dt.datetime.combine(x.date, x.time, tzinfo=IST)
        emit(x.kind, when, x.order.order_id, x.amount,
             _order_html(x.party, x.amount, x.order, when, x.account, x.ref), x.key)

    # noise the sender/subject filter has to reject
    for i, (addr, subject, html) in enumerate(_NOISE):
        when = dt.datetime.combine(dt.date(2025, 7, 10 + i), dt.time(9, i), tzinfo=IST)
        msg = EmailMessage()
        msg["From"] = addr
        msg["To"] = USER_EMAIL
        msg["Subject"] = subject
        msg["Date"] = email.utils.format_datetime(when)
        msg["Message-ID"] = f"<noise{i}@example>"
        msg.set_content(_html_to_text(html))
        msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")
        msg.set_boundary(f"----=_FinPilot_noise{i:02d}")
        (out_dir / f"noise_{i:02d}.eml").write_bytes(msg.as_bytes())

    return refs


# --------------------------------------------------------------------------
# bills (text PDFs, so they parse without a model)
# --------------------------------------------------------------------------


def render_bills(bills: list[Bill], out_dir: Path) -> dict[str, str]:
    """Render each bill as a text-layer PDF.

    A photographed bill would need a vision model, and this pipeline reads
    documents with code only -- so the fixtures are what a biller actually
    emails you: a generated PDF with a real text layer and labelled fields.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    out_dir.mkdir(parents=True, exist_ok=True)
    refs: dict[str, str] = {}
    styles = getSampleStyleSheet()
    masthead = ParagraphStyle(
        "masthead", parent=styles["Title"], alignment=0, fontSize=20, spaceAfter=2,
    )

    for bill in bills:
        path = out_dir / bill.filename
        doc = SimpleDocTemplate(
            str(path), pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm,
            leftMargin=18 * mm, rightMargin=18 * mm, title=f"{bill.biller} bill",
        )
        rows = [
            ["Customer Name", USER_NAME],
            ["Consumer Number", bill.consumer_no],
            ["Bill Date", bill.bill_date.strftime("%d-%m-%Y")],
            ["Due Date", bill.due_date.strftime("%d-%m-%Y")],
            ["Billing Period", bill.billing_period],
            ["Service Address", "Flat 402 Sunrise Apts, Andheri West, Mumbai 400058"],
        ]
        details = Table(rows, colWidths=[52 * mm, 110 * mm])
        details.setStyle(
            TableStyle([
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555555")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#dddddd")),
            ])
        )
        total = Table(
            [["Total Amount Payable", f"Rs. {indian_grouping(bill.amount_due)}"]],
            colWidths=[100 * mm, 62 * mm],
        )
        total.setStyle(
            TableStyle([
                ("FONTSIZE", (0, 0), (0, 0), 11),
                ("FONTSIZE", (1, 0), (1, 0), 14),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("TEXTCOLOR", (1, 0), (1, 0), colors.HexColor("#123a6b")),
                ("LINEABOVE", (0, 0), (-1, 0), 1, colors.HexColor("#999999")),
                ("TOPPADDING", (0, 0), (-1, 0), 8),
            ])
        )
        doc.build([
            Paragraph(bill.biller.upper(), masthead),
            Paragraph("Tax Invoice", styles["Normal"]),
            Spacer(1, 8 * mm),
            details,
            Spacer(1, 6 * mm),
            total,
            Spacer(1, 8 * mm),
            Paragraph(
                "Please pay by the due date to avoid late payment charges. "
                "This is a computer generated bill.",
                styles["Normal"],
            ),
        ])
        refs[bill.filename] = f"bills/{bill.filename}#1:1"
    return refs


# --------------------------------------------------------------------------
# spreadsheet (signed amount style)
# --------------------------------------------------------------------------


def render_spreadsheet(rows: list[SheetRow], path: Path) -> dict[str, str]:
    wb = Workbook()
    ws = wb.active
    ws.title = "Expenses"
    ws.append(["My expense tracker 2025"])
    ws.append(["Amounts are negative for spends"])
    ws.append([])
    ws.append(["Date", "Description", "Amount", "Mode", "Note"])
    refs: dict[str, str] = {}
    for i, r in enumerate(rows, start=1):
        ws.append(
            [
                r.date.strftime("%d-%m-%Y"),
                r.description,
                float(-r.amount),  # signed: spends are negative
                r.mode,
                r.note,
            ]
        )
        refs[f"sheet_{i}"] = f"{path.name}#{i}"
        r_key = r.duplicate_of_key
        if r_key:
            refs[f"sheetlink_{r_key}"] = f"{path.name}#{i}"
    wb.save(path)
    return refs


# --------------------------------------------------------------------------
# ground truth
# --------------------------------------------------------------------------


def _channel_for(payment_mode: str) -> str | None:
    """Ingestion derives channel from payment mode only -- a statement never
    says whether a card was swiped or typed, so guessing would be noise."""
    return {
        "UPI": "app",
        "CARD": "pos",
        "ATM": "atm",
        "NEFT": "online",
        "IMPS": "online",
        "RTGS": "online",
        "ACH": "online",
        "CASH": "other",
        "CHEQUE": "other",
    }.get(payment_mode)


_LINK_RANK = {"primary": 0, "email": 1, "receipt": 2, "spreadsheet": 3, "bill": 4}


@dataclass
class Expected:
    entry: Entry
    primary_ref: str
    primary_method: str
    source_type: str
    links: list[tuple[str, str]] = field(default_factory=list)  # (link_type, source_ref)

    def linked_sources(self) -> str:
        ordered = sorted(set(self.links), key=lambda t: (_LINK_RANK[t[0]], t[1]))
        return "|".join(ref for _, ref in ordered)


def build_ground_truth(
    entries: list[Entry],
    bills: list[Bill],
    sheet_rows: list[SheetRow],
    extras: list[ExtraEmail],
    file_refs: dict[str, dict[str, str]],
) -> tuple[list[dict], list[dict]]:
    """Compose the exact rows the pipeline must produce."""
    hdfc_a, hdfc_b = file_refs[HDFC_FILE_A], file_refs[HDFC_FILE_B]
    sbi_refs, card_refs = file_refs[SBI_FILE], file_refs[CARD_FILE]
    email_refs, sheet_refs = file_refs["emails"], file_refs[SHEET_FILE]
    bill_refs = file_refs["bills"]

    expected: list[Expected] = []
    for e in entries:
        if e.account is HDFC:
            # the overlap: the earlier statement wins the primary slot
            primary = hdfc_a.get(e.key) or hdfc_b.get(e.key)
            others = [r for r in (hdfc_a.get(e.key), hdfc_b.get(e.key)) if r and r != primary]
            method, source_type = "template", "bank_statement"
        elif e.account is SBI:
            primary, others = sbi_refs.get(e.key), []
            method, source_type = "template", "bank_statement"
        elif e.account is CARD:
            primary, others = card_refs.get(e.key), []
            method, source_type = "pdfplumber", "cc_statement"
        else:
            continue
        if primary is None:
            continue
        exp = Expected(e, primary, method, source_type)
        exp.links.append(("primary", primary))
        for o in others:
            exp.links.append(("primary", o))
        if e.key in email_refs:
            exp.links.append(("email", email_refs[e.key]))
        expected.append(exp)

    by_key = {x.entry.key: x for x in expected}

    # spreadsheet rows that describe an existing transaction become links
    for r in sheet_rows:
        if r.duplicate_of_key and r.duplicate_of_key in by_key:
            ref = sheet_refs.get(f"sheetlink_{r.duplicate_of_key}")
            if ref:
                by_key[r.duplicate_of_key].links.append(("spreadsheet", ref))

    # bills link to the transaction that paid them
    for bill in bills:
        if bill.paid_by_key and bill.paid_by_key in by_key:
            by_key[bill.paid_by_key].links.append(("bill", bill_refs[bill.filename]))

    rows: list[dict] = []
    for exp in expected:
        rows.append(_expected_row(exp))

    # a refund points at the debit it reverses, which is only knowable once
    # every row has an id
    ids_by_key = {exp.entry.key: rows[i]["txn_id"] for i, exp in enumerate(expected)}
    for i, exp in enumerate(expected):
        if exp.entry.refund_of_key:
            rows[i]["refund_of"] = ids_by_key.get(exp.entry.refund_of_key, "")

    # genuine cash spends from the sheet become their own rows
    for i, r in enumerate(sheet_rows, start=1):
        if r.duplicate_of_key:
            continue
        ref = sheet_refs[f"sheet_{i}"]
        rows.append(_expected_sheet_row(r, ref))

    # an order email dated after the last statement for its account -> new row
    for x in extras:
        if not x.future:
            continue
        rows.append(_expected_email_row(x, email_refs[x.key]))

    rows.sort(key=lambda r: (r["date"], r["account"], r["txn_id"]))
    obligations = _expected_obligations(bills, by_key, bill_refs)
    return rows, obligations


def _expected_row(exp: Expected) -> dict:
    e = exp.entry
    if e.account is HDFC:
        raw = hdfc_narration(e)
    elif e.account is SBI:
        raw = sbi_narration(e)
    else:
        raw = card_narration(e)
        if e.fx:
            # the PDF wraps the currency detail onto the line below, and the
            # extractor folds it back into the narration
            currency, foreign = e.fx
            rate = (e.amount / foreign).quantize(Decimal("0.01"))
            raw = f"{raw} {currency} {foreign:.2f} @ {rate}"
    raw_masked = mask_text(raw)
    ref = e.ref if e.account is not CARD else None
    txn_id = make_txn_id(
        account=e.account.masked,
        date=e.date,
        amount=e.amount,
        direction=e.direction,
        reference_no=ref,
        raw_description=raw_masked,
    )
    order = e.order
    email_linked = any(lt == "email" for lt, _ in exp.links)
    handle = None
    if e.payment_mode == "UPI" and e.party.vpa:
        handle = "@" + e.party.vpa.split("@")[-1].lower()
    row = _blank_row()
    row.update(
        txn_id=txn_id,
        date=e.date.isoformat(),
        value_date=e.date.isoformat() if e.account in (HDFC, SBI) else "",
        time=(e.time.strftime("%H:%M:%S") if (e.time and email_linked) else ""),
        amount=f"{e.amount:.2f}",
        direction=e.direction,
        currency="INR",
        original_currency=e.fx[0] if e.fx else "",
        fx_amount=f"{e.fx[1]:.2f}" if e.fx else "",
        account=e.account.masked,
        account_type=e.account.kind,
        source_type=exp.source_type,
        source_ref=exp.primary_ref,
        raw_description=raw_masked,
        balance_after=f"{e.balance_after:.2f}" if e.balance_after is not None else "",
        reference_no=ref or "",
        merchant=e.party.merchant or "",
        counterparty_type=e.party.kind,
        counterparty_upi_handle=handle or "",
        payment_mode=e.payment_mode,
        channel=_channel_for(e.payment_mode) or "",
        upi_remark=_expected_upi_remark(e),
        # the card number is in the narration for the ATM withdrawal and the
        # card-bill autopay, and on every row of the card statement itself
        card_last4=(
            CARD.number[-4:]
            if e.account is CARD or e.payment_mode == "ATM" or e.key == "cc_payment_bank"
            else ""
        ),
        mcc=(e.party.mcc or "") if e.account is CARD else "",
        # only the ordinary card spends print "<MERCHANT> <CITY> <CC>"; the EMI
        # and payment-received lines carry no city, so none can be extracted
        merchant_city=(
            e.party.city
            if e.account is CARD and not e.emi_flag and e.key != "cc_payment_card"
            else ""
        ),
        order_id=(order.order_id if order and email_linked else ""),
        item_details=(_expected_item_details(order) if order and email_linked else ""),
        # a zero discount or delivery fee is not printed on the receipt at all,
        # so there is nothing for the pipeline to extract
        gst_amount=(f"{order.gst:.2f}" if order and order.gst and email_linked else ""),
        discount=(f"{order.discount:.2f}" if order and order.discount and email_linked else ""),
        delivery_fee=(
            f"{order.delivery_fee:.2f}" if order and order.delivery_fee and email_linked else ""
        ),
        emi_flag=e.emi_flag,
        is_fee=e.is_fee,
        is_cash_withdrawal=e.is_cash_withdrawal,
        is_transfer=e.is_transfer,
        is_refund=e.is_refund,
        linked_sources=exp.linked_sources(),
        extraction_method=exp.primary_method,
        extraction_confidence=METHOD_CONFIDENCE[exp.primary_method],
        needs_review=False,
    )
    return row


def _expected_sheet_row(r: SheetRow, ref: str) -> dict:
    raw = r.description if not r.note else f"{r.description} ({r.note})"
    txn_id = make_txn_id(
        account=CASH.masked,
        date=r.date,
        amount=r.amount,
        direction="debit",
        reference_no=None,
        raw_description=raw,
    )
    row = _blank_row()
    row.update(
        txn_id=txn_id,
        date=r.date.isoformat(),
        amount=f"{r.amount:.2f}",
        direction="debit",
        currency="INR",
        account=CASH.masked,
        account_type="cash",
        source_type="spreadsheet",
        source_ref=ref,
        raw_description=raw,
        merchant="",
        counterparty_type="unknown",
        payment_mode="CASH",
        channel="other",
        linked_sources=ref,
        extraction_method="spreadsheet",
        extraction_confidence=METHOD_CONFIDENCE["spreadsheet"],
    )
    return row


def _expected_email_row(x: ExtraEmail, ref: str) -> dict:
    raw = f"{x.party.merchant} order {x.order.order_id}"
    txn_id = make_txn_id(
        account=x.account.masked,
        date=x.date,
        amount=x.amount,
        direction="debit",
        reference_no=x.ref,
        raw_description=raw,
    )
    row = _blank_row()
    row.update(
        txn_id=txn_id,
        date=x.date.isoformat(),
        time=x.time.strftime("%H:%M:%S"),
        amount=f"{x.amount:.2f}",
        direction="debit",
        currency="INR",
        account=x.account.masked,
        account_type=x.account.kind,
        source_type="email",
        source_ref=ref,
        raw_description=raw,
        reference_no=x.ref,
        merchant=x.party.merchant or "",
        counterparty_type="merchant",
        payment_mode="UPI",
        channel="app",
        order_id=x.order.order_id,
        item_details=x.order.item_details or "",
        gst_amount=f"{x.order.gst:.2f}" if x.order.gst else "",
        discount=f"{x.order.discount:.2f}" if x.order.discount else "",
        delivery_fee=f"{x.order.delivery_fee:.2f}" if x.order.delivery_fee else "",
        linked_sources=ref,
        extraction_method="email",
        extraction_confidence=METHOD_CONFIDENCE["email"],
    )
    return row


def _blank_row() -> dict:
    """Ingestion always writes a real boolean into the flag columns, never a
    blank, so the ground truth has to as well."""
    row = {c: "" for c in ALL_TRANSACTION_COLUMNS}
    for column in BOOL_COLUMNS:
        row[column] = False
    return row


def _expected_item_details(order: OrderDetail) -> str:
    """What the order email actually lists, which is what can be extracted."""
    if order.item_details:
        return order.item_details
    return ", ".join(name for name, _, _ in order.items)


def _expected_upi_remark(e: Entry) -> str:
    """The remark exactly as the renderer wrote it into the file.

    The two dialects mangle it differently -- HDFC keeps it upper-case, SBI
    title-cases and truncates it -- so the ground truth has to reproduce the
    dialect rather than the canonical value.
    """
    if e.payment_mode != "UPI":
        return ""
    if e.account is SBI:
        return (e.remark or "Payment").title()[:20]
    return e.remark or "PAYMENT"


def _expected_obligations(bills: list[Bill], by_key: dict[str, Expected],
                          bill_refs: dict[str, str]) -> list[dict]:
    from ingestion.schema import make_bill_id

    out: list[dict] = []
    for bill in bills:
        paid_txn_id = ""
        status = "due"
        if bill.paid_by_key and bill.paid_by_key in by_key:
            paid_txn_id = _expected_row(by_key[bill.paid_by_key])["txn_id"]
            status = "paid"
        elif bill.due_date < AS_OF:
            status = "overdue"
        out.append(
            {
                "bill_id": make_bill_id(
                    biller=bill.biller,
                    consumer_no=bill.masked_consumer_no,
                    bill_date=bill.bill_date,
                    amount_due=bill.amount_due,
                ),
                "biller": bill.biller,
                "category_hint": bill.category_hint,
                "amount_due": f"{bill.amount_due:.2f}",
                "bill_date": bill.bill_date.isoformat(),
                "due_date": bill.due_date.isoformat(),
                "billing_period": bill.billing_period,
                "consumer_no": bill.masked_consumer_no,
                "status": status,
                "paid_txn_id": paid_txn_id,
                "source_ref": bill_refs[bill.filename],
            }
        )
    out.sort(key=lambda r: r["bill_id"])
    return out


def _write_csv(path: Path, rows: list[dict], columns: tuple[str, ...]) -> None:
    import csv

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: _csv_value(row.get(c, "")) for c in columns})


def _csv_value(v) -> str:
    if v is True:
        return "True"
    if v is False:
        return "False"
    if v is None:
        return ""
    return str(v)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


DEFAULT_OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "synthetic"


def generate(out_dir: Path) -> dict:
    # reportlab stamps a creation date and a random document id into every PDF,
    # which would make the fixtures differ byte-for-byte on each run and turn
    # the golden test into a coin toss.
    from reportlab import rl_config

    rl_config.invariant = 1

    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    entries = build_ledger()
    bills = build_bills()
    sheet_rows = build_sheet_rows(entries, rng)
    extras = build_extra_emails()

    file_refs: dict[str, dict[str, str]] = {}
    file_refs[HDFC_FILE_A] = render_hdfc_csv(entries, out_dir / HDFC_FILE_A, *HDFC_A_RANGE)
    file_refs[HDFC_FILE_B] = render_hdfc_csv(entries, out_dir / HDFC_FILE_B, *HDFC_B_RANGE)
    file_refs[SBI_FILE] = render_sbi_csv(entries, out_dir / SBI_FILE, *SBI_RANGE)
    file_refs[CARD_FILE] = render_card_pdf(entries, out_dir / CARD_FILE, *CARD_RANGE)
    file_refs["emails"] = render_emails(entries, extras, out_dir / "emails")
    file_refs["bills"] = render_bills(bills, out_dir / "bills")
    file_refs[SHEET_FILE] = render_spreadsheet(sheet_rows, out_dir / SHEET_FILE)

    txn_rows, obligation_rows = build_ground_truth(
        entries, bills, sheet_rows, extras, file_refs
    )
    _write_csv(out_dir / "expected_transactions.csv", txn_rows, ALL_TRANSACTION_COLUMNS)
    _write_csv(out_dir / "expected_obligations.csv", obligation_rows, OBLIGATION_COLUMNS)

    return {
        "out_dir": str(out_dir),
        "ledger_entries": len(entries),
        "expected_transactions": len(txn_rows),
        "expected_obligations": len(obligation_rows),
        "emails": len(list((out_dir / "emails").glob("*.eml"))),
        "bills": len(bills),
        "sheet_rows": len(sheet_rows),
        "files": {
            HDFC_FILE_A: len(file_refs[HDFC_FILE_A]),
            HDFC_FILE_B: len(file_refs[HDFC_FILE_B]),
            SBI_FILE: len(file_refs[SBI_FILE]),
            CARD_FILE: len(file_refs[CARD_FILE]),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    stats = generate(args.out)
    width = max(len(k) for k in stats)
    for k, v in stats.items():
        print(f"{k:<{width}}  {v}")


if __name__ == "__main__":
    main()
