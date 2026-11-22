"""Tests for tutor discovery and course management.

The listing filters exist for a reason — admin and staff accounts carry
role='tutor' in this data set, so a search that bypasses the exclusions
exposes them as bookable tutors.

Subject and price live on Course now, so searching by subject means searching
what a tutor teaches rather than one field on their profile.
"""
from datetime import time

from django.test import TestCase

from accounts.models import Education, User
from tutors.models import Course, TutorProfile

RATE = 4000


class TutorListingTests(TestCase):
    def setUp(self):
        self.visible = User.objects.create_user('mathstutor', password='pw', role='tutor')
        TutorProfile.objects.create(user=self.visible, bio='Algebra and calculus')
        self.course = Course.objects.create(
            tutor=self.visible, subject='Mathematics', name='HSC Higher Maths',
            days='0,2', time=time(10, 0), months=3, monthly_rate=RATE,
        )
        # Everything below must stay out of the listing.
        self.admin = User.objects.create_superuser('admin', password='pw', role='tutor')
        TutorProfile.objects.create(user=self.admin, bio='')
        self.staff = User.objects.create_user('staffer', password='pw', role='tutor', is_staff=True)
        TutorProfile.objects.create(user=self.staff, bio='')
        self.inactive = User.objects.create_user('dormant', password='pw', role='tutor', is_active=False)
        TutorProfile.objects.create(user=self.inactive, bio='')
        self.no_profile = User.objects.create_user('profileless', password='pw', role='tutor')

    def listed(self, **params):
        response = self.client.get('/tutors/', params)
        return list(response.context['tutors'])

    def test_only_bookable_tutors_are_listed(self):
        self.assertEqual(self.listed(), [self.visible])

    def test_searching_does_not_bypass_the_exclusions(self):
        """The bug: an OR-combined queryset dropped the staff filters."""
        for term in ('admin', 'staffer', 'Mathematics', 'dormant'):
            with self.subTest(search=term):
                for tutor in self.listed(search=term):
                    self.assertNotIn(
                        tutor, [self.admin, self.staff, self.inactive],
                        f'searching {term!r} exposed a hidden account',
                    )

    def test_search_matches_name_subject_and_course_name(self):
        self.assertIn(self.visible, self.listed(search='mathstutor'))
        self.assertIn(self.visible, self.listed(search='Mathematics'))
        self.assertIn(self.visible, self.listed(search='HSC'))

    def test_a_tutor_appears_once_however_many_courses_match(self):
        """Joining to courses repeats a row per match without distinct()."""
        Course.objects.create(tutor=self.visible, subject='Mathematics',
                              name='SSC Maths', days='1', time=time(9, 0),
                              months=2, monthly_rate=RATE)
        self.assertEqual(self.listed(search='Mathematics').count(self.visible), 1)

    def test_the_subject_filter_matches_a_course(self):
        self.assertIn(self.visible, self.listed(subject='Mathematics'))
        self.assertNotIn(self.visible, self.listed(subject='Chemistry'))

    def test_an_unparseable_rating_filter_does_not_crash(self):
        for value in ('abc', '', '1e9', '-1'):
            with self.subTest(rating=value):
                self.assertEqual(self.client.get('/tutors/', {'rating': value}).status_code, 200)

    def test_the_rating_filter_works(self):
        self.visible.tutor_profile.rating = 4.5
        self.visible.tutor_profile.save()
        self.assertIn(self.visible, self.listed(rating='4'))
        self.assertNotIn(self.visible, self.listed(rating='5'))

    def test_the_detail_page_is_addressed_by_user_id(self):
        self.assertEqual(self.client.get(f'/tutors/{self.visible.id}/').status_code, 200)

    def test_a_tutor_without_a_profile_has_no_detail_page(self):
        self.assertEqual(self.client.get(f'/tutors/{self.no_profile.id}/').status_code, 404)

    def test_the_detail_page_lists_the_courses(self):
        html = self.client.get(f'/tutors/{self.visible.id}/').content.decode()
        self.assertIn('HSC Higher Maths', html)
        self.assertIn('Mathematics', html)
        self.assertIn('4,000', html)                # the monthly rate
        self.assertIn('12,000', html)               # 3 months in total
        self.assertIn('Sun, Tue at 10:00 AM', html)


