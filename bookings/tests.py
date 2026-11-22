"""Tests for the booking lifecycle and the guards on creating one.

Four separate defects turned out to be the same bug — a transition with no
guard on the current status — so every transition is pinned from every state
rather than only from the one the UI offers.
"""
from datetime import time

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from payments.models import Payment
from tutors.models import Course, TutorProfile

RATE = 4000


class BookingLifecycleTestCase(TestCase):
    """Shared cast: one student, one tutor with a profile."""

    def setUp(self):
        self.student = User.objects.create_user('student', password='pw', role='student')
        self.tutor = User.objects.create_user('tutor', password='pw', role='tutor')
        TutorProfile.objects.create(user=self.tutor, bio='Experienced')

    def booking(self, status='pending', months=1, student=None, tutor=None):
        return Booking.objects.create(
            student=student or self.student,
            tutor=tutor or self.tutor,
            date='2026-01-01', months=months,
            agreed_monthly_rate=RATE, status=status,
        )


class ConfirmAndRejectTests(BookingLifecycleTestCase):
    """Only a pending booking may be confirmed or rejected."""

    def test_tutor_confirms_a_pending_booking(self):
        booking = self.booking('pending')
        self.client.force_login(self.tutor)
        self.client.post(f'/bookings/{booking.id}/confirm/')
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'confirmed')

    def test_tutor_rejects_a_pending_booking(self):
        booking = self.booking('pending')
        self.client.force_login(self.tutor)
        self.client.post(f'/bookings/{booking.id}/reject/')
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'rejected')

    def test_closed_bookings_cannot_be_revived(self):
        self.client.force_login(self.tutor)
        for status in ('cancelled', 'rejected', 'completed'):
            with self.subTest(status=status):
                booking = self.booking(status)
                self.client.post(f'/bookings/{booking.id}/confirm/')
                booking.refresh_from_db()
                self.assertEqual(
                    booking.status, status,
                    f'{status} must not be reachable from confirm',
                )

    def test_only_the_assigned_tutor_may_confirm(self):
        booking = self.booking('pending')
        intruder = User.objects.create_user('other', password='pw', role='tutor')
        self.client.force_login(intruder)
        self.client.post(f'/bookings/{booking.id}/confirm/')
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'pending')

    def test_the_student_cannot_confirm_their_own_booking(self):
        booking = self.booking('pending')
        self.client.force_login(self.student)
        self.client.post(f'/bookings/{booking.id}/confirm/')
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'pending')


class CancellationTests(BookingLifecycleTestCase):
    """A student may cancel while the booking is live and unpaid."""

    def test_pending_booking_with_no_payment_can_be_cancelled(self):
        booking = self.booking('pending')
        self.client.force_login(self.student)
        self.client.post(f'/bookings/{booking.id}/cancel/')
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'cancelled')

    def test_a_rejected_payment_does_not_block_cancellation(self):
        """Otherwise the booking is stuck forever, still holding a slot."""
        booking = self.booking('pending')
        Payment.objects.create(
            booking=booking, amount=100, commission=0, tutor_income=0,
            status='rejected',
        )
        self.client.force_login(self.student)
        self.client.post(f'/bookings/{booking.id}/cancel/')
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'cancelled')

    def test_real_payments_block_cancellation(self):
        for status in ('pending', 'approved'):
            with self.subTest(payment=status):
                booking = self.booking('confirmed')
                Payment.objects.create(
                    booking=booking, amount=100, commission=0, tutor_income=0,
                    status=status,
                )
                self.client.force_login(self.student)
                self.client.post(f'/bookings/{booking.id}/cancel/')
                booking.refresh_from_db()
                self.assertEqual(booking.status, 'confirmed')

    def test_closed_bookings_cannot_be_cancelled(self):
        self.client.force_login(self.student)
        for status in ('completed', 'cancelled', 'rejected'):
            with self.subTest(status=status):
                booking = self.booking(status)
                self.client.post(f'/bookings/{booking.id}/cancel/')
                booking.refresh_from_db()
                self.assertEqual(booking.status, status)

    def test_the_tutor_cannot_cancel(self):
        booking = self.booking('pending')
        self.client.force_login(self.tutor)
        self.client.post(f'/bookings/{booking.id}/cancel/')
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'pending')

    def test_is_cancellable_matches_what_the_view_allows(self):
        """The template shows the button from this property, so they must agree."""
        cases = [
            ('pending', None, True),
            ('confirmed', None, True),
            ('pending', 'rejected', True),
            ('pending', 'pending', False),
            ('confirmed', 'approved', False),
            ('completed', None, False),
            ('cancelled', None, False),
        ]
        for status, payment_status, expected in cases:
            with self.subTest(status=status, payment=payment_status):
                booking = self.booking(status)
                if payment_status:
                    Payment.objects.create(
                        booking=booking, amount=100, commission=0,
                        tutor_income=0, status=payment_status,
                    )
                self.assertEqual(booking.is_cancellable, expected)


