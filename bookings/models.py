from django.db import models
from django.core.validators import MinValueValidator
from accounts.models import User
from tutors.models import Course


class Booking(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('completed', 'Completed'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    )

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='student_bookings')
    tutor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tutor_bookings')

    date     = models.DateField()
    months   = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    # What was booked. SET_NULL rather than CASCADE: a tutor removing a course
    # must not erase the history of who studied it and what they paid. The
    # price survives the course because of agreed_monthly_rate below.
    course   = models.ForeignKey(
        Course, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='bookings'
    )
    status   = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')

    # The rate agreed when the booking was made. Snapshotted so that a tutor
    # later changing monthly_rate cannot re-price bookings already in flight.
    agreed_monthly_rate = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Monthly rate captured at booking time'
    )

    meet_link = models.URLField(blank=True, default='', help_text='Google Meet / Zoom link set by tutor')
    tutor_message = models.TextField(blank=True, default='', help_text='Message from tutor to student')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.student.username} -> {self.tutor.username} ({self.months} month(s), {self.status})"

    # ── Helpers for templates ──────────────────────────────────────────────
    @property
    def payment(self):
        """Latest approved payment, or latest pending, or None."""
        approved = [p for p in self._payments if p.status == 'approved']
        if approved:
            return max(approved, key=lambda p: p.created_at)
        pending = [p for p in self._payments if p.status == 'pending']
        return max(pending, key=lambda p: p.created_at) if pending else None

    @property
    def _payments(self):
        """All payments, read from the prefetch cache when there is one.

        Every aggregate below goes through this, so a list view that calls
        `prefetch_related('payments')` costs one query for the whole page
        instead of several per row.
        """
        return self.payments.all()

    @property
    def total_paid(self):
        """Sum of all approved payments."""
        return sum(p.amount for p in self._payments if p.status == 'approved') or 0

    @property
    def total_commission(self):
        """Sum of commission from approved payments."""
        return sum(p.commission for p in self._payments if p.status == 'approved') or 0

    @property
    def total_amount(self):
        """Full booking cost: the agreed rate × months."""
        return float(self.monthly_rate) * self.months

    @property
    def monthly_rate(self):
        """Rate this booking is priced at.

        The snapshot is the answer whenever there is one. The fall-back to the
        course exists only for rows written before the snapshot, and it can
        still come up empty if the course has since been deleted — hence the
        final default rather than an exception on a page that only wanted to
        show a number.
        """
        if self.agreed_monthly_rate is not None:
            return self.agreed_monthly_rate
        if self.course is not None:
            return self.course.monthly_rate
        return 0

    @property
    def balance_due(self):
        return round(self.total_amount - float(self.total_paid), 2)

    @property
    def is_fully_paid(self):
        return self.balance_due <= 0

    @property
    def is_payable(self):
        """A booking may be paid only once the tutor has accepted it.

        Taking money for a request the tutor can still decline leaves the
        student out of pocket with nothing to show for it, and the app has no
        refund path. So payment opens at 'confirmed', not before.
        """
        return self.status == 'confirmed' and not self.is_fully_paid

    @property
    def is_cancellable(self):
        """A student may cancel while the booking is still live and no money
        has been taken. Rejected payments do not count as money taken."""
        if self.status not in ('pending', 'confirmed'):
            return False
        return not any(
            p.status in ('pending', 'approved') for p in self._payments
        )

    @property
    def first_payment_made(self):
        """True if at least one payment has been submitted."""
        return bool(self._payments)

    # ── Ending early ──────────────────────────────────────────────────────

    @property
    def subject(self):
        """What this booking is for, for listings and the assistant."""
        return self.course.subject if self.course else 'Tutoring'

    @property
    def course_name(self):
        return self.course.name if self.course else 'a course'

    @property
    def natural_end_date(self):
        """The day the booked course was always going to finish."""
        from dateutil.relativedelta import relativedelta
        return self.date + relativedelta(months=self.months)

    @property
    def has_run_its_course(self):
        from django.utils import timezone
        return timezone.localdate() >= self.natural_end_date

    @property
    def open_request(self):
        """The request waiting on an admin, if there is one."""
        return next((r for r in self.requests.all() if r.status == 'pending'), None)

    @property
    def can_request_early_end(self):
        """Whether either party may ask to finish or terminate early.

        Only while the booking is live and still inside its agreed term — once
        the course has run its length, completion needs no one's permission.
        """
        return (self.status == 'confirmed'
                and not self.has_run_its_course
                and self.open_request is None)


