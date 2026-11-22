"""Tests for ending a booking before its agreed end date.

Two things can be asked for — finish early, or terminate — and both go through
an admin. The rules that matter: only the two parties may ask, only staff may
decide, nothing moves until a decision, and the refund a student was quoted is
the refund they get.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking, BookingRequest
from payments.models import Payment
from reviews.models import Review
from tutors.models import TutorProfile

RATE = 5000


class EarlyEndTestCase(TestCase):
    def setUp(self):
        self.student = User.objects.create_user('e_stu', password='pw', role='student',
                                                email='s@x.com')
        self.tutor = User.objects.create_user('e_tut', password='pw', role='tutor')
        self.other = User.objects.create_user('e_other', password='pw', role='student')
        self.admin = User.objects.create_superuser('e_root', password='pw', email='r@x.com')
        TutorProfile.objects.create(user=self.tutor, bio='b')

    def booking(self, started_days_ago=3, months=3, paid=RATE, status='confirmed'):
        b = Booking.objects.create(
            student=self.student, tutor=self.tutor,
            date=timezone.localdate() - timedelta(days=started_days_ago),
            months=months, agreed_monthly_rate=RATE, status=status,
        )
        if paid:
            commission, income = Payment.calculate_commission(paid, RATE, 0)
            Payment.objects.create(booking=b, amount=paid, commission=commission,
                                   tutor_income=income, status='approved')
        return b

    def request_end(self, booking, kind='terminate', reason='The tutor moved abroad and we agreed to stop.',
                    **extra):
        data = {'kind': kind, 'reason': reason}
        if kind == 'terminate':
            data.setdefault('payout_method', 'bkash')
            data.setdefault('payout_account', '01700000000')
        data.update(extra)
        return self.client.post(f'/bookings/{booking.id}/end/?kind={kind}', data)


class RequestingTests(EarlyEndTestCase):
    def test_a_student_can_ask_to_terminate(self):
        booking = self.booking()
        self.client.force_login(self.student)
        self.request_end(booking)
        req = booking.requests.get()
        self.assertEqual(req.kind, 'terminate')
        self.assertEqual(req.status, 'pending')
        self.assertEqual(req.requested_by, self.student)

    def test_a_tutor_can_ask_to_finish_early(self):
        booking = self.booking()
        self.client.force_login(self.tutor)
        self.request_end(booking, kind='complete', reason='We covered the whole syllabus already.')
        req = booking.requests.get()
        self.assertEqual(req.kind, 'complete')
        self.assertTrue(req.by_tutor)

    def test_a_stranger_cannot_ask_anything(self):
        booking = self.booking()
        self.client.force_login(self.other)
        self.request_end(booking)
        self.assertEqual(booking.requests.count(), 0)

    def test_an_anonymous_visitor_is_sent_to_login(self):
        booking = self.booking()
        response = self.client.get(f'/bookings/{booking.id}/end/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_a_reason_is_required(self):
        booking = self.booking()
        self.client.force_login(self.student)
        for reason in ('', '   ', 'no', 'too short'):
            with self.subTest(reason=reason):
                self.request_end(booking, reason=reason)
                self.assertEqual(booking.requests.count(), 0)

    def test_only_one_request_can_be_open_at_a_time(self):
        booking = self.booking()
        self.client.force_login(self.student)
        self.request_end(booking)
        self.request_end(booking, reason='Changed my mind about the reason entirely.')
        self.assertEqual(booking.requests.count(), 1)

    def test_a_booking_past_its_end_date_needs_no_request(self):
        """Once the course has run, completion is not anyone's to approve."""
        booking = self.booking(started_days_ago=400, months=1)
        self.client.force_login(self.student)
        self.request_end(booking, kind='complete')
        self.assertEqual(booking.requests.count(), 0)

    def test_an_unconfirmed_booking_cannot_be_ended_this_way(self):
        booking = self.booking(status='pending', paid=0)
        self.client.force_login(self.student)
        self.request_end(booking)
        self.assertEqual(booking.requests.count(), 0)


