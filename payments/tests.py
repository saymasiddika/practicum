"""Tests for the commission engine and payment eligibility.

The commission rule is mirrored in three places — `Payment.calculate_commission`,
the live preview in `templates/payments/create.html`, and the reporting
aggregation in `payment_admin_view`. These tests pin the model, which is the
one the other two must agree with.
"""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from accounts.models import User
from bookings.models import Booking
from payments.models import Payment
from tutors.models import TutorProfile

RATE = 4000


class CommissionCalculationTests(TestCase):
    """50% on the first month, 10% on everything past it."""

    def test_single_payment_covering_first_month_and_more(self):
        commission, tutor_income = Payment.calculate_commission(10000, RATE, 0)
        # 4000 x 50% = 2000, plus 6000 x 10% = 600
        self.assertEqual(commission, 2600.0)
        self.assertEqual(tutor_income, 7400.0)

    def test_exactly_the_first_month(self):
        commission, tutor_income = Payment.calculate_commission(RATE, RATE, 0)
        self.assertEqual(commission, 2000.0)
        self.assertEqual(tutor_income, 2000.0)

    def test_first_month_paid_in_two_instalments_is_still_charged_50_percent(self):
        """The regression that motivated the rewrite.

        A boolean "has any payment" flag charged the second instalment 10%,
        under-charging the first month.
        """
        first, _ = Payment.calculate_commission(500, RATE, 0)
        second, _ = Payment.calculate_commission(3500, RATE, 500)
        self.assertEqual(first, 250.0)
        self.assertEqual(second, 1750.0)
        self.assertEqual(first + second, 2000.0, 'first month must total 50%')

    def test_payment_straddling_the_first_month_boundary(self):
        # 1000 already paid, so 3000 of this payment finishes month one.
        commission, _ = Payment.calculate_commission(5000, RATE, 1000)
        self.assertEqual(commission, 1700.0)  # 3000 x 50% + 2000 x 10%

    def test_once_first_month_is_covered_everything_is_ten_percent(self):
        commission, tutor_income = Payment.calculate_commission(RATE, RATE, RATE)
        self.assertEqual(commission, 400.0)
        self.assertEqual(tutor_income, 3600.0)

    def test_first_month_is_never_charged_twice_across_a_long_plan(self):
        paid, total = 0.0, 0.0
        for _ in range(6):
            commission, _ = Payment.calculate_commission(RATE, RATE, paid)
            total += commission
            paid += RATE
        # 2000 on month one, then 400 x 5
        self.assertEqual(total, 4000.0)

    def test_commission_never_exceeds_the_payment(self):
        for amount in (1, 100, RATE, 10000):
            commission, tutor_income = Payment.calculate_commission(amount, RATE, 0)
            self.assertLessEqual(commission, amount)
            self.assertGreaterEqual(tutor_income, 0)

    def test_zero_amount_is_harmless(self):
        self.assertEqual(Payment.calculate_commission(0, RATE, 0), (0.0, 0.0))


class EffectiveRateTests(TestCase):
    """The admin table reports what was actually charged, not a headline rate."""

    def setUp(self):
        self.student = User.objects.create_user('s', password='x', role='student')
        self.tutor = User.objects.create_user('t', password='x', role='tutor')
        TutorProfile.objects.create(user=self.tutor, bio='')
        self.booking = Booking.objects.create(
            student=self.student, tutor=self.tutor, date='2026-01-01',
            months=6, agreed_monthly_rate=RATE, status='confirmed',
        )

    def test_blended_payment_reports_its_blended_rate(self):
        payment = Payment.objects.create(
            booking=self.booking, amount=24000, commission=4000,
            tutor_income=20000, is_first_payment=True, status='approved',
        )
        # 4000 of 24000 — not the "50% flat" the template used to claim.
        self.assertEqual(payment.effective_rate, 16.7)

    def test_recurring_payment_reports_ten_percent(self):
        payment = Payment.objects.create(
            booking=self.booking, amount=4000, commission=400,
            tutor_income=3600, status='approved',
        )
        self.assertEqual(payment.effective_rate, 10.0)

    def test_zero_amount_does_not_divide_by_zero(self):
        payment = Payment.objects.create(
            booking=self.booking, amount=0, commission=0, tutor_income=0,
        )
        self.assertEqual(payment.effective_rate, 0.0)