class CompletionTests(BookingLifecycleTestCase):
    """Completion is gated on the course period having elapsed."""

    def test_a_confirmed_booking_cannot_complete_early(self):
        booking = self.booking('confirmed')
        booking.date = timezone.localdate()
        booking.months = 6
        booking.save()
        self.client.force_login(self.tutor)
        self.client.post(f'/bookings/{booking.id}/complete/')
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'confirmed')

    def test_a_confirmed_booking_completes_once_the_period_has_passed(self):
        booking = self.booking('confirmed')
        booking.date = timezone.localdate().replace(year=2020)
        booking.save()
        self.client.force_login(self.tutor)
        self.client.post(f'/bookings/{booking.id}/complete/')
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'completed')

    def test_the_time_remaining_is_reported_truthfully(self):
        """Counting calendar months alone misreports both ends of the range.

        A course finishing in a few days used to be announced as a whole month
        remaining, and one finishing later in the current month as zero.
        """
        from datetime import timedelta
        today = timezone.localdate()
        self.client.force_login(self.tutor)

        cases = [
            (timedelta(days=4),  '4 days',   ('month',)),
            (timedelta(days=1),  '1 day',    ('month', 'days')),
            (timedelta(days=40), '1 month',  ()),
        ]
        for delta, expected, forbidden in cases:
            with self.subTest(expected=expected):
                booking = self.booking('confirmed')
                # months=0 so the end date is exactly the start date plus delta
                booking.months = 0
                booking.date = today + delta
                booking.save()

                response = self.client.post(f'/bookings/{booking.id}/complete/',
                                            follow=True)
                text = ' '.join(m.message for m in response.context['messages'])

                self.assertIn(expected, text)
                for word in forbidden:
                    self.assertNotIn(word, text)
                self.assertNotIn('0 month', text)

    def test_only_pending_or_confirmed_can_be_completed(self):
        self.client.force_login(self.tutor)
        for status in ('pending', 'cancelled', 'rejected'):
            with self.subTest(status=status):
                booking = self.booking(status)
                booking.date = timezone.localdate().replace(year=2020)
                booking.save()
                self.client.post(f'/bookings/{booking.id}/complete/')
                booking.refresh_from_db()
                self.assertEqual(booking.status, status)


class MutationsRequirePostTests(BookingLifecycleTestCase):
    """State changes must not happen on a GET — that is CSRF-reachable."""

    def test_get_is_rejected_and_changes_nothing(self):
        self.client.force_login(self.tutor)
        for verb in ('confirm', 'reject', 'complete'):
            with self.subTest(verb=verb):
                booking = self.booking('pending')
                response = self.client.get(f'/bookings/{booking.id}/{verb}/')
                booking.refresh_from_db()
                self.assertEqual(response.status_code, 405)
                self.assertEqual(booking.status, 'pending')

    def test_post_without_a_csrf_token_is_refused(self):
        booking = self.booking('pending')
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(self.tutor)
        response = client.post(f'/bookings/{booking.id}/confirm/')
        booking.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(booking.status, 'pending')


