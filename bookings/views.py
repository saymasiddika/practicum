from django.views.decorators.http import require_POST
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db import transaction, IntegrityError
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from dateutil.relativedelta import relativedelta
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Booking, BookingRequest
from .refunds import quote_refund
from .serializers import BookingSerializer
from accounts.models import User
from tutors.models import Course, TutorProfile
import json

MIN_BOOKING_MONTHS = 1
MAX_BOOKING_MONTHS = 24


class SlotTaken(Exception):
    """Raised when a schedule slot is claimed between selection and save."""

@login_required(login_url='login')
def booking_list_view(request):
    bookings = Booking.objects.select_related(
        'student', 'tutor', 'tutor__tutor_profile', 'course'
    ).prefetch_related('payments').order_by('-created_at')
    if request.user.role == 'student':
        bookings = bookings.filter(student=request.user)
    else:
        bookings = bookings.filter(tutor=request.user)

    context = {
        'bookings': bookings,
    }
    return render(request, 'bookings/list.html', context)

@login_required(login_url='login')
def booking_detail_view(request, pk):
    booking = get_object_or_404(Booking, pk=pk)

    # Check if user has permission to view this booking
    if booking.student != request.user and booking.tutor != request.user:
        messages.error(request, 'You do not have permission to view this booking!')
        return redirect('booking-list')

    context = {
        'booking': booking,
    }
    return render(request, 'bookings/detail.html', context)

@login_required(login_url='login')
def booking_create_view(request):
    """Book a course.

    A course carries its own price, slot and length, so choosing one settles
    all three. The student picks a start date and nothing else.
    """
    if request.user.role != 'student':
        messages.error(request, 'Only students can create bookings!')
        return redirect('home')

    # Only courses a student could legitimately reach: published, by a real
    # tutor with a profile, and not already taken.
    courses = (
        Course.objects.filter(
            is_active=True,
            tutor__role='tutor', tutor__is_active=True,
            tutor__is_superuser=False, tutor__is_staff=False,
            tutor__tutor_profile__isnull=False,
        )
        .select_related('tutor', 'tutor__tutor_profile')
        .prefetch_related('bookings')
    )
    available = [c for c in courses if c.is_bookable]

    selected_id = request.GET.get('course') or request.GET.get('course_id')

    def render_form(chosen=None, **extra):
        context = {
            'courses': available,
            'selected_course_id': chosen or selected_id,
            'course_data_json': json.dumps({
                str(c.id): {
                    'tutor': c.tutor.get_full_name() or c.tutor.username,
                    'subject': c.subject,
                    'name': c.name,
                    'schedule': c.schedule_display,
                    'months': c.months,
                    'rate': float(c.monthly_rate),
                }
                for c in available
            }),
            'today': timezone.localdate().isoformat(),
        }
        context.update(extra)
        return render(request, 'bookings/create.html', context)

    if request.method == 'POST':
        course_id = request.POST.get('course')
        date = request.POST.get('date') or timezone.localdate()

        def reject(message):
            messages.error(request, message)
            return render_form(chosen=course_id)

        # Re-apply the same filter the form used: a posted id must not reach a
        # course the page deliberately hides.
        course = next((c for c in available if str(c.id) == str(course_id)), None)
        if course is None:
            return reject('That course is not available for booking.')

        # `months` is submitted by the page, so treat it as untrusted. It
        # defaults to the course's own length.
        try:
            months = int(request.POST.get('months') or course.months)
        except (TypeError, ValueError):
            return reject('Please choose a valid course duration.')
        if not MIN_BOOKING_MONTHS <= months <= MAX_BOOKING_MONTHS:
            return reject(
                f'Course duration must be between {MIN_BOOKING_MONTHS} and '
                f'{MAX_BOOKING_MONTHS} months.'
            )

        try:
            with transaction.atomic():
                # Re-check inside the transaction so two students cannot both
                # take the same slot.
                if Booking.objects.filter(
                    course=course, status__in=['pending', 'confirmed']
                ).exists():
                    raise SlotTaken
                booking = Booking.objects.create(
                    student=request.user,
                    tutor=course.tutor,
                    course=course,
                    date=date,
                    months=months,
                    # Captured now, so a later price change by the tutor
                    # cannot re-price this booking.
                    agreed_monthly_rate=course.monthly_rate,
                    status='pending',
                )
        except SlotTaken:
            return reject('Someone has just booked that course. Please pick another.')

        messages.success(request, 'Booking created successfully! Please proceed to payment.')
        return redirect(f'/payments/create/?booking_id={booking.id}')

    return render_form()


