from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError, PermissionDenied
from django.http import FileResponse, Http404
from django.views.decorators.http import require_POST
from decimal import Decimal, InvalidOperation
from PIL import Image
from .models import User, Education

MAX_AVATAR_BYTES = 5 * 1024 * 1024
ALLOWED_AVATAR_FORMATS = {'JPEG', 'PNG', 'WEBP'}

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
ALLOWED_DOCUMENT_EXTENSIONS = ('.pdf', '.jpg', '.jpeg', '.png', '.webp')

# The message a would-be tutor sees for SSC/HSC-only eligibility, shared
# between initial registration and the student-to-tutor upgrade request so
# the two paths never drift into saying something different for the same rule.
TEACHER_EDUCATION_REQUIREMENT_MESSAGE = (
    "Teacher registration requires at least current enrollment in an "
    "undergraduate/Bachelor's program. SSC/HSC-level users can register as "
    "Students but cannot register as Teachers."
)


def _validate_identity_photo(upload):
    """Shared check for a photo of an identity document — the NID at
    registration, and later an NID at role-upgrade time. Always a photo,
    never a PDF, unlike _validate_education_document.
    """
    if upload.size > MAX_AVATAR_BYTES:
        return 'Image must be 5MB or smaller.'
    try:
        image = Image.open(upload)
        image.verify()
    except Exception:
        return 'Please upload a valid JPG, PNG or WEBP image.'
    if image.format not in ALLOWED_AVATAR_FORMATS:
        return 'Image must be a JPG, PNG or WEBP image.'
    upload.seek(0)
    return None


def _validate_education_document(upload):
    """A result slip or certificate can be a scan (image) or a PDF export,
    unlike the avatar/NID uploads which are always photos — so this checks a
    PDF's magic bytes instead of trying to open it as an image.
    """
    if upload.size > MAX_DOCUMENT_BYTES:
        return 'Document must be 10MB or smaller.'

    name = upload.name.lower()
    if not name.endswith(ALLOWED_DOCUMENT_EXTENSIONS):
        return 'Document must be a PDF, JPG, PNG or WEBP file.'

    if name.endswith('.pdf'):
        header = upload.read(5)
        upload.seek(0)
        if header != b'%PDF-':
            return 'Please upload a valid PDF file.'
        return None

    try:
        image = Image.open(upload)
        image.verify()
    except Exception:
        return 'Please upload a valid JPG, PNG or WEBP image.'
    upload.seek(0)
    return None


def register_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('confirm_password', '')
        role = request.POST.get('role', 'student')
        first_name = request.POST.get('first_name', '')
        last_name = request.POST.get('last_name', '')
        nid_number= request.POST.get('nid_number', '')
        education_level = (request.POST.get('education_level') or '').strip()
        education_institution = (request.POST.get('education_institution') or '').strip()

        # What the visitor typed, handed back on any refusal so they only fix
        # the field that was wrong. Passwords are never sent back.
        form = {
            'username': username, 'email': email, 'role': role,
            'first_name': first_name, 'last_name': last_name, 'nid_number': nid_number,
            'education_level': education_level, 'education_institution': education_institution,
        }

        def refuse(*problems):
            for problem in problems:
                messages.error(request, problem)
            return render(request, 'accounts/register.html', {'form': form})

        # The browser form marks these required, but nothing stops a request
        # arriving without them. Without this check a missing password reaches
        # validate_password(None) and a missing username reaches create_user(''),
        # and both raise — turning a bad request into a 500.
        if not username or not email or not password:
            return refuse('Username, email and password are all required.')

        if password != password_confirm:
            return refuse('Passwords do not match!')
        if role == 'tutor' and not nid_number:
            return refuse('NID Number is required for tutor registration!')

        nid_image = request.FILES.get('nid_image')
        if role == 'tutor':
            if not nid_image:
                return refuse('A photo or scan of your NID is required for tutor registration!')
            image_error = _validate_identity_photo(nid_image)
            if image_error:
                return refuse(image_error)

        # Teacher/Tutor eligibility: SSC/HSC alone is not enough — reaching
        # undergraduate level or beyond is required, whether that degree is
        # finished or still in progress. Checked server-side because the
        # dropdown only ever offers qualifying levels, but nothing stops a
        # crafted request from posting 'ssc' or an empty value directly.
        if role == 'tutor':
            if not education_institution:
                return refuse('Your institution name is required for tutor registration!')
            if education_level not in Education.QUALIFYING_LEVELS:
                return refuse(TEACHER_EDUCATION_REQUIREMENT_MESSAGE)

        if User.objects.filter(username=username).exists():
            return refuse('Username already exists!')

        if User.objects.filter(email__iexact=email).exists():
            return refuse('Email already exists!')

        # AUTH_PASSWORD_VALIDATORS are not applied by create_user(); run them
        # explicitly so registration matches the change-password rules.
        try:
            validate_password(password, User(
                username=username, email=email,
                first_name=first_name, last_name=last_name,
            ))
        except ValidationError as exc:
            return refuse(*exc.messages)

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            role=role,
            first_name=first_name,
            last_name=last_name,
            nid_number=nid_number
        )

        if role == 'tutor':
            user.nid_image = nid_image
            user.nid_verification_status = 'pending'
            user.save(update_fields=['nid_image', 'nid_verification_status'])

            # A minimal seed record — registration only asks for the level
            # and institution needed to check eligibility. The tutor fills
            # in dates, GPA and any further degrees on the Education page.
            Education.objects.create(
                user=user,
                education_level=education_level,
                institution=education_institution,
            )

        messages.success(request, 'Account created successfully! Please login.')
        return redirect('login')

    return render(request, 'accounts/register.html')