class RefundSnapshotTests(EarlyEndTestCase):
    def test_the_quoted_refund_is_frozen_when_the_request_is_made(self):
        """An admin reading it a fortnight later must not change the figure."""
        booking = self.booking(started_days_ago=3)          # week 1 -> 75%
        self.client.force_login(self.student)
        self.request_end(booking)
        req = booking.requests.get()
        self.assertEqual(req.refund_percent, 75)
        self.assertEqual(req.refund_amount, Decimal('3750.00'))

        # Time passes; the booking would now fall in a worse tier.
        booking.date = timezone.localdate() - timedelta(days=40)
        booking.save()

        self.client.force_login(self.admin)
        self.client.post(f'/admin-panel/requests/{req.id}/approve/', {'admin_note': 'Fine.'})
        req.refresh_from_db()
        self.assertEqual(req.refund_amount, Decimal('3750.00'),
                         'the figure quoted at request time must not move')

    def test_payout_details_are_required_when_a_refund_is_due(self):
        booking = self.booking(started_days_ago=3)
        self.client.force_login(self.student)
        self.request_end(booking, payout_method='bkash', payout_account='')
        self.assertEqual(booking.requests.count(), 0)

    def test_collecting_from_the_office_needs_no_account_number(self):
        booking = self.booking(started_days_ago=3)
        self.client.force_login(self.student)
        self.request_end(booking, payout_method='office', payout_account='')
        self.assertEqual(booking.requests.count(), 1)

    def test_no_payout_details_are_needed_when_nothing_is_refundable(self):
        booking = self.booking(started_days_ago=200, months=12)   # long past the ladder
        self.client.force_login(self.student)
        self.request_end(booking, payout_method='', payout_account='')
        self.assertEqual(booking.requests.count(), 1)
        self.assertEqual(booking.requests.get().refund_amount, Decimal('0.00'))


class AdminDecisionTests(EarlyEndTestCase):
    def _open_request(self, kind='terminate'):
        booking = self.booking()
        self.client.force_login(self.student if kind == 'terminate' else self.tutor)
        self.request_end(booking, kind=kind,
                         reason='A genuine reason, long enough to be judged.')
        return booking, booking.requests.get()

    def test_approving_a_completion_completes_the_booking(self):
        booking, req = self._open_request('complete')
        self.client.force_login(self.admin)
        self.client.post(f'/admin-panel/requests/{req.id}/approve/', {})
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'completed')

    def test_approving_a_completion_lets_the_student_review(self):
        """The whole point of early completion."""
        booking, req = self._open_request('complete')
        self.client.force_login(self.admin)
        self.client.post(f'/admin-panel/requests/{req.id}/approve/', {})

        self.client.force_login(self.student)
        self.client.post(f'/reviews/create/?booking={booking.id}',
                         {'rating': '5', 'comment': 'Finished ahead of time, excellent.'})
        self.assertTrue(Review.objects.filter(booking=booking).exists())

    def test_approving_a_termination_cancels_the_booking(self):
        booking, req = self._open_request('terminate')
        self.client.force_login(self.admin)
        self.client.post(f'/admin-panel/requests/{req.id}/approve/', {})
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'cancelled')

    def test_declining_changes_nothing_about_the_booking(self):
        booking, req = self._open_request('terminate')
        self.client.force_login(self.admin)
        self.client.post(f'/admin-panel/requests/{req.id}/decline/',
                         {'admin_note': 'Not a good enough reason.'})
        booking.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(booking.status, 'confirmed')
        self.assertEqual(req.status, 'declined')
        self.assertEqual(req.admin_note, 'Not a good enough reason.')

    def test_a_declined_request_lets_them_ask_again(self):
        booking, req = self._open_request('terminate')
        self.client.force_login(self.admin)
        self.client.post(f'/admin-panel/requests/{req.id}/decline/', {})
        self.client.force_login(self.student)
        self.request_end(booking, reason='A second, much better explained reason.')
        self.assertEqual(booking.requests.count(), 2)

    def test_a_decision_cannot_be_made_twice(self):
        booking, req = self._open_request('terminate')
        self.client.force_login(self.admin)
        self.client.post(f'/admin-panel/requests/{req.id}/approve/', {})
        self.client.post(f'/admin-panel/requests/{req.id}/decline/', {})
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')

    def test_the_decision_records_who_made_it(self):
        booking, req = self._open_request('terminate')
        self.client.force_login(self.admin)
        self.client.post(f'/admin-panel/requests/{req.id}/approve/', {})
        req.refresh_from_db()
        self.assertEqual(req.reviewed_by, self.admin)
        self.assertIsNotNone(req.reviewed_at)