class CourseManagementTests(TestCase):
    """A tutor publishes what they teach, one course at a time."""

    def setUp(self):
        self.tutor = User.objects.create_user('tutor', password='pw', role='tutor')
        TutorProfile.objects.create(user=self.tutor, bio='I teach.')
        self.client.force_login(self.tutor)

    def add(self, **overrides):
        data = {'subject': 'Physics', 'name': 'HSC Physics',
                'days': ['0', '2'], 'time': '10:00',
                'months': '3', 'monthly_rate': '5000'}
        data.update(overrides)
        return self.client.post('/tutors/courses/add/', data)

    def test_a_valid_course_is_published(self):
        self.add()
        course = Course.objects.get()
        self.assertEqual(course.subject, 'Physics')
        self.assertEqual(course.name, 'HSC Physics')
        self.assertEqual(course.days, '0,2')
        self.assertEqual(course.months, 3)
        self.assertEqual(course.monthly_rate, 5000)
        self.assertTrue(course.is_active)

    def test_a_tutor_may_publish_several_courses_at_different_prices(self):
        """The whole point of the change."""
        self.add(subject='Physics', name='HSC Physics', monthly_rate='5000')
        self.add(subject='Chemistry', name='SSC Chemistry', monthly_rate='3500',
                 time='14:00')
        self.assertEqual(Course.objects.count(), 2)
        self.assertEqual(self.tutor.tutor_profile.lowest_rate, 3500)
        self.assertEqual(sorted(self.tutor.tutor_profile.subjects),
                         ['Chemistry', 'Physics'])

    def test_a_course_needs_a_subject_and_a_name(self):
        for field in ('subject', 'name'):
            with self.subTest(missing=field):
                self.add(**{field: ''})
                self.assertEqual(Course.objects.count(), 0)

    def test_duration_outside_one_to_twentyfour_is_refused(self):
        for months in ('0', '999', '-4', 'abc', ''):
            with self.subTest(months=months):
                self.add(months=months)
                self.assertEqual(Course.objects.count(), 0)

    def test_the_fee_must_be_a_positive_number(self):
        for rate in ('0', '-100', 'abc', ''):
            with self.subTest(rate=rate):
                self.add(monthly_rate=rate)
                self.assertEqual(Course.objects.count(), 0)

    def test_invalid_day_codes_are_discarded(self):
        self.add(days=['9', '0'])
        self.assertEqual(Course.objects.get().days, '0')

    def test_a_course_needs_at_least_one_day(self):
        self.add(days=[])
        self.assertEqual(Course.objects.count(), 0)

    def test_students_cannot_add_courses(self):
        student = User.objects.create_user('student', password='pw', role='student')
        self.client.force_login(student)
        self.add()
        self.assertEqual(Course.objects.count(), 0)

    def test_a_tutor_without_a_profile_is_sent_to_make_one(self):
        """A course is unreachable without a profile — the listing hides it."""
        fresh = User.objects.create_user('fresh', password='pw', role='tutor')
        self.client.force_login(fresh)
        response = self.add()
        self.assertEqual(Course.objects.count(), 0)
        self.assertIn('/tutors/create-profile/', response['Location'])

    def test_a_tutor_can_edit_their_own_course(self):
        self.add()
        course = Course.objects.get()
        self.client.post(f'/tutors/courses/{course.id}/edit/', {
            'subject': 'Physics', 'name': 'Renamed', 'days': ['1'],
            'time': '11:00', 'months': '2', 'monthly_rate': '6000',
            'is_active': 'on',
        })
        course.refresh_from_db()
        self.assertEqual(course.name, 'Renamed')
        self.assertEqual(course.monthly_rate, 6000)

    def test_a_tutor_cannot_touch_another_tutors_course(self):
        other = User.objects.create_user('other', password='pw', role='tutor')
        TutorProfile.objects.create(user=other, bio='')
        course = Course.objects.create(tutor=other, subject='X', name='Theirs',
                                       days='1', time=time(9, 0), months=1,
                                       monthly_rate=1000)
        for url in (f'/tutors/courses/{course.id}/edit/',
                    f'/tutors/courses/{course.id}/delete/',
                    f'/tutors/courses/{course.id}/toggle/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {}).status_code, 404)
        self.assertTrue(Course.objects.filter(pk=course.pk).exists())

    def test_a_course_can_be_unpublished_and_published_again(self):
        self.add()
        course = Course.objects.get()
        self.client.post(f'/tutors/courses/{course.id}/toggle/')
        course.refresh_from_db()
        self.assertFalse(course.is_active)
        self.client.post(f'/tutors/courses/{course.id}/toggle/')
        course.refresh_from_db()
        self.assertTrue(course.is_active)

    def test_an_unbooked_course_is_deleted_outright(self):
        self.add()
        course = Course.objects.get()
        self.client.post(f'/tutors/courses/{course.id}/delete/')
        self.assertEqual(Course.objects.count(), 0)

    def test_a_booked_course_is_unpublished_rather_than_deleted(self):
        """Deleting it would orphan a student's booking."""
        from bookings.models import Booking
        self.add()
        course = Course.objects.get()
        student = User.objects.create_user('stu', password='pw', role='student')
        Booking.objects.create(student=student, tutor=self.tutor, course=course,
                               date='2026-01-01', months=1,
                               agreed_monthly_rate=5000, status='confirmed')

        self.client.post(f'/tutors/courses/{course.id}/delete/')
        course.refresh_from_db()
        self.assertTrue(Course.objects.filter(pk=course.pk).exists())
        self.assertFalse(course.is_active)

    def test_the_course_list_page_shows_them(self):
        self.add()
        html = self.client.get('/tutors/courses/').content.decode()
        self.assertIn('HSC Physics', html)
        self.assertIn('Add a course', html)


class CourseBookabilityTests(TestCase):
    """One course holds one student, because one hour holds one lesson."""

    def setUp(self):
        self.tutor = User.objects.create_user('c_tut', password='pw', role='tutor')
        TutorProfile.objects.create(user=self.tutor, bio='')
        self.student = User.objects.create_user('c_stu', password='pw', role='student')
        self.course = Course.objects.create(
            tutor=self.tutor, subject='Physics', name='HSC Physics',
            days='0', time=time(10, 0), months=1, monthly_rate=RATE,
        )

    def _book(self, status):
        from bookings.models import Booking
        return Booking.objects.create(
            student=self.student, tutor=self.tutor, course=self.course,
            date='2026-01-01', months=1, agreed_monthly_rate=RATE, status=status,
        )

    def test_a_fresh_course_is_bookable(self):
        self.assertTrue(self.course.is_bookable)

    def test_a_live_booking_makes_it_unbookable(self):
        for status in ('pending', 'confirmed'):
            with self.subTest(status=status):
                booking = self._book(status)
                self.assertFalse(self.course.is_bookable)
                booking.delete()

    def test_a_closed_booking_frees_it_again(self):
        for status in ('completed', 'cancelled', 'rejected'):
            with self.subTest(status=status):
                booking = self._book(status)
                self.assertTrue(self.course.is_bookable)
                booking.delete()

    def test_an_unpublished_course_is_never_bookable(self):
        self.course.is_active = False
        self.course.save()
        self.assertFalse(self.course.is_bookable)

    def test_the_profile_reports_whether_anything_can_be_booked(self):
        self.assertTrue(self.tutor.tutor_profile.is_bookable)
        self.course.is_active = False
        self.course.save()
        self.assertFalse(self.tutor.tutor_profile.is_bookable)

    def test_the_schedule_reads_as_english(self):
        self.assertIn('Sun', self.course.schedule_display)
        self.assertIn('10:00 AM', self.course.schedule_display)

    def test_the_total_price_is_the_rate_times_the_months(self):
        self.assertEqual(self.course.total_price, RATE)
        self.course.months = 3
        self.assertEqual(self.course.total_price, RATE * 3)


class RefusedCourseFormKeepsInputTests(TestCase):
    """A refused course form comes back with what the tutor typed."""

    def setUp(self):
        self.tutor = User.objects.create_user('k_tut', password='pw', role='tutor')
        TutorProfile.objects.create(user=self.tutor, bio='b')
        self.client.force_login(self.tutor)

    def test_a_refused_new_course_comes_back_filled_in(self):
        response = self.client.post('/tutors/courses/add/', {
            'subject': 'Chemistry', 'name': 'SSC Chemistry Crash Course',
            'days': ['1', '3'], 'time': '19:30', 'months': '2', 'monthly_rate': '0',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="SSC Chemistry Crash Course"')
        self.assertContains(response, 'value="19:30"')
        self.assertEqual(response.context['selected_days'], ['1', '3'])
        self.assertEqual(Course.objects.count(), 0)

    def test_a_refused_edit_shows_the_new_values_and_saves_nothing(self):
        course = Course.objects.create(tutor=self.tutor, subject='Physics', name='Old name',
                                       days='0', time=time(9, 0), months=1, monthly_rate=1000)
        response = self.client.post(f'/tutors/courses/{course.id}/edit/', {
            'subject': 'Physics', 'name': 'New name', 'days': [],
            'time': '10:00', 'months': '1', 'monthly_rate': '1000',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="New name"')
        course.refresh_from_db()
        self.assertEqual(course.name, 'Old name')


class TutorEducationEligibilityTests(TestCase):
    """Creating a TutorProfile is the gate that actually lets a tutor operate
    (courses require one, and search only shows tutors who have one), so it
    has to enforce the SSC/HSC-is-not-enough rule server-side — independent
    of whatever the registration form already checked.
    """

    def setUp(self):
        self.tutor = User.objects.create_user('elig_tut', password='pw', role='tutor')
        self.client.force_login(self.tutor)

    def test_no_education_on_file_blocks_profile_creation(self):
        response = self.client.post('/tutors/create-profile/', {'bio': 'Hello there'}, follow=True)
        self.assertFalse(TutorProfile.objects.filter(user=self.tutor).exists())
        self.assertRedirects(response, '/accounts/education/')

    def test_ssc_or_hsc_only_blocks_profile_creation(self):
        Education.objects.create(user=self.tutor, education_level='hsc', institution='XYZ College')
        response = self.client.post('/tutors/create-profile/', {'bio': 'Hello there'}, follow=True)
        self.assertFalse(TutorProfile.objects.filter(user=self.tutor).exists())
        self.assertRedirects(response, '/accounts/education/')

    def test_undergraduate_or_higher_allows_profile_creation(self):
        Education.objects.create(user=self.tutor, education_level='undergraduate',
                                 institution='Dhaka University', currently_studying=True)
        response = self.client.post('/tutors/create-profile/', {'bio': 'Hello there'})
        self.assertTrue(TutorProfile.objects.filter(user=self.tutor).exists())
        self.assertEqual(response.status_code, 302)
