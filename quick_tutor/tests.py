"""Tests for the project's own configuration.

settings.py is the one module no other test imports deliberately — it is
imported for them, once, before anything runs. So the guards that stop the
project starting with development credentials in production are checked here
by re-executing the module under a controlled environment.
"""
import os
import subprocess
import sys
from pathlib import Path

from django.core.management.utils import get_random_secret_key
from django.test import SimpleTestCase, TestCase

BASE_DIR = Path(__file__).resolve().parent.parent


def _check(env):
    """Run `manage.py check` in a subprocess with the given environment.

    A subprocess is the honest way to test this: the guards run at import time,
    and settings are already imported in this process.
    """
    environ = dict(os.environ)
    # Ignore any .env on the developer's machine so the test is hermetic.
    environ.update({'DJANGO_SECRET_KEY': '', 'DJANGO_ALLOWED_HOSTS': ''})
    environ.update(env)
    environ = {k: v for k, v in environ.items() if v != ''}
    return subprocess.run(
        [sys.executable, 'manage.py', 'check'],
        cwd=BASE_DIR, env=environ, capture_output=True, text=True,
    )


class ProductionGuardTests(SimpleTestCase):
    """With DEBUG off, the project must refuse to start on dev credentials."""

    def test_the_published_secret_key_is_refused_in_production(self):
        result = _check({'DJANGO_DEBUG': '0', 'DJANGO_ALLOWED_HOSTS': 'example.com'})
        self.assertNotEqual(result.returncode, 0, 'should refuse to start')
        self.assertIn('DJANGO_SECRET_KEY must be set', result.stderr)

    def test_an_empty_host_list_is_refused_in_production(self):
        result = _check({'DJANGO_DEBUG': '0',
                         'DJANGO_SECRET_KEY': get_random_secret_key()})
        self.assertNotEqual(result.returncode, 0, 'should refuse to start')
        self.assertIn('DJANGO_ALLOWED_HOSTS must list', result.stderr)

    def test_a_correctly_configured_production_environment_starts(self):
        result = _check({'DJANGO_DEBUG': '0',
                         'DJANGO_SECRET_KEY': get_random_secret_key(),
                         'DJANGO_ALLOWED_HOSTS': 'eduai.example.com'})
        self.assertEqual(result.returncode, 0,
                         f'should start cleanly:\n{result.stderr}')

    def test_development_needs_no_configuration_at_all(self):
        """A fresh checkout must run without anyone setting a variable."""
        result = _check({})
        self.assertEqual(result.returncode, 0,
                         f'should start cleanly:\n{result.stderr}')


class TransportSecurityTests(SimpleTestCase):
    """The HTTPS-only settings must follow DEBUG, not be hardcoded either way."""

    def test_development_does_not_force_https(self):
        """Forcing SSL on the runserver would redirect to a dead port."""
        from django.conf import settings
        if settings.DEBUG:
            self.assertFalse(getattr(settings, 'SECURE_SSL_REDIRECT', False))
            self.assertFalse(getattr(settings, 'SESSION_COOKIE_SECURE', False))

    def test_production_settings_are_present_in_the_module(self):
        """Read the source: these must be set on the not-DEBUG branch."""
        source = (BASE_DIR / 'quick_tutor' / 'settings.py').read_text(encoding='utf-8')
        for setting in ('SECURE_SSL_REDIRECT', 'SESSION_COOKIE_SECURE',
                        'CSRF_COOKIE_SECURE', 'SECURE_HSTS_SECONDS',
                        'SECURE_CONTENT_TYPE_NOSNIFF'):
            self.assertIn(setting, source, f'{setting} is not configured')