@login_required(login_url='login')
@require_POST
def booking_confirm_view(request, pk):
    booking = get_object_or_404(Booking, pk=pk)

    if booking.tutor != request.user:
        messages.error(request, 'Only the assigned tutor can confirm this booking!')
        return redirect('booking-list')

    if booking.status != 'pending':
        messages.error(
            request,
            f'This booking is already {booking.get_status_display().lower()} '
            f'and cannot be confirmed.'
        )
        return redirect('booking-detail', pk=pk)

    booking.status = 'confirmed'
    booking.save()
    messages.success(request, 'Booking confirmed successfully!')
    return redirect('booking-detail', pk=pk)

@login_required(login_url='login')
@require_POST
def booking_reject_view(request, pk):
    booking = get_object_or_404(Booking, pk=pk)

    if booking.tutor != request.user:
        messages.error(request, 'Only the assigned tutor can reject this booking!')
        return redirect('booking-list')

    if booking.status != 'pending':
        messages.error(
            request,
            f'This booking is already {booking.get_status_display().lower()} '
            f'and cannot be rejected.'
        )
        return redirect('booking-detail', pk=pk)

    booking.status = 'rejected'
    booking.save()
    messages.success(request, 'Booking rejected!')
    return redirect('booking-list')


@login_required(login_url='login')
@require_POST
def booking_complete_view(request, pk):
    """Tutor OR student marks the booking as completed — enables student to leave a review."""
    booking = get_object_or_404(Booking, pk=pk)

    # Both tutor and student can mark as completed
    if booking.tutor != request.user and booking.student != request.user:
        messages.error(request, 'You do not have permission to complete this booking!')
        return redirect('booking-list')

    if booking.status != 'confirmed':
        messages.error(request, 'Only confirmed bookings can be marked as completed.')
        return redirect('booking-detail', pk=pk)

    # ── Duration check ─────────────────────────────────────────────────────
    # Calculate end date: start date + booked months
    end_date = booking.date + relativedelta(months=booking.months)
    today = timezone.localdate()

    if today < end_date:
        # Count whole months first, then whatever days are left over. Counting
        # calendar months alone reads wrong at both ends: a course finishing in
        # four days would say '1 month remaining', and one finishing later this
        # month would say '0 months remaining'.
        whole_months = 0
        cursor = today
        while cursor + relativedelta(months=1) <= end_date:
            cursor += relativedelta(months=1)
            whole_months += 1
        days = (end_date - cursor).days

        parts = []
        if whole_months:
            parts.append(f'{whole_months} month{"s" if whole_months != 1 else ""}')
        if days:
            parts.append(f'{days} day{"s" if days != 1 else ""}')
        remaining = ' and '.join(parts) if parts else 'less than a day'

        # The course has not run its length. That is not a refusal any more:
        # either party may ask to finish early, with a reason for an admin to
        # verify. Send them to that form rather than turning them away.
        messages.info(
            request,
            f'This course runs until {end_date.strftime("%B %d, %Y")} '
            f'({remaining} remaining). To finish sooner, tell us why and an '
            f'admin will review it.'
        )
        return redirect(f'/bookings/{pk}/end/?kind=complete')
    # ───────────────────────────────────────────────────────────────────────

    booking.status = 'completed'
    booking.save()
    messages.success(request, 'Booking marked as completed! You can now leave a review.')
    return redirect('booking-detail', pk=pk)

