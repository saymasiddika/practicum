from django.contrib.auth.models import AbstractUser
from django.db import models

from .storage import private_storage


class User(AbstractUser):
    ROLE_CHOICES = (
        ('student', 'Student'),
        ('tutor', 'Tutor'),
    )
    # Shared with Education.verification_status below — one admin never has
    # to remember two different vocabularies for "has anyone checked this".
    VERIFICATION_STATUS_CHOICES = (
        ('unverified', 'Not submitted'),
        ('pending', 'Pending review'),
        ('verified', 'Verified'),
        ('rejected', 'Rejected'),
    )

    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    education = models.CharField(max_length=150, blank=True, null=True,
                                  help_text='e.g. BSc in CSE, Dhaka University')
    id_card_number = models.CharField(max_length=40, blank=True, null=True,
                                      help_text='University ID card number')
    # Identity/verification documents — private storage (see accounts/storage.py):
    # never reachable at a public URL, only through a permission-checked view.
    id_card_front = models.ImageField(upload_to='id_cards/', storage=private_storage,
                                      blank=True, null=True)
    id_card_back = models.ImageField(upload_to='id_cards/', storage=private_storage,
                                     blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    nid_number = models.CharField(max_length=30, blank=True, null=True)
    nid_image = models.ImageField(upload_to='nid_cards/', storage=private_storage,
                                  blank=True, null=True,
                                  help_text='Photo/scan of the NID — required for tutor registration')
    # This is the "Teacher Verification" status — kept on User because NID is
    # a fact about the ACCOUNT, not about any one qualification. It moves to
    # 'pending' automatically whenever an NID is (re)submitted, and only an
    # admin can move it to 'verified' or 'rejected'.
    nid_verification_status = models.CharField(
        max_length=10, choices=VERIFICATION_STATUS_CHOICES, default='unverified',
    )

    def __str__(self):
        return f"{self.username} ({self.role})"

    @property
    def avatar_url(self):
        if self.avatar:
            return self.avatar.url
        return None


class Education(models.Model):
    """One qualification on a user's Educational Background — LinkedIn-style:
    a student or tutor can list several of these, add new ones as they
    progress, and edit or remove any entry independently.

    The shape is the same row for every level, but the form built on top of
    it (accounts/views.py, education_form.html) shows and labels fields
    differently for SSC/HSC vs university-level entries — see
    PRE_UNIVERSITY_LEVELS below.
    """
    LEVEL_CHOICES = (
        ('ssc', 'Secondary School Certificate (SSC)'),
        ('hsc', 'Higher Secondary Certificate (HSC)'),
        ('undergraduate', "Undergraduate / Bachelor's"),
        ('graduate', "Graduate / Master's"),
        ('mphil', 'MPhil or equivalent postgraduate research degree'),
        ('phd', 'PhD / Doctorate'),
        ('other', 'Other recognized higher education'),
    )
    # SSC/HSC use GPA and a Group (Science/Commerce/Arts); everything above
    # them uses Degree/Program, Department/Major and CGPA on a university name.
    PRE_UNIVERSITY_LEVELS = ('ssc', 'hsc')
    # Reaching undergraduate level or beyond — whether still studying or
    # already finished — is what tutor eligibility is gated on.
    QUALIFYING_LEVELS = ('undergraduate', 'graduate', 'mphil', 'phd', 'other')

    MONTH_CHOICES = (
        (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
        (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
        (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December'),
    )
    MONTH_NAMES = dict(MONTH_CHOICES)

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='educations')

    education_level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default='other')
    institution = models.CharField(max_length=150, help_text='School, college or university')
    degree = models.CharField(max_length=100, blank=True, default='',
                              help_text='e.g. BSc in CSE — not used for SSC/HSC')
    field_of_study = models.CharField(max_length=150, blank=True, default='',
                                      help_text='Group for SSC/HSC, department/major otherwise')

    # Month is optional and mainly meaningful for university-level entries
    # ("Start Date: January 2025"); SSC/HSC only ever fill in the year
    # ("Passing Year: 2022").
    start_month = models.PositiveSmallIntegerField(null=True, blank=True, choices=MONTH_CHOICES)
    start_year = models.PositiveIntegerField(null=True, blank=True)
    end_month = models.PositiveSmallIntegerField(null=True, blank=True, choices=MONTH_CHOICES)
    end_year = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Actual completion year, or the expected one while still studying'
    )
    currently_studying = models.BooleanField(default=False)
    gpa = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True,
                              help_text='GPA or CGPA obtained (or current, while studying)')
    gpa_scale = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True,
                                    help_text='Scale the GPA/CGPA is out of, e.g. 5.00 or 4.00')
    description = models.TextField(blank=True, default='',
                                   help_text='Additional academic information — optional')
    # Private storage: the fact that an entry HAS a document is fine to show
    # on the public-facing parts of a profile, but the document itself is a
    # verification artefact, not public-profile content — see
    # accounts/storage.py and the note on this model below.
    document = models.FileField(upload_to='education_documents/', storage=private_storage,
                                blank=True, null=True,
                                help_text='Certificate, transcript or result — optional, for verification')
    # Moves to 'pending' automatically whenever `document` is (re)uploaded;
    # stays 'unverified' for an entry with nothing to check. Only an admin
    # can move it to 'verified' or 'rejected' — see quick_tutor/admin_views.py.
    # This tracks trust in ONE qualification, separate from tutor eligibility
    # itself, which is level-based and does not wait on verification.
    verification_status = models.CharField(
        max_length=10, choices=User.VERIFICATION_STATUS_CHOICES, default='unverified',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-currently_studying', '-end_year', '-start_year', '-created_at']

    def __str__(self):
        return f'{self.institution} — {self.user.username}'

    @property
    def is_pre_university(self):
        return self.education_level in self.PRE_UNIVERSITY_LEVELS

    @property
    def display_title(self):
        """The headline for a card — 'BSc in Computer Science and
        Engineering' for a degree, or the level itself ('Higher Secondary
        Certificate (HSC)') for SSC/HSC, where field_of_study means Group
        rather than a program name and must not be used as the headline.
        """
        if self.is_pre_university:
            return self.get_education_level_display()
        if self.degree and self.field_of_study:
            return f'{self.degree} in {self.field_of_study}'
        return self.degree or self.field_of_study or self.get_education_level_display()

    @property
    def display_group(self):
        """SSC/HSC's Group (Science/Commerce/Arts) line, shown under the
        institution — university entries fold field_of_study into the
        headline instead, so there is nothing extra to show here.
        """
        return self.field_of_study if self.is_pre_university else ''

    @classmethod
    def user_qualifies_for_tutoring(cls, user):
        """Tutor eligibility: reaching undergraduate level or beyond,
        whether that record is 'currently studying' or already completed.
        Someone whose only records are SSC/HSC (or who has none at all)
        does not qualify — see the Teacher/Tutor Eligibility rule.
        """
        return cls.objects.filter(
            user=user, education_level__in=cls.QUALIFYING_LEVELS,
        ).exists()

    def _format_date(self, month, year):
        if not year:
            return None
        if month:
            return f'{self.MONTH_NAMES.get(month, "")} {year}'.strip()
        return str(year)

    @property
    def date_range(self):
        """A student can be mid-degree with only an expected finish date, so
        'currently studying' does not mean the end date is empty — it means
        the end date is a projection rather than a fact. Both are shown.
        """
        start = self._format_date(self.start_month, self.start_year)
        end = self._format_date(self.end_month, self.end_year)

        if self.currently_studying:
            if start and end:
                return f'{start} – Expected {end}'
            if end:
                return f'Expected {end}'
            return f'{start} – Present' if start else 'Present'

        if start and end:
            return f'{start} – {end}'
        return end or start or '—'

    @property
    def gpa_display(self):
        if self.gpa is None:
            return ''
        if self.gpa_scale is not None:
            return f'{self.gpa} / {self.gpa_scale}'
        return f'{self.gpa}'