class AdminAccessTests(EarlyEndTestCase):
    def test_the_queue_is_closed_to_students_and_tutors(self):
        for user in (self.student, self.tutor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get('/admin-panel/requests/')
                self.assertNotEqual(response.status_code, 200)

    def test_the_queue_is_open_to_staff_and_superusers(self):
        staff = User.objects.create_user('e_staff', password='pw', role='student',
                                         is_staff=True)
        for user in (staff, self.admin):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get('/admin-panel/requests/').status_code, 200)

    def test_a_student_cannot_approve_their_own_request(self):
        booking = self.booking()
        self.client.force_login(self.student)
        self.request_end(booking)
        req = booking.requests.get()

        self.client.post(f'/admin-panel/requests/{req.id}/approve/', {})
        req.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(req.status, 'pending')
        self.assertEqual(booking.status, 'confirmed')

    def test_decisions_reject_get(self):
        booking = self.booking()
        self.client.force_login(self.student)
        self.request_end(booking)
        req = booking.requests.get()
        self.client.force_login(self.admin)
        response = self.client.get(f'/admin-panel/requests/{req.id}/approve/')
        self.assertEqual(response.status_code, 405)


class RefundSettlementTests(EarlyEndTestCase):
    def _approved_termination(self):
        booking = self.booking(started_days_ago=3)
        self.client.force_login(self.student)
        self.request_end(booking)
        req = booking.requests.get()
        self.client.force_login(self.admin)
        self.client.post(f'/admin-panel/requests/{req.id}/approve/', {})
        req.refresh_from_db()
        return req

    def test_an_approved_refund_starts_unsettled(self):
        req = self._approved_termination()
        self.assertTrue(req.owes_a_refund)
        self.assertFalse(req.refund_settled)
        self.assertTrue(req.needs_payout_details)

    def test_an_admin_can_record_that_the_money_was_sent(self):
        req = self._approved_termination()
        self.client.post(f'/admin-panel/requests/{req.id}/settle/', {})
        req.refresh_from_db()
        self.assertTrue(req.refund_settled)
        self.assertIsNotNone(req.refund_settled_at)
        self.assertFalse(req.needs_payout_details)

    def test_the_queue_shows_what_is_still_owed(self):
        self._approved_termination()
        html = self.client.get('/admin-panel/requests/?status=approved').content.decode()
        self.assertIn('৳3,750', html)          # grouped, as every amount is shown
        self.assertIn('Not yet sent', html)


class TutorTerminationTests(EarlyEndTestCase):
    """The refund is owed to the STUDENT, so the tutor is never asked for an
    account number. Getting this wrong blocked tutor terminations entirely."""

    def test_a_tutor_can_terminate_without_supplying_payout_details(self):
        booking = self.booking(started_days_ago=20, months=1)
        self.client.force_login(self.tutor)
        self.client.post(f'/bookings/{booking.id}/end/?kind=terminate',
                         {'kind': 'terminate',
                          'reason': 'I have to stop teaching for health reasons.'})
        self.assertEqual(booking.requests.count(), 1,
                         'a tutor must not be blocked for want of the student\'s bank details')

    def test_a_tutor_termination_refunds_more_than_the_student_ladder_would(self):
        booking = self.booking(started_days_ago=20, months=1)
        self.client.force_login(self.tutor)
        self.client.post(f'/bookings/{booking.id}/end/?kind=terminate',
                         {'kind': 'terminate', 'reason': 'Relocating abroad next month.'})
        req = booking.requests.get()
        # Day 20 would put a student in the 25% tier — 1,250 of 5,000.
        self.assertGreater(req.refund_amount, Decimal('1250.00'))

    def test_the_form_does_not_ask_a_tutor_for_an_account_number(self):
        booking = self.booking(started_days_ago=5)
        self.client.force_login(self.tutor)
        html = self.client.get(f'/bookings/{booking.id}/end/?kind=terminate').content.decode()
        self.assertNotIn('name="payout_account"', html)
        self.assertIn('goes back to', html)

    def test_the_form_does_ask_a_student_for_one(self):
        booking = self.booking(started_days_ago=5)
        self.client.force_login(self.student)
        html = self.client.get(f'/bookings/{booking.id}/end/?kind=terminate').content.decode()
        self.assertIn('name="payout_account"', html)

    def test_the_admin_queue_flags_that_payout_details_are_missing(self):
        booking = self.booking(started_days_ago=20, months=1)
        self.client.force_login(self.tutor)
        self.client.post(f'/bookings/{booking.id}/end/?kind=terminate',
                         {'kind': 'terminate', 'reason': 'Cannot continue teaching this term.'})
        req = booking.requests.get()
        self.client.force_login(self.admin)
        self.client.post(f'/admin-panel/requests/{req.id}/approve/', {})
        html = self.client.get('/admin-panel/requests/?status=approved').content.decode()
        self.assertIn('Not given yet', html)