def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, f'Welcome back, {user.username}!')
            return redirect('dashboard')
        else:
            messages.error(request, 'Invalid username or password!')

    return render(request, 'accounts/login.html')


def logout_view(request):
    logout(request)
    messages.success(request, 'You have been logged out successfully!')
    return redirect('home')


@login_required(login_url='login')
def profile_view(request):
    return render(request, 'accounts/profile.html')


@login_required(login_url='login')
def edit_profile_view(request):
    user = request.user
    if request.method == 'POST':
        email = (request.POST.get('email') or user.email or '').strip()

        # Registration rejects duplicate addresses; enforce the same here.
        if email and User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
            messages.error(request, 'That email address is already in use by another account.')
            return redirect('edit_profile')

        # Basic info
        user.first_name = request.POST.get('first_name', user.first_name)
        user.last_name  = request.POST.get('last_name', user.last_name)
        user.email      = email
        user.phone      = request.POST.get('phone', user.phone or '')
        user.bio        = request.POST.get('bio', user.bio or '')

        # Avatar upload — the model field's own checks never run here because
        # this view assigns request.FILES directly instead of using a form.
        upload = request.FILES.get('avatar')
        if upload:
            if upload.size > MAX_AVATAR_BYTES:
                messages.error(request, 'Profile picture must be 5MB or smaller.')
                return redirect('edit_profile')
            try:
                image = Image.open(upload)
                image.verify()
            except Exception:
                messages.error(request, 'Please upload a valid JPG, PNG or WEBP image.')
                return redirect('edit_profile')
            if image.format not in ALLOWED_AVATAR_FORMATS:
                messages.error(request, 'Profile picture must be a JPG, PNG or WEBP image.')
                return redirect('edit_profile')
            upload.seek(0)

        if upload:
            # Delete old avatar if exists
            if user.avatar:
                try:
                    import os
                    if os.path.isfile(user.avatar.path):
                        os.remove(user.avatar.path)
                except Exception:
                    pass
            user.avatar = request.FILES['avatar']

        user.save()
        messages.success(request, 'Profile updated successfully!')
        return redirect('profile')

    return render(request, 'accounts/edit_profile.html')


@login_required(login_url='login')
def education_view(request):
    return render(request, 'accounts/education.html', {
        'educations': request.user.educations.all(),
    })


