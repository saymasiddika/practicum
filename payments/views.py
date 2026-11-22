from payments.money import taka
from django.views.decorators.http import require_POST
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.db import transaction, IntegrityError
from decimal import Decimal, InvalidOperation

# Allow only sub-currency rounding drift, not a whole taka.
OVERPAY_TOLERANCE = Decimal('0.01')

# The largest amount the column can hold: Payment.amount is DecimalField(10, 2).
# An amount that cannot be stored is not a valid amount, so it is refused here
# rather than left to fail on save.
MAX_AMOUNT = Decimal('99999999.99')


def parse_amount(raw):
    """Read a money amount from user input, or return None.

    Both payment routes go through this, because both were caught out by the
    same thing: "NaN" is a perfectly valid input to Decimal() and float().
    It then poisons whatever it touches — a Decimal NaN raises on comparison
    (a 500), and a float nan silently answers False to `nan <= 0`, sliding
    past the very check meant to reject it. So the test has to be that the
    value IS a finite positive number, not that it is not obviously bad.
    """
    try:
        amount = Decimal(str(raw).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT:
        return None
    return amount
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Payment
from payments import gateway
from bookings.models import Booking


@login_required(login_url='login')
def payment_create_view(request):
    booking_id = request.GET.get('booking_id')
    booking = get_object_or_404(Booking, id=booking_id)

    if booking.student != request.user:
        messages.error(request, 'You do not have permission to pay for this booking!')
        return redirect('booking-list')

    if booking.status == 'pending':
        messages.error(
            request,
            'Your tutor hasn’t confirmed this booking yet. You can pay as soon '
            'as they accept it — nothing is owed until then.'
        )
        return redirect('booking-detail', pk=booking.id)

    if booking.status != 'confirmed':
        messages.error(
            request,
            f'This booking is {booking.get_status_display().lower()} and can no longer be paid for.'
        )
        return redirect('booking-detail', pk=booking.id)

    if booking.is_fully_paid:
        messages.info(request, 'This booking is fully paid!')
        return redirect('booking-detail', pk=booking.id)

    monthly_rate = booking.monthly_rate

    total_amount = booking.total_amount
    total_paid   = float(booking.total_paid)
    balance_due  = booking.balance_due

    # The first month counts as covered only once the approved payments for
    # this booking actually add up to the tutor's monthly rate.
    first_month_covered   = total_paid >= float(monthly_rate)
    first_month_remaining = max(0.0, float(monthly_rate) - total_paid)

    # Whether this payment carries the first month's commission. Rejected
    # attempts do not count — otherwise a retry is charged the 50% first-month
    # rate while being labelled as a 10% recurring instalment.
    is_first_transaction = not booking.payments.filter(
        status__in=['pending', 'approved']
    ).exists()

    # Block new submission if a pending payment already exists
    pending_payment = booking.payments.filter(status='pending').first()

    # Commission preview values for template
    if not first_month_covered:
        preview_first_commission = round(first_month_remaining * 0.50, 2)
        preview_excess_rate = 10
        preview_note = f"50% on the remaining {taka(first_month_remaining)} of month 1, then 10% on the rest"
    else:
        preview_first_commission = None
        preview_excess_rate = 10
        preview_note = "10% platform charge on full amount"

    if request.method == 'POST':
        # Block if a pending payment already exists
        if pending_payment:
            messages.error(request, 'You already have a pending payment awaiting review. Please wait for admin approval before submitting another.')
            return redirect('booking-detail', pk=booking.id)

        payment_method = request.POST.get('payment_method', 'bkash')
        sender_number  = request.POST.get('sender_number', '').strip()
        transaction_id = request.POST.get('transaction_id', '').strip()
        amount_str     = request.POST.get('amount', '').strip()

        if not sender_number or not transaction_id:
            messages.error(request, 'Please provide your sender number and transaction ID.')
        elif not amount_str:
            messages.error(request, 'Please enter the payment amount.')
        else:
            amount = parse_amount(amount_str)
            if amount is None:
                messages.error(request, 'Invalid amount.')

            if amount:
                if amount > Decimal(str(balance_due)) + OVERPAY_TOLERANCE:
                    messages.error(request, f'Amount exceeds balance due ({taka(balance_due)}).')
                else:
                    commission, tutor_income = Payment.calculate_commission(
                        amount, monthly_rate, total_paid
                    )
                    try:
                        # Re-read inside the transaction and let the database
                        # constraint settle any concurrent submission.
                        with transaction.atomic():
                            if booking.payments.filter(status='pending').exists():
                                raise IntegrityError('pending payment already exists')
                            Payment.objects.create(
                                booking=booking,
                                amount=amount,
                                commission=commission,
                                tutor_income=tutor_income,
                                is_first_payment=is_first_transaction,
                                payment_method=payment_method,
                                sender_number=sender_number,
                                transaction_id=transaction_id,
                                status='pending',
                            )
                    except IntegrityError:
                        messages.error(
                            request,
                            'You already have a payment awaiting review. '
                            'Please wait for it to be approved before submitting another.'
                        )
                        return redirect('booking-detail', pk=booking.id)

                    messages.success(request, f'Payment of {taka(amount)} submitted! Admin will review shortly.')
                    return redirect('booking-detail', pk=booking.id)

    all_payments = booking.payments.order_by('created_at')

    context = {
        'booking':                booking,
        'total_amount':           total_amount,
        'total_paid':             total_paid,
        'balance_due':            balance_due,
        'monthly_rate':           monthly_rate,
        'first_month_covered':    first_month_covered,
        'first_month_remaining':  first_month_remaining,
        'is_first_payment':       is_first_transaction,   # kept for JS
        'pending_payment':        pending_payment,
        'preview_first_commission': preview_first_commission,
        'preview_note':           preview_note,
        'payment_methods':        [m for m in Payment.METHOD_CHOICES if m[0] != 'sslcommerz'],
        'gateway_available':      gateway.is_configured(),
        'all_payments':           all_payments,
        'recipient_number':       settings.PAYMENT_RECIPIENT_NUMBER,
        'recipient_display':      settings.PAYMENT_RECIPIENT_DISPLAY,
    }
    return render(request, 'payments/create.html', context)


@login_required(login_url='login')
def payment_list_view(request):
    if request.user.role == 'student':
        bookings = (
            request.user.student_bookings
            .select_related('tutor', 'tutor__tutor_profile')
            .prefetch_related('payments')
            .order_by('-created_at')
        )
    else:
        bookings = (
            request.user.tutor_bookings
            .select_related('student', 'tutor', 'tutor__tutor_profile')
            .prefetch_related('payments')
            .order_by('-created_at')
        )

    booking_groups = []
    for booking in bookings:
        monthly_rate = booking.monthly_rate
        total_amount = float(monthly_rate) * booking.months
        payments = sorted(booking.payments.all(), key=lambda p: p.created_at)
        has_pending = any(p.status == 'pending' for p in payments)
        booking_groups.append({
            'booking': booking,
            'payments': payments,
            'monthly_rate': monthly_rate,
            'total_amount': total_amount,
            'has_pending': has_pending,
        })

    return render(request, 'payments/list.html', {'booking_groups': booking_groups})


@login_required(login_url='login')
def payment_detail_view(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    # Staff review payments at /payments/manage/, so they must be able to open
    # an individual one too.
    if (payment.booking.student != request.user
            and payment.booking.tutor != request.user
            and not (request.user.is_staff or request.user.is_superuser)):
        messages.error(request, 'You do not have permission to view this payment!')
        return redirect('payment-list')
    return render(request, 'payments/detail.html', {'payment': payment})


@login_required(login_url='login')
@require_POST
def admin_approve_payment(request, pk):
    if not request.user.is_superuser and not request.user.is_staff:
        return redirect('home')
    payment = get_object_or_404(Payment, pk=pk)

    # An online payment is settled by SSLCommerz's validation API and by
    # nothing else. An admin cannot see whether the card was really charged,
    # so approving one by hand credits money that may never have arrived —
    # which is exactly what an abandoned checkout looks like from here.
    if payment.is_gateway:
        messages.error(
            request,
            f'Payment #{payment.id} was made online, so it is confirmed by the '
            f'payment gateway rather than by hand. If it is stuck, check the '
            f'transaction in your SSLCommerz dashboard.'
        )
        return redirect('payment-admin')

    if payment.status != 'pending':
        messages.error(
            request,
            f'Payment #{payment.id} is already '
            f'{payment.get_status_display().lower()} and cannot be approved.'
        )
        return redirect('payment-admin')

    payment.status = 'approved'
    payment.save()
    messages.success(request, f'Payment #{payment.id} approved!')
    return redirect('payment-admin')


@login_required(login_url='login')
@require_POST
def admin_reject_payment(request, pk):
    if not request.user.is_superuser and not request.user.is_staff:
        return redirect('home')
    payment = get_object_or_404(Payment, pk=pk)

    # Rejecting a settled payment would quietly erase money the tutor has
    # already been credited for, and the booking's balance would jump back up
    # with no record of why.
    if payment.status == 'approved':
        messages.error(
            request,
            f'Payment #{payment.id} has already been approved and cannot be '
            f'rejected. Reverse it with the payment provider instead.'
        )
        return redirect('payment-admin')

    payment.status = 'rejected'
    payment.save()
    messages.success(request, f'Payment #{payment.id} rejected.')
    return redirect('payment-admin')


@login_required(login_url='login')
def payment_admin_view(request):
    if not request.user.is_superuser and not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('home')

    status_filter = request.GET.get('status', '')
    payments = Payment.objects.all().select_related(
        'booking__student', 'booking__tutor', 'booking__tutor__tutor_profile'
    ).order_by('-created_at')
    if status_filter:
        payments = payments.filter(status=status_filter)

    from django.db.models import Sum
    import json

    total_revenue    = Payment.objects.filter(status='approved').aggregate(s=Sum('amount'))['s'] or 0
    total_commission = Payment.objects.filter(status='approved').aggregate(s=Sum('commission'))['s'] or 0
    total_tutor_pay  = Payment.objects.filter(status='approved').aggregate(s=Sum('tutor_income'))['s'] or 0

    stats = {
        'total':            Payment.objects.count(),
        'pending':          Payment.objects.filter(status='pending').count(),
        'approved':         Payment.objects.filter(status='approved').count(),
        'rejected':         Payment.objects.filter(status='rejected').count(),
        'total_amount':     total_revenue,
        'total_commission': total_commission,
        'total_tutor_pay':  total_tutor_pay,
    }

    # Monthly chart: last 6 months
    from datetime import date
    try:
        from dateutil.relativedelta import relativedelta
        chart_labels = []
        chart_revenue = []
        chart_commission = []
        chart_tutor = []
        for i in range(5, -1, -1):
            ms = date.today().replace(day=1) - relativedelta(months=i)
            me = ms + relativedelta(months=1)
            label = ms.strftime('%b %Y')
            rev  = Payment.objects.filter(status='approved', created_at__date__gte=ms, created_at__date__lt=me).aggregate(s=Sum('amount'))['s'] or 0
            comm = Payment.objects.filter(status='approved', created_at__date__gte=ms, created_at__date__lt=me).aggregate(s=Sum('commission'))['s'] or 0
            tut  = Payment.objects.filter(status='approved', created_at__date__gte=ms, created_at__date__lt=me).aggregate(s=Sum('tutor_income'))['s'] or 0
            chart_labels.append(label)
            chart_revenue.append(float(rev))
            chart_commission.append(float(comm))
            chart_tutor.append(float(tut))
    except ImportError:
        chart_labels = chart_revenue = chart_commission = chart_tutor = []

    method_data = {}
    for m_val, m_label in Payment.METHOD_CHOICES:
        count = Payment.objects.filter(payment_method=m_val, status='approved').count()
        if count:
            method_data[m_label] = count

    # ── Detailed commission breakdown ────────────────────────────────────────
    # Replay each booking's approved payments in order so the first-month
    # portion is attributed correctly even when it is paid in instalments.
    approved_payments = Payment.objects.filter(status='approved').select_related(
        'booking__tutor__tutor_profile'
    ).order_by('booking_id', 'created_at')

    total_first_month_commission = 0.0   # 50% charged on the first month
    total_broker_commission      = 0.0   # 40% of the first month
    total_platform_commission    = 0.0   # 10% on everything
    total_recurring_commission   = 0.0   # 10% on months 2+

    paid_so_far = {}
    for p in approved_payments:
        mr = float(p.booking.monthly_rate)
        amt = float(p.amount)

        already = paid_so_far.get(p.booking_id, 0.0)
        first_portion, excess = Payment.commission_portions(amt, mr, already)
        paid_so_far[p.booking_id] = already + amt

        ex_comm = round(excess * 0.10, 2)
        total_first_month_commission += round(first_portion * 0.50, 2)
        total_broker_commission      += round(first_portion * 0.40, 2)
        total_platform_commission    += round(first_portion * 0.10, 2) + ex_comm
        total_recurring_commission   += ex_comm

    total_broker_commission      = round(total_broker_commission, 2)
    total_platform_commission    = round(total_platform_commission, 2)
    total_first_month_commission = round(total_first_month_commission, 2)
    total_recurring_commission   = round(total_recurring_commission, 2)

    context = {
        'payments': payments,
        'stats': stats,
        'status_filter': status_filter,
        'chart_labels':     json.dumps(chart_labels),
        'chart_revenue':    json.dumps(chart_revenue),
        'chart_commission': json.dumps(chart_commission),
        'chart_tutor':      json.dumps(chart_tutor),
        'method_labels':    json.dumps(list(method_data.keys())),
        'method_counts':    json.dumps(list(method_data.values())),
        # Breakdown stats
        'first_month_commission': total_first_month_commission,
        'recurring_commission':   total_recurring_commission,
        'broker_commission':      total_broker_commission,
        'platform_commission':    total_platform_commission,
        'filter_tabs': [
            ('', 'All', '#3525cd'),
            ('pending', 'Pending', '#d97706'),
            ('approved', 'Approved', '#16a34a'),
            ('rejected', 'Rejected', '#dc2626'),
        ],
    }
    return render(request, 'payments/admin.html', context)