@login_required(login_url='login')
@require_POST
def booking_cancel_view(request, pk):
    booking = get_object_or_404(Booking, pk=pk)

    if booking.student != request.user:
        messages.error(request, 'Only the student can cancel this booking!')
        return redirect('booking-list')

    if not booking.is_cancellable:
        messages.error(
            request,
            'This booking can no longer be cancelled — it is either closed or '
            'already has a payment against it.'
        )
        return redirect('booking-detail', pk=pk)

    booking.status = 'cancelled'
    booking.save()
    messages.success(request, 'Booking cancelled successfully.')
    return redirect('booking-list')

@login_required(login_url='login')
def booking_tutor_update_view(request, pk):
    """Tutor sets meet link and sends message to student."""
    booking = get_object_or_404(Booking, pk=pk)

    if booking.tutor != request.user:
        messages.error(request, 'Only the assigned tutor can update this booking!')
        return redirect('booking-list')

    # The link is only meant to live as long as the course is actually
    # running — the template already hides this form once the booking isn't
    # 'confirmed', but that's a UI convenience, not the enforcement. Without
    # this check a tutor could still POST directly and change the link on a
    # completed or cancelled booking.
    if booking.status != 'confirmed':
        messages.error(
            request,
            'The session link can only be set while the booking is confirmed and active.'
        )
        return redirect('booking-detail', pk=pk)

    if request.method == 'POST':
        meet_link = request.POST.get('meet_link', '').strip()
        tutor_message = request.POST.get('tutor_message', '').strip()

        # The link is rendered straight into an href, so only allow schemes a
        # browser will treat as navigation — never javascript:, data:, etc.
        if meet_link:
            try:
                URLValidator(schemes=['http', 'https'])(meet_link)
            except ValidationError:
                messages.error(
                    request,
                    'Enter a valid meeting link starting with http:// or https://'
                )
                return redirect('booking-detail', pk=pk)

        booking.meet_link = meet_link
        booking.tutor_message = tutor_message
        booking.save()
        messages.success(request, 'Meet link and message sent to student successfully!')

    return redirect('booking-detail', pk=pk)

