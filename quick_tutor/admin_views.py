from payments.money import taka
from django.views.decorators.http import require_POST
"""
Custom Admin Dashboard Views
Replaces Django's built-in /admin/ panel with a Luminary-styled dashboard.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Count, Q
from django.http import JsonResponse

from accounts.models import User, Education
from bookings.models import Booking, BookingRequest
from tutors.models import TutorProfile
from reviews.models import Review
from payments.models import Payment


def _gate(test, denial):
    """Build a decorator that admits only users passing `test`."""
    def decorator(view_fn):
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            if not test(request.user):
                messages.error(request, denial)
                return redirect('home')
            return view_fn(request, *args, **kwargs)
        wrapper.__name__ = view_fn.__name__
        wrapper.__doc__ = view_fn.__doc__
        return wrapper
    return decorator


# Read access and day-to-day moderation: staff or superuser.
staff_required = _gate(
    lambda u: u.is_staff or u.is_superuser,
    'Access denied. Admin only.',
)

# Anything that can grant privileges or destroy accounts: superuser only.
# Staff must not be able to promote users or delete them, or `is_staff`
# would be self-propagating and equivalent to full control.
superuser_required = _gate(
    lambda u: u.is_superuser,
    'Access denied. This action requires a superuser account.',
)


def _delete_impact(obj):
    """What a cascade delete of `obj` would actually destroy.

    Deleting a user removes their bookings, and with them the payment records
    those bookings carry — so an admin must see the financial consequence
    before confirming, not just "cannot be undone".
    """
    from django.db.models.deletion import Collector

    collector = Collector(using='default')
    collector.collect([obj])

    labels = {
        'User': 'user account',
        'Booking': 'booking',
        'Payment': 'payment record',
        'Review': 'review',
        'TutorProfile': 'tutor profile',
        'Course': 'course',
    }
    # Rows reachable via `data`, plus those Django will "fast delete" (models
    # with no cascades of their own — Payment lands here, and it is the one
    # that matters most).
    found = {}
    for model, instances in collector.data.items():
        found.setdefault(model.__name__, []).extend(instances)
    for queryset in collector.fast_deletes:
        found.setdefault(queryset.model.__name__, []).extend(queryset)

    lines, money = [], 0
    for name, instances in found.items():
        if name not in labels or (name == 'User' and len(instances) == 1):
            continue
        count = len(instances)
        if not count:
            continue
        noun = labels[name] + ('s' if count != 1 else '')
        lines.append(f'{count} {noun}')
        if name == 'Payment':
            money = sum(
                float(p.amount) for p in instances
                if getattr(p, 'status', '') == 'approved'
            )
    return {'lines': lines, 'approved_total': round(money, 2)}


# ─────────────────────────────────────────────
#  OVERVIEW
# ─────────────────────────────────────────────
@staff_required
def admin_overview(request):
    total_users    = User.objects.count()
    total_students = User.objects.filter(role='student').count()
    total_tutors   = User.objects.filter(role='tutor').count()
    total_bookings = Booking.objects.count()
    total_payments = Payment.objects.filter(status='approved').count()
    total_revenue  = Payment.objects.filter(status='approved').aggregate(s=Sum('amount'))['s'] or 0
    total_commission = Payment.objects.filter(status='approved').aggregate(s=Sum('commission'))['s'] or 0
    total_reviews  = Review.objects.count()
    pending_payments = Payment.objects.filter(status='pending').count()
    pending_bookings = Booking.objects.filter(status='pending').count()

    recent_users    = User.objects.order_by('-date_joined')[:5]
    recent_bookings = Booking.objects.select_related('student', 'tutor').order_by('-created_at')[:5]
    recent_payments = Payment.objects.select_related('booking__student', 'booking__tutor').order_by('-created_at')[:5]

    context = {
        'total_users': total_users,
        'total_students': total_students,
        'total_tutors': total_tutors,
        'total_bookings': total_bookings,
        'total_payments': total_payments,
        'total_revenue': total_revenue,
        'total_commission': total_commission,
        'total_reviews': total_reviews,
        'pending_payments': pending_payments,
        'pending_bookings': pending_bookings,
        'recent_users': recent_users,
        'recent_bookings': recent_bookings,
        'recent_payments': recent_payments,
    }
    return render(request, 'admin_panel/overview.html', context)


# ─────────────────────────────────────────────
#  USER MANAGEMENT
# ─────────────────────────────────────────────
@staff_required
def admin_users(request):
    role_filter = request.GET.get('role', '')
    search      = request.GET.get('q', '')
    users = User.objects.select_related('tutor_profile').order_by('-date_joined')
    if role_filter:
        users = users.filter(role=role_filter)
    if search:
        users = users.filter(Q(username__icontains=search) | Q(email__icontains=search) | Q(first_name__icontains=search))
    context = {'users': users, 'role_filter': role_filter, 'search': search}
    return render(request, 'admin_panel/users.html', context)


@staff_required
def admin_user_detail(request, pk):
    user = get_object_or_404(User, pk=pk)
    bookings = []
    if user.role == 'student':
        bookings = Booking.objects.filter(student=user).select_related('tutor').order_by('-created_at')
    elif user.role == 'tutor':
        bookings = Booking.objects.filter(tutor=user).select_related('student').order_by('-created_at')
    payments = Payment.objects.filter(booking__in=bookings).order_by('-created_at')
    context = {'target_user': user, 'bookings': bookings, 'payments': payments}
    return render(request, 'admin_panel/user_detail.html', context)


@superuser_required
def admin_user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        user.first_name  = request.POST.get('first_name', user.first_name)
        user.last_name   = request.POST.get('last_name', user.last_name)
        user.email       = request.POST.get('email', user.email)
        user.phone       = request.POST.get('phone', user.phone)
        user.bio         = request.POST.get('bio', user.bio)
        user.is_active   = request.POST.get('is_active') == 'on'
        user.is_staff    = request.POST.get('is_staff') == 'on'
        user.save()
        messages.success(request, f'User {user.username} updated.')
        return redirect('admin-user-detail', pk=pk)
    return render(request, 'admin_panel/user_edit.html', {'target_user': user})


@superuser_required
@require_POST
def admin_user_toggle_active(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user == request.user:
        messages.error(request, "You can't deactivate yourself.")
    else:
        user.is_active = not user.is_active
        user.save()
        state = 'activated' if user.is_active else 'deactivated'
        messages.success(request, f'User {user.username} {state}.')
    return redirect('admin-users')


@superuser_required
def admin_user_delete(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user == request.user:
        messages.error(request, "You can't delete yourself.")
        return redirect('admin-users')
    if request.method == 'POST':
        username = user.username
        user.delete()
        messages.success(request, f'User {username} deleted.')
        return redirect('admin-users')
    return render(request, 'admin_panel/confirm_delete.html', {
        'obj': user, 'obj_type': 'User', 'cancel_url': '/admin-panel/users/',
        'impact': _delete_impact(user),
    })


# ─────────────────────────────────────────────
#  BOOKING MANAGEMENT
# ─────────────────────────────────────────────
@staff_required
def admin_bookings(request):
    status_filter = request.GET.get('status', '')
    search        = request.GET.get('q', '')
    bookings = Booking.objects.select_related('student', 'tutor', 'tutor__tutor_profile').order_by('-created_at')
    if status_filter:
        bookings = bookings.filter(status=status_filter)
    if search:
        bookings = bookings.filter(
            Q(student__username__icontains=search) | Q(tutor__username__icontains=search)
        )
    context = {'bookings': bookings, 'status_filter': status_filter, 'search': search,
               'STATUS_CHOICES': Booking.STATUS_CHOICES}
    return render(request, 'admin_panel/bookings.html', context)


@staff_required
@require_POST
def admin_booking_status(request, pk, new_status):
    booking = get_object_or_404(Booking, pk=pk)
    valid = [value for value, _ in Booking.STATUS_CHOICES]
    if new_status in valid:
        booking.status = new_status
        booking.save()
        messages.success(request, f'Booking #{pk} marked as {new_status}.')
    return redirect('admin-bookings')


@staff_required
def admin_booking_delete(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    if request.method == 'POST':
        booking.delete()
        messages.success(request, f'Booking #{pk} deleted.')
        return redirect('admin-bookings')
    return render(request, 'admin_panel/confirm_delete.html', {
        'obj': booking, 'obj_type': 'Booking', 'cancel_url': '/admin-panel/bookings/',
        'impact': _delete_impact(booking),
    })


# ─────────────────────────────────────────────
#  TUTOR PROFILE MANAGEMENT
# ─────────────────────────────────────────────
@staff_required
def admin_tutors(request):
    search = request.GET.get('q', '')
    profiles = (TutorProfile.objects.select_related('user')
                .prefetch_related('user__courses')
                .order_by('-created_at'))
    if search:
        # Subject lives on the courses now, so searching a subject means
        # searching what the tutor teaches.
        profiles = profiles.filter(
            Q(user__username__icontains=search)
            | Q(user__courses__subject__icontains=search)
            | Q(user__courses__name__icontains=search)
        ).distinct()
    context = {'profiles': profiles, 'search': search}
    return render(request, 'admin_panel/tutors.html', context)


@staff_required
def admin_tutor_edit(request, pk):
    profile = get_object_or_404(
        TutorProfile.objects.select_related('user').prefetch_related('user__courses'),
        pk=pk,
    )
    if request.method == 'POST':
        # Subject and price are no longer here — they belong to the tutor's
        # courses, which are theirs to set. An admin edits the profile text
        # and, where a rating needs correcting, the rating.
        profile.bio = request.POST.get('bio', profile.bio)
        profile.rating = request.POST.get('rating', profile.rating)
        profile.save()
        messages.success(request, f'Tutor profile for {profile.user.username} updated.')
        return redirect('admin-tutors')
    return render(request, 'admin_panel/tutor_edit.html', {
        'profile': profile,
        'courses': profile.user.courses.all(),
    })


@staff_required
def admin_tutor_delete(request, pk):
    profile = get_object_or_404(TutorProfile, pk=pk)
    if request.method == 'POST':
        username = profile.user.username
        profile.delete()
        messages.success(request, f'Tutor profile for {username} deleted.')
        return redirect('admin-tutors')
    return render(request, 'admin_panel/confirm_delete.html', {
        'obj': profile, 'obj_type': 'Tutor Profile', 'cancel_url': '/admin-panel/tutors/'
    })


# ─────────────────────────────────────────────
#  REVIEW MANAGEMENT
# ─────────────────────────────────────────────
@staff_required
def admin_reviews(request):
    rating_filter = request.GET.get('rating', '')
    reviews = Review.objects.select_related('booking__student', 'booking__tutor').order_by('-created_at')
    if rating_filter:
        reviews = reviews.filter(rating=rating_filter)
    context = {'reviews': reviews, 'rating_filter': rating_filter}
    return render(request, 'admin_panel/reviews.html', context)


@staff_required
def admin_review_delete(request, pk):
    review = get_object_or_404(Review, pk=pk)
    if request.method == 'POST':
        review.delete()
        messages.success(request, f'Review #{pk} deleted.')
        return redirect('admin-reviews')
    return render(request, 'admin_panel/confirm_delete.html', {
        'obj': review, 'obj_type': 'Review', 'cancel_url': '/admin-panel/reviews/'
    })


# ── Early-end requests ───────────────────────────────────────────────────

@staff_required
def admin_requests(request):
    """The queue of requests to finish or terminate a booking early.

    An admin's job here is to judge the stated reason. The refund figure is
    not theirs to set — it was fixed by policy when the request was made.
    """
    status = request.GET.get('status', 'pending')
    # booking__payments is prefetched because the template shows each
    # booking's total_paid, which walks its payments. Without it the page
    # issues one extra query per row — measured at 10 queries for 3 requests
    # and 34 for 27.
    requests_qs = BookingRequest.objects.select_related(
        'booking', 'booking__student', 'booking__tutor', 'requested_by', 'reviewed_by'
    ).prefetch_related('booking__payments')
    if status in dict(BookingRequest.STATUS_CHOICES):
        requests_qs = requests_qs.filter(status=status)

    counts = {
        key: BookingRequest.objects.filter(status=key).count()
        for key, _label in BookingRequest.STATUS_CHOICES
    }
    owed = BookingRequest.objects.filter(
        kind='terminate', status='approved', refund_settled=False
    ).aggregate(total=Sum('refund_amount'))['total'] or 0

    return render(request, 'admin_panel/requests.html', {
        'requests': requests_qs,
        'active_status': status,
        'counts': counts,
        'statuses': BookingRequest.STATUS_CHOICES,
        'refund_owed': owed,
    })


@staff_required
@require_POST
def admin_request_decide(request, pk, decision):
    booking_request = get_object_or_404(BookingRequest, pk=pk)
    note = (request.POST.get('admin_note') or '').strip()

    if not booking_request.is_open:
        messages.error(
            request,
            f'Request #{booking_request.id} has already been '
            f'{booking_request.get_status_display().lower()}.'
        )
        return redirect('admin-requests')

    if decision == 'approve':
        booking_request.approve(request.user, note)
        if booking_request.kind == 'complete':
            messages.success(
                request,
                f'Approved. Booking #{booking_request.booking_id} is now complete '
                f'and the student can leave a review.'
            )
        elif booking_request.refund_amount > 0:
            messages.success(
                request,
                f'Approved. Booking #{booking_request.booking_id} is cancelled and '
                f'{taka(booking_request.refund_amount)} is owed to '
                f'{booking_request.booking.student.username}. Mark it settled once '
                f'you have sent the money.'
            )
        else:
            messages.success(
                request,
                f'Approved. Booking #{booking_request.booking_id} is cancelled. No '
                f'refund is due under the policy.'
            )
    elif decision == 'decline':
        booking_request.decline(request.user, note)
        messages.success(
            request,
            f'Declined. Booking #{booking_request.booking_id} carries on unchanged.'
        )
    else:
        messages.error(request, 'Unknown decision.')

    return redirect('admin-requests')


@staff_required
@require_POST
def admin_request_settle(request, pk):
    """Record that the refund has actually been paid out."""
    from django.utils import timezone

    booking_request = get_object_or_404(BookingRequest, pk=pk)
    if not booking_request.owes_a_refund:
        messages.error(request, 'There is no refund outstanding on that request.')
        return redirect('admin-requests')

    booking_request.refund_settled = True
    booking_request.refund_settled_at = timezone.now()
    booking_request.save(update_fields=['refund_settled', 'refund_settled_at', 'updated_at'])
    messages.success(
        request,
        f'Marked {taka(booking_request.refund_amount)} as refunded to '
        f'{booking_request.booking.student.username}.'
    )
    return redirect('admin-requests')


# ── Chat assistant ───────────────────────────────────────────────────────

@staff_required
def admin_chat_log(request):
    """What people asked the assistant, and what it could not answer.

    The unanswered list is the point of this page. It is the only reliable
    signal for what the knowledge base is missing — guessing at what users
    will ask is how FAQ pages end up answering questions nobody has.
    """
    from chatbot.models import ChatMessage

    total = ChatMessage.objects.count()
    answered = ChatMessage.objects.filter(answered=True).count()
    rate = round(answered * 100 / total) if total else 0

    unanswered = (ChatMessage.objects.filter(answered=False)
                  .order_by('-asked_at')[:40])

    popular = (ChatMessage.objects.filter(answered=True)
               .values('intent')
               .annotate(times=Count('id'))
               .order_by('-times')[:12])

    return render(request, 'admin_panel/chat_log.html', {
        'total': total,
        'answered': answered,
        'unanswered_count': total - answered,
        'rate': rate,
        'unanswered': unanswered,
        'popular': popular,
        'recent': ChatMessage.objects.select_related('user')[:25],
    })


# ─────────────────────────────────────────────
#  TEACHER VERIFICATION
# ─────────────────────────────────────────────
_VERIFICATION_DECISIONS = {
    'verify': 'verified',
    'reject': 'rejected',
    'reset': 'pending',
}


@staff_required
@require_POST
def admin_verify_nid(request, pk, decision):
    """Move a tutor's NID verification status — the account-level identity
    check, separate from any one Education record."""
    target = get_object_or_404(User, pk=pk)
    status = _VERIFICATION_DECISIONS.get(decision)
    if status is None:
        messages.error(request, 'Unknown decision.')
        return redirect('admin-user-detail', pk=pk)

    target.nid_verification_status = status
    target.save(update_fields=['nid_verification_status'])
    messages.success(request, f"{target.username}'s NID marked {status}.")
    return redirect('admin-user-detail', pk=pk)


@staff_required
@require_POST
def admin_verify_education(request, pk, decision):
    """Move one Education record's verification status — a check on that
    specific qualification's document, not on the account as a whole."""
    entry = get_object_or_404(Education, pk=pk)
    status = _VERIFICATION_DECISIONS.get(decision)
    if status is None:
        messages.error(request, 'Unknown decision.')
        return redirect('admin-user-detail', pk=entry.user_id)

    entry.verification_status = status
    entry.save(update_fields=['verification_status'])
    messages.success(request, f"{entry.institution} entry marked {status}.")
    return redirect('admin-user-detail', pk=entry.user_id)
