from django.core.validators import MinValueValidator
from django.db import models

from accounts.models import User


class TutorProfile(models.Model):
    """Who the tutor is. Not what they teach.

    Subject and price used to live here, which meant a tutor could offer
    exactly one thing at one price. They belong to a Course now, so a tutor
    can teach Physics for one fee and Maths for another.

    What is left is genuinely about the person: how they describe themselves,
    and the rating earned across everything they teach. Their name, photo,
    phone and NID come from User.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE,
                                related_name='tutor_profile')
    bio = models.TextField(help_text='How you would introduce yourself to a student')

    # Derived, never entered: recalculated from every review of every course.
    rating = models.FloatField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user.username} — tutor profile'

    @property
    def active_courses(self):
        return [c for c in self.user.courses.all() if c.is_active]

    @property
    def subjects(self):
        """The distinct subjects this tutor teaches, for listings and search."""
        seen, out = set(), []
        for course in self.active_courses:
            if course.subject.lower() not in seen:
                seen.add(course.subject.lower())
                out.append(course.subject)
        return out

    @property
    def lowest_rate(self):
        """What 'from ৳X' should say on a listing, or None with no courses."""
        rates = [c.monthly_rate for c in self.active_courses]
        return min(rates) if rates else None

    @property
    def is_bookable(self):
        """A profile with no published course cannot be booked."""
        return any(c.is_bookable for c in self.active_courses)


class Course(models.Model):
    """One thing a tutor teaches, at one price, in one weekly slot.

    `subject` is the searchable category and `name` is the tutor's own
    wording, because "Physics" is what a student searches for and "HSC
    Physics 1st Paper" is what the tutor wants to call it.

    A course holds ONE slot, so it can carry one active booking at a time —
    a tutor cannot teach two students at the same hour. A tutor who wants two
    students publishes two courses.
    """

    DAY_CHOICES = [
        ('0', 'Sun'), ('1', 'Mon'), ('2', 'Tue'), ('3', 'Wed'),
        ('4', 'Thu'), ('5', 'Fri'), ('6', 'Sat'),
    ]

    tutor = models.ForeignKey(User, on_delete=models.CASCADE,
                              related_name='courses')

    subject = models.CharField(max_length=60,
                               help_text="The subject, as a student would search for it")
    name = models.CharField(max_length=120,
                            help_text='What you call this course')

    days = models.CharField(max_length=50,
                            help_text="Comma separated days like '0,2,4'")
    time = models.TimeField(help_text='When the class starts')
    months = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1)],
        help_text='How many months the course runs')

    monthly_rate = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(1)],
        help_text='What a student pays per month')

    # Unpublishing rather than deleting, because a course may carry bookings.
    is_active = models.BooleanField(
        default=True, help_text='Uncheck to stop taking new students')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['subject', 'name']

    def __str__(self):
        return f'{self.name} ({self.subject}) — {self.tutor.username}'

    @property
    def day_names(self):
        labels = dict(self.DAY_CHOICES)
        return [labels[d.strip()] for d in self.days.split(',')
                if d.strip() in labels]

    @property
    def schedule_display(self):
        days = ', '.join(self.day_names)
        return f'{days} at {self.time:%I:%M %p}' if days else f'{self.time:%I:%M %p}'

    @property
    def display(self):
        return (f'{self.schedule_display} ({self.months} '
                f'month{"s" if self.months != 1 else ""})')

    @property
    def total_price(self):
        return self.monthly_rate * self.months

    @property
    def active_booking(self):
        """The booking currently holding this slot, if any."""
        return next((b for b in self.bookings.all()
                     if b.status in ('pending', 'confirmed')), None)

    @property
    def is_bookable(self):
        """Published, and nobody is in the slot."""
        return self.is_active and self.active_booking is None