class SecretHygieneTests(SimpleTestCase):
    """Credentials must come from the environment, never from the repository."""

    def test_gateway_credentials_are_not_hardcoded(self):
        source = (BASE_DIR / 'quick_tutor' / 'settings.py').read_text(encoding='utf-8')
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if 'SSLCOMMERZ_STORE' in stripped and '=' in stripped:
                self.assertIn('environ', line,
                              f'gateway credential must come from the environment: {line.strip()}')

    def test_the_env_file_is_not_tracked_by_git(self):
        result = subprocess.run(['git', 'ls-files', '--error-unmatch', '.env'],
                                cwd=BASE_DIR, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0,
                            '.env is tracked by git — it holds live credentials')


class HiddenAttributeTests(SimpleTestCase):
    """The `hidden` attribute must actually hide things.

    Browsers ship `[hidden] { display: none }` in their OWN stylesheet, and any
    author rule that sets `display` beats it. Several classes here do —
    `.field`, `.avatar`, `.chatwidget__panel` — so `hidden` was silently inert
    on the registration NID field, the refund bank field, the avatar preview
    and the chat panel, whose close button therefore appeared to do nothing.

    None of that is visible from Python, which is why it is pinned here.
    """

    STYLESHEET = BASE_DIR / 'static' / 'css' / 'eduai.css'
    TEMPLATES = BASE_DIR / 'templates'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = cls.STYLESHEET.read_text(encoding='utf-8')

    def test_the_stylesheet_forces_hidden_elements_to_stay_hidden(self):
        self.assertRegex(
            self.css.replace('\n', ' '),
            r'\[hidden\]\s*\{[^}]*display:\s*none\s*!important',
            'eduai.css must override display for [hidden]; without !important a '
            'class rule such as .field { display: flex } silently defeats it',
        )

    def test_the_rule_comes_before_the_classes_that_would_beat_it(self):
        """Belt as well as braces: even without !important, order would win."""
        rule = self.css.find('[hidden]')
        self.assertNotEqual(rule, -1)
        # It belongs with the reset at the top, not buried among components.
        self.assertLess(rule, len(self.css) // 4,
                        '[hidden] should sit with the reset near the top')

    def test_every_class_toggled_by_hidden_is_covered(self):
        """Any element a template hides must be inside the guarantee.

        This is a smoke check that the rule is global — an unqualified
        [hidden] selector — rather than scoped to one component.
        """
        import re
        match = re.search(r'(^|\})\s*\[hidden\]\s*\{', self.css, re.MULTILINE)
        self.assertIsNotNone(
            match,
            'the [hidden] rule must be a bare global selector, not scoped to '
            'one component, or the next element to use hidden breaks again',
        )


class ChatWidgetStartsClosedTests(TestCase):
    """The assistant must not be sitting open on every page load.

    It was, twice over: the panel carries `hidden`, but `.chatwidget__panel
    { display: flex }` beat the browser's `[hidden]` rule — and once that was
    corrected, the browser went on serving the old stylesheet because the dev
    server sends no Cache-Control.
    """

    def test_the_panel_is_marked_hidden_in_the_markup(self):
        html = self.client.get('/').content.decode()
        self.assertIn('id="chatpanel"', html)
        self.assertRegex(html, r'id="chatpanel"[^>]*\shidden',
                         'the panel must start hidden, not open')

    def test_the_component_hides_its_own_panel(self):
        """Scoped to the component, so it does not depend on the global rule."""
        css = (BASE_DIR / 'static' / 'css' / 'eduai.css').read_text(encoding='utf-8')
        self.assertRegex(
            css.replace('\n', ' '),
            r'\.chatwidget__panel\[hidden\]\s*\{[^}]*display:\s*none',
            '.chatwidget__panel[hidden] must set display:none, or the panel '
            'shows open because .chatwidget__panel sets display:flex',
        )

    def test_the_launcher_is_present_so_it_can_be_opened(self):
        html = self.client.get('/').content.decode()
        self.assertIn('id="chatlaunch"', html)
        self.assertIn('id="chatclose"', html)