class PaymentEligibilityTests(TestCase):
    """Which bookings may be paid, and how much."""

    def setUp(self):
        self.student = User.objects.create_user('s', password='pw', role='student')
        self.tutor = User.objects.create_user('t', password='pw', role='tutor')
        TutorProfile.objects.create(user=self.tutor, bio='')
        self.client.force_login(self.student)

    def _booking(self, status='confirmed', months=1):
        return Booking.objects.create(
            student=self.student, tutor=self.tutor, date='2026-01-01',
            months=months, agreed_monthly_rate=RATE, status=status,
        )

    def _pay(self, booking, amount):
        return self.client.post(
            f'/payments/create/?booking_id={booking.id}',
            {'amount': str(amount), 'payment_method': 'bkash',
             'sender_number': '01700000000', 'transaction_id': 'TX'},
        )

    def test_confirmed_booking_accepts_payment(self):
        booking = self._booking()
        self._pay(booking, RATE)
        self.assertEqual(booking.payments.count(), 1)

    def test_an_unconfirmed_booking_cannot_be_paid(self):
        """Money must not change hands for a request the tutor can still decline.

        The app has no refund path, so a student who paid for a booking that was
        then rejected would simply be out of pocket.
        """
        booking = self._booking(status='pending')
        self._pay(booking, RATE)
        self.assertEqual(booking.payments.count(), 0)

    def test_is_payable_matches_what_the_view_allows(self):
        """The Pay button is rendered from this property, so they must agree."""
        cases = [('pending', False), ('confirmed', True), ('completed', False),
                 ('cancelled', False), ('rejected', False)]
        for status, expected in cases:
            with self.subTest(status=status):
                self.assertEqual(self._booking(status=status).is_payable, expected)

    def test_a_fully_paid_booking_is_no_longer_payable(self):
        booking = self._booking()
        Payment.objects.create(booking=booking, amount=RATE, commission=0,
                               tutor_income=RATE, status='approved')
        self.assertFalse(booking.is_payable)

    def test_closed_bookings_reject_payment(self):
        for status in ('cancelled', 'rejected', 'completed'):
            with self.subTest(status=status):
                booking = self._booking(status=status)
                self._pay(booking, RATE)
                self.assertEqual(
                    booking.payments.count(), 0,
                    f'a {status} booking must not accept payment',
                )

    def test_payment_above_the_balance_is_rejected(self):
        booking = self._booking()
        self._pay(booking, RATE + 1)
        self.assertEqual(booking.payments.count(), 0)

    def test_sub_currency_rounding_is_still_allowed(self):
        booking = self._booking()
        self._pay(booking, Decimal('4000.01'))
        self.assertEqual(booking.payments.count(), 1)

    def test_a_second_pending_payment_is_refused(self):
        booking = self._booking(months=2)
        self._pay(booking, RATE)
        self._pay(booking, RATE)
        self.assertEqual(booking.payments.filter(status='pending').count(), 1)

    def test_database_refuses_two_pending_payments(self):
        """The view guards this, but the constraint is what closes the race."""
        booking = self._booking(months=2)
        Payment.objects.create(
            booking=booking, amount=100, commission=0, tutor_income=0,
            status='pending',
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Payment.objects.create(
                    booking=booking, amount=200, commission=0, tutor_income=0,
                    status='pending',
                )

    def test_retry_after_rejection_still_carries_the_first_month(self):
        booking = self._booking()
        Payment.objects.create(
            booking=booking, amount=100, commission=0, tutor_income=0,
            status='rejected',
        )
        self._pay(booking, RATE)
        retry = booking.payments.get(status='pending')
        self.assertTrue(
            retry.is_first_payment,
            'a rejected attempt must not make the retry look like an instalment',
        )
        self.assertEqual(retry.commission, Decimal('2000.00'))

    def test_a_student_cannot_pay_for_someone_elses_booking(self):
        other = User.objects.create_user('other', password='pw', role='student')
        booking = Booking.objects.create(
            student=other, tutor=self.tutor, date='2026-01-01', months=1,
            agreed_monthly_rate=RATE, status='confirmed',
        )
        self._pay(booking, RATE)
        self.assertEqual(booking.payments.count(), 0)


class BookingTotalsTests(TestCase):
    """Money aggregates read every approved instalment."""

    def setUp(self):
        self.student = User.objects.create_user('s', password='pw', role='student')
        self.tutor = User.objects.create_user('t', password='pw', role='tutor')
        TutorProfile.objects.create(user=self.tutor, bio='')
        self.booking = Booking.objects.create(
            student=self.student, tutor=self.tutor, date='2026-01-01',
            months=2, agreed_monthly_rate=RATE, status='confirmed',
        )

    def test_total_paid_sums_every_approved_instalment(self):
        for amount in (2000, 2000, 4000):
            Payment.objects.create(
                booking=self.booking, amount=amount, commission=0,
                tutor_income=amount, status='approved',
            )
        self.assertEqual(self.booking.total_paid, 8000)

    def test_pending_and_rejected_payments_do_not_count_as_paid(self):
        Payment.objects.create(
            booking=self.booking, amount=4000, commission=0, tutor_income=0,
            status='rejected',
        )
        self.assertEqual(self.booking.total_paid, 0)
        self.assertFalse(self.booking.is_fully_paid)

    def test_agreed_rate_is_immune_to_later_rate_changes(self):
        self.assertEqual(self.booking.total_amount, 8000.0)
        profile = self.tutor.tutor_profile
        profile.monthly_rate = 9000
        profile.save()
        self.booking.refresh_from_db()
        self.assertEqual(
            self.booking.total_amount, 8000.0,
            'a settled price must not follow the tutor’s current rate',
        )


class NonFiniteAmountTests(PaymentEligibilityTests):
    """"NaN" is valid input to both Decimal() and float(), and poisons both.

    A Decimal NaN raises InvalidOperation on the first comparison — a 500. A
    float nan answers False to `nan <= 0`, sliding straight past the check
    written to reject it. Neither payment route survived it.
    """

    # Non-finite, plus the finite-but-unstorable. Decimal calls 1e999 finite;
    # the column is DecimalField(10, 2), so it is still not a real amount.
    NON_FINITE = ('NaN', 'nan', '-NaN', 'sNaN', 'Infinity', '-Infinity',
                  'inf', '-inf', '1e999', '-1e999', '99999999999')

    def test_parse_amount_accepts_only_finite_positive_numbers(self):
        from payments.views import parse_amount
        for raw in self.NON_FINITE + ('abc', '', '   ', '0', '-5', None):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_amount(raw), f'{raw!r} should be refused')
        for raw in ('1', '5000', '4999.99', ' 250 '):
            with self.subTest(raw=raw):
                self.assertIsNotNone(parse_amount(raw), f'{raw!r} should be accepted')

    def test_the_manual_route_refuses_a_non_finite_amount(self):
        booking = self._booking()
        for raw in self.NON_FINITE:
            with self.subTest(amount=raw):
                response = self.client.post(
                    f'/payments/create/?booking_id={booking.id}',
                    {'amount': raw, 'sender_number': '01700000000',
                     'transaction_id': 'TX1'},
                )
                self.assertLess(response.status_code, 500,
                                f'{raw!r} crashed the manual payment form')
                self.assertEqual(booking.payments.count(), 0)

    @override_settings(SSLCOMMERZ_STORE_ID='s', SSLCOMMERZ_STORE_PASSWORD='p',
                       SSLCOMMERZ_SANDBOX=True, SSLCOMMERZ_BASE_URL='http://testserver')
    def test_the_gateway_route_refuses_a_non_finite_amount(self):
        booking = self._booking()
        for raw in self.NON_FINITE:
            with self.subTest(amount=raw):
                response = self.client.post('/payments/gateway/start/',
                                            {'booking_id': booking.id, 'amount': raw})
                self.assertLess(response.status_code, 500,
                                f'{raw!r} crashed the gateway checkout')
                self.assertEqual(booking.payments.count(), 0)


