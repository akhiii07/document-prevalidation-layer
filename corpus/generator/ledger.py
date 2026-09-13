"""Synthetic current-account ledger generation.

The defining property here is **arithmetic consistency**: every transaction's stated
balance equals the previous balance plus credits minus debits, exactly. That is what
makes the balance-continuity validator (ADR-007) a real test. A generator that produced
approximately-correct balances would make the validator look like it works while it was
in fact detecting the generator's own noise.

Generation is seeded, so the corpus is reproducible: the same seed and as-of date always
produce byte-identical ledgers.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

# Realistic MSME current-account activity. Each entry is
# (description template, is_credit, minimum rupees, maximum rupees, relative weight).
_PATTERNS: list[tuple[str, bool, int, int, int]] = [
    ("UPI/{ref}/{party}/Payment from", True, 1_500, 85_000, 18),
    ("NEFT-{ref}-{party}", True, 12_000, 450_000, 10),
    ("IMPS/{ref}/{party}", True, 2_000, 120_000, 8),
    ("CASH DEPOSIT SELF", True, 10_000, 200_000, 4),
    ("RTGS CR {ref} {party}", True, 200_000, 900_000, 3),
    ("UPI/{ref}/{party}/Payment to", False, 500, 45_000, 16),
    ("NEFT DR-{ref}-{party}", False, 8_000, 320_000, 9),
    ("CHQ PAID {ref}", False, 15_000, 250_000, 6),
    ("POS/{ref}/FUEL", False, 1_000, 12_000, 5),
    ("GST PAYMENT {ref}", False, 8_000, 95_000, 3),
    ("SALARY DISBURSEMENT {ref}", False, 60_000, 340_000, 3),
    ("ATM WDL {ref}", False, 2_000, 20_000, 4),
    ("BANK CHARGES", False, 100, 900, 3),
    ("ELECTRICITY BILL {ref}", False, 3_000, 28_000, 3),
]

_PARTIES = [
    "SHREE BALAJI TRADERS",
    "AGARWAL ENTERPRISES",
    "KRISHNA TEXTILES",
    "NEW INDIA HARDWARE",
    "SUNRISE PACKAGING",
    "MAHALAXMI STEEL",
    "GANESH TRANSPORT",
    "RELIABLE POLYMERS",
    "SARASWATI PRINTS",
    "VIJAY AUTO PARTS",
    "DEEP INDUSTRIES",
    "ANAND FOODS",
]


@dataclass(frozen=True)
class Transaction:
    txn_date: date
    value_date: date
    description: str
    debit_paise: int  # 0 when this is a credit
    credit_paise: int  # 0 when this is a debit
    balance_paise: int
    reference: str

    @property
    def is_credit(self) -> bool:
        return self.credit_paise > 0

    @property
    def amount_paise(self) -> int:
        return self.credit_paise or self.debit_paise


@dataclass
class Statement:
    bank_key: str
    bank_name: str
    branch: str
    ifsc: str
    account_holder: str
    account_number: str
    account_type: str
    period_start: date
    period_end: date
    opening_balance_paise: int
    transactions: list[Transaction] = field(default_factory=list)

    @property
    def closing_balance_paise(self) -> int:
        return (
            self.transactions[-1].balance_paise
            if self.transactions
            else self.opening_balance_paise
        )

    @property
    def total_debits_paise(self) -> int:
        return sum(t.debit_paise for t in self.transactions)

    @property
    def total_credits_paise(self) -> int:
        return sum(t.credit_paise for t in self.transactions)

    def masked_account_number(self) -> str:
        """Never render or store a full account number outside the document itself."""
        tail = self.account_number[-4:]
        return f"{'X' * (len(self.account_number) - 4)}{tail}"

    def assert_consistent(self) -> None:
        """Fail loudly if the generator produced an unintended balance break."""
        balance = self.opening_balance_paise
        for i, txn in enumerate(self.transactions):
            balance = balance + txn.credit_paise - txn.debit_paise
            if balance != txn.balance_paise:
                raise AssertionError(
                    f"ledger inconsistent at transaction {i}: "
                    f"expected {balance}, statement says {txn.balance_paise}"
                )


def _expand_narration(base: str, party: str, ref: str, rng: random.Random) -> str:
    """Produce the long, slash-delimited narration real UPI/NEFT entries carry.

    Narration that overflows its column and wraps onto a second line is the hardest
    part of statement extraction: rows stop mapping one-to-one onto transactions. At
    least one bank profile must exercise it, or the extractor will look more capable
    than it is.
    """
    handle = party.split()[0].lower()
    bank = rng.choice(["HDFC", "ICIC", "SBIN", "UTIB", "KKBK", "PYTM"])
    tail = rng.choice(
        [
            f"/{handle}@ok{bank.lower()}bank/Payment towards invoice {rng.randint(1000, 9999)}",
            f"/{handle}.pay@{bank.lower()}/NEFT settlement ref {ref}",
            f"/{handle}@ybl/Goods supplied against PO-{rng.randint(10000, 99999)}",
            f"/{handle}@paytm/Part payment bill no {rng.randint(100, 999)}",
        ]
    )
    return f"{base}/{bank}/{rng.randint(10**11, 10**12 - 1)}{tail}"


def _reference(rng: random.Random) -> str:
    return "".join(rng.choices("0123456789", k=rng.choice([9, 12])))


def generate_statement(
    *,
    bank_key: str,
    bank_name: str,
    branch: str,
    ifsc: str,
    account_holder: str,
    account_number: str,
    period_start: date,
    period_end: date,
    seed: int,
    account_type: str = "CURRENT",
    opening_balance_paise: int = 4_85_000_00,
    txns_per_month: tuple[int, int] = (58, 86),
    verbose_narration: bool = False,
) -> Statement:
    """Build an arithmetically consistent statement for the given period."""
    rng = random.Random(seed)

    months = max(1, round((period_end - period_start).days / 30.44))
    count = sum(rng.randint(*txns_per_month) for _ in range(months))

    span_days = (period_end - period_start).days
    # Sorted offsets give chronologically ordered transactions, with several
    # transactions legitimately sharing a date -- as a real account would.
    offsets = sorted(rng.randint(0, span_days) for _ in range(count))

    weights = [p[4] for p in _PATTERNS]
    balance = opening_balance_paise
    transactions: list[Transaction] = []

    for offset in offsets:
        template, is_credit, lo, hi, _ = rng.choices(_PATTERNS, weights=weights, k=1)[0]
        ref = _reference(rng)
        party = rng.choice(_PARTIES)
        description = template.format(ref=ref, party=party)
        if verbose_narration:
            description = _expand_narration(description, party, ref, rng)

        # Round to whole rupees most of the time, as real transfers usually are.
        amount = rng.randint(lo, hi) * 100
        if rng.random() < 0.25:
            amount += rng.randint(1, 99)

        # Keep the account solvent: an overdrawn current account is realistic but
        # would add a variable the corpus does not need.
        if not is_credit and amount > balance - 25_000_00:
            is_credit = True

        balance = balance + amount if is_credit else balance - amount

        txn_date = period_start + timedelta(days=offset)
        transactions.append(
            Transaction(
                txn_date=txn_date,
                # Value date occasionally lags, as it does in practice.
                value_date=txn_date + timedelta(days=1 if rng.random() < 0.08 else 0),
                description=description,
                debit_paise=0 if is_credit else amount,
                credit_paise=amount if is_credit else 0,
                balance_paise=balance,
                reference=ref,
            )
        )

    statement = Statement(
        bank_key=bank_key,
        bank_name=bank_name,
        branch=branch,
        ifsc=ifsc,
        account_holder=account_holder,
        account_number=account_number,
        account_type=account_type,
        period_start=period_start,
        period_end=period_end,
        opening_balance_paise=opening_balance_paise,
        transactions=transactions,
    )
    statement.assert_consistent()
    return statement
