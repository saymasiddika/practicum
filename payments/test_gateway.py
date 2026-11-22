"""Tests for the SSLCommerz integration.

The important ones are in ForgedCallbackTests: SSLCommerz posts the payment
result to our success URL over the open internet, so the whole integration
rests on never trusting that POST and asking the validation API instead. These
tests forge callbacks and assert the money is not credited.

Every test patches the network — nothing here talks to SSLCommerz.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from accounts.models import User
from bookings.models import Booking
from payments import gateway
from payments.models import Payment
from tutors.models import TutorProfile

RATE = 4000

GATEWAY_SETTINGS = dict(
    SSLCOMMERZ_STORE_ID='teststore',
    SSLCOMMERZ_STORE_PASSWORD='testpass',
    SSLCOMMERZ_SANDBOX=True,
    SSLCOMMERZ_BASE_URL='http://testserver',
)


@override_settings(**GATEWAY_SETTINGS)
class GatewayTestCase(TestCase):
    def setUp(self):
        self.student = User.objects.create_user('student', password='pw', role='student',
                                                email='s@example.com')
        self.tutor = User.objects.create_user('tutor', password='pw', role='tutor')
        TutorProfile.objects.create(user=self.tutor, bio='')
        self.booking = Booking.objects.create(
            student=self.student, tutor=self.tutor, date='2026-01-01',
            months=2, agreed_monthly_rate=RATE, status='confirmed',
        )
        self.client.force_login(self.student)

    def start(self, amount='4000'):
        return self.client.post('/payments/gateway/start/',
                                {'booking_id': self.booking.id, 'amount': amount})

    def pending(self):
        """The open checkout attempt.

        'initiated' means a session was opened at SSLCommerz and nothing has
        come back yet — deliberately not 'pending', which is the manual
        flow's admin-review queue.
        """
        return self.booking.payments.filter(status='initiated').first()

    def valid_response(self, payment, **overrides):
        data = {
            'status': 'VALID',
            'tran_id': payment.gateway_tran_id,
            'currency': 'BDT',
            'amount': str(payment.amount),
            'val_id': 'VAL123',
            'bank_tran_id': 'BANK123',
        }
        data.update(overrides)
        return data


class SessionStartTests(GatewayTestCase):
    @patch('payments.gateway.create_session', return_value=('https://sandbox.sslcommerz.com/x', 'k'))
    def test_starting_checkout_creates_an_initiated_payment_and_redirects(self, mocked):
        response = self.start('4000')
        payment = self.pending()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://sandbox.sslcommerz.com/x')
        self.assertEqual(payment.payment_method, 'sslcommerz')
        self.assertTrue(payment.gateway_tran_id.startswith('EDUAI-'))
        # Never 'pending': no human is waiting to sign this off.
        self.assertEqual(payment.status, 'initiated')

    @patch('payments.gateway.create_session', return_value=('https://x', 'k'))
    def test_the_amount_may_not_exceed_the_balance(self, mocked):
        self.start('99999')
        self.assertIsNone(self.pending())

    @patch('payments.gateway.create_session', return_value=('https://x', 'k'))
    def test_a_closed_booking_cannot_be_paid(self, mocked):
        self.booking.status = 'cancelled'
        self.booking.save()
        self.start()
        self.assertIsNone(self.pending())

    @patch('payments.gateway.create_session', return_value=('https://x', 'k'))
    def test_an_unconfirmed_booking_cannot_reach_checkout(self, mocked):
        """No checkout session at all until the tutor has accepted."""
        self.booking.status = 'pending'
        self.booking.save()
        self.start()
        mocked.assert_not_called()
        self.assertEqual(self.booking.payments.count(), 0)

    @patch('payments.gateway.create_session', return_value=('https://x', 'k'))
    def test_another_students_booking_cannot_be_paid(self, mocked):
        other = User.objects.create_user('other', password='pw', role='student')
        self.client.force_login(other)
        self.start()
        self.assertEqual(self.booking.payments.count(), 0)

    @patch('payments.gateway.create_session', side_effect=gateway.GatewayError('down'))
    def test_a_gateway_failure_leaves_no_dead_pending_row(self, mocked):
        """Otherwise the one-pending-payment rule would lock the student out."""
        self.start()
        self.assertEqual(self.booking.payments.count(), 0)


class ForgedCallbackTests(GatewayTestCase):
    """The success URL is public. None of these may credit a payment."""

    @patch('payments.gateway.create_session', return_value=('https://x', 'k'))
    def _pending_payment(self, mocked):
        self.start('4000')
        return self.pending()

    @patch('payments.gateway.validate')
    def test_a_callback_claiming_success_is_refused_when_the_gateway_says_failed(self, validate):
        payment = self._pending_payment()
        validate.return_value = self.valid_response(payment, status='FAILED')

        self.client.post('/payments/gateway/success/', {
            'tran_id': payment.gateway_tran_id, 'val_id': 'VAL123', 'status': 'VALID',
        })
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'rejected')

    @patch('payments.gateway.validate')
    def test_a_short_payment_is_refused(self, validate):
        """A forged callback would understate what was actually settled."""
        payment = self._pending_payment()
        validate.return_value = self.valid_response(payment, amount='1.00')

        self.client.post('/payments/gateway/success/', {
            'tran_id': payment.gateway_tran_id, 'val_id': 'VAL123',
        })
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'rejected')

    @patch('payments.gateway.validate')
    def test_a_mismatched_transaction_id_is_refused(self, validate):
        payment = self._pending_payment()
        validate.return_value = self.valid_response(payment, tran_id='SOMEONE-ELSE')

        self.client.post('/payments/gateway/success/', {
            'tran_id': payment.gateway_tran_id, 'val_id': 'VAL123',
        })
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'rejected')

    @patch('payments.gateway.validate')
    def test_a_foreign_currency_is_refused(self, validate):
        payment = self._pending_payment()
        validate.return_value = self.valid_response(payment, currency='USD')

        self.client.post('/payments/gateway/success/', {
            'tran_id': payment.gateway_tran_id, 'val_id': 'VAL123',
        })
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'rejected')

    @patch('payments.gateway.validate')
    def test_a_callback_for_an_unknown_transaction_credits_nothing(self, validate):
        self.client.post('/payments/gateway/success/', {
            'tran_id': 'MADE-UP', 'val_id': 'VAL123',
        })
        validate.assert_not_called()
        self.assertEqual(Payment.objects.filter(status='approved').count(), 0)

    @patch('payments.gateway.validate', side_effect=gateway.GatewayError('unreachable'))
    def test_an_unreachable_validator_leaves_the_payment_pending(self, validate):
        """Never approve on faith, and never reject a payment that may be real."""
        payment = self._pending_payment()
        self.client.post('/payments/gateway/success/', {
            'tran_id': payment.gateway_tran_id, 'val_id': 'VAL123',
        })
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'initiated')


class SettlementTests(GatewayTestCase):
    @patch('payments.gateway.create_session', return_value=('https://x', 'k'))
    def _pending_payment(self, mocked, amount='4000'):
        self.start(amount)
        return self.pending()

    @patch('payments.gateway.validate')
    def test_a_verified_payment_is_approved_and_priced(self, validate):
        payment = self._pending_payment()
        validate.return_value = self.valid_response(payment)

        self.client.post('/payments/gateway/success/', {
            'tran_id': payment.gateway_tran_id, 'val_id': 'VAL123',
        })
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'approved')
        self.assertEqual(payment.gateway_val_id, 'VAL123')
        self.assertEqual(payment.gateway_bank_tran_id, 'BANK123')
        # First month, so the 50% rule applies exactly as for a manual payment.
        self.assertEqual(payment.commission, Decimal('2000.00'))
        self.assertEqual(payment.tutor_income, Decimal('2000.00'))

    @patch('payments.gateway.validate')
    def test_settling_twice_does_not_double_count(self, validate):
        """The browser redirect and the IPN both arrive; only one may settle."""
        payment = self._pending_payment()
        validate.return_value = self.valid_response(payment)
        body = {'tran_id': payment.gateway_tran_id, 'val_id': 'VAL123'}

        self.client.post('/payments/gateway/success/', body)
        self.client.post('/payments/gateway/ipn/', body)

        payment.refresh_from_db()
        self.assertEqual(payment.status, 'approved')
        self.assertEqual(self.booking.total_paid, Decimal('4000.00'))
        self.assertEqual(self.booking.payments.count(), 1)

    @patch('payments.gateway.validate')
    def test_the_ipn_can_settle_a_payment_on_its_own(self, validate):
        """When the browser never comes back, the IPN is what saves the payment."""
        payment = self._pending_payment()
        validate.return_value = self.valid_response(payment)

        response = self.client.post('/payments/gateway/ipn/', {
            'tran_id': payment.gateway_tran_id, 'val_id': 'VAL123',
        })
        payment.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payment.status, 'approved')

    @patch('payments.gateway.create_session', return_value=('https://x', 'k'))
    def test_cancelling_removes_the_pending_payment(self, mocked):
        self.start('4000')
        payment = self.pending()
        self.client.post('/payments/gateway/cancel/', {'tran_id': payment.gateway_tran_id})
        self.assertEqual(self.booking.payments.count(), 0)

    @patch('payments.gateway.create_session', return_value=('https://x', 'k'))
    def test_a_failed_payment_is_marked_rejected(self, mocked):
        self.start('4000')
        payment = self.pending()
        self.client.post('/payments/gateway/fail/', {'tran_id': payment.gateway_tran_id})
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'rejected')


class ConfigurationTests(TestCase):
    @override_settings(SSLCOMMERZ_STORE_ID='', SSLCOMMERZ_STORE_PASSWORD='')
    def test_without_credentials_the_gateway_reports_itself_unavailable(self):
        self.assertFalse(gateway.is_configured())

    @override_settings(**GATEWAY_SETTINGS)
    def test_with_credentials_it_is_available(self):
        self.assertTrue(gateway.is_configured())

    @override_settings(SSLCOMMERZ_SANDBOX=True, **{k: v for k, v in GATEWAY_SETTINGS.items()
                                                   if k != 'SSLCOMMERZ_SANDBOX'})
    def test_sandbox_uses_the_sandbox_host(self):
        self.assertEqual(gateway.host(), gateway.SANDBOX_HOST)

    @override_settings(SSLCOMMERZ_SANDBOX=False, **{k: v for k, v in GATEWAY_SETTINGS.items()
                                                    if k != 'SSLCOMMERZ_SANDBOX'})
    def test_live_uses_the_live_host(self):
        self.assertEqual(gateway.host(), gateway.LIVE_HOST)

    @override_settings(SSLCOMMERZ_STORE_ID='', SSLCOMMERZ_STORE_PASSWORD='')
    def test_the_online_option_is_hidden_when_unconfigured(self):
        student = User.objects.create_user('s2', password='pw', role='student')
        tutor = User.objects.create_user('t2', password='pw', role='tutor')
        TutorProfile.objects.create(user=tutor, bio='')
        booking = Booking.objects.create(student=student, tutor=tutor, date='2026-01-01',
                                         months=1, agreed_monthly_rate=RATE, status='confirmed')
        self.client.force_login(student)
        html = self.client.get(f'/payments/create/?booking_id={booking.id}').content.decode()
        self.assertNotIn('Continue to secure checkout', html)


class AbandonedCheckoutTests(GatewayTestCase):
    """A customer who never finishes paying at SSLCommerz.

    Reported from live testing: reaching the demo card page and closing it
    without entering the OTP left a payment that the portal presented as
    confirmed and awaiting approval. Nothing was ever charged.

    The cause was that the payment row is created before the customer
    authenticates, with the same 'pending' status the manual flow uses to mean
    "a student says they paid, please check". One status, two meanings.
    """

    def _abandon(self):
        """Open a checkout and hear nothing back — no success, fail or IPN."""
        with patch('payments.gateway.create_session',
                   return_value=('https://sandbox.sslcommerz.com/x', 'k')):
            self.start('4000')
        return self.booking.payments.get()

    def test_an_abandoned_checkout_is_not_awaiting_review(self):
        payment = self._abandon()
        self.assertEqual(payment.status, 'initiated')
        self.assertFalse(payment.is_manually_approvable)
        self.assertEqual(Payment.objects.filter(status='pending').count(), 0)

    def test_an_abandoned_checkout_credits_nothing(self):
        self._abandon()
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.total_paid, 0)
        self.assertFalse(self.booking.is_fully_paid)

    def test_an_admin_cannot_approve_an_online_payment_by_hand(self):
        """The heart of it: this used to credit money that never arrived.

        An admin has no way to know whether the card was charged. Only
        SSLCommerz's validation API does, so only it may approve.
        """
        payment = self._abandon()
        admin = User.objects.create_superuser('gw_root', password='pw', email='r@x.com')
        staff = User.objects.create_user('gw_staff', password='pw', role='student',
                                         is_staff=True)

        for user in (admin, staff):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.client.post(f'/payments/approve/{payment.id}/')
                payment.refresh_from_db()
                self.booking.refresh_from_db()
                self.assertEqual(payment.status, 'initiated')
                self.assertEqual(self.booking.total_paid, 0)

    def test_the_admin_queue_offers_no_approve_button_for_one(self):
        payment = self._abandon()
        admin = User.objects.create_superuser('gw_root2', password='pw', email='r2@x.com')
        self.client.force_login(admin)
        html = self.client.get('/payments/manage/').content.decode()
        self.assertNotIn(f'/payments/approve/{payment.id}/', html)
        self.assertIn('Never completed', html)

    def test_the_student_is_not_locked_out_and_can_try_again(self):
        """The abandoned attempt must not block a fresh one."""
        first = self._abandon()
        with patch('payments.gateway.create_session',
                   return_value=('https://sandbox.sslcommerz.com/y', 'k')) as mocked:
            self.start('4000')
        self.assertTrue(mocked.called, 'a retry must reach the gateway')
        self.assertEqual(self.booking.payments.count(), 1,
                         'the abandoned attempt should be cleared, not stacked up')
        self.assertFalse(Payment.objects.filter(pk=first.pk).exists())

    def test_an_abandoned_checkout_still_lets_the_booking_be_cancelled(self):
        """No money is in flight, so it must not hold the booking hostage."""
        self._abandon()
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.is_cancellable)

    def test_a_genuine_settlement_is_unaffected(self):
        """The guard must not break the path that actually works."""
        payment = self._abandon()
        with patch('payments.gateway.validate') as validate:
            validate.return_value = self.valid_response(payment)
            self.client.post('/payments/gateway/success/', {
                'tran_id': payment.gateway_tran_id, 'val_id': 'VAL123',
            })
        payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(payment.status, 'approved')
        self.assertEqual(self.booking.total_paid, Decimal('4000.00'))


class ManualApprovalGuardTests(GatewayTestCase):
    """The manual flow must keep working exactly as before."""

    def _manual_payment(self, status='pending'):
        return Payment.objects.create(
            booking=self.booking, amount=4000, commission=2000,
            tutor_income=2000, payment_method='bkash',
            transaction_id='TX123', sender_number='01700000000', status=status,
        )

    def test_a_manual_payment_can_still_be_approved(self):
        payment = self._manual_payment()
        admin = User.objects.create_superuser('m_root', password='pw', email='m@x.com')
        self.client.force_login(admin)
        self.client.post(f'/payments/approve/{payment.id}/')
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'approved')

    def test_an_already_approved_payment_cannot_be_approved_again(self):
        payment = self._manual_payment(status='approved')
        admin = User.objects.create_superuser('m_root2', password='pw', email='m2@x.com')
        self.client.force_login(admin)
        self.client.post(f'/payments/approve/{payment.id}/')
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'approved')

    def test_an_approved_payment_cannot_be_rejected(self):
        """Rejecting a settled payment would erase money already credited."""
        payment = self._manual_payment(status='approved')
        admin = User.objects.create_superuser('m_root3', password='pw', email='m3@x.com')
        self.client.force_login(admin)
        self.client.post(f'/payments/reject/{payment.id}/')
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'approved')