def _read_education_form(request):
    """Shared read+validate for the add/edit education forms."""
    institution = (request.POST.get('institution') or '').strip()
    education_level = (request.POST.get('education_level') or '').strip()
    valid_levels = {value for value, _ in Education.LEVEL_CHOICES}

    if not institution:
        return None, 'Institution name is required.'
    if education_level not in valid_levels:
        return None, 'Please select an education level.'

    def to_year(field):
        raw = (request.POST.get(field) or '').strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def to_month(field):
        raw = (request.POST.get(field) or '').strip()
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if 1 <= value <= 12 else None

    def to_gpa(field):
        raw = (request.POST.get(field) or '').strip()
        if not raw:
            return None, None
        try:
            value = Decimal(raw)
        except InvalidOperation:
            return None, 'GPA/CGPA must be a number.'
        # Decimal('nan') and Decimal('inf') parse without raising, so a
        # finite-range check is the only thing that actually catches them.
        if not value.is_finite() or value < 0 or value > 10:
            return None, 'GPA/CGPA must be a number between 0 and 10.'
        return value, None

    gpa, error = to_gpa('gpa')
    if error:
        return None, error
    gpa_scale, error = to_gpa('gpa_scale')
    if error:
        return None, error

    # `end_year`/`end_month` are kept even while currently studying — for a
    # student mid-degree they hold the EXPECTED finish, not an actual one.
    # Clearing them here would throw away exactly the value a student sets
    # early and comes back to correct once they actually graduate.
    currently_studying = bool(request.POST.get('currently_studying'))
    data = {
        'education_level': education_level,
        'institution': institution,
        'degree': (request.POST.get('degree') or '').strip(),
        'field_of_study': (request.POST.get('field_of_study') or '').strip(),
        'start_month': to_month('start_month'),
        'start_year': to_year('start_year'),
        'end_month': to_month('end_month'),
        'end_year': to_year('end_year'),
        'currently_studying': currently_studying,
        'gpa': gpa,
        'gpa_scale': gpa_scale,
        'description': (request.POST.get('description') or '').strip(),
    }
    return data, None


@login_required(login_url='login')
def education_add_view(request):
    if request.method == 'POST':
        data, error = _read_education_form(request)
        document = request.FILES.get('document')
        if not error and document:
            error = _validate_education_document(document)

        if error:
            messages.error(request, error)
            return render(request, 'accounts/education_form.html', {
                'is_new': True, 'form': request.POST,
                'level_choices': Education.LEVEL_CHOICES,
                'pre_university_levels': list(Education.PRE_UNIVERSITY_LEVELS),
                'month_choices': Education.MONTH_CHOICES,
            })

        # A freshly-attached document has never been looked at by anyone —
        # 'pending' until an admin verifies or rejects it. No document means
        # nothing to review, so the default 'unverified' stands.
        verification_status = 'pending' if document else 'unverified'
        Education.objects.create(
            user=request.user, document=document,
            verification_status=verification_status, **data,
        )
        messages.success(request, 'Education added.')
        return redirect('education')

    return render(request, 'accounts/education_form.html', {
        'is_new': True,
        'level_choices': Education.LEVEL_CHOICES,
        'pre_university_levels': list(Education.PRE_UNIVERSITY_LEVELS),
        'month_choices': Education.MONTH_CHOICES,
    })


@login_required(login_url='login')
def education_edit_view(request, pk):
    entry = get_object_or_404(Education, pk=pk, user=request.user)

    if request.method == 'POST':
        data, error = _read_education_form(request)
        document = request.FILES.get('document')
        if not error and document:
            error = _validate_education_document(document)

        if error:
            messages.error(request, error)
            return render(request, 'accounts/education_form.html', {
                'is_new': False, 'entry': entry, 'form': request.POST,
                'level_choices': Education.LEVEL_CHOICES,
                'pre_university_levels': list(Education.PRE_UNIVERSITY_LEVELS),
                'month_choices': Education.MONTH_CHOICES,
            })

        for field, value in data.items():
            setattr(entry, field, value)
        # A new upload replaces the old one; leaving the field empty keeps
        # whatever document is already on file rather than clearing it. A
        # replaced document has not been looked at again yet, so it goes
        # back to 'pending' rather than keeping a stale 'verified'/'rejected'.
        if document:
            entry.document = document
            entry.verification_status = 'pending'
        entry.save()
        messages.success(request, 'Education updated.')
        return redirect('education')

    return render(request, 'accounts/education_form.html', {
        'is_new': False, 'entry': entry,
        'level_choices': Education.LEVEL_CHOICES,
        'pre_university_levels': list(Education.PRE_UNIVERSITY_LEVELS),
        'month_choices': Education.MONTH_CHOICES,
    })


@login_required(login_url='login')
@require_POST
def education_delete_view(request, pk):
    entry = get_object_or_404(Education, pk=pk, user=request.user)
    entry.delete()
    messages.success(request, 'Education entry removed.')
    return redirect('education')


