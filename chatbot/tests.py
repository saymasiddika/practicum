"""Tests for the assistant.

Three things matter more than the rest, and they are tested hardest:

  1. **It must not leak.** No student learns anything about another, and no
     student is shown commission — a rule the rest of the codebase already
     keeps, and which a chatbot is the easiest place to break.
  2. **It must not guess.** Answering an out-of-scope question confidently is
     worse than admitting ignorance, because the person believes it.
  3. **Its numbers must be the site's numbers.** Every figure is asserted
     against the same function the pages call, so the two can never drift.
"""
import json
from datetime import time, timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from bookings.refunds import quote_refund
from chatbot import retrieval
from chatbot.engine import ask, looks_like_follow_up
from chatbot.knowledge import ALL_ENTRIES, entries_for
from chatbot.models import ChatMessage
from chatbot.normalise import normalise
from chatbot.timeshift import offset_in
from payments.models import Payment
from tutors.models import Course, TutorProfile

RATE = 5000


class ChatTestCase(TestCase):
    def setUp(self):
        retrieval.clear_cache()
        self.student = User.objects.create_user('ch_stu', password='pw', role='student',
                                                email='s@x.com', first_name='Rina')
        self.tutor = User.objects.create_user('ch_tut', password='pw', role='tutor',
                                              first_name='Sayma')
        self.admin = User.objects.create_superuser('ch_root', password='pw',
                                                   email='r@x.com')
        TutorProfile.objects.create(user=self.tutor, bio='b')
        self.course = Course.objects.create(
            tutor=self.tutor, subject='Physics', name='HSC Physics',
            days='0,2', time=time(18, 0), months=3, monthly_rate=RATE,
        )

    def booking(self, days_ago=9, months=3, paid=RATE, student=None):
        b = Booking.objects.create(
            student=student or self.student, tutor=self.tutor,
            course=self.course,
            date=timezone.localdate() - timedelta(days=days_ago),
            months=months, agreed_monthly_rate=RATE, status='confirmed',
        )
        if paid:
            c, i = Payment.calculate_commission(paid, RATE, 0)
            Payment.objects.create(booking=b, amount=paid, commission=c,
                                   tutor_income=i, status='approved')
        return b

    def post(self, question):
        return self.client.post('/chat/ask/',
                                data=json.dumps({'question': question}),
                                content_type='application/json').json()


# ── Retrieval ────────────────────────────────────────────────────────────

class RetrievalTests(ChatTestCase):
    """One question per intent, phrased as a person would."""

    CASES = [
        ('how much does a tutor cost', 'pricing'),
        ('koto taka lagbe', 'pricing'),
        ('how do i find a tutor', 'how_to_find_tutor'),
        ('how do i book a tutor', 'how_to_book'),
        ('can i pay in instalments', 'instalments'),
        ('how do refunds work', 'refund_policy'),
        ('how do i cancel my booking', 'how_to_terminate'),
        ('how do i leave a review', 'how_to_review'),
        ('how do i become a tutor', 'become_tutor'),
        ('hello', 'greeting'),
        ('thank you', 'thanks'),
        ('who are you', 'who_are_you'),
    ]

    def test_each_question_reaches_its_intent(self):
        for question, expected in self.CASES:
            with self.subTest(question=question):
                response = ask(question)
                self.assertEqual(response.intent, expected)
                self.assertTrue(response.answered)

    def test_typos_still_find_the_answer(self):
        for typo, expected in [('refnd policy', 'refund_policy'),
                               ('what is the commision', 'commission_general'),
                               ('how do i find a tutior', 'how_to_find_tutor')]:
            with self.subTest(typo=typo):
                self.assertEqual(ask(typo).intent, expected)

    def test_banglish_is_understood(self):
        """The audience writes this way; a bot that cannot read it is unused."""
        for phrase, expected in [('koto taka lagbe', 'pricing'),
                                 ('taka ferot pabo', 'refund_policy'),
                                 ('shikkhok hote chai', 'become_tutor')]:
            with self.subTest(phrase=phrase):
                self.assertEqual(ask(phrase).intent, expected)

    def test_bengali_script_is_understood(self):
        self.assertEqual(ask('টাকা কত').intent, 'pricing')


