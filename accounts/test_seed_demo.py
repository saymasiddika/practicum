"""The demo seeder fills an empty database, is safe to re-run, and never
changes the password of an account that already exists."""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import User
from bookings.models import Booking
from payments.models import Payment
from reviews.models import Review
from tutors.models import Course, TutorProfile

PASSWORD = 'Demo!Pass2026x'
MODELS = (User, TutorProfile, Course, Booking, Payment, Review)


def seed(password=PASSWORD):
    call_command('seed_demo', password=password, stdout=StringIO())


class SeedDemoTests(TestCase):
    def test_an_empty_database_is_filled(self):
        seed()
        self.assertEqual(TutorProfile.objects.count(), 4)
        self.assertEqual(Course.objects.count(), 8)
        self.assertEqual(Booking.objects.count(), 5)
        self.assertEqual(Review.objects.count(), 2)
        self.assertEqual(Payment.objects.filter(status='pending').count(), 1)

    def test_running_it_twice_changes_nothing(self):
        seed()
        before = [model.objects.count() for model in MODELS]
        seed()
        self.assertEqual(before, [model.objects.count() for model in MODELS])

    def test_an_existing_accounts_password_is_left_alone(self):
        User.objects.create_user('Miraz', password='MyOwnPass!123', role='student')
        seed()
        self.assertTrue(User.objects.get(username='Miraz').check_password('MyOwnPass!123'))

    def test_new_demo_accounts_use_the_given_password(self):
        seed()
        self.assertTrue(User.objects.get(username='rahim').check_password(PASSWORD))

    def test_a_weak_password_is_refused_before_anything_is_written(self):
        with self.assertRaises(CommandError):
            seed(password='123')
        self.assertEqual(User.objects.count(), 0)

    def test_no_course_holds_two_live_bookings(self):
        seed()
        for course in Course.objects.all():
            with self.subTest(course=course.name):
                live = course.bookings.filter(status__in=('pending', 'confirmed')).count()
                self.assertLessEqual(live, 1)

    def test_approved_payments_carry_the_real_commission(self):
        seed()
        for booking in Booking.objects.all():
            paid = 0.0
            for payment in booking.payments.filter(status='approved').order_by('id'):
                commission, _income = Payment.calculate_commission(
                    float(payment.amount), float(booking.monthly_rate), paid)
                self.assertAlmostEqual(float(payment.commission), float(commission), places=2)
                paid += float(payment.amount)

    def test_completed_bookings_have_run_their_course(self):
        seed()
        for booking in Booking.objects.filter(status='completed'):
            with self.subTest(booking=booking.id):
                self.assertTrue(booking.has_run_its_course)

    def test_the_home_page_has_tutors_and_subjects_to_show(self):
        seed()
        html = self.client.get('/').content.decode()
        self.assertIn('/tutors/?subject=', html)
        self.assertNotIn('No tutors have joined yet', html)
