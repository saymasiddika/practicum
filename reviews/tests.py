"""Tests for review eligibility and rating validation.

`Review.rating` declares choices of 1-5, but Django does not enforce choices on
save(), and the stored value feeds the tutor's public rating — which in turn
orders the tutor list. So the bound has to be tested at the view.
"""
from django.test import TestCase

from accounts.models import User
from bookings.models import Booking
from reviews.models import Review
from tutors.models import TutorProfile

RATE = 4000


class ReviewTestCase(TestCase):
    def setUp(self):
        self.student = User.objects.create_user('student', password='pw', role='student')
        self.tutor = User.objects.create_user('tutor', password='pw', role='tutor')
        self.profile = TutorProfile.objects.create(user=self.tutor, bio='')
        self.client.force_login(self.student)

    def booking(self, status='completed'):
        return Booking.objects.create(
            student=self.student, tutor=self.tutor, date='2026-01-01',
            months=1, agreed_monthly_rate=RATE, status=status,
        )

    def review(self, booking, rating='5', comment='Very helpful'):
        return self.client.post(
            f'/reviews/create/?booking={booking.id}',
            {'rating': rating, 'comment': comment},
        )


class ReviewEligibilityTests(ReviewTestCase):
    def test_a_completed_booking_can_be_reviewed(self):
        booking = self.booking('completed')
        self.review(booking)
        self.assertTrue(Review.objects.filter(booking=booking).exists())

    def test_an_unfinished_booking_cannot_be_reviewed(self):
        """Otherwise the course-duration gate can simply be walked around."""
        for status in ('pending', 'confirmed', 'rejected', 'cancelled'):
            with self.subTest(status=status):
                booking = self.booking(status)
                self.review(booking)
                self.assertFalse(
                    Review.objects.filter(booking=booking).exists(),
                    f'a {status} booking must not be reviewable',
                )

    def test_only_the_student_may_review(self):
        booking = self.booking()
        self.client.force_login(self.tutor)
        self.review(booking)
        self.assertFalse(Review.objects.filter(booking=booking).exists())

    def test_a_booking_can_only_be_reviewed_once(self):
        booking = self.booking()
        self.review(booking, rating='5')
        self.review(booking, rating='1')
        self.assertEqual(Review.objects.filter(booking=booking).count(), 1)


class RatingValidationTests(ReviewTestCase):
    def test_ratings_outside_one_to_five_are_refused(self):
        for rating in ('0', '6', '100', '-3'):
            with self.subTest(rating=rating):
                booking = self.booking()
                self.review(booking, rating=rating)
                self.assertFalse(Review.objects.filter(booking=booking).exists())

    def test_a_non_numeric_rating_does_not_raise(self):
        booking = self.booking()
        response = self.review(booking, rating='abc')
        self.assertEqual(response.status_code, 200)   # the form, filled in
        self.assertFalse(Review.objects.filter(booking=booking).exists())

    def test_every_valid_rating_is_accepted(self):
        for rating in range(1, 6):
            with self.subTest(rating=rating):
                booking = self.booking()
                self.review(booking, rating=str(rating))
                self.assertEqual(Review.objects.get(booking=booking).rating, rating)

    def test_an_empty_comment_is_refused(self):
        booking = self.booking()
        self.review(booking, comment='   ')
        self.assertFalse(Review.objects.filter(booking=booking).exists())


class TutorRatingTests(ReviewTestCase):
    def test_the_tutor_rating_is_the_average_of_their_reviews(self):
        for rating in (5, 4):
            self.review(self.booking(), rating=str(rating))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.rating, 4.5)

    def test_a_rejected_rating_cannot_move_the_tutor_score(self):
        self.review(self.booking(), rating='100')
        self.profile.refresh_from_db()
        self.assertEqual(
            self.profile.rating, 0,
            'an out-of-range rating must never reach the public score',
        )

    def test_reviewing_a_tutor_without_a_profile_does_not_crash(self):
        bare = User.objects.create_user('bare', password='pw', role='tutor')
        booking = Booking.objects.create(
            student=self.student, tutor=bare, date='2026-01-01', months=1,
            agreed_monthly_rate=RATE, status='completed',
        )
        response = self.review(booking)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Review.objects.filter(booking=booking).exists())


class RefusedReviewKeepsInputTests(ReviewTestCase):
    """A refused review comes back filled in, so nothing has to be retyped."""

    def test_the_comment_survives_a_bad_rating(self):
        booking = self.booking()
        response = self.review(booking, rating='abc', comment='Explained vectors really clearly')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Explained vectors really clearly')
        self.assertFalse(Review.objects.filter(booking=booking).exists())

    def test_the_chosen_rating_survives_an_empty_comment(self):
        booking = self.booking()
        response = self.review(booking, rating='4', comment='   ')
        self.assertContains(response, '<option value="4" selected>')