class BookingRequest(models.Model):
    """A request to end a booking before its agreed end date.

    Two things can be asked for, and they share this one model because they
    share a shape — someone asks with a reason, an admin verifies it, and the
    booking moves. Only what happens on approval differs.

      · 'complete'  — the course finished early. On approval the booking is
                      completed, which is what lets the student leave a review.
      · 'terminate' — the student or tutor is pulling out. On approval the
                      booking is cancelled and a refund becomes payable.

    The refund figures are SNAPSHOTTED when the request is made, not
    recalculated at approval time. A student who is quoted 75% on the last day
    of week one must not silently drop to 50% because an admin read their
    request on Monday.
    """

    KIND_CHOICES = (
        ('complete', 'Finish the course early'),
        ('terminate', 'End the booking'),
    )
    STATUS_CHOICES = (
        ('pending', 'Awaiting review'),
        ('approved', 'Approved'),
        ('declined', 'Declined'),
    )
    PAYOUT_CHOICES = (
        ('bkash', 'bKash'),
        ('nagad', 'Nagad'),
        ('rocket', 'Rocket'),
        ('bank', 'Bank transfer'),
        ('office', 'Collect from the office'),
    )

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE,
                                related_name='requests')
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE,
                                     related_name='booking_requests')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    reason = models.TextField(help_text='Why the booking should end early')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')

    # ── Refund snapshot, frozen when the request was made ─────────────────
    refund_percent = models.PositiveIntegerField(default=0)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    refund_tier = models.CharField(max_length=60, blank=True, default='',
                                   help_text='Which rule produced this figure')

    # ── Where the refund should go ────────────────────────────────────────
    payout_method = models.CharField(max_length=10, choices=PAYOUT_CHOICES,
                                     blank=True, default='')
    payout_account = models.CharField(max_length=40, blank=True, default='',
                                      help_text='Mobile banking or bank account number')
    payout_bank_name = models.CharField(max_length=80, blank=True, default='')
    refund_settled = models.BooleanField(default=False,
                                         help_text='Admin has actually sent the money')
    refund_settled_at = models.DateTimeField(null=True, blank=True)

    # ── Review ────────────────────────────────────────────────────────────
    admin_note = models.TextField(blank=True, default='')
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='reviewed_requests')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # One open request per booking, so a student cannot flood the queue
            # or hold two contradictory requests at once.
            models.UniqueConstraint(
                fields=['booking'],
                condition=models.Q(status='pending'),
                name='one_open_request_per_booking',
            ),
        ]

    def __str__(self):
        return f'{self.get_kind_display()} — booking #{self.booking_id} ({self.status})'

    @property
    def is_open(self):
        return self.status == 'pending'

    @property
    def by_tutor(self):
        return self.requested_by_id == self.booking.tutor_id

    @property
    def owes_a_refund(self):
        return (self.kind == 'terminate' and self.status == 'approved'
                and self.refund_amount > 0)

    @property
    def needs_payout_details(self):
        """A refund is owed and we do not yet know where to send it."""
        return self.owes_a_refund and not self.refund_settled

    def approve(self, admin, note=''):
        """Verify the reason and move the booking. Safe to call only once."""
        from django.utils import timezone
        if self.status != 'pending':
            return False

        self.status = 'approved'
        self.admin_note = note
        self.reviewed_by = admin
        self.reviewed_at = timezone.now()
        self.save()

        booking = self.booking
        booking.status = 'completed' if self.kind == 'complete' else 'cancelled'
        booking.save(update_fields=['status', 'updated_at'])
        return True

    def decline(self, admin, note=''):
        """Refuse the request. The booking carries on untouched."""
        from django.utils import timezone
        if self.status != 'pending':
            return False

        self.status = 'declined'
        self.admin_note = note
        self.reviewed_by = admin
        self.reviewed_at = timezone.now()
        self.save()
        return True
