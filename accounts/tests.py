"""Tests for authentication, account data rules, and the admin permission gates.

The gate tests exist because a decorator named `superuser_required` in fact
admitted any staff user, which made `is_staff` self-propagating: a staff
account could promote others and delete accounts. Each route is pinned to the
tier it belongs to.
"""
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from accounts.models import User
from tutors.models import TutorProfile

STRONG_PASSWORD = 'Str0ngPass!23'


def png_bytes(size=32):
    buffer = BytesIO()
    Image.new('RGB', (size, size), (80, 60, 200)).save(buffer, 'PNG')
    return buffer.getvalue()


class RegistrationTests(TestCase):
    def _register(self, **overrides):
        data = {
            'username': 'newcomer', 'email': 'newcomer@example.com',
            'password': STRONG_PASSWORD, 'confirm_password': STRONG_PASSWORD,
            'role': 'student', 'first_name': 'New', 'last_name': 'Comer',
        }
        data.update(overrides)
        # Tutor registration requires an NID image; tests that are not
        # specifically exercising that rule get a valid one for free so they
        # do not all have to know about it.
        if data.get('role') == 'tutor' and 'nid_image' not in overrides:
            data['nid_image'] = SimpleUploadedFile(
                'nid.png', png_bytes(), content_type='image/png',
            )
        return self.client.post('/accounts/register/', data)

    def test_a_student_can_register(self):
        self._register()
        self.assertTrue(User.objects.filter(username='newcomer').exists())

    def test_password_validators_are_applied(self):
        """create_user() does not run AUTH_PASSWORD_VALIDATORS by itself."""
        weak = ['1', 'password', '12345678', 'abc']
        for password in weak:
            with self.subTest(password=password):
                self._register(password=password, confirm_password=password)
                self.assertFalse(
                    User.objects.filter(username='newcomer').exists(),
                    f'{password!r} should not be accepted',
                )

    def test_a_password_resembling_the_username_is_refused(self):
        self._register(username='alexander', password='alexander',
                       confirm_password='alexander')
        self.assertFalse(User.objects.filter(username='alexander').exists())

    def test_a_malformed_request_is_refused_not_crashed(self):
        """Registration is open to anonymous visitors, so a request that skips
        the browser form must be refused politely rather than raising.

        A missing password used to reach validate_password(None) and a missing
        username used to reach create_user(''), and both raise — returning a
        500 (and, with DEBUG on, a full traceback) to whoever sent it.
        """
        bodies = [
            ('nothing at all', {}),
            ('no password', {'username': 'x', 'email': 'x@example.com'}),
            ('no username', {'email': 'y@example.com', 'password': STRONG_PASSWORD,
                             'confirm_password': STRONG_PASSWORD}),
            ('no email', {'username': 'z', 'password': STRONG_PASSWORD,
                          'confirm_password': STRONG_PASSWORD}),
            ('blank username', {'username': '   ', 'email': 'w@example.com',
                                'password': STRONG_PASSWORD,
                                'confirm_password': STRONG_PASSWORD}),
        ]
        for label, body in bodies:
            with self.subTest(body=label):
                response = self.client.post('/accounts/register/', body)
                self.assertLess(response.status_code, 500,
                                f'{label} should be refused, not crash')
                self.assertEqual(User.objects.count(), 0,
                                 f'{label} should not create an account')

    def test_mismatched_confirmation_is_refused(self):
        self._register(confirm_password='SomethingElse!9')
        self.assertFalse(User.objects.filter(username='newcomer').exists())

    def test_a_tutor_must_supply_an_nid_number(self):
        self._register(role='tutor', nid_number='')
        self.assertFalse(User.objects.filter(username='newcomer').exists())

    def test_a_tutor_registering_with_an_nid_number_succeeds(self):
        self._register(role='tutor', nid_number='1234567890',
                       education_level='undergraduate', education_institution='Dhaka University')
        tutor = User.objects.get(username='newcomer')
        self.assertEqual(tutor.nid_number, '1234567890')
        self.assertEqual(tutor.role, 'tutor')
        self.assertTrue(tutor.nid_image)

    def test_a_tutor_must_supply_an_nid_image(self):
        self._register(role='tutor', nid_number='1234567890', nid_image='',
                       education_level='undergraduate', education_institution='Dhaka University')
        self.assertFalse(User.objects.filter(username='newcomer').exists())

    def test_a_tutor_nid_image_must_actually_be_an_image(self):
        bogus = SimpleUploadedFile('nid.png', b'not an image', content_type='image/png')
        self._register(role='tutor', nid_number='1234567890', nid_image=bogus,
                       education_level='undergraduate', education_institution='Dhaka University')
        self.assertFalse(User.objects.filter(username='newcomer').exists())

    def test_a_tutor_must_meet_the_education_requirement(self):
        """SSC/HSC alone must not be enough to register as a tutor — the rule
        has to hold server-side, since the registration dropdown only ever
        offers qualifying levels and a crafted request could post anything.
        """
        for level in ('ssc', 'hsc', '', 'not-a-real-level'):
            with self.subTest(level=level):
                self._register(role='tutor', nid_number='1234567890',
                               education_level=level, education_institution='ABC School')
                self.assertFalse(
                    User.objects.filter(username='newcomer').exists(),
                    f'{level!r} should not be enough to register as a tutor',
                )

    def test_a_tutor_must_supply_an_institution_name(self):
        self._register(role='tutor', nid_number='1234567890',
                       education_level='undergraduate', education_institution='')
        self.assertFalse(User.objects.filter(username='newcomer').exists())

    def test_qualifying_levels_let_a_tutor_register(self):
        from accounts.models import Education
        for level in Education.QUALIFYING_LEVELS:
            with self.subTest(level=level):
                User.objects.filter(username='newcomer').delete()
                self._register(role='tutor', nid_number='1234567890',
                               education_level=level, education_institution='Some Institution')
                tutor = User.objects.get(username='newcomer')
                self.assertEqual(
                    tutor.educations.get().education_level, level,
                )

    def test_registration_creates_the_seed_education_record(self):
        from accounts.models import Education
        self._register(role='tutor', nid_number='1234567890',
                       education_level='graduate', education_institution='BUET')
        tutor = User.objects.get(username='newcomer')
        entry = tutor.educations.get()
        self.assertEqual(entry.education_level, 'graduate')
        self.assertEqual(entry.institution, 'BUET')
        self.assertTrue(Education.user_qualifies_for_tutoring(tutor))

    def test_duplicate_usernames_and_emails_are_refused(self):
        self._register()
        self._register(email='different@example.com')
        self.assertEqual(User.objects.filter(username='newcomer').count(), 1)
        self._register(username='another')
        self.assertFalse(User.objects.filter(username='another').exists())


class ProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'owner', email='owner@example.com', password=STRONG_PASSWORD, role='student'
        )
        User.objects.create_user('taken', email='taken@example.com',
                                 password=STRONG_PASSWORD, role='tutor')
        self.client.force_login(self.user)

    def _edit(self, **overrides):
        data = {'first_name': 'A', 'last_name': 'B', 'email': self.user.email}
        data.update(overrides)
        return self.client.post('/accounts/edit-profile/', data)

    def test_email_can_be_changed_to_an_unused_address(self):
        self._edit(email='fresh@example.com')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'fresh@example.com')

    def test_email_cannot_duplicate_another_account(self):
        self._edit(email='taken@example.com')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'owner@example.com')

    def test_duplicate_check_ignores_case(self):
        self._edit(email='TAKEN@EXAMPLE.COM')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'owner@example.com')

    def test_a_genuine_image_is_accepted_as_an_avatar(self):
        upload = SimpleUploadedFile('a.png', png_bytes(), content_type='image/png')
        self._edit(avatar=upload)
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar)
        self.user.avatar.delete(save=True)

    def test_non_images_are_refused(self):
        cases = [
            ('big.jpg', b'\xff\xd8\xff' + b'A' * (6 * 1024 * 1024), 'image/jpeg'),
            ('fake.png', b'not an image at all', 'image/png'),
            ('x.svg', b'<svg onload="alert(1)"></svg>', 'image/svg+xml'),
        ]
        for name, content, content_type in cases:
            with self.subTest(upload=name):
                upload = SimpleUploadedFile(name, content, content_type=content_type)
                self._edit(avatar=upload)
                self.user.refresh_from_db()
                self.assertFalse(self.user.avatar, f'{name} should be refused')

    def test_changing_password_applies_the_same_validators(self):
        self.client.post('/accounts/change-password/', {
            'current_password': STRONG_PASSWORD,
            'new_password': '1', 'confirm_password': '1',
        })
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(STRONG_PASSWORD))

    def test_a_strong_new_password_is_accepted(self):
        self.client.post('/accounts/change-password/', {
            'current_password': STRONG_PASSWORD,
            'new_password': 'An0ther!Strong1', 'confirm_password': 'An0ther!Strong1',
        })
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('An0ther!Strong1'))


