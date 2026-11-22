"""Tests for the early-termination refund policy.

The policy is money, so every tier boundary is pinned from both sides: the
last day that still earns a tier and the first day that does not. A rule
like "25% for two to four weeks" is only unambiguous once day 28 and day 29
are both written down.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from bookings.models import Booking
from bookings.refunds import REFUND_TIERS, quote_refund, refund_split
from payments.models import Payment
from tutors.models import TutorProfile

RATE = 5000
START = date(2026, 3, 1)


class RefundTestCase(TestCase):
    def setUp(self):
        self.student = User.objects.create_user('r_stu', password='pw', role='student',
                                                email='s@x.com')
        self.tutor = User.objects.create_user('r_tut', password='pw', role='tutor')
        TutorProfile.objects.create(user=self.tutor, bio='b')

    def booking(self, months=1, paid=RATE):
        b = Booking.objects.create(
            student=self.student, tutor=self.tutor, date=START,
            months=months, agreed_monthly_rate=RATE, status='confirmed',
        )
        if paid:
            commission, income = Payment.calculate_commission(paid, RATE, 0)
            Payment.objects.create(booking=b, amount=paid, commission=commission,
                                   tutor_income=income, status='approved')
        return b

    def on_day(self, n):
        """The date n days after the course started."""
        return START + timedelta(days=n)


class LadderBoundaryTests(RefundTestCase):
    """Every tier, checked on its last day and the first day past it."""

    def assert_percent(self, day, expected):
        quote = quote_refund(self.booking(), on_date=self.on_day(day))
        self.assertEqual(quote.percent, expected,
                         f'day {day} should refund {expected}%, got {quote.percent}%')

    def test_the_first_week_refunds_seventy_five_percent(self):
        for day in (0, 1, 3, 7):
            with self.subTest(day=day):
                self.assert_percent(day, 75)

    def test_the_second_week_refunds_fifty_percent(self):
        for day in (8, 10, 14):
            with self.subTest(day=day):
                self.assert_percent(day, 50)

    def test_weeks_three_and_four_refund_twenty_five_percent(self):
        for day in (15, 21, 28):
            with self.subTest(day=day):
                self.assert_percent(day, 25)

    def test_after_four_weeks_there_is_no_refund(self):
        for day in (29, 30, 45, 400):
            with self.subTest(day=day):
                self.assert_percent(day, 0)

    def test_each_boundary_is_pinned_on_both_sides(self):
        """The cliff edges, stated as a single readable table."""
        edges = [(7, 75), (8, 50), (14, 50), (15, 25), (28, 25), (29, 0)]
        for day, expected in edges:
            with self.subTest(day=day):
                self.assert_percent(day, expected)

    def test_the_tiers_never_increase_as_time_passes(self):
        """A student can never do better by waiting longer."""
        previous = 101
        for day in range(0, 60):
            percent = quote_refund(self.booking(), on_date=self.on_day(day)).percent
            self.assertLessEqual(percent, previous, f'refund went up on day {day}')
            previous = percent


class BeforeTheCourseStartsTests(RefundTestCase):
    def test_pulling_out_before_the_first_lesson_refunds_everything(self):
        for days_early in (1, 10, 90):
            with self.subTest(days_early=days_early):
                quote = quote_refund(self.booking(), on_date=self.on_day(-days_early))
                self.assertEqual(quote.percent, 100)
                self.assertEqual(quote.amount, Decimal('5000.00'))

    def test_the_reason_given_explains_the_course_had_not_begun(self):
        """The wording is shown to the student, so it has to read as English."""
        quote = quote_refund(self.booking(), on_date=self.on_day(-3))
        self.assertIn('before the course', quote.tier.lower())


class AmountTests(RefundTestCase):
    def test_the_amount_is_a_percentage_of_what_was_actually_paid(self):
        quote = quote_refund(self.booking(paid=5000), on_date=self.on_day(2))
        self.assertEqual(quote.amount, Decimal('3750.00'))

    def test_a_part_paid_booking_refunds_only_against_what_was_paid(self):
        """Not against the course price — you cannot get back what you never paid."""
        booking = self.booking(months=3, paid=5000)          # 15,000 course, 5,000 paid
        quote = quote_refund(booking, on_date=self.on_day(2))
        self.assertEqual(quote.amount, Decimal('3750.00'))

    def test_an_unpaid_booking_refunds_nothing(self):
        quote = quote_refund(self.booking(paid=0), on_date=self.on_day(2))
        self.assertEqual(quote.amount, Decimal('0.00'))

    def test_pending_and_rejected_payments_do_not_count(self):
        booking = self.booking(paid=0)
        Payment.objects.create(booking=booking, amount=5000, commission=0,
                               tutor_income=5000, status='pending')
        Payment.objects.create(booking=booking, amount=5000, commission=0,
                               tutor_income=5000, status='rejected')
        quote = quote_refund(booking, on_date=self.on_day(2))
        self.assertEqual(quote.amount, Decimal('0.00'))

    def test_the_amount_never_exceeds_what_was_paid(self):
        for day in range(-5, 40):
            booking = self.booking(paid=5000)
            quote = quote_refund(booking, on_date=self.on_day(day))
            self.assertLessEqual(quote.amount, Decimal('5000.00'))
            self.assertGreaterEqual(quote.amount, Decimal('0.00'))


class TutorTerminationTests(RefundTestCase):
    """When the tutor walks away the student is not at fault, so the ladder
    does not apply — they get back whatever was not taught."""

    def test_the_student_gets_the_untaught_portion_back(self):
        # A one-month course starting 1 March runs 31 days. Ending on day 20
        # leaves 11 days untaught.
        booking = self.booking(months=1, paid=5000)
        quote = quote_refund(booking, on_date=self.on_day(20), initiated_by='tutor')
        expected = (Decimal('5000') * Decimal(11) / Decimal(31)).quantize(Decimal('0.01'))
        self.assertEqual(quote.amount, expected)

    def test_it_beats_the_student_ladder_on_the_same_day(self):
        """Day 20 would be the 25% tier. The student must not be punished."""
        booking = self.booking(paid=5000)
        student_quote = quote_refund(booking, on_date=self.on_day(20))
        tutor_quote = quote_refund(booking, on_date=self.on_day(20), initiated_by='tutor')
        self.assertGreater(tutor_quote.amount, student_quote.amount)

    def test_a_tutor_quitting_before_the_first_lesson_refunds_everything(self):
        quote = quote_refund(self.booking(), on_date=self.on_day(-1), initiated_by='tutor')
        self.assertEqual(quote.percent, 100)

    def test_a_tutor_quitting_after_the_course_ended_refunds_nothing(self):
        quote = quote_refund(self.booking(months=1), on_date=self.on_day(60),
                             initiated_by='tutor')
        self.assertEqual(quote.amount, Decimal('0.00'))


class RefundSplitTests(RefundTestCase):
    """Platform and tutor each hand back the same proportion of their share."""

    def test_the_split_adds_back_up_to_the_refund(self):
        booking = self.booking(paid=5000)          # commission 2500, tutor 2500
        quote = quote_refund(booking, on_date=self.on_day(2))     # 75% = 3750
        platform, tutor = refund_split(booking, quote.amount)
        self.assertEqual(platform + tutor, quote.amount)

    def test_each_side_returns_the_same_proportion_it_received(self):
        booking = self.booking(paid=5000)
        quote = quote_refund(booking, on_date=self.on_day(2))
        platform, tutor = refund_split(booking, quote.amount)
        # 50/50 split of the first month, so 75% of each half.
        self.assertEqual(platform, Decimal('1875.00'))
        self.assertEqual(tutor, Decimal('1875.00'))

    def test_neither_side_ever_returns_more_than_it_received(self):
        booking = self.booking(paid=5000)
        for day in range(-2, 35):
            quote = quote_refund(booking, on_date=self.on_day(day))
            platform, tutor = refund_split(booking, quote.amount)
            self.assertLessEqual(platform, Decimal('2500.00'))
            self.assertLessEqual(tutor, Decimal('2500.00'))

    def test_an_unpaid_booking_splits_to_nothing(self):
        booking = self.booking(paid=0)
        platform, tutor = refund_split(booking, Decimal('0.00'))
        self.assertEqual((platform, tutor), (Decimal('0.00'), Decimal('0.00')))


class PolicyTableTests(TestCase):
    """The policy must stay readable as a table, not scattered through logic."""

    def test_the_tiers_are_declared_in_order_and_decrease(self):
        days = [d for d, _pct, _label in REFUND_TIERS]
        percents = [p for _d, p, _label in REFUND_TIERS]
        self.assertEqual(days, sorted(days), 'tiers must be in day order')
        self.assertEqual(percents, sorted(percents, reverse=True),
                         'later tiers must refund less')

    def test_every_tier_has_a_human_readable_label(self):
        for _day, _percent, label in REFUND_TIERS:
            self.assertTrue(label and not label.isdigit(),
                            'each tier needs wording the student can be shown')


class TutorTerminationNeverPunishesTheStudentTests(RefundTestCase):
    """A tutor walking away must never leave the student worse off than if
    they had quit themselves.

    Pro rata is usually far more generous than the ladder, but not always:
    late in a SHORT course most of it has been taught, so the untaught
    fraction can fall below the tier the ladder would have given. On a
    one-month course, day 26 is 16% pro rata against the ladder's 25%.
    """

    def test_the_student_is_never_worse_off_on_any_day_of_any_course(self):
        worse = []
        for months in (1, 2, 3, 6):
            booking = self.booking(months=months, paid=RATE)
            for day in range(0, 40):
                on = self.on_day(day)
                ladder = quote_refund(booking, on_date=on, initiated_by='student')
                tutor = quote_refund(booking, on_date=on, initiated_by='tutor')
                if tutor.amount < ladder.amount:
                    worse.append((months, day, float(tutor.amount), float(ladder.amount)))
        self.assertEqual(worse, [],
                         'the tutor branch offered less than the student ladder')

    def test_the_short_course_case_that_was_wrong(self):
        """Day 26 of a one-month course: pro rata 16%, ladder 25%."""
        booking = self.booking(months=1, paid=RATE)
        quote = quote_refund(booking, on_date=self.on_day(26), initiated_by='tutor')
        self.assertEqual(quote.amount, Decimal('1250.00'))     # the 25% tier
        self.assertIn('tutor', quote.tier)

    def test_pro_rata_still_wins_where_it_is_more_generous(self):
        """The common case must be unaffected: day 20 of three months."""
        booking = self.booking(months=3, paid=RATE)
        quote = quote_refund(booking, on_date=self.on_day(20), initiated_by='tutor')
        ladder = quote_refund(booking, on_date=self.on_day(20), initiated_by='student')
        self.assertGreater(quote.amount, ladder.amount)
        self.assertGreater(quote.percent, 50)