class RefusalTests(ChatTestCase):
    """Not knowing, and saying so, is a feature."""

    OUT_OF_SCOPE = ('what is the weather in dhaka', 'who won the football match',
                    'tell me a joke', 'what is the capital of france',
                    'asdfghjkl qwerty', 'write me a poem')

    def test_out_of_scope_questions_are_refused_not_guessed(self):
        for question in self.OUT_OF_SCOPE:
            with self.subTest(question=question):
                response = ask(question)
                self.assertFalse(response.answered,
                                 f'answered {question!r} with {response.text[:60]!r}')
                self.assertIn('do not know', response.text)

    def test_the_refusal_says_what_it_can_do(self):
        response = ask('what is the weather')
        self.assertIn('tutor', response.text.lower())
        self.assertTrue(response.follow_ups)

    def test_an_empty_question_is_handled(self):
        for blank in ('', '   ', '\n'):
            with self.subTest(blank=repr(blank)):
                self.assertFalse(ask(blank).answered)


# ── Permission and privacy ───────────────────────────────────────────────

class PrivacyTests(ChatTestCase):
    def test_an_anonymous_visitor_is_invited_to_log_in_not_answered(self):
        response = ask('how much do i owe')
        self.assertFalse(response.answered)
        self.assertTrue(response.needs_login)

    def test_an_anonymous_visitor_still_gets_general_answers(self):
        self.assertTrue(ask('how much does a tutor cost').answered)

    def test_one_student_learns_nothing_about_another(self):
        self.booking()                       # Rina's, ৳5,000 paid
        intruder = User.objects.create_user('ch_thief', password='pw', role='student')

        for probe in ('how much does Rina owe',
                      'what is Rina balance',
                      'how much has Rina paid for Physics'):
            with self.subTest(probe=probe):
                response = ask(probe, user=intruder)
                self.assertNotIn('5,000', response.text)
                self.assertNotIn('10,000', response.text)
                self.assertNotIn('Rina', response.text)

    def test_a_student_is_never_shown_commission(self):
        """Commission is deliberately hidden from students site-wide."""
        self.booking()
        for probe in ('how does commission work', 'what cut do you take',
                      'how much commission', 'platform fee'):
            with self.subTest(probe=probe):
                text = ask(probe, user=self.student).text.lower()
                self.assertNotIn('40%', text)
                self.assertNotIn('50%', text)
                self.assertNotIn('broker', text)

    def test_a_tutor_may_see_the_commission_split(self):
        """It is their money, so they are entitled to the arithmetic."""
        text = ask('how much commission do you take', user=self.tutor).text
        self.assertIn('50%', text)

    def test_a_student_cannot_reach_tutor_only_answers(self):
        self.assertNotEqual(ask('how much have i earned', user=self.student).intent,
                            'my_earnings')

    def test_a_tutor_cannot_reach_student_only_answers(self):
        self.assertNotEqual(ask('what refund would i get', user=self.tutor).intent,
                            'my_refund_quote')

    def test_only_staff_reach_the_admin_summary(self):
        self.assertEqual(ask('what is in the admin queue', user=self.admin).intent,
                         'admin_queue')
        for user in (self.student, self.tutor):
            with self.subTest(user=user.username):
                self.assertNotEqual(ask('what is in the admin queue', user=user).intent,
                                    'admin_queue')


# ── Grounded answers ─────────────────────────────────────────────────────

