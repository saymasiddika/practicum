"""SSLCommerz hosted-checkout views.

These run beside the manual transaction-ID flow in `views.py`, which is
untouched. One rule governs everything here: only `gateway.verify_settlement()`
may decide that a payment succeeded. The browser POST that SSLCommerz sends to
our success URL travels over the open internet and can be forged, so it is
treated as a hint about which payment to check — never as proof of payment.
"""
from payments.money import taka
import logging
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from bookings.models import Booking
from payments import gateway
from payments.models import Payment
from payments.views import OVERPAY_TOLERANCE, parse_amount

logger = logging.getLogger(__name__)


def _absolute(name):
    """Absolute callback URL — SSLCommerz redirects a browser to these."""
    return settings.SSLCOMMERZ_BASE_URL.rstrip('/') + reverse(name)


def _find_payment(post):
    tran_id = (post.get('tran_id') or '').strip()
    return Payment.objects.filter(gateway_tran_id=tran_id).first() if tran_id else None


def _settle(payment, result):
    """Mark a verified payment approved, exactly once.

    Both the browser redirect and the server-to-server IPN call this, so it has
    to be safe to run twice: whichever arrives first settles the payment and the
    other becomes a no-op.
    """
    with transaction.atomic():
        fresh = Payment.objects.select_for_update().get(pk=payment.pk)
        if fresh.status == 'approved':
            return False

        booking = fresh.booking
        already_paid = float(
            booking.payments.filter(status='approved')
            .exclude(pk=fresh.pk)
            .aggregate(s=Sum('amount'))['s'] or 0
        )
        commission, tutor_income = Payment.calculate_commission(
            fresh.amount, booking.monthly_rate, already_paid
        )
        fresh.commission = commission
        fresh.tutor_income = tutor_income
        fresh.status = 'approved'
        fresh.gateway_val_id = str(result.get('val_id', ''))[:80]
        fresh.gateway_bank_tran_id = str(result.get('bank_tran_id', ''))[:80]
        fresh.save(update_fields=[
            'commission', 'tutor_income', 'status',
            'gateway_val_id', 'gateway_bank_tran_id', 'updated_at',
        ])
    return True


@login_required(login_url='login')
@require_POST
def gateway_start(request):
    """Open a checkout session and hand the customer to SSLCommerz."""
    booking = get_object_or_404(Booking, id=request.POST.get('booking_id'))

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
        messages.error(request, 'This booking can no longer be paid for.')
        return redirect('booking-detail', pk=booking.id)

    if not gateway.is_configured():
        messages.error(request, 'Online payment is not available at the moment.')
        return redirect('booking-detail', pk=booking.id)

    back_to_form = f'/payments/create/?booking_id={booking.id}'

    # parse_amount rejects anything that is not a finite positive number.
    # "NaN" survives Decimal() and then raises on the first comparison, so
    # checking `amount <= 0` here was not enough — see payments.views.
    amount = parse_amount(request.POST.get('amount', ''))
    if amount is None:
        messages.error(request, 'Enter a valid amount.')
        return redirect(back_to_form)

    if amount > Decimal(str(booking.balance_due)) + OVERPAY_TOLERANCE:
        messages.error(request, f'Amount exceeds balance due ({taka(booking.balance_due)}).')
        return redirect(back_to_form)

    commission, tutor_income = Payment.calculate_commission(
        amount, booking.monthly_rate, float(booking.total_paid)
    )

    try:
        with transaction.atomic():
            if booking.payments.filter(status='pending').exists():
                raise IntegrityError('pending payment already exists')

            # Clear any earlier checkout the customer walked away from — they
            # closed the card page, or never entered the OTP. Nothing was
            # charged on those, so they must not block this attempt.
            booking.payments.filter(status='initiated').delete()

            payment = Payment.objects.create(
                booking=booking,
                amount=amount,
                commission=commission,
                tutor_income=tutor_income,
                is_first_payment=not booking.payments.filter(
                    status__in=['pending', 'approved']).exists(),
                payment_method='sslcommerz',
                # Not 'pending': nothing is awaiting a human here. The customer
                # has not finished paying, and only the validation API may say
                # they have.
                status='initiated',
                gateway_tran_id=f'EDUAI-{booking.id}-{uuid.uuid4().hex[:12].upper()}',
            )
    except IntegrityError:
        messages.error(
            request,
            'You already have a payment awaiting review. Please wait for it to '
            'be resolved before starting another.'
        )
        return redirect('booking-detail', pk=booking.id)

    try:
        checkout_url, _session_key = gateway.create_session(payment, {
            'success': _absolute('payment-gateway-success'),
            'fail':    _absolute('payment-gateway-fail'),
            'cancel':  _absolute('payment-gateway-cancel'),
            'ipn':     _absolute('payment-gateway-ipn'),
        })
    except gateway.GatewayError as exc:
        # Nothing was charged, so don't leave a dead pending row blocking
        # the student's next attempt.
        payment.delete()
        logger.error('SSLCommerz session failed for booking %s: %s', booking.id, exc)
        messages.error(request, 'Could not reach the payment gateway. Please try again.')
        return redirect(back_to_form)

    return redirect(checkout_url)


