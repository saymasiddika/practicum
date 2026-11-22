from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from bookings.models import Booking
from reviews.models import Review
from payments.models import Payment
from django.db.models import Sum


def home(request):
    from tutors.models import TutorProfile

    featured_tutors = TutorProfile.objects.filter(
        user__is_active=True, user__is_superuser=False, user__is_staff=False,
        user__role='tutor'
    ).select_related('user').order_by('-rating', '-created_at')[:3]

    # Subjects someone could book right now, for the hero's "Popular" links.
    from chatbot.catalogue import subject_summary
    return render(request, 'home.html', {
        'tutors': featured_tutors,
        'subjects': list(subject_summary())[:5],
    })


@login_required(login_url='login')
def dashboard_view(request):
    user = request.user

    # Superusers and staff see the Admin Overview at /dashboard/
    if user.is_superuser or user.is_staff:
        from .admin_views import admin_overview
        return admin_overview(request)

    if user.role == 'student':
        bookings = Booking.objects.filter(student=user).select_related(
            'tutor', 'tutor__tutor_profile'
        ).prefetch_related('payments', 'review').order_by('-date')

        total_bookings = bookings.count()
        completed_bookings = bookings.filter(status='completed').count()
        pending_bookings  = bookings.filter(status='pending').count()
        approved_bookings = bookings.filter(status='confirmed').count()

        # Every approved payment counts — bookings are paid in instalments.
        total_spent = Payment.objects.filter(
            booking__student=user, status='approved'
        ).aggregate(s=Sum('amount'))['s'] or 0
        average_rating = (
            sum(b.review.rating for b in bookings if hasattr(b, 'review') and b.review)
            / bookings.filter(review__isnull=False).count()
            if bookings.filter(review__isnull=False).count() > 0 else 0
        )

        # Recent 4 bookings for session cards
        recent_bookings = bookings[:4]

        # Unique tutors student has booked
        tutor_ids = bookings.values_list('tutor', flat=True).distinct()
        from accounts.models import User
        recent_tutors = User.objects.filter(
            id__in=tutor_ids, role='tutor'
        ).select_related('tutor_profile')[:6]

        # Percentages for the progress bars — only where a real ratio exists.
        completed_pct = round(completed_bookings / total_bookings * 100) if total_bookings else 0
        rating_pct    = round(average_rating / 5 * 100) if average_rating else 0

        context = {
            'completed_pct':     completed_pct,
            'rating_pct':        rating_pct,
            'total_bookings':    total_bookings,
            'completed_bookings': completed_bookings,
            'pending_bookings':  pending_bookings,
            'approved_bookings': approved_bookings,
            'total_spent':       round(total_spent, 2),
            'average_rating':    round(average_rating, 1),
            'recent_bookings':   recent_bookings,
            'recent_tutors':     recent_tutors,
        }

    else:  # tutor
        bookings = Booking.objects.filter(tutor=user).select_related(
            'student'
        ).prefetch_related('payments').order_by('-date')

        total_students   = bookings.values('student').distinct().count()
        pending_bookings = bookings.filter(status='pending').count()
        confirmed_bookings = bookings.filter(status='confirmed').count()
        # Net of commission, across every approved instalment — `amount` is
        # gross revenue and overstates what the tutor actually receives.
        total_earned = Payment.objects.filter(
            booking__tutor=user, status='approved'
        ).aggregate(s=Sum('tutor_income'))['s'] or 0

        tutor_profile = getattr(user, 'tutor_profile', None)
        rating = tutor_profile.rating if tutor_profile else 0

        recent_students = bookings.order_by('-date')[:6]

        total_bookings = bookings.count()
        pending_pct = round(pending_bookings / total_bookings * 100) if total_bookings else 0
        rating_pct  = round(float(rating) / 5 * 100) if rating else 0

        context = {
            'pending_pct':       pending_pct,
            'rating_pct':        rating_pct,
            'total_students':    total_students,
            'pending_bookings':  pending_bookings,
            'confirmed_bookings': confirmed_bookings,
            'total_earned':      round(total_earned, 2),
            'rating':            rating,
            'recent_students':   recent_students,
        }

    return render(request, 'dashboard.html', context)