class AdminPermissionTests(TestCase):
    """Staff moderate; only superusers grant privileges or delete accounts."""

    # Reachable by staff and superusers alike.
    MODERATION_ROUTES = [
        '/dashboard/',
        '/admin-panel/users/',
        '/admin-panel/bookings/',
        '/admin-panel/tutors/',
        '/admin-panel/reviews/',
        '/payments/manage/',
    ]

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            'root', password=STRONG_PASSWORD, role='tutor'
        )
        self.staff = User.objects.create_user(
            'helper', password=STRONG_PASSWORD, role='student', is_staff=True
        )
        self.student = User.objects.create_user(
            'plain', password=STRONG_PASSWORD, role='student'
        )
        self.victim = User.objects.create_user(
            'victim', password=STRONG_PASSWORD, role='student'
        )

    def test_moderation_routes_are_open_to_staff_and_superusers(self):
        for user in (self.staff, self.superuser):
            self.client.force_login(user)
            for route in self.MODERATION_ROUTES:
                with self.subTest(user=user.username, route=route):
                    self.assertEqual(self.client.get(route).status_code, 200)

    def test_moderation_routes_are_closed_to_ordinary_users(self):
        self.client.force_login(self.student)
        for route in self.MODERATION_ROUTES[1:]:
            with self.subTest(route=route):
                response = self.client.get(route, follow=True)
                self.assertTrue(
                    response.redirect_chain,
                    f'{route} must not be reachable by a student',
                )

    def test_staff_cannot_promote_another_user(self):
        self.client.force_login(self.staff)
        self.client.post(f'/admin-panel/users/{self.victim.id}/edit/', {
            'first_name': 'x', 'last_name': 'y', 'email': 'v@example.com',
            'is_active': 'on', 'is_staff': 'on',
        })
        self.victim.refresh_from_db()
        self.assertFalse(
            self.victim.is_staff,
            'staff must not be able to grant staff — it would be self-propagating',
        )

    def test_staff_cannot_delete_a_user(self):
        self.client.force_login(self.staff)
        self.client.post(f'/admin-panel/users/{self.victim.id}/delete/')
        self.assertTrue(User.objects.filter(pk=self.victim.pk).exists())

    def test_staff_cannot_deactivate_a_user(self):
        self.client.force_login(self.staff)
        self.client.post(f'/admin-panel/users/{self.victim.id}/toggle/')
        self.victim.refresh_from_db()
        self.assertTrue(self.victim.is_active)

    def test_a_superuser_can_promote_deactivate_and_delete(self):
        self.client.force_login(self.superuser)
        self.client.post(f'/admin-panel/users/{self.victim.id}/edit/', {
            'first_name': 'x', 'last_name': 'y', 'email': 'v@example.com',
            'is_active': 'on', 'is_staff': 'on',
        })
        self.victim.refresh_from_db()
        self.assertTrue(self.victim.is_staff)

        self.client.post(f'/admin-panel/users/{self.victim.id}/toggle/')
        self.victim.refresh_from_db()
        self.assertFalse(self.victim.is_active)

        self.client.post(f'/admin-panel/users/{self.victim.id}/delete/')
        self.assertFalse(User.objects.filter(pk=self.victim.pk).exists())

    def test_a_superuser_cannot_delete_themselves(self):
        self.client.force_login(self.superuser)
        self.client.post(f'/admin-panel/users/{self.superuser.id}/delete/')
        self.assertTrue(User.objects.filter(pk=self.superuser.pk).exists())

    def test_privileged_routes_reject_get(self):
        self.client.force_login(self.superuser)
        response = self.client.get(f'/admin-panel/users/{self.victim.id}/toggle/')
        self.victim.refresh_from_db()
        self.assertEqual(response.status_code, 405)
        self.assertTrue(self.victim.is_active)

    def test_anonymous_visitors_are_sent_to_login(self):
        for route in self.MODERATION_ROUTES:
            with self.subTest(route=route):
                response = self.client.get(route, follow=True)
                self.assertTrue(response.redirect_chain)