@login_required(login_url='login')
def change_password_view(request):
    if request.method == 'POST':
        current_password = request.POST.get('current_password')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')

        if not request.user.check_password(current_password):
            messages.error(request, 'Current password is incorrect.')
            return redirect('change_password')

        if new_password != confirm_password:
            messages.error(request, 'New passwords do not match.')
            return redirect('change_password')

        try:
            validate_password(new_password, request.user)
        except ValidationError as exc:
            for problem in exc.messages:
                messages.error(request, problem)
            return redirect('change_password')

        request.user.set_password(new_password)
        request.user.save()
        update_session_auth_hash(request, request.user)  # Keep user logged in
        messages.success(request, 'Password changed successfully!')
        return redirect('profile')

    return render(request, 'accounts/change_password.html')


@login_required(login_url='login')
def delete_account_view(request):
    if request.method == 'POST':
        password = request.POST.get('password')
        if not request.user.check_password(password):
            messages.error(request, 'Incorrect password. Account not deleted.')
            return redirect('delete_account')
        user = request.user
        logout(request)
        user.delete()
        messages.success(request, 'Your account has been permanently deleted.')
        return redirect('home')
    return render(request, 'accounts/delete_account.html')


@login_required(login_url='login')
def apply_to_teach_view(request):
    """A student's path to becoming a tutor after the fact.

    Registration only ever asks for education/NID once, at signup — a
    student who was SSC/HSC-only back then and has since reached
    undergraduate level had no way to become a tutor without this. The
    eligibility rule itself is not duplicated here: it is the same
    Education.user_qualifies_for_tutoring() check used at registration and
    again before a TutorProfile can be created, so this is a third
    independent enforcement of one rule, not a second rule.
    """
    user = request.user
    if user.role == 'tutor':
        messages.info(request, 'Your account is already a tutor account.')
        return redirect('profile')
    if user.role != 'student':
        messages.error(request, 'Only student accounts can apply to become a teacher.')
        return redirect('profile')

    qualifies = Education.user_qualifies_for_tutoring(user)

    if request.method == 'POST':
        if not qualifies:
            messages.error(request, TEACHER_EDUCATION_REQUIREMENT_MESSAGE)
            return redirect('apply_to_teach')

        nid_number = (request.POST.get('nid_number') or '').strip()
        nid_image = request.FILES.get('nid_image')

        def refuse(problem):
            messages.error(request, problem)
            return redirect('apply_to_teach')

        if not nid_number:
            return refuse('NID Number is required to apply as a teacher.')
        if not nid_image:
            return refuse('A photo or scan of your NID is required to apply as a teacher.')
        image_error = _validate_identity_photo(nid_image)
        if image_error:
            return refuse(image_error)

        user.role = 'tutor'
        user.nid_number = nid_number
        user.nid_image = nid_image
        user.nid_verification_status = 'pending'
        user.save(update_fields=['role', 'nid_number', 'nid_image', 'nid_verification_status'])

        messages.success(
            request,
            'You are now a teacher! Set up your tutor profile so students can find you.',
        )
        return redirect('tutor-create-profile')

    return render(request, 'accounts/apply_to_teach.html', {
        'qualifies': qualifies,
        'educations': user.educations.all(),
    })


# ── Verification documents ──────────────────────────────────────────────
# NID photos and education documents live in private storage (see
# accounts/storage.py) precisely so the only way to ever see one is through
# these two views — never a public /media/ URL. Both use the same rule: the
# owner may see their own, and staff/superuser may see anyone's for
# verification. Nobody else gets past PermissionDenied, and a missing file
# is a plain 404 rather than leaking whether the field is set.

def _can_view_verification_document(request_user, owner):
    return request_user == owner or request_user.is_staff or request_user.is_superuser


@login_required(login_url='login')
def nid_image_view(request, pk):
    target = get_object_or_404(User, pk=pk)
    if not _can_view_verification_document(request.user, target):
        raise PermissionDenied('You do not have permission to view this document.')
    if not target.nid_image:
        raise Http404
    return FileResponse(target.nid_image.open('rb'))


@login_required(login_url='login')
def education_document_view(request, pk):
    entry = get_object_or_404(Education, pk=pk)
    if not _can_view_verification_document(request.user, entry.user):
        raise PermissionDenied('You do not have permission to view this document.')
    if not entry.document:
        raise Http404
    return FileResponse(entry.document.open('rb'))