from django.test import SimpleTestCase  # noqa: E402

from payments.money import taka  # noqa: E402


class TakaFormatTests(SimpleTestCase):
    """Messages must write money the way the pages beside them do."""

    def test_whole_amounts_are_grouped_without_paise(self):
        from decimal import Decimal
        self.assertEqual(taka(Decimal('8000.0')), '৳8,000')
        self.assertEqual(taka(99999999), '৳99,999,999')

    def test_paise_are_kept_when_there_are_some(self):
        from decimal import Decimal
        self.assertEqual(taka(Decimal('8000.50')), '৳8,000.50')

    def test_nothing_is_zero(self):
        self.assertEqual(taka(None), '৳0')


class PaymentReviewTableTests(TestCase):
    """The review table never shows a commission that has not been set yet."""

    def setUp(self):
        from datetime import date
        from accounts.models import User
        from bookings.models import Booking
        self.admin = User.objects.create_superuser('pr_root', password='pw', email='pr@x.com')
        student = User.objects.create_user('pr_stu', password='pw', role='student')
        tutor = User.objects.create_user('pr_tut', password='pw', role='tutor')
        make = lambda: Booking.objects.create(student=student, tutor=tutor, date=date(2026, 1, 1),
                                              months=2, agreed_monthly_rate=4000, status='confirmed')
        Payment.objects.create(booking=make(), amount=4000, status='pending',
                               payment_method='bkash', transaction_id='PR-PENDING')
        Payment.objects.create(booking=make(), amount=4000, status='initiated',
                               payment_method='sslcommerz')
        self.client.force_login(self.admin)

    def test_a_pending_payment_says_commission_is_set_on_approval(self):
        html = self.client.get('/payments/manage/').content.decode()
        self.assertIn('Set on approval', html)
        self.assertNotIn('0.0% effective', html)

    def test_a_gateway_payment_is_labelled_online_with_the_full_name_on_hover(self):
        html = self.client.get('/payments/manage/').content.decode()
        self.assertIn('title="Card / mobile banking (online)">Online', html)
