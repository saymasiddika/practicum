"""What a student gets back when a booking ends early.

The policy lives in REFUND_TIERS below, as a table rather than a chain of
`if` statements, so the business rule can be read — and argued with — without
reading any logic. Changing what the platform refunds should mean editing
that table and nothing else.

Two rules sit outside the table because they are not about how long the
student waited:

  · Before the course starts, everything comes back. Nothing was taught, so
    there is nothing to keep.
  · When the TUTOR ends the booking, the ladder does not price it. It exists
    for a student changing their mind, and a student whose tutor walks away
    has not changed their mind. They get the untaught portion back, pro rata
    — or the ladder figure if that happens to be larger, which it can be late
    in a short course. Whichever leaves them better off; the point is that
    the tutor's decision never costs them.

Everything here is a pure calculation over a booking. Nothing is written to
the database, so a quote can be shown on a form before anyone commits to it.
"""
from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta
from django.utils import timezone

TWO_PLACES = Decimal('0.01')

# ── The policy ───────────────────────────────────────────────────────────
# (last day of the tier, percent refunded, wording shown to the student)
#
# Days are counted from the course start date, with the start date itself
# being day 0. A tier covers every day up to and including its first value,
# so the 75% tier is days 0-7 and the 50% tier is days 8-14.
REFUND_TIERS = (
    (7,  75, 'within the first week'),
    (14, 50, 'within the second week'),
    (28, 25, 'within the first four weeks'),
)

# Past the last tier, nothing is refunded.
NO_REFUND = (0, 'after the first four weeks')

BEFORE_START = (100, 'before the course had started')
TUTOR_ENDED = 'the tutor ended the booking'


@dataclass(frozen=True)
class RefundQuote:
    """What a student would get back, and why.

    `percent` is the headline figure; `amount` is what it comes to against
    what this student actually paid. `tier` is the wording to show them.
    """
    percent: int
    amount: Decimal
    tier: str

    @property
    def is_refundable(self):
        return self.amount > 0


def _money(value):
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def course_length_days(booking):
    """How many days the booked course runs for."""
    end = booking.date + relativedelta(months=booking.months)
    return max(1, (end - booking.date).days)


def days_since_start(booking, on_date):
    """Days elapsed since teaching began. Negative before the start date."""
    return (on_date - booking.date).days


def _ladder_quote(paid, elapsed):
    """The tiered refund for a student who is `elapsed` days into the course.

    Split out because the tutor branch needs it too — to check it is not
    quietly offering the student less than they could have had themselves.
    """
    for last_day, percent, wording in REFUND_TIERS:
        if elapsed <= last_day:
            return RefundQuote(
                percent=percent,
                amount=_money(paid * Decimal(percent) / Decimal(100)),
                tier=wording,
            )
    percent, wording = NO_REFUND
    return RefundQuote(percent=percent, amount=_money(0), tier=wording)


def quote_refund(booking, on_date=None, initiated_by='student'):
    """What this booking would refund if it ended on `on_date`.

    `initiated_by` is 'student' or 'tutor' — see the module docstring for why
    that changes the answer.
    """
    if on_date is None:
        on_date = timezone.localdate()
    if isinstance(on_date, date_type) and hasattr(on_date, 'date'):
        on_date = on_date.date()

    paid = Decimal(str(booking.total_paid or 0))
    elapsed = days_since_start(booking, on_date)

    # Nothing taught yet, whoever is ending it.
    if elapsed < 0:
        percent, wording = BEFORE_START
        return RefundQuote(percent=percent, amount=_money(paid), tier=wording)

    if initiated_by == 'tutor':
        total = course_length_days(booking)
        untaught = max(0, total - elapsed)
        percent = int(round(untaught * 100 / total))
        amount = _money(paid * Decimal(untaught) / Decimal(total))

        # Pro rata is usually far more generous than the ladder — but not
        # always. Late in a SHORT course most of it has been taught, so the
        # untaught fraction can fall below the tier the student would have
        # got by walking away themselves: on a one-month course, day 26 is
        # 16% pro rata against the ladder's 25%.
        #
        # That would punish the student for their tutor's decision, which is
        # the one thing this branch exists to prevent. So they get whichever
        # is better for them.
        ladder = _ladder_quote(paid, elapsed)
        if ladder.amount > amount:
            return RefundQuote(percent=ladder.percent, amount=ladder.amount,
                               tier=TUTOR_ENDED)
        return RefundQuote(percent=percent, amount=amount, tier=TUTOR_ENDED)

    return _ladder_quote(paid, elapsed)


def refund_split(booking, amount):
    """How a refund is shared between the platform and the tutor.

    Each gives back the same proportion of what it took, so neither is left
    owing more than it received. Returns (platform_returns, tutor_returns).
    """
    amount = _money(amount)
    if amount <= 0:
        return _money(0), _money(0)

    approved = [p for p in booking.payments.all() if p.status == 'approved']
    commission = sum((Decimal(str(p.commission)) for p in approved), Decimal('0'))
    income = sum((Decimal(str(p.tutor_income)) for p in approved), Decimal('0'))
    total = commission + income

    if total <= 0:
        return _money(0), _money(0)

    platform = _money(amount * commission / total)
    # The tutor covers the remainder, so the two halves always add back up to
    # the refund even after rounding.
    return platform, _money(amount - platform)
