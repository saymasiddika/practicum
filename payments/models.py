from django.db import models
from bookings.models import Booking


class Payment(models.Model):
    STATUS_CHOICES = (
        # A checkout session was opened at SSLCommerz and we have not heard
        # back. No money is known to have moved. This is NOT a queue an admin
        # works through: only the gateway's own validation API may move one of
        # these to 'approved'. Kept apart from 'pending' because a customer who
        # abandons the card page at the OTP step leaves one behind, and it must
        # never look like a payment awaiting sign-off.
        ('initiated', 'Not completed'),
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )
    METHOD_CHOICES = (
        ('bkash', 'bKash'),
        ('nagad', 'Nagad'),
        ('rocket', 'Rocket'),
        ('sslcommerz', 'Card / mobile banking (online)'),
    )

    # ForeignKey to allow multiple partial payments per booking
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='payments')

    amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Commission breakdown
    commission = models.DecimalField(max_digits=10, decimal_places=2, default=0,
        help_text='Platform commission deducted from this payment')
    tutor_income = models.DecimalField(max_digits=10, decimal_places=2, default=0,
        help_text='Net amount tutor receives from this payment')
    is_first_payment = models.BooleanField(default=False,
        help_text='True if this is the first payment for this booking (50% commission applies)')

    payment_method = models.CharField(max_length=12, choices=METHOD_CHOICES, default='bkash')
    sender_number = models.CharField(max_length=20, blank=True, default='')
    transaction_id = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')

    # ── SSLCommerz ───────────────────────────────────────────────────────────
    # Set only on gateway payments. `gateway_tran_id` is the reference we
    # generate and hand to SSLCommerz; the other two come back from them and
    # are kept so a payment can be reconciled against their dashboard.
    gateway_tran_id = models.CharField(
        max_length=40, blank=True, default='', db_index=True,
        help_text='Our reference sent to SSLCommerz')
    gateway_val_id = models.CharField(
        max_length=80, blank=True, default='',
        help_text='SSLCommerz validation id for the settled transaction')
    gateway_bank_tran_id = models.CharField(
        max_length=80, blank=True, default='',
        help_text="The bank's own reference, as reported by SSLCommerz")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_gateway(self):
        return self.payment_method == 'sslcommerz'

    @property
    def is_manually_approvable(self):
        """Whether an admin may sign this payment off by hand.

        Only the manual flow, where a student submits a transaction ID for a
        human to check against their mobile-banking account. A gateway payment
        is settled by SSLCommerz's validation API and by nothing else — an
        admin has no way to know whether the card was actually charged, so
        offering them an Approve button invites crediting money that never
        arrived.
        """
        return self.status == 'pending' and not self.is_gateway

    @property
    def counts_as_money_in_flight(self):
        """Whether this payment represents a real claim on the student's money.

        An 'initiated' row does not: it is an abandoned or unfinished checkout
        with nothing charged.
        """
        return self.status in ('pending', 'approved')

    class Meta:
        constraints = [
            # A booking may carry at most one payment awaiting review. Enforced
            # in the database so concurrent submissions cannot both slip through.
            models.UniqueConstraint(
                fields=['booking'],
                condition=models.Q(status='pending'),
                name='one_pending_payment_per_booking',
            ),
        ]

    @staticmethod
    def calculate_commission(amount, monthly_rate, already_paid=0):
        """
        Commission is charged against how much of the FIRST MONTH is still
        uncovered, so split/partial payments are billed correctly.

          - the slice of this payment that covers the remaining first month:
            50% commission (broker 40% + platform 10%)
          - everything beyond the first month: 10% commission (platform only)

        Example: rate = 4,000, nothing paid yet, student pays 10,000
          -> first 4,000 x 50%  = 2,000
          -> excess 6,000 x 10% =   600
          -> commission 2,600, tutor receives 7,400

        Example: rate = 4,000, already paid 1,000, student pays 5,000
          -> remaining first month is 3,000 x 50% = 1,500
          -> excess 2,000 x 10%                   =   200
          -> commission 1,700 (the first month is still charged 50% in total)

        Args:
            amount:        payment amount submitted
            monthly_rate:  tutor's monthly_rate
            already_paid:  total approved amount already recorded for this booking
        """
        first_portion, excess = Payment.commission_portions(
            amount, monthly_rate, already_paid
        )
        commission   = round(first_portion * 0.50 + excess * 0.10, 2)
        tutor_income = round(float(amount) - commission, 2)
        return commission, tutor_income

    @staticmethod
    def commission_portions(amount, monthly_rate, already_paid=0):
        """Split `amount` into (first-month portion, excess portion)."""
        amount       = float(amount)
        monthly_rate = float(monthly_rate)
        already_paid = float(already_paid or 0)

        remaining_first_month = max(0.0, monthly_rate - already_paid)
        first_portion = min(amount, remaining_first_month)
        return first_portion, amount - first_portion

    @property
    def effective_rate(self):
        """Commission as a percentage of this payment's amount.

        The headline rules are 50% on the first month and 10% after, but a
        payment that spans both is charged a blend — so report what was
        actually taken instead of a headline figure.
        """
        amount = float(self.amount or 0)
        if not amount:
            return 0.0
        return round(float(self.commission) / amount * 100, 1)

    @property
    def breakdown(self):
        """Human-readable commission breakdown for this payment."""
        # The booking's own rate: the snapshot taken when it was made,
        # falling back to its course. Never the tutor's current price, which
        # may have moved since.
        rate = float(self.booking.monthly_rate or 0)

        already_paid = sum(
            float(p.amount) for p in self.booking.payments.filter(
                status='approved', created_at__lt=self.created_at
            )
        ) if self.created_at else 0.0

        first_portion, excess = Payment.commission_portions(
            self.amount, rate, already_paid
        )
        return {
            'broker_charge':   round(first_portion * 0.40, 2),
            'platform_charge': round(first_portion * 0.10 + excess * 0.10, 2),
            'advance':         round(excess, 2),
        }

    def __str__(self):
        tag = '[1st]' if self.is_first_payment else '[+]'
        return f"{tag} Booking#{self.booking.id} ৳{self.amount} → commission ৳{self.commission} ({self.get_status_display()})"