class GroundedAnswerTests(ChatTestCase):
    def test_the_balance_matches_the_booking(self):
        booking = self.booking()
        text = ask('how much do i owe', user=self.student).text
        self.assertIn(f'{booking.balance_due:,.0f}', text)

    def test_the_refund_matches_quote_refund_exactly(self):
        """The bot must never do its own arithmetic about money."""
        booking = self.booking(days_ago=9)
        expected = quote_refund(booking, initiated_by='student')
        text = ask('what refund would i get if i stop now', user=self.student).text
        self.assertIn(f'{expected.amount:,.0f}', text)
        self.assertIn(f'{expected.percent}%', text)

    def test_a_student_with_no_bookings_is_told_so(self):
        text = ask('how much do i owe', user=self.student).text
        self.assertIn('no active bookings', text)

    def test_the_course_end_date_is_reported(self):
        booking = self.booking()
        text = ask('when does my course end', user=self.student).text
        self.assertIn(booking.natural_end_date.strftime('%d %B %Y'), text)

    def test_payment_status_reflects_the_latest_payment(self):
        self.booking()
        self.assertIn('approved',
                      ask('has my payment been approved', user=self.student).text)

    def test_an_abandoned_checkout_is_described_honestly(self):
        booking = self.booking(paid=0)
        Payment.objects.create(booking=booking, amount=RATE, commission=0,
                               tutor_income=0, status='initiated',
                               payment_method='sslcommerz')
        text = ask('has my payment been approved', user=self.student).text
        self.assertIn('never finished', text)
        self.assertIn('nothing was charged', text.lower())

    def test_tutor_earnings_come_from_stored_income(self):
        self.booking()
        expected = Payment.objects.filter(booking__tutor=self.tutor,
                                          status='approved').first().tutor_income
        self.assertIn(f'{expected:,.0f}', ask('how much have i earned',
                                              user=self.tutor).text)

    def test_answers_carry_useful_actions(self):
        self.booking()
        response = ask('how much do i owe', user=self.student)
        self.assertTrue(any('/payments/create/' in url for _label, url in response.actions))


class MultipleBookingTests(ChatTestCase):
    def setUp(self):
        super().setUp()
        self.maths_tutor = User.objects.create_user('ch_tut2', password='pw', role='tutor',
                                                    first_name='Kamal')
        TutorProfile.objects.create(user=self.maths_tutor, bio='b')
        self.chemistry = Course.objects.create(
            tutor=self.maths_tutor, subject='Chemistry', name='SSC Chemistry',
            days='1', time=time(9, 0), months=2, monthly_rate=RATE,
        )

    def _second_booking(self):
        b = Booking.objects.create(student=self.student, tutor=self.maths_tutor,
                                   course=self.chemistry,
                                   date=timezone.localdate() - timedelta(days=9),
                                   months=2, agreed_monthly_rate=RATE,
                                   status='confirmed')
        c, i = Payment.calculate_commission(RATE, RATE, 0)
        Payment.objects.create(booking=b, amount=RATE, commission=c,
                               tutor_income=i, status='approved')
        return b

    def test_naming_a_subject_picks_the_right_booking(self):
        self.booking()               # Physics
        self._second_booking()       # Chemistry
        text = ask('what refund would i get on chemistry if i stop now',
                   user=self.student).text
        self.assertIn('Chemistry', text)
        self.assertNotIn('Physics', text)

    def test_naming_a_tutor_picks_the_right_booking(self):
        self.booking()
        self._second_booking()
        text = ask('what refund on my course with kamal', user=self.student).text
        self.assertIn('Chemistry', text)

    def test_an_ambiguous_question_asks_rather_than_guessing(self):
        """Guessing here would report a confident, wrong amount of money."""
        self.booking()
        self._second_booking()
        text = ask('what refund would i get if i stop now', user=self.student).text
        self.assertIn('which one', text.lower())
        self.assertIn('Physics', text)
        self.assertIn('Chemistry', text)


# ── Conversation ─────────────────────────────────────────────────────────

class FollowUpTests(ChatTestCase):
    def test_fragments_are_recognised_as_continuations(self):
        for fragment in ('and after next week', 'what about next month',
                         'and then', 'yes', 'why'):
            with self.subTest(fragment=fragment):
                self.assertTrue(looks_like_follow_up(fragment))

    def test_full_questions_are_not_treated_as_continuations(self):
        for question in ('how much does a tutor cost',
                         'what refund would i get if i stop now today'):
            with self.subTest(question=question):
                self.assertFalse(looks_like_follow_up(question))

    def test_a_follow_up_inherits_the_previous_subject(self):
        self.booking(days_ago=3)
        response = ask('and after next week', user=self.student,
                       last_question='what refund would i get if i stop now')
        self.assertEqual(response.intent, 'my_refund_quote')

    def test_a_follow_up_projects_the_refund_forward(self):
        """Answering "and after next week?" with today's figure is quietly
        wrong, which is worse than obviously wrong."""
        booking = self.booking(days_ago=3)          # week 1 -> 75%
        today = quote_refund(booking, initiated_by='student')
        later = quote_refund(booking,
                             on_date=timezone.localdate() + timedelta(days=7),
                             initiated_by='student')
        self.assertNotEqual(today.percent, later.percent, 'test needs a tier change')

        text = ask('and after next week', user=self.student,
                   last_question='what refund would i get if i stop now').text
        self.assertIn(f'{later.percent}%', text)
        self.assertNotIn(f'{today.percent}%', text)