class NavigationByRoleTests(TestCase):
    """The shared sidebar must match who is actually looking at it.

    Its branches were `staff / student / else`, with no anonymous case, so
    visitors fell through to the tutor menu and were shown "My Students" and
    a Logout link with no way to sign in.
    """

    def setUp(self):
        self.tutor = User.objects.create_user('atutor', password=STRONG_PASSWORD, role='tutor')
        TutorProfile.objects.create(user=self.tutor, bio='')
        # Per-instance: a class-level list would accumulate stale ids between tests.
        self.public_pages = ['/tutors/', f'/tutors/{self.tutor.id}/']

    def test_visitors_are_offered_a_way_in_not_a_way_out(self):
        for page in self.public_pages:
            with self.subTest(page=page):
                html = self.client.get(page).content.decode()
                self.assertIn('/accounts/login/', html)
                self.assertIn('/accounts/register/', html)
                self.assertNotIn('/accounts/logout/', html)

    def test_visitors_are_not_shown_the_tutor_menu(self):
        for page in self.public_pages:
            with self.subTest(page=page):
                html = self.client.get(page).content.decode()
                # Assert on the nav destinations — "Tutor Profile" is also the
                # page heading on a tutor's own profile page.
                self.assertNotIn('/tutors/edit-profile/', html)
                self.assertNotIn('My Students', html)

    def test_each_role_sees_its_own_menu(self):
        student = User.objects.create_user('astudent', password=STRONG_PASSWORD, role='student')
        admin = User.objects.create_superuser('anadmin', password=STRONG_PASSWORD, role='tutor')
        expectations = [
            (student, 'My Bookings', ['My Students', 'User Management']),
            (self.tutor, 'My Students', ['My Bookings', 'User Management']),
            (admin, 'User Management', ['My Students', 'My Bookings']),
        ]
        for user, expected, absent in expectations:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                html = self.client.get('/tutors/').content.decode()
                self.assertIn(expected, html)
                for item in absent:
                    self.assertNotIn(item, html)
                self.assertIn('/accounts/logout/', html)
                self.assertNotIn('/accounts/login/', html)


class AuthenticationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'member', password=STRONG_PASSWORD, role='student'
        )

    def test_valid_credentials_log_in(self):
        response = self.client.post('/accounts/login/', {
            'username': 'member', 'password': STRONG_PASSWORD,
        })
        self.assertEqual(response.status_code, 302)

    def test_a_deactivated_account_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()
        self.client.post('/accounts/login/', {
            'username': 'member', 'password': STRONG_PASSWORD,
        })
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_a_wrong_password_does_not_log_in(self):
        self.client.post('/accounts/login/', {
            'username': 'member', 'password': 'wrong-password',
        })
        self.assertNotIn('_auth_user_id', self.client.session)


class RefusedRegistrationKeepsInputTests(TestCase):
    """A refused registration keeps what was typed, but never the password."""

    def test_typed_details_survive_a_refusal_but_passwords_do_not(self):
        response = self.client.post('/accounts/register/', {
            'username': 'keepme', 'email': 'keep@example.com', 'role': 'tutor',
            'first_name': 'Nadia', 'last_name': 'Islam', 'nid_number': '1234567890',
            'password': STRONG_PASSWORD, 'confirm_password': 'Different!99',
        })
        self.assertEqual(response.status_code, 200)
        for typed in ('value="keepme"', 'value="keep@example.com"', 'value="Nadia"',
                      'value="Islam"', 'value="1234567890"', 'value="tutor" checked'):
            with self.subTest(typed=typed):
                self.assertContains(response, typed)
        self.assertNotContains(response, STRONG_PASSWORD)
        self.assertNotContains(response, 'Different!99')
        self.assertFalse(User.objects.filter(username='keepme').exists())