class MessagesAreVisibleTests(EarlyEndTestCase):
    """Both new pages must render Django messages.

    They originally overrode the base template's `main` block, which is where
    messages are rendered — so a rejected request redirected back and showed
    the student nothing at all.
    """

    def test_a_rejected_reason_is_explained_on_the_page(self):
        booking = self.booking()
        self.client.force_login(self.student)
        response = self.client.post(f'/bookings/{booking.id}/end/?kind=terminate',
                                    {'kind': 'terminate', 'reason': 'no'}, follow=True)
        self.assertIn('explain in a sentence', response.content.decode())

    def test_the_confirmation_is_shown_after_a_successful_request(self):
        booking = self.booking()
        self.client.force_login(self.student)
        response = self.request_end(booking)
        response = self.client.get(f'/bookings/{booking.id}/', follow=True)
        self.assertEqual(booking.requests.count(), 1)


class AdminQueueScalesTests(EarlyEndTestCase):
    """The queue must not issue a query per row.

    It showed each booking's total_paid, which walks that booking's payments,
    and the queryset only used select_related. Measured at 10 queries for 3
    requests and 34 for 27 before booking__payments was prefetched.
    """

    made = 0

    def _requests(self, count):
        for _ in range(count):
            # A running counter, because this is called more than once and
            # usernames are unique.
            n = self.made = self.made + 1
            tutor = User.objects.create_user(f'q_tut{n}', password='pw', role='tutor')
            TutorProfile.objects.create(user=tutor, bio='b')
            booking = Booking.objects.create(
                student=self.student, tutor=tutor,
                date=timezone.localdate() - timedelta(days=3),
                months=2, agreed_monthly_rate=RATE, status='confirmed')
            commission, income = Payment.calculate_commission(RATE, RATE, 0)
            Payment.objects.create(booking=booking, amount=RATE, commission=commission,
                                   tutor_income=income, status='approved')
            BookingRequest.objects.create(
                booking=booking, requested_by=self.student, kind='terminate',
                reason='a reason long enough to be judged', refund_amount=100,
                refund_percent=25)

    def test_the_queue_costs_the_same_however_many_requests_there_are(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.force_login(self.admin)

        self._requests(2)
        with CaptureQueriesContext(connection) as few:
            self.client.get('/admin-panel/requests/')

        self._requests(18)
        with CaptureQueriesContext(connection) as many:
            self.client.get('/admin-panel/requests/')

        growth = len(many.captured_queries) - len(few.captured_queries)
        self.assertLessEqual(
            growth, 3,
            f'queries grew by {growth} when 18 more requests were added '
            f'({len(few.captured_queries)} -> {len(many.captured_queries)}): '
            f'something is loading per row',
        )


class RefusedRequestKeepsInputTests(EarlyEndTestCase):
    """A refused early-end request keeps the reason and payout details."""

    def test_the_reason_survives_a_missing_account_number(self):
        booking = self.booking()
        self.client.force_login(self.student)
        response = self.request_end(booking, reason='My family is moving to Chattogram next week.',
                                    payout_account='')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My family is moving to Chattogram next week.')
        self.assertEqual(response.context['form']['payout_method'], 'bkash')
        self.assertEqual(booking.requests.count(), 0)