class TimeShiftTests(TestCase):
    def test_future_phrases_are_read(self):
        for phrase, days in [('next week', 7), ('in 2 weeks', 14),
                             ('after three days', 3), ('in 10 days', 10)]:
            with self.subTest(phrase=phrase):
                delta = offset_in(phrase)
                self.assertIsNotNone(delta)
                self.assertEqual(delta.days, days)

    def test_now_means_today(self):
        for phrase in ('right now', 'today', 'ekhon'):
            with self.subTest(phrase=phrase):
                self.assertEqual(offset_in(phrase).days, 0)

    def test_nothing_is_read_from_a_plain_question(self):
        self.assertIsNone(offset_in('what refund would i get'))

    def test_an_absurd_offset_is_ignored(self):
        """A wrong date becomes a wrong number about someone's money."""
        self.assertIsNone(offset_in('in 900 weeks'))


# ── The endpoint ─────────────────────────────────────────────────────────

class EndpointTests(ChatTestCase):
    def test_the_endpoint_answers_json(self):
        data = self.post('how much does a tutor cost')
        self.assertTrue(data['answered'])
        self.assertIn('monthly rate', data['answer'])

    def test_the_endpoint_rejects_get(self):
        self.assertEqual(self.client.get('/chat/ask/').status_code, 405)

    def test_an_empty_question_is_a_bad_request(self):
        response = self.client.post('/chat/ask/', data=json.dumps({'question': ''}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_malformed_json_does_not_crash(self):
        response = self.client.post('/chat/ask/', data='not json at all',
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_a_very_long_question_is_truncated_not_refused(self):
        data = self.post('how much does a tutor cost ' + 'x' * 5000)
        self.assertIn('answer', data)

    def test_questions_are_logged_for_the_knowledge_base(self):
        self.post('how much does a tutor cost')
        self.post('what is the weather in dhaka')
        self.assertEqual(ChatMessage.objects.count(), 2)
        self.assertEqual(ChatMessage.objects.filter(answered=False).count(), 1)

    def test_the_log_never_stores_the_answer(self):
        """Answers contain people's financial details; questions do not."""
        self.client.force_login(self.student)
        self.booking()
        self.post('how much do i owe')
        logged = ChatMessage.objects.first()
        self.assertFalse(hasattr(logged, 'answer'))
        self.assertNotIn('5,000', logged.question)

    def test_rapid_fire_is_rate_limited(self):
        last = None
        for _ in range(30):
            last = self.client.post('/chat/ask/',
                                    data=json.dumps({'question': 'hello'}),
                                    content_type='application/json')
        self.assertEqual(last.status_code, 429)


class SurfaceTests(ChatTestCase):
    def test_the_full_page_opens_for_anyone(self):
        self.assertEqual(self.client.get('/chat/').status_code, 200)

    def test_the_full_page_works_without_javascript(self):
        """The widget is an enhancement; the page must answer on its own."""
        response = self.client.post('/chat/', {'question': 'how much does a tutor cost'},
                                    follow=True)
        self.assertContains(response, 'monthly rate')

    def test_the_widget_is_on_ordinary_pages(self):
        self.client.force_login(self.student)
        self.assertContains(self.client.get('/bookings/'), 'chatwidget')

    def test_the_admin_log_is_staff_only(self):
        for user in (self.student, self.tutor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertNotEqual(self.client.get('/admin-panel/chat/').status_code, 200)

    def test_the_admin_log_shows_unanswered_questions(self):
        self.post('something it has never heard of before entirely')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get('/admin-panel/chat/'),
                            'never heard of')


# ── The knowledge base itself ────────────────────────────────────────────

class KnowledgeBaseTests(TestCase):
    """Guards against entries rotting unnoticed."""

    def test_every_intent_is_unique(self):
        intents = [e.intent for e in ALL_ENTRIES]
        self.assertEqual(len(intents), len(set(intents)))

    def test_every_entry_has_something_to_say(self):
        for entry in ALL_ENTRIES:
            with self.subTest(intent=entry.intent):
                self.assertTrue(entry.answer or entry.handler,
                                'an entry needs either an answer or a handler')

    def test_every_handler_named_actually_exists(self):
        from chatbot.answers import HANDLERS
        for entry in ALL_ENTRIES:
            if entry.handler:
                with self.subTest(intent=entry.intent):
                    self.assertIn(entry.handler, HANDLERS)

    def test_every_phrasing_survives_normalisation(self):
        """A phrasing that reduces to nothing can never be matched."""
        for entry in ALL_ENTRIES:
            for phrase in entry.phrasings:
                with self.subTest(intent=entry.intent, phrase=phrase):
                    self.assertTrue(normalise(phrase),
                                    f'{phrase!r} normalises away entirely')

    def test_every_entry_is_reachable_by_its_own_first_phrasing(self):
        """If an entry cannot be found by how it describes itself, it is dead
        weight — and the collision that hides it will hide it silently."""
        retrieval.clear_cache()
        unreachable = []
        for entry in ALL_ENTRIES:
            audience = entry.audience[0]
            authenticated = audience != 'anonymous'
            found = retrieval.retriever_for(audience, authenticated).search(
                entry.phrasings[0])
            if found.intent != entry.intent:
                unreachable.append(f'{entry.intent} (found {found.intent})')
        self.assertEqual(unreachable, [],
                         'these entries cannot be reached by their own wording')

    # Reading live data is not by itself private. The catalogue is grounded
    # AND public on purpose: "are there any maths teachers?" is the question
    # that decides whether a visitor signs up at all, and demanding a login
    # first is the one place this assistant could cost the platform users.
    # So the set of public grounded handlers is listed here rather than
    # derived — adding one has to be a deliberate edit to a test that says
    # why, not a flag someone set on a new entry without thinking.
    PUBLIC_HANDLERS = {
        'answer_find_courses',
        'answer_subjects_offered',
        'answer_cheapest',
    }

    def test_a_grounded_entry_is_private_unless_it_reads_the_catalogue(self):
        for entry in ALL_ENTRIES:
            if entry.handler and entry.handler not in self.PUBLIC_HANDLERS:
                with self.subTest(intent=entry.intent):
                    self.assertTrue(
                        entry.needs_login,
                        f'{entry.intent} reads live data without a login and is '
                        f'not a declared catalogue handler',
                    )

    def test_every_public_handler_still_exists(self):
        """A renamed handler must not silently become private — or, worse,
        leave a stale name in the allowlist that a future entry reuses."""
        from chatbot.answers import HANDLERS
        live = {e.handler for e in ALL_ENTRIES if e.handler}
        for handler in self.PUBLIC_HANDLERS:
            with self.subTest(handler=handler):
                self.assertIn(handler, HANDLERS)
                self.assertIn(handler, live)

    def test_a_grounded_entry_visible_to_anonymous_always_requires_a_login(self):
        """An anonymous visitor may MATCH a personal question — that is how
        they get invited to log in rather than told they were not understood.
        What must never happen is being ANSWERED from one, and `needs_login`
        is what the engine checks."""
        for entry in entries_for('anonymous', False):
            if entry.handler and entry.handler not in self.PUBLIC_HANDLERS:
                with self.subTest(intent=entry.intent):
                    self.assertTrue(
                        entry.needs_login,
                        f'{entry.intent} reads live data but does not require a login',
                    )

    def test_no_grounded_answer_ever_reaches_an_anonymous_visitor(self):
        """The property that actually matters, checked end to end."""
        personal = [e for e in ALL_ENTRIES
                    if e.handler and e.handler not in self.PUBLIC_HANDLERS]
        for entry in personal:
            with self.subTest(intent=entry.intent):
                response = ask(entry.phrasings[0])          # no user
                self.assertFalse(
                    response.answered,
                    f'{entry.intent} answered an anonymous visitor',
                )


# ── The catalogue ────────────────────────────────────────────────────────

class CatalogueTests(ChatTestCase):
    """"Are there any maths teachers?" — the first question a student asks.

    Every question here was typed by a real user into the live widget and got
    a wrong answer: mostly "you have no bookings", because nothing in the
    knowledge base was about who teaches what. They are pinned verbatim.
    """

    def setUp(self):
        super().setUp()
        self.maths = Course.objects.create(
            tutor=self.tutor, subject='Mathematics', name='SSC General Maths',
            days='1,3', time=time(17, 0), months=2, monthly_rate=3000,
        )

    def assertIntent(self, question, intent, user=None):
        response = ask(question, user=user)
        self.assertEqual(response.intent, intent,
                         f'{question!r} answered as {response.intent!r}')
        self.assertTrue(response.answered, f'{question!r} was refused')
        return response

    def test_asking_for_teachers_lists_courses(self):
        for question in ('are there any available teachers',
                         'are there any teachers',
                         'which tutors are free',
                         'show me the courses'):
            self.assertIntent(question, 'find_courses', self.student)

    def test_asking_by_subject_narrows_to_that_subject(self):
        reply = self.assertIntent('are there any teachers for mathematics',
                                  'find_courses', self.student)
        self.assertIn('Mathematics', reply.text)
        self.assertNotIn('HSC Physics', reply.text)

    def test_a_short_form_subject_reaches_the_full_name(self):
        """"maths" and "Mathematics" score only 0.36 on trigrams — under the
        fuzzy threshold — so this passes on prefix matching alone.

        Excluding Physics is the real assertion: when the subject is not
        recognised the handler falls back to listing EVERYTHING, which still
        contains the maths course and would pass a naive check.
        """
        reply = self.assertIntent('show me maths courses', 'find_courses',
                                  self.student)
        self.assertIn('SSC General Maths', reply.text)
        self.assertNotIn('HSC Physics', reply.text)

    def test_a_visitor_who_is_not_logged_in_still_gets_an_answer(self):
        """The catalogue is public: this is the question that wins signups."""
        reply = self.assertIntent(
            'i want to learn math so are there any mathematics teachers available?',
            'find_courses')
        self.assertIn('Mathematics', reply.text)

    def test_a_subject_nobody_teaches_is_named_not_papered_over(self):
        """Answering a chemistry question with maths reads as a misread."""
        reply = self.assertIntent('do you have any chemistry tutors',
                                  'find_courses', self.student)
        self.assertIn('Chemistry', reply.text)
        self.assertNotIn('SSC General Maths', reply.text)

    def test_a_taken_course_is_never_offered(self):
        """The assistant must not offer what the booking form would refuse."""
        Booking.objects.create(
            student=self.student, tutor=self.tutor, course=self.maths,
            date=timezone.localdate(), months=2,
            agreed_monthly_rate=3000, status='confirmed',
        )
        reply = ask('are there any maths teachers', user=self.student)
        self.assertNotIn('SSC General Maths', reply.text)

    def test_subjects_offered_comes_from_the_data(self):
        reply = self.assertIntent('what subjects can i learn',
                                  'subjects_offered', self.student)
        self.assertIn('Mathematics', reply.text)
        self.assertIn('Physics', reply.text)

    def test_the_cheapest_figure_is_never_computed_by_the_assistant(self):
        reply = self.assertIntent('how much is the cheapest maths course',
                                  'cheapest_course', self.student)
        self.assertIn('3,000', reply.text)

    def test_counting_bookings_is_not_the_how_to_book_leaflet(self):
        """Both reduce to the single token "booking"; the tie is broken by
        the first-person "do I have", which survives no other way."""
        self.booking()
        reply = self.assertIntent('how many bookings do i have?',
                                  'my_bookings', self.student)
        self.assertIn('Physics', reply.text)

    def test_how_to_book_still_reaches_the_leaflet(self):
        """The other half of that tie, which the fix must not steal."""
        self.assertIntent('how do i book a tutor', 'how_to_book', self.student)

    def test_the_catalogue_never_mentions_commission(self):
        for question in ('are there any teachers', 'what subjects can i learn',
                         'what is the cheapest course'):
            text = ask(question, user=self.student).text.lower()
            self.assertNotIn('commission', text)
            self.assertNotIn('broker', text)