class ApplyToTeachTests(TestCase):
    """A student's path to becoming a tutor after registration — the same
    eligibility rule as registration and TutorProfile creation, enforced a
    third time so a student who was SSC/HSC-only at signup and has since
    reached undergraduate level is not stuck as a student forever.
    """

    def setUp(self):
        self.student = User.objects.create_user('upgrader', password=STRONG_PASSWORD, role='student')
        self.client.force_login(self.student)

    def _apply(self, **overrides):
        from accounts.models import Education
        data = {
            'nid_number': '1234567890',
            'nid_image': SimpleUploadedFile('nid.png', png_bytes(), content_type='image/png'),
        }
        data.update(overrides)
        return self.client.post('/accounts/apply-to-teach/', data, follow=True)

    def test_no_education_on_file_is_refused(self):
        self._apply()
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, 'student')

    def test_ssc_or_hsc_only_is_refused(self):
        from accounts.models import Education
        Education.objects.create(user=self.student, education_level='hsc', institution='ABC College')
        self._apply()
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, 'student')

    def test_undergraduate_or_higher_with_nid_upgrades_the_role(self):
        from accounts.models import Education
        Education.objects.create(user=self.student, education_level='undergraduate',
                                 institution='XYZ University', currently_studying=True)
        response = self._apply()
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, 'tutor')
        self.assertEqual(self.student.nid_number, '1234567890')
        self.assertTrue(self.student.nid_image)
        self.assertRedirects(response, '/tutors/create-profile/')

    def test_qualifying_education_without_nid_is_refused(self):
        from accounts.models import Education
        Education.objects.create(user=self.student, education_level='undergraduate',
                                 institution='XYZ University', currently_studying=True)
        self._apply(nid_number='')
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, 'student')

    def test_a_tutor_cannot_apply_again(self):
        tutor = User.objects.create_user('already_tutor', password=STRONG_PASSWORD, role='tutor')
        self.client.force_login(tutor)
        response = self._apply()
        self.assertEqual(response.redirect_chain[-1][0], '/accounts/profile/')


class VerificationDocumentAccessTests(TestCase):
    """NID photos and education documents must never be reachable except
    through the owner or an admin — see accounts/storage.py and the
    nid_image_view / education_document_view permission checks.
    """
    def setUp(self):
        from accounts.models import Education
        self.owner = User.objects.create_user('doc_owner', password=STRONG_PASSWORD, role='tutor')
        self.owner.nid_image = SimpleUploadedFile('nid.png', png_bytes(), content_type='image/png')
        self.owner.save()
        self.entry = Education.objects.create(
            user=self.owner, education_level='undergraduate', institution='X University',
            document=SimpleUploadedFile('cert.pdf', b'%PDF-1.4 fake', content_type='application/pdf'),
        )
        self.stranger = User.objects.create_user('stranger', password=STRONG_PASSWORD, role='student')
        self.staff = User.objects.create_user('staffmember', password=STRONG_PASSWORD,
                                              role='student', is_staff=True)

    def test_files_are_not_under_the_public_media_root(self):
        self.assertNotIn('\\media\\', self.owner.nid_image.path.replace('/', '\\'))
        self.assertIn('private_media', self.owner.nid_image.path)

    def test_anonymous_is_redirected_to_login(self):
        for url in (f'/accounts/nid-image/{self.owner.id}/',
                    f'/accounts/education/{self.entry.id}/document/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/accounts/login/', response.url)

    def test_the_owner_can_view_their_own_documents(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(f'/accounts/nid-image/{self.owner.id}/').status_code, 200)
        self.assertEqual(
            self.client.get(f'/accounts/education/{self.entry.id}/document/').status_code, 200,
        )

    def test_a_stranger_is_forbidden(self):
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(f'/accounts/nid-image/{self.owner.id}/').status_code, 403)
        self.assertEqual(
            self.client.get(f'/accounts/education/{self.entry.id}/document/').status_code, 403,
        )

    def test_staff_can_view_for_verification(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(f'/accounts/nid-image/{self.owner.id}/').status_code, 200)
        self.assertEqual(
            self.client.get(f'/accounts/education/{self.entry.id}/document/').status_code, 200,
        )

    def test_a_missing_document_is_a_plain_404_not_a_crash(self):
        bare = User.objects.create_user('no_docs', password=STRONG_PASSWORD, role='tutor')
        self.client.force_login(bare)
        response = self.client.get(f'/accounts/nid-image/{bare.id}/')
        self.assertEqual(response.status_code, 404)