@csrf_exempt
@require_POST
def gateway_success(request):
    """Where SSLCommerz returns the browser. Verify before believing it."""
    payment = _find_payment(request.POST)
    if payment is None:
        messages.error(request, 'That payment could not be matched to a booking.')
        return redirect('payment-list')

    val_id = (request.POST.get('val_id') or '').strip()
    if not val_id:
        messages.error(request, 'The gateway did not return a validation reference.')
        return redirect('booking-detail', pk=payment.booking_id)

    try:
        ok, detail = gateway.verify_settlement(payment, val_id)
    except gateway.GatewayError as exc:
        logger.error('SSLCommerz validation unavailable for %s: %s',
                     payment.gateway_tran_id, exc)
        messages.warning(
            request,
            'We could not confirm your payment with the gateway just now. '
            'It stays pending and will be reconciled shortly.'
        )
        return redirect('booking-detail', pk=payment.booking_id)

    if not ok:
        payment.status = 'rejected'
        payment.save(update_fields=['status', 'updated_at'])
        messages.error(request, f'Payment could not be verified — {detail}.')
        return redirect('booking-detail', pk=payment.booking_id)

    _settle(payment, detail)
    messages.success(request, f'Payment of {taka(payment.amount)} received. Thank you.')
    return redirect('booking-detail', pk=payment.booking_id)


@csrf_exempt
@require_POST
def gateway_fail(request):
    payment = _find_payment(request.POST)
    if payment is None:
        messages.error(request, 'The payment did not go through.')
        return redirect('payment-list')
    if payment.status in ('initiated', 'pending'):
        payment.status = 'rejected'
        payment.save(update_fields=['status', 'updated_at'])
    messages.error(request, 'The payment did not go through. Nothing was charged.')
    return redirect('booking-detail', pk=payment.booking_id)


@csrf_exempt
@require_POST
def gateway_cancel(request):
    payment = _find_payment(request.POST)
    if payment is None:
        messages.info(request, 'Payment cancelled.')
        return redirect('payment-list')
    booking_id = payment.booking_id
    if payment.status in ('initiated', 'pending'):
        payment.delete()   # the student backed out — leave no trace
    messages.info(request, 'Payment cancelled. Nothing was charged.')
    return redirect('booking-detail', pk=booking_id)


@csrf_exempt
@require_POST
def gateway_ipn(request):
    """Server-to-server notification: the reliable path when a browser
    redirect is lost. Same verification, no user-facing response."""
    payment = _find_payment(request.POST)
    if payment is None:
        return HttpResponse('unknown transaction', status=404)

    val_id = (request.POST.get('val_id') or '').strip()
    if not val_id:
        return HttpResponse('missing val_id', status=400)

    try:
        ok, detail = gateway.verify_settlement(payment, val_id)
    except gateway.GatewayError as exc:
        logger.error('SSLCommerz IPN validation failed for %s: %s',
                     payment.gateway_tran_id, exc)
        return HttpResponse('validation unavailable', status=503)

    if not ok:
        if payment.status in ('initiated', 'pending'):
            payment.status = 'rejected'
            payment.save(update_fields=['status', 'updated_at'])
        return HttpResponse('not valid', status=200)

    _settle(payment, detail)
    return HttpResponse('ok', status=200)