class BookingCreationTests(BookingLifecycleTestCase):
    """The POST body is untrusted, whatever the rendered form offered.

    A booking now names a COURSE, which carries its own price, slot and
    length — so the posted id is the one thing that has to be checked hard.
    """

    def setUp(self):
        super().setUp()
        self.course = Course.objects.create(
            tutor=self.tutor, subject='Maths', name='HSC Maths',
            days='0,2', time=time(10, 0), months=1, monthly_rate=RATE,
        )
        self.client.force_login(self.student)

    def _create(self, **overrides):
        data = {'course': self.course.id, 'months': '1', 'date': '2026-01-01'}
        data.update(overrides)
        return self.client.post('/bookings/create/', data)

    def test_a_valid_booking_is_created_and_snapshots_the_rate(self):
        self._create(months='3')
        booking = Booking.objects.get()
        self.assertEqual(booking.months, 3)
        self.assertEqual(booking.agreed_monthly_rate, RATE)
        self.assertEqual(booking.course, self.course)
        self.assertEqual(booking.tutor, self.tutor)
        self.assertEqual(booking.status, 'pending')

    def test_the_price_comes_from_the_course_not_the_form(self):
        """Posting a rate must not change what is charged."""
        self._create(agreed_monthly_rate='1', monthly_rate='1')
        self.assertEqual(Booking.objects.get().agreed_monthly_rate, RATE)

    def test_months_outside_the_allowed_range_is_refused(self):
        for months in ('0', '-5', '9999', 'abc'):
            with self.subTest(months=months):
                self._create(months=months)
                self.assertEqual(Booking.objects.count(), 0)

    def test_an_empty_months_falls_back_to_the_course_length(self):
        self.course.months = 4
        self.course.save()
        self._create(months='')
        self.assertEqual(Booking.objects.get().months, 4)

    def test_courses_from_hidden_tutors_cannot_be_booked(self):
        hidden = [
            User.objects.create_user('super', password='pw', role='tutor', is_superuser=True),
            User.objects.create_user('staff', password='pw', role='tutor', is_staff=True),
            User.objects.create_user('off', password='pw', role='tutor', is_active=False),
            User.objects.create_user('noprofile', password='pw', role='tutor'),
        ]
        for user in hidden:
            if user.username != 'noprofile':
                TutorProfile.objects.create(user=user, bio='')
            course = Course.objects.create(
                tutor=user, subject='X', name='Hidden', days='1',
                time=time(9, 0), months=1, monthly_rate=RATE,
            )
            with self.subTest(tutor=user.username):
                self._create(course=course.id)
                self.assertEqual(Booking.objects.count(), 0)

    def test_an_unpublished_course_cannot_be_booked(self):
        self.course.is_active = False
        self.course.save()
        self._create()
        self.assertEqual(Booking.objects.count(), 0)

    def test_a_course_that_does_not_exist_is_refused(self):
        for value in ('9999', 'abc', ''):
            with self.subTest(course=value):
                self._create(course=value)
                self.assertEqual(Booking.objects.count(), 0)

    def test_only_students_may_create_bookings(self):
        self.client.force_login(self.tutor)
        self._create()
        self.assertEqual(Booking.objects.count(), 0)

    def test_a_taken_course_cannot_be_double_booked(self):
        """One course, one slot, one student at a time."""
        self._create()
        self.assertEqual(Booking.objects.count(), 1)

        other = User.objects.create_user('student2', password='pw', role='student')
        self.client.force_login(other)
        self._create()
        self.assertEqual(
            Booking.objects.filter(
                course=self.course, status__in=['pending', 'confirmed']).count(),
            1, 'a course must not be held by two active bookings',
        )

    def test_a_cancelled_booking_frees_the_course_again(self):
        self._create()
        booking = Booking.objects.get()
        booking.status = 'cancelled'
        booking.save()

        other = User.objects.create_user('student3', password='pw', role='student')
        self.client.force_login(other)
        self._create()
        self.assertEqual(Booking.objects.filter(status='pending').count(), 1)


class BookingOrderingTests(BookingLifecycleTestCase):
    def test_bookings_come_back_newest_first(self):
        first = self.booking()
        second = self.booking()
        self.assertEqual(list(Booking.objects.all()), [second, first])