class StylesheetIsCacheBustedTests(TestCase):
    """A CSS fix nobody can see is not a fix.

    The development server sends Last-Modified and no Cache-Control, so a
    browser may keep an edited stylesheet for a while. Versioning the URL
    makes every change a new URL.
    """

    def test_the_stylesheet_url_carries_a_version(self):
        html = self.client.get('/').content.decode()
        self.assertRegex(
            html, r'href="[^"]*css/eduai\.css\?v=\d+"',
            'the stylesheet link must be versioned, or CSS changes can stay '
            'invisible behind a cached copy',
        )

    def test_the_version_changes_when_the_file_does(self):
        """Checked with DEBUG on, which is when files are actually edited.

        With DEBUG off the value is memoised deliberately — a production
        server should not stat the file on every render — and Django turns
        DEBUG off during tests, so it has to be switched back on here.
        """
        import os
        from django.test import override_settings
        from quick_tutor.templatetags.assets import static_v

        path = BASE_DIR / 'static' / 'css' / 'eduai.css'
        original = os.path.getmtime(path)
        try:
            with override_settings(DEBUG=True):
                before = static_v('css/eduai.css')
                os.utime(path, (original + 60, original + 60))
                self.assertNotEqual(before, static_v('css/eduai.css'),
                                    'editing the file must change its URL')
        finally:
            os.utime(path, (original, original))

    def test_the_version_is_memoised_when_debug_is_off(self):
        """A live server must not stat the file on every page render."""
        import os
        from django.test import override_settings
        from quick_tutor.templatetags import assets

        path = BASE_DIR / 'static' / 'css' / 'eduai.css'
        original = os.path.getmtime(path)
        assets._versions.clear()
        try:
            with override_settings(DEBUG=False):
                first = assets.static_v('css/eduai.css')
                os.utime(path, (original + 60, original + 60))
                self.assertEqual(first, assets.static_v('css/eduai.css'),
                                 'the value should be cached with DEBUG off')
        finally:
            os.utime(path, (original, original))
            assets._versions.clear()

    def test_a_missing_file_does_not_break_the_page(self):
        from quick_tutor.templatetags.assets import static_v
        self.assertTrue(static_v('css/does-not-exist.css'))


class NoRawTemplateSyntaxTests(TestCase):
    """A multi-line {# #} is not a comment in Django: it renders as text.

    The chat widget's header comment did exactly that, printing itself at the
    foot of every page that includes the widget.
    """

    def test_no_template_comment_leaks_into_a_page(self):
        for url in ('/', '/tutors/', '/chat/', '/reviews/'):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertNotIn('{#', html)
                self.assertNotIn('#}', html)


class HomeHeroTests(TestCase):
    """The hero shows the tutoring photo on its own and uses no em dashes."""

    def hero(self):
        html = self.client.get('/').content.decode()
        return html[html.index('<section class="lp-hero">'):html.index('<!-- Featured tutors -->')]

    def test_the_photo_is_shown_with_alt_text_and_nothing_over_it(self):
        hero = self.hero()
        self.assertIn('img/hero-tutoring.jpg', hero)
        self.assertIn('alt="A tutor guiding a student', hero)
        self.assertNotIn('One-to-one lessons', hero)
        self.assertNotIn('Photo by', hero)

    def test_the_photo_file_ships_with_the_project(self):
        self.assertTrue((BASE_DIR / 'static' / 'img' / 'hero-tutoring.jpg').is_file())

    def test_popular_subjects_come_from_open_courses(self):
        from datetime import time
        from accounts.models import User
        from tutors.models import Course, TutorProfile
        tutor = User.objects.create_user('hero_tut', password='pw', role='tutor', first_name='Nadia')
        TutorProfile.objects.create(user=tutor, bio='b')
        Course.objects.create(tutor=tutor, subject='Physics', name='HSC Physics',
                              days='0,2', time=time(18, 0), months=3, monthly_rate=4500)
        self.assertIn('/tutors/?subject=Physics', self.hero())

    def test_no_em_dash_in_the_hero(self):
        hero = self.hero()
        self.assertNotIn('\u2014', hero)
        self.assertNotIn('&mdash;', hero)
