"""
Management command: seed_demo

Fills a development database with a small, believable data set so the site
can be demonstrated: tutors with courses, students with bookings in each
state, approved payments, one payment awaiting admin review, and reviews.

Usage:
    python manage.py seed_demo --password "<password for new demo accounts>"

Safe to run more than once: accounts, courses and bookings that already exist
are left as they are, and the password of an existing account is never
changed. The password is an argument rather than a constant so that no
account password is ever committed to the repository.
"""
from datetime import time, timedelta
from decimal import Decimal

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Avg
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from payments.models import Payment
from reviews.models import Review
from tutors.models import Course, TutorProfile

# Days are Course.DAY_CHOICES codes: 0 = Sunday ... 6 = Saturday.
TUTORS = [
    # (username, first name, last name, bio,
    #  [(subject, course name, days, start time, months, monthly rate), ...])
    ('Sayma', 'Sayma', 'Swarna',
     'Chemistry and biology tutor for SSC and HSC students. I teach from the '
     'board syllabus and set a short practice sheet every week.',
     [('Chemistry', 'HSC Chemistry 1st Paper', '0,2', time(18, 0), 3, 4500),
      ('Biology', 'SSC Biology', '1,3', time(17, 0), 2, 3500)]),
    ('rahim', 'Rahim', 'Uddin',
     'Physics teacher with eight years of HSC coaching. We work through '
     'problems first and explain the theory through them.',
     [('Physics', 'HSC Physics 2nd Paper', '1,6', time(19, 0), 3, 5000),
      ('Physics', 'SSC Physics Basics', '4', time(16, 0), 2, 3000)]),
    ('nusrat', 'Nusrat', 'Jahan',
     'Mathematics tutor from Class 9 to HSC, and patient with students who '
     'find maths hard.',
     [('Mathematics', 'SSC General Maths', '0,3', time(17, 30), 3, 3500),
      ('Higher Mathematics', 'HSC Higher Maths', '2,5', time(20, 0), 4, 5500)]),
    ('tanvir', 'Tanvir', 'Ahmed',
     'IELTS and spoken English trainer. Most of each class is speaking '
     'practice, with feedback on every answer.',
     [('English', 'IELTS Speaking and Writing', '4,6', time(20, 30), 2, 6000),
      ('English', 'Spoken English for Beginners', '5', time(10, 0), 2, 3000)]),
]

STUDENTS = [
    ('Miraz', 'Miraz', ''),
    ('farhan', 'Farhan', 'Hossain'),
    ('tasnim', 'Tasnim', 'Akter'),
]

BOOKINGS = [
    # (student, tutor, course name, status, start date relative to today,
    #  [(amount, payment status), ...], (rating, comment) or None)
    ('Miraz', 'Sayma', 'HSC Chemistry 1st Paper', 'confirmed', -9,
     [(4500, 'approved')], None),
    ('Miraz', 'nusrat', 'HSC Higher Maths', 'completed', -130,
     [(5500, 'approved'), (16500, 'approved')],
     (5, 'Explained integration step by step until it finally made sense. '
         'I would book her again.')),
    ('farhan', 'nusrat', 'SSC General Maths', 'completed', -100,
     [(10500, 'approved')],
     (4, 'Very clear and patient. A couple of classes started a few minutes late.')),
    ('farhan', 'rahim', 'HSC Physics 2nd Paper', 'pending', 6, [], None),
    ('tasnim', 'tanvir', 'IELTS Speaking and Writing', 'confirmed', -3,
     [(6000, 'pending')], None),
]


class Command(BaseCommand):
    help = 'Fill the database with demo tutors, courses, bookings, payments and reviews'

    def add_arguments(self, parser):
        parser.add_argument(
            '--password',
            required=True,
            help='Password for the demo accounts this command creates. '
                 'Accounts that already exist keep their own.',
        )

    def handle(self, *args, **options):
        password = options['password']
        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError('That password is too weak: ' + ' '.join(exc.messages))

        created = dict.fromkeys(
            ('accounts', 'profiles', 'courses', 'bookings', 'payments', 'reviews'), 0)

        with transaction.atomic():
            users = {}

            for username, first, last, bio, courses in TUTORS:
                user = self._account(username, first, last, 'tutor', password, created)
                users[username] = user
                if not TutorProfile.objects.filter(user=user).exists():
                    TutorProfile.objects.create(user=user, bio=bio)
                    created['profiles'] += 1
                for subject, name, days, start, months, rate in courses:
                    _course, made = Course.objects.get_or_create(
                        tutor=user, name=name,
                        defaults={'subject': subject, 'days': days, 'time': start,
                                  'months': months, 'monthly_rate': rate},
                    )
                    created['courses'] += int(made)

            for username, first, last in STUDENTS:
                users[username] = self._account(username, first, last, 'student', password, created)

            today = timezone.localdate()
            for student, tutor, course_name, status, offset, payments, review in BOOKINGS:
                course = Course.objects.get(tutor=users[tutor], name=course_name)
                if Booking.objects.filter(student=users[student], tutor=users[tutor],
                                          course=course).exists():
                    continue

                booking = Booking.objects.create(
                    student=users[student], tutor=users[tutor], course=course,
                    date=today + timedelta(days=offset), months=course.months,
                    agreed_monthly_rate=course.monthly_rate, status=status,
                )
                created['bookings'] += 1

                paid = 0.0
                for index, (amount, pay_status) in enumerate(payments):
                    commission = income = 0
                    # Commission is fixed when an admin approves a payment, so
                    # one still awaiting review carries none yet.
                    if pay_status == 'approved':
                        commission, income = Payment.calculate_commission(
                            float(amount), float(course.monthly_rate), paid)
                        paid += float(amount)
                    Payment.objects.create(
                        booking=booking, amount=Decimal(amount),
                        commission=commission, tutor_income=income,
                        is_first_payment=(index == 0), payment_method='bkash',
                        sender_number=f'01711{booking.id:06d}',
                        transaction_id=f'DEMO{booking.id:04d}{index + 1}',
                        status=pay_status,
                    )
                    created['payments'] += 1

                if review is not None:
                    Review.objects.create(booking=booking, rating=review[0], comment=review[1])
                    created['reviews'] += 1

            # A tutor's rating is the average of their reviews, as the review
            # view keeps it.
            for profile in TutorProfile.objects.filter(user__in=users.values()):
                average = Review.objects.filter(booking__tutor=profile.user).aggregate(
                    a=Avg('rating'))['a']
                if average is not None:
                    profile.rating = round(average, 1)
                    profile.save(update_fields=['rating'])

        if any(created.values()):
            summary = ', '.join(f'{count} {kind}' for kind, count in created.items())
            self.stdout.write(self.style.SUCCESS(f'Demo data ready. Created: {summary}.'))
        else:
            self.stdout.write(self.style.SUCCESS('Nothing to do: the demo data is already there.'))

    def _account(self, username, first, last, role, password, created):
        """Find the account, or create it with the demo password.

        An existing account is returned untouched, password included.
        """
        user = User.objects.filter(username=username).first()
        if user is not None:
            if user.role != role:
                raise CommandError(
                    f'"{username}" already exists as a {user.role}, but the demo '
                    f'data needs a {role} with that name.')
            return user
        user = User.objects.create_user(
            username=username, password=password, role=role,
            first_name=first, last_name=last,
            email=f'{username.lower()}@demo.eduai.test',
        )
        created['accounts'] += 1
        return user