class VerificationStatusTests(TestCase):
    """Uploading a document/NID is self-reported until an admin acts on it —
    status starts 'pending' the moment there is something to check, and only
    quick_tutor.admin_views can move it to 'verified' or 'rejected'.
    """

    def setUp(self):
        self.tutor = User.objects.create_user('status_tutor', password=STRONG_PASSWORD, role='tutor')
        self.staff = User.objects.create_user('status_staff', password=STRONG_PASSWORD,
                                              role='student', is_staff=True)
        self.client.force_login(self.tutor)

    def test_registration_nid_starts_pending(self):
        response = self.client.post('/accounts/register/', {
            'username': 'pendingtutor', 'email': 'pt@example.com',
            'password': STRONG_PASSWORD, 'confirm_password': STRONG_PASSWORD,
            'role': 'tutor', 'first_name': 'P', 'last_name': 'T',
            'nid_number': '1234567890',
            'nid_image': SimpleUploadedFile('nid.png', png_bytes(), content_type='image/png'),
            'education_level': 'undergraduate', 'education_institution': 'Dhaka University',
        })
        tutor = User.objects.get(username='pendingtutor')
        self.assertEqual(tutor.nid_verification_status, 'pending')

    def test_education_without_a_document_stays_unverified(self):
        response = self.client.post('/accounts/education/add/', {
            'education_level': 'undergraduate', 'institution': 'X University',
        })
        entry = self.tutor.educations.get()
        self.assertEqual(entry.verification_status, 'unverified')

    def test_education_with_a_document_starts_pending(self):
        self.client.post('/accounts/education/add/', {
            'education_level': 'undergraduate', 'institution': 'X University',
            'document': SimpleUploadedFile('cert.pdf', b'%PDF-1.4 x', content_type='application/pdf'),
        })
        entry = self.tutor.educations.get()
        self.assertEqual(entry.verification_status, 'pending')

    def test_replacing_a_document_resets_status_to_pending(self):
        from accounts.models import Education
        entry = Education.objects.create(
            user=self.tutor, education_level='undergraduate', institution='X University',
            document=SimpleUploadedFile('old.pdf', b'%PDF-1.4 x', content_type='application/pdf'),
            verification_status='verified',
        )
        self.client.post(f'/accounts/education/{entry.id}/edit/', {
            'education_level': 'undergraduate', 'institution': 'X University',
            'document': SimpleUploadedFile('new.pdf', b'%PDF-1.4 y', content_type='application/pdf'),
        })
        entry.refresh_from_db()
        self.assertEqual(entry.verification_status, 'pending')

    def test_only_staff_can_verify_an_nid(self):
        response = self.client.post(f'/admin-panel/users/{self.tutor.id}/verify-nid/verify/', follow=True)
        self.tutor.refresh_from_db()
        self.assertEqual(self.tutor.nid_verification_status, 'unverified')

        self.client.force_login(self.staff)
        self.client.post(f'/admin-panel/users/{self.tutor.id}/verify-nid/verify/')
        self.tutor.refresh_from_db()
        self.assertEqual(self.tutor.nid_verification_status, 'verified')

    def test_only_staff_can_verify_an_education_document(self):
        # Created directly via the ORM (not the add-education view), so the
        # auto-pending-on-upload behaviour does not apply here — start it at
        # 'pending' explicitly, since that is the state a real upload leaves
        # it in and it is what this test is actually about: who may move it
        # on from there.
        from accounts.models import Education
        entry = Education.objects.create(
            user=self.tutor, education_level='undergraduate', institution='X University',
            document=SimpleUploadedFile('cert.pdf', b'%PDF-1.4 x', content_type='application/pdf'),
            verification_status='pending',
        )

        self.client.post(f'/admin-panel/educations/{entry.id}/verify/reject/', follow=True)
        entry.refresh_from_db()
        self.assertEqual(entry.verification_status, 'pending')

        self.client.force_login(self.staff)
        self.client.post(f'/admin-panel/educations/{entry.id}/verify/reject/')
        entry.refresh_from_db()
        self.assertEqual(entry.verification_status, 'rejected')
