from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Education, User

from .models import Course, TutorProfile
from .serializers import TutorProfileSerializer

MIN_COURSE_MONTHS = 1
MAX_COURSE_MONTHS = 24
MAX_RATE = 1000000


def _visible_tutors():
    """Tutors a student may actually find and book.

    Staff and superusers are excluded deliberately: an admin account is not a
    product listing.
    """
    return User.objects.filter(
        role='tutor', is_active=True, is_superuser=False, is_staff=False,
        tutor_profile__isnull=False,
    ).select_related('tutor_profile').prefetch_related('courses__bookings')


def tutor_list_view(request):
    search = request.GET.get('search', '')
    subject = request.GET.get('subject', '')
    rating = request.GET.get('rating', '')

    tutors = _visible_tutors()

    if search:
        # Match the tutor's name OR anything they teach. Filtering inside the
        # already-restricted queryset, so the staff exclusion above cannot be
        # bypassed by a crafted search.
        tutors = tutors.filter(
            Q(username__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(courses__subject__icontains=search)
            | Q(courses__name__icontains=search)
        )

    if subject:
        tutors = tutors.filter(courses__subject__icontains=subject)

    if rating:
        try:
            tutors = tutors.filter(tutor_profile__rating__gte=float(rating))
        except (TypeError, ValueError):
            rating = ''      # ignore an unparseable filter rather than erroring

    # Joining to courses can repeat a tutor once per matching course.
    tutors = tutors.distinct().order_by('-tutor_profile__rating')

    # The subjects on offer, for the filter dropdown.
    subjects = (Course.objects.filter(is_active=True)
                .values_list('subject', flat=True).distinct().order_by('subject'))

    return render(request, 'tutors/list.html', {
        'tutors': tutors,
        'search': search,
        'subject': subject,
        'rating': rating,
        'subjects': subjects,
    })


def tutor_detail_view(request, pk):
    """One tutor, and everything they teach.

    Looked up by USER id, not TutorProfile id — a long-standing quirk of this
    project that the templates rely on.
    """
    profile = get_object_or_404(
        TutorProfile.objects.select_related('user').prefetch_related('user__courses__bookings'),
        user_id=pk,
    )
    tutor = profile.user
    reviews = tutor.tutor_bookings.filter(review__isnull=False).select_related('review')

    return render(request, 'tutors/detail.html', {
        'tutor': tutor,
        'courses': [c for c in tutor.courses.all() if c.is_active],
        'reviews': [b.review for b in reviews if hasattr(b, 'review')],
    })


# ── The profile: who the tutor is ────────────────────────────────────────

@login_required(login_url='login')
def tutor_create_profile_view(request):
    if request.user.role != 'tutor':
        messages.error(request, 'Only tutors can create a profile!')
        return redirect('home')

    if TutorProfile.objects.filter(user=request.user).exists():
        messages.info(request, 'You already have a profile. Here it is.')
        return redirect('edit-profile')

    # Backend enforcement of tutor eligibility, independent of whatever the
    # registration form checked — this is the gate that actually stops a
    # profile (and therefore courses, and appearing in search) from existing
    # for an SSC/HSC-only account, regardless of how the account was made.
    if not Education.user_qualifies_for_tutoring(request.user):
        messages.error(
            request,
            'You need at least an undergraduate-level education on file to '
            'become a tutor — SSC or HSC alone is not enough. Add your '
            'education first.',
        )
        return redirect('education')

    if request.method == 'POST':
        bio = (request.POST.get('bio') or '').strip()
        if not bio:
            messages.error(request, 'Please write a short introduction.')
            return redirect('tutor-create-profile')

        TutorProfile.objects.create(user=request.user, bio=bio)
        messages.success(
            request,
            'Profile created. Now add a course so students can book you.',
        )
        return redirect('course-add')

    return render(request, 'tutors/edit.html', {'is_new': True})


@login_required(login_url='login')
def tutor_edit_profile_view(request):
    if request.user.role != 'tutor':
        messages.error(request, 'Only tutors can access this page!')
        return redirect('home')

    profile, created = TutorProfile.objects.get_or_create(
        user=request.user, defaults={'bio': ''})

    if request.method == 'POST':
        bio = (request.POST.get('bio') or '').strip()
        if not bio:
            messages.error(request, 'Please write a short introduction.')
            return redirect('edit-profile')

        profile.bio = bio
        profile.save()
        messages.success(request, 'Profile updated.')
        return redirect('profile')

    return render(request, 'tutors/edit.html', {
        'tutor_profile': profile,
        'is_new': created,
        'courses': request.user.courses.all(),
    })


# ── Courses: what the tutor teaches ──────────────────────────────────────

def _read_course_form(request):
    """Pull a course out of the POST, or return (None, error message)."""
    subject = (request.POST.get('subject') or '').strip()
    name = (request.POST.get('name') or '').strip()
    days = request.POST.getlist('days')
    time = (request.POST.get('time') or '').strip()

    if not subject:
        return None, 'Please give the subject you teach.'
    if not name:
        return None, 'Please give the course a name.'

    valid = {choice for choice, _label in Course.DAY_CHOICES}
    days = [d for d in days if d in valid]
    if not days:
        return None, 'Please choose at least one day.'
    if not time:
        return None, 'Please choose a start time.'

    try:
        months = int(request.POST.get('months') or 0)
    except (TypeError, ValueError):
        return None, 'Please give a valid course length in months.'
    if not MIN_COURSE_MONTHS <= months <= MAX_COURSE_MONTHS:
        return None, (f'Course length must be between {MIN_COURSE_MONTHS} and '
                      f'{MAX_COURSE_MONTHS} months.')

    try:
        rate = float(request.POST.get('monthly_rate') or 0)
    except (TypeError, ValueError):
        return None, 'Please give a valid monthly fee.'
    if not 0 < rate <= MAX_RATE:
        return None, 'The monthly fee must be a positive amount.'

    return {
        'subject': subject[:60],
        'name': name[:120],
        'days': ','.join(days),
        'time': time,
        'months': months,
        'monthly_rate': rate,
    }, None


def _course_form_echo(request):
    """What the tutor typed, so a refused course form comes back filled in."""
    return {
        'form': {
            'subject': request.POST.get('subject', ''),
            'name': request.POST.get('name', ''),
            'time': request.POST.get('time', ''),
            'months': request.POST.get('months', ''),
            'monthly_rate': request.POST.get('monthly_rate', ''),
            'is_active': bool(request.POST.get('is_active')),
        },
        'selected_days': request.POST.getlist('days'),
    }


@login_required(login_url='login')
def course_list_view(request):
    """The tutor's own courses, with the button that adds another."""
    if request.user.role != 'tutor':
        messages.error(request, 'Only tutors have courses.')
        return redirect('home')

    has_profile = TutorProfile.objects.filter(user=request.user).exists()
    return render(request, 'tutors/courses.html', {
        'courses': request.user.courses.prefetch_related('bookings').order_by('created_at'),
        'has_profile': has_profile,
    })


@login_required(login_url='login')
def course_add_view(request):
    if request.user.role != 'tutor':
        messages.error(request, 'Only tutors can add courses.')
        return redirect('home')

    # A course without a profile is unreachable — the listing only shows
    # tutors who have one.
    if not TutorProfile.objects.filter(user=request.user).exists():
        messages.info(
            request,
            'Set up your profile first, then add the courses you teach.',
        )
        return redirect('tutor-create-profile')

    if request.method == 'POST':
        data, error = _read_course_form(request)
        if error:
            messages.error(request, error)
            return render(request, 'tutors/course_form.html', {
                'day_choices': Course.DAY_CHOICES,
                'is_new': True,
                **_course_form_echo(request),
            })

        course = Course.objects.create(tutor=request.user, **data)
        messages.success(
            request,
            f'“{course.name}” is now published — students can book it.',
        )
        return redirect('course-list')

    return render(request, 'tutors/course_form.html', {
        'day_choices': Course.DAY_CHOICES,
        'is_new': True,
    })


@login_required(login_url='login')
def course_edit_view(request, pk):
    course = get_object_or_404(Course, pk=pk, tutor=request.user)

    if request.method == 'POST':
        data, error = _read_course_form(request)
        if error:
            messages.error(request, error)
            return render(request, 'tutors/course_form.html', {
                'course': course,
                'day_choices': Course.DAY_CHOICES,
                'is_new': False,
                **_course_form_echo(request),
            })

        for field, value in data.items():
            setattr(course, field, value)
        course.is_active = bool(request.POST.get('is_active'))
        course.save()
        messages.success(request, f'“{course.name}” updated.')
        return redirect('course-list')

    return render(request, 'tutors/course_form.html', {
        'course': course,
        'day_choices': Course.DAY_CHOICES,
        'selected_days': course.days.split(','),
        'is_new': False,
    })


@login_required(login_url='login')
@require_POST
def course_delete_view(request, pk):
    course = get_object_or_404(Course, pk=pk, tutor=request.user)

    # A course with a live booking is unpublished rather than deleted, so the
    # student's booking keeps pointing at something real.
    if course.active_booking is not None:
        course.is_active = False
        course.save(update_fields=['is_active', 'updated_at'])
        messages.info(
            request,
            f'“{course.name}” has a student on it, so it has been unpublished '
            f'rather than deleted. It will take no new bookings.',
        )
    else:
        name = course.name
        course.delete()
        messages.success(request, f'“{name}” removed.')
    return redirect('course-list')


@login_required(login_url='login')
@require_POST
def course_toggle_view(request, pk):
    """Publish or unpublish without deleting."""
    course = get_object_or_404(Course, pk=pk, tutor=request.user)
    course.is_active = not course.is_active
    course.save(update_fields=['is_active', 'updated_at'])
    messages.success(
        request,
        f'“{course.name}” is now {"published" if course.is_active else "unpublished"}.',
    )
    return redirect('course-list')


# ── Unrouted DRF scaffolding, kept as-is ─────────────────────────────────

class TutorListView(APIView):
    def get(self, request):
        tutors = TutorProfile.objects.all()
        serializer = TutorProfileSerializer(tutors, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
class TutorDetailView(APIView):
    def get(self, request, pk):
        try:
            tutor = TutorProfile.objects.get(pk=pk)
            serializer = TutorProfileSerializer(tutor)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except TutorProfile.DoesNotExist:
            return Response({'error': 'Tutor not found'},
                            status=status.HTTP_404_NOT_FOUND)


class TutorCreateView(APIView):
    def post(self, request):
        if not request.user.is_authenticated or request.user.role != 'tutor':
            return Response({'error': 'Only tutors can create a profile'},
                            status=status.HTTP_403_FORBIDDEN)

        serializer = TutorProfileSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