class BookingListView(APIView):
    def get(self, request):
        if request.user.is_authenticated:
            if request.user.role == 'student':
                bookings = Booking.objects.filter(student=request.user)
            else:  # tutor
                bookings = Booking.objects.filter(tutor=request.user)
            serializer = BookingSerializer(bookings, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response({'error': 'Not authenticated'}, status=status.HTTP_401_UNAUTHORIZED)

class BookingCreateView(APIView):
    def post(self, request):
        if not request.user.is_authenticated or request.user.role != 'student':
            return Response({'error': 'Only students can create bookings'}, status=status.HTTP_403_FORBIDDEN)

        serializer = BookingSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(student=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class BookingUpdateView(APIView):
    def patch(self, request, pk):
        try:
            booking = Booking.objects.get(pk=pk)

            if request.user == booking.tutor:
                serializer = BookingSerializer(booking, data=request.data, partial=True)
                if serializer.is_valid():
                    serializer.save()
                    return Response(serializer.data, status=status.HTTP_200_OK)
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            return Response({'error': 'Only the assigned tutor can update this booking'}, status=status.HTTP_403_FORBIDDEN)
        except Booking.DoesNotExist:
            return Response({'error': 'Booking not found'}, status=status.HTTP_404_NOT_FOUND)


# ── Ending a booking early ───────────────────────────────────────────────
# Either party may ask to finish the course early or to pull out of it. An
# admin verifies the reason before anything moves, because both outcomes are
# consequential: one unlocks a public review, the other moves money.

@login_required(login_url='login')
def booking_request_end_view(request, pk):
    """Ask to finish early, or to terminate with a refund."""
    booking = get_object_or_404(Booking, pk=pk)

    if request.user not in (booking.student, booking.tutor):
        messages.error(request, 'You do not have permission to change this booking!')
        return redirect('booking-list')

    kind = request.GET.get('kind') or request.POST.get('kind') or 'terminate'
    if kind not in ('complete', 'terminate'):
        kind = 'terminate'

    if booking.status != 'confirmed':
        messages.error(
            request,
            f'This booking is {booking.get_status_display().lower()}, so there is '
            f'nothing to end.'
        )
        return redirect('booking-detail', pk=pk)

    if booking.has_run_its_course:
        messages.info(
            request,
            'This course has already reached its end date — you can mark it '
            'complete directly, no request needed.'
        )
        return redirect('booking-detail', pk=pk)

    existing = booking.open_request
    if existing is not None:
        messages.info(
            request,
            'There is already a request on this booking waiting for an admin '
            'to review it.'
        )
        return redirect('booking-detail', pk=pk)

    initiated_by = 'tutor' if request.user == booking.tutor else 'student'
    quote = quote_refund(booking, initiated_by=initiated_by)
    context = {
        'booking': booking,
        'kind': kind,
        'quote': quote,
        'initiated_by': initiated_by,
        'payout_choices': BookingRequest.PAYOUT_CHOICES,
    }

    if request.method == 'POST':
        reason = (request.POST.get('reason') or '').strip()
        payout_method = (request.POST.get('payout_method') or '').strip()
        payout_account = (request.POST.get('payout_account') or '').strip()
        payout_bank_name = (request.POST.get('payout_bank_name') or '').strip()

        # A refused request comes back with what was typed, so the reason
        # does not have to be written again.
        context['form'] = {
            'reason': reason, 'payout_method': payout_method,
            'payout_account': payout_account, 'payout_bank_name': payout_bank_name,
        }

        def refuse(problem):
            messages.error(request, problem)
            return render(request, 'bookings/request_end.html', context)

        if len(reason) < 10:
            return refuse(
                'Please explain in a sentence or two why this booking should '
                'end early — an admin has to be able to judge it.'
            )

        # Only a refundable termination needs to know where the money goes —
        # and only the student can say, because the refund is owed to them. A
        # tutor ending the booking is not the one being paid, so asking them
        # for an account number would be nonsense.
        wants_payout = (kind == 'terminate' and quote.is_refundable
                        and initiated_by == 'student')
        if wants_payout:
            if payout_method not in dict(BookingRequest.PAYOUT_CHOICES):
                return refuse('Please choose how you would like the refund.')
            if payout_method != 'office' and not payout_account:
                return refuse('Please give the account number the refund should be sent to.')
            if payout_method == 'bank' and not payout_bank_name:
                return refuse('Please name the bank and branch.')

        try:
            with transaction.atomic():
                if booking.requests.filter(status='pending').exists():
                    raise IntegrityError('a request is already open')
                BookingRequest.objects.create(
                    booking=booking,
                    requested_by=request.user,
                    kind=kind,
                    reason=reason,
                    # Frozen now. The figure the requester was shown is the
                    # figure they get, however long the admin takes.
                    refund_percent=quote.percent if kind == 'terminate' else 0,
                    refund_amount=quote.amount if kind == 'terminate' else 0,
                    refund_tier=quote.tier if kind == 'terminate' else '',
                    payout_method=payout_method if kind == 'terminate' else '',
                    payout_account=payout_account if kind == 'terminate' else '',
                    payout_bank_name=payout_bank_name if kind == 'terminate' else '',
                )
        except IntegrityError:
            messages.error(request, 'A request on this booking is already awaiting review.')
            return redirect('booking-detail', pk=pk)

        messages.success(
            request,
            'Request submitted. An admin will review your reason and you will '
            'see the outcome on this booking.'
        )
        return redirect('booking-detail', pk=pk)

    return render(request, 'bookings/request_end.html', context)
