"""Answers built from the asker's own records.

Every handler takes (user, question) and returns a Reply.

One rule governs all of them: **no money is calculated here.** Balances come
from `Booking.balance_due`, refunds from `quote_refund`, income from the
stored `tutor_income`. If this module did its own arithmetic the assistant
could quietly disagree with the page the student is looking at, and there
would be no way to tell which was right.

Two things are never disclosed:

  · Another person's records. Every query is filtered to the asker.
  · Commission, to a student. That is a deliberate product decision made
    elsewhere in the codebase, and a chatbot is the easiest place to undo it
    by accident.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from bookings.models import Booking
from bookings.refunds import quote_refund
from chatbot import catalogue, entities
from chatbot.timeshift import describe as describe_when, offset_in

LIVE_STATUSES = ('pending', 'confirmed')


@dataclass
class Reply:
    text: str
    actions: list = field(default_factory=list)      # [(label, url)]
    follow_ups: list = field(default_factory=list)
    needs_choice: bool = False


def money(value):
    """Whole taka, comma-grouped — nobody wants '3750.00' in a sentence."""
    return f'৳{Decimal(str(value or 0)):,.0f}'


def _student_bookings(user, statuses=LIVE_STATUSES):
    return list(
        Booking.objects.filter(student=user, status__in=statuses)
        .select_related('tutor', 'tutor__tutor_profile')
        .prefetch_related('payments')
        .order_by('-created_at')
    )


def _tutor_bookings(user, statuses=LIVE_STATUSES):
    return list(
        Booking.objects.filter(tutor=user, status__in=statuses)
        .select_related('student')
        .prefetch_related('payments')
        .order_by('-created_at')
    )


def _ask_which(bookings, question_word):
    """When several bookings fit, name them rather than guessing."""
    listed = '\n'.join(f'• {entities.describe(b)}' for b in bookings)
    return Reply(
        text=f'You have {len(bookings)} active bookings, so tell me which one '
             f'you mean and I will {question_word}:\n{listed}',
        needs_choice=True,
        follow_ups=tuple(entities.describe(b) for b in bookings[:3]),
    )


# ── Student ──────────────────────────────────────────────────────────────

def answer_balance(user, question):
    bookings = _student_bookings(user)
    if not bookings:
        return Reply(
            text='You have no active bookings, so there is nothing to pay.',
            actions=[('Find a tutor', '/tutors/')],
        )

    booking, alternatives = entities.resolve(bookings, question)
    if booking is None:
        # Several bookings and no clue which — give the whole picture, which
        # is more useful here than a question.
        lines, total = [], Decimal('0')
        for b in alternatives:
            total += Decimal(str(b.balance_due))
            lines.append(f'• {entities.describe(b)} — {money(b.balance_due)} left '
                         f'of {money(b.total_amount)}')
        return Reply(
            text='Across your bookings you owe ' + money(total) + ':\n'
                 + '\n'.join(lines),
            actions=[('See my payments', '/payments/')],
            follow_ups=('What payment methods can I use?',
                        'Can I pay in instalments?'),
        )

    if booking.is_fully_paid:
        return Reply(
            text=f'Your {entities.describe(booking)} is fully paid — '
                 f'{money(booking.total_paid)} of {money(booking.total_amount)}. '
                 f'Nothing is owed.',
            follow_ups=('When does my course end?', 'How do I leave a review?'),
        )

    text = (f'You owe {money(booking.balance_due)} on your '
            f'{entities.describe(booking)} — {money(booking.total_paid)} paid '
            f'of {money(booking.total_amount)}.')
    actions = []
    if booking.is_payable:
        actions.append(('Pay now', f'/payments/create/?booking_id={booking.id}'))
    elif booking.status == 'pending':
        text += (' Nothing is owed yet, though — your tutor has not accepted '
                 'the booking. You can pay once they do.')
    return Reply(text=text, actions=actions,
                 follow_ups=('Can I pay in instalments?',
                             'What payment methods can I use?'))


def answer_refund_quote(user, question):
    bookings = _student_bookings(user, statuses=('confirmed',))
    if not bookings:
        return Reply(text='You have no active bookings, so there is nothing to '
                          'refund at the moment.')

    booking, alternatives = entities.resolve(bookings, question)
    if booking is None:
        return _ask_which(alternatives, 'work out the refund')

    # "and after next week?" is the same question about a different day.
    # Quoting today's figure for it would be quietly wrong.
    shift = offset_in(question)
    on_date = (timezone.localdate() + shift) if shift else None
    when = describe_when(shift)

    quote = quote_refund(booking, on_date=on_date, initiated_by='student')
    paid = booking.total_paid

    if not paid:
        return Reply(
            text=f'You have not paid anything on your '
                 f'{entities.describe(booking)} yet, so there is nothing to '
                 f'refund. You can end it without losing money.',
            actions=[('End this booking', f'/bookings/{booking.id}/end/?kind=terminate')],
        )

    if quote.amount <= 0:
        lead = ('Nothing, I am afraid.' if when == 'today'
                else f'{when.capitalize()}, nothing.')
        return Reply(
            text=f'{lead} You would be {quote.tier} on your '
                 f'{entities.describe(booking)}, and the policy refunds nothing '
                 f'past four weeks. You can still end the booking.',
            actions=[('End this booking', f'/bookings/{booking.id}/end/?kind=terminate')],
            follow_ups=('How do refunds work?', 'How do I end a booking?'),
        )

    lead = ('You would get' if when == 'today'
            else f'{when.capitalize()} you would get')
    return Reply(
        text=f'{lead} {money(quote.amount)} back — {quote.percent}% of '
             f'the {money(paid)} you have paid on your '
             f'{entities.describe(booking)}, because you would be {quote.tier}.',
        actions=[('End this booking', f'/bookings/{booking.id}/end/?kind=terminate')],
        follow_ups=('How do I get the refund money?',
                    'How do refunds work?',
                    'How do I end a booking?'),
    )


def answer_payment_status(user, question):
    from payments.models import Payment

    payments = list(
        Payment.objects.filter(booking__student=user)
        .select_related('booking', 'booking__tutor')
        .order_by('-created_at')[:5]
    )
    if not payments:
        return Reply(text='You have not made any payments yet.',
                     follow_ups=('How much do I owe?',
                                 'What payment methods can I use?'))

    latest = payments[0]
    where = entities.describe(latest.booking)

    if latest.status == 'approved':
        text = (f'Your most recent payment of {money(latest.amount)} for '
                f'{where} has been approved.')
    elif latest.status == 'pending':
        text = (f'Your payment of {money(latest.amount)} for {where} is still '
                f'being checked. Manual payments are usually reviewed within a '
                f'day.')
    elif latest.status == 'initiated':
        text = (f'A card payment of {money(latest.amount)} for {where} was '
                f'started but never finished — nothing was charged. You can try '
                f'again whenever you like.')
    else:
        text = (f'Your payment of {money(latest.amount)} for {where} was not '
                f'accepted. Nothing was taken.')

    approved = sum(1 for p in payments if p.status == 'approved')
    if len(payments) > 1:
        text += f' Of your last {len(payments)} payments, {approved} are approved.'

    return Reply(text=text, actions=[('See my payments', '/payments/')],
                 follow_ups=('How much do I owe?', 'How long does approval take?'))


# ── Either role ──────────────────────────────────────────────────────────

def answer_bookings(user, question):
    is_tutor = user.role == 'tutor'
    bookings = _tutor_bookings(user) if is_tutor else _student_bookings(user)

    if not bookings:
        if is_tutor:
            return Reply(text='You have no active bookings. Students will find '
                              'you once your tutor profile is published.',
                         actions=[('My tutor profile', '/tutors/edit-profile/')])
        return Reply(text='You have no active bookings yet.',
                     actions=[('Find a tutor', '/tutors/')])

    lines = []
    for b in bookings:
        other = (b.student.get_full_name() or b.student.username) if is_tutor \
            else entities.describe(b)
        lines.append(f'• {other} — {b.get_status_display().lower()}, '
                     f'{b.months} month{"s" if b.months != 1 else ""} '
                     f'from {b.date:%d %b %Y}')

    return Reply(
        text=f'You have {len(bookings)} active booking'
             f'{"s" if len(bookings) != 1 else ""}:\n' + '\n'.join(lines),
        actions=[('Open my bookings', '/bookings/')],
    )


def answer_course_end(user, question):
    is_tutor = user.role == 'tutor'
    bookings = _tutor_bookings(user) if is_tutor else _student_bookings(user)
    if not bookings:
        return Reply(text='You have no active bookings.')

    booking, alternatives = entities.resolve(bookings, question)
    if booking is None:
        lines = [f'• {entities.describe(b)} — ends {b.natural_end_date:%d %b %Y}'
                 for b in alternatives]
        return Reply(text='Here is when each of your courses finishes:\n'
                          + '\n'.join(lines))

    end = booking.natural_end_date
    today = timezone.localdate()
    if booking.has_run_its_course:
        return Reply(
            text=f'Your {entities.describe(booking)} reached its end date on '
                 f'{end:%d %B %Y}. It can be marked complete now, and then '
                 f'reviewed.',
            actions=[('Open the booking', f'/bookings/{booking.id}/')],
            follow_ups=('How do I leave a review?',),
        )

    days = (end - today).days
    return Reply(
        text=f'Your {entities.describe(booking)} runs until {end:%d %B %Y} — '
             f'{days} day{"s" if days != 1 else ""} from now.',
        actions=[('Open the booking', f'/bookings/{booking.id}/')],
        follow_ups=('Can I finish the course early?',
                    'What refund would I get if I stop now?'),
    )


# ── Tutor ────────────────────────────────────────────────────────────────

def answer_earnings(user, question):
    from payments.models import Payment

    earned = Payment.objects.filter(
        booking__tutor=user, status='approved'
    ).aggregate(total=Sum('tutor_income'))['total'] or 0

    pending = Payment.objects.filter(
        booking__tutor=user, status='pending'
    ).aggregate(total=Sum('tutor_income'))['total'] or 0

    if not earned and not pending:
        return Reply(text='You have not earned anything yet — no payments have '
                          'come in on your bookings so far.',
                     actions=[('My bookings', '/bookings/')])

    text = f'You have earned {money(earned)} so far, after commission.'
    if pending:
        text += (f' Another {money(pending)} is waiting on payments that an '
                 f'admin has not approved yet.')
    return Reply(text=text, actions=[('My bookings', '/bookings/')],
                 follow_ups=('How much commission do you take?',
                             'What are my bookings?'))


def answer_pending_work(user, question):
    if user.is_staff or user.is_superuser:
        return answer_admin_queue(user, question)

    waiting = Booking.objects.filter(tutor=user, status='pending').count()
    requests = Booking.objects.filter(
        tutor=user, requests__status='pending'
    ).distinct().count()

    if not waiting and not requests:
        return Reply(text='Nothing needs your attention right now.')

    parts = []
    if waiting:
        parts.append(f'{waiting} booking request{"s" if waiting != 1 else ""} '
                     f'waiting for you to accept or decline')
    if requests:
        parts.append(f'{requests} booking{"s" if requests != 1 else ""} with an '
                     f'early-end request under review')
    return Reply(text='You have ' + ', and '.join(parts) + '.',
                 actions=[('Open my bookings', '/bookings/')])


# ── Staff ────────────────────────────────────────────────────────────────

def answer_admin_queue(user, question):
    from bookings.models import BookingRequest
    from payments.models import Payment

    payments = Payment.objects.filter(status='pending').count()
    requests = BookingRequest.objects.filter(status='pending').count()
    owed = BookingRequest.objects.filter(
        kind='terminate', status='approved', refund_settled=False
    ).aggregate(total=Sum('refund_amount'))['total'] or 0

    if not payments and not requests and not owed:
        return Reply(text='The queues are clear — no payments to review, no '
                          'early-end requests, and no refunds outstanding.')

    parts = []
    if payments:
        parts.append(f'{payments} payment{"s" if payments != 1 else ""} '
                     f'awaiting review')
    if requests:
        parts.append(f'{requests} early-end request'
                     f'{"s" if requests != 1 else ""} to judge')
    if owed:
        parts.append(f'{money(owed)} in approved refunds not yet sent')

    actions = []
    if payments:
        actions.append(('Review payments', '/payments/manage/'))
    if requests or owed:
        actions.append(('Early-end requests', '/admin-panel/requests/'))

    return Reply(text='There ' + ('is ' if len(parts) == 1 else 'are ')
                      + ', and '.join(parts) + '.',
                 actions=actions)


# The handler name in knowledge.py maps to a function here.
HANDLERS = {
    'answer_balance': answer_balance,
    'answer_refund_quote': answer_refund_quote,
    'answer_bookings': answer_bookings,
    'answer_course_end': answer_course_end,
    'answer_payment_status': answer_payment_status,
    'answer_earnings': answer_earnings,
    'answer_pending_work': answer_pending_work,
    'answer_admin_queue': answer_admin_queue,
}


# ── The catalogue: who is available, and for what ────────────────────────
# These answer the first question a student actually asks. They need no
# login, because what is on offer is public.

def answer_find_courses(user, question):
    """Who can teach this, and what does it cost.

    The subject is pulled out of the question and matched against what is
    really being taught, so the answer stays right as tutors come and go.
    """
    subject = catalogue.subject_in(question)
    # A subject nobody teaches is still a subject the student asked about.
    # Passing it through means the "nobody is teaching X" branch below
    # answers the question actually asked, rather than listing everything.
    subject = subject or catalogue.unavailable_subject(question)
    courses = catalogue.available_courses(subject=subject, limit=6)

    if not courses:
        offered = catalogue.subject_summary()
        if subject and offered:
            others = ', '.join(offered)
            return Reply(
                text=f'Nobody is teaching {subject} at the moment. What is '
                     f'available right now: {others}.',
                actions=[('Browse tutors', '/tutors/')],
                follow_ups=tuple(f'Any {s.lower()} tutors?' for s in list(offered)[:3]),
            )
        if subject:
            return Reply(
                text=f'Nobody is teaching {subject} at the moment — in fact no '
                     f'tutor has published a course yet. It is worth checking '
                     f'back.',
                actions=[('Browse tutors', '/tutors/')],
            )
        return Reply(
            text='No courses are open for booking at the moment. Every one is '
                 'either taken or not yet published.',
            actions=[('Browse tutors', '/tutors/')],
            follow_ups=('How do I become a tutor?',),
        )

    lines = []
    for course in courses:
        who = course.tutor.get_full_name() or course.tutor.username
        lines.append(
            f'• {course.name} — {who}, {money(course.monthly_rate)}/month, '
            f'{course.schedule_display}'
        )

    total = len(catalogue.available_courses(subject=subject))
    heading = (f'Yes — {total} course{"s" if total != 1 else ""} in {subject} '
               f'you can book' if subject
               else f'{total} course{"s" if total != 1 else ""} are open for booking')
    if total > len(courses):
        heading += f' (showing the {len(courses)} cheapest)'

    return Reply(
        text=heading + ':\n' + '\n'.join(lines),
        actions=[('Browse tutors', f'/tutors/?subject={subject}' if subject else '/tutors/')],
        follow_ups=('How do I book a tutor?', 'When do I pay?',
                    'Can I pay in instalments?'),
    )


def answer_subjects_offered(user, question):
    """What can be learned here at all."""
    offered = catalogue.subject_summary()
    if not offered:
        return Reply(
            text='No tutor has published a course yet, so there is nothing to '
                 'book at the moment.',
            actions=[('Browse tutors', '/tutors/')],
            follow_ups=('How do I become a tutor?',),
        )

    lines = [
        f'• {subject} — {info["count"]} course{"s" if info["count"] != 1 else ""}, '
        f'from {money(info["cheapest"])}/month'
        for subject, info in offered.items()
    ]
    return Reply(
        text=f'{len(offered)} subject{"s" if len(offered) != 1 else ""} are '
             f'being taught right now:\n' + '\n'.join(lines),
        actions=[('Browse tutors', '/tutors/')],
        follow_ups=tuple(f'Any {s.lower()} tutors?' for s in list(offered)[:3]),
    )


def answer_cheapest(user, question):
    """What the least expensive option is, overall or in one subject."""
    subject = catalogue.subject_in(question)
    courses = catalogue.available_courses(subject=subject, limit=1)

    if not courses:
        return answer_find_courses(user, question)

    course = courses[0]
    who = course.tutor.get_full_name() or course.tutor.username
    where = f' in {subject}' if subject else ''
    return Reply(
        text=f'The cheapest{where} right now is {course.name} with {who}, at '
             f'{money(course.monthly_rate)} a month — {course.schedule_display}, '
             f'{course.months} month{"s" if course.months != 1 else ""}, '
             f'{money(course.total_price)} in total.',
        actions=[('See this tutor', f'/tutors/{course.tutor_id}/')],
        follow_ups=('How do I book a tutor?', 'Can I pay in instalments?'),
    )


HANDLERS.update({
    'answer_find_courses': answer_find_courses,
    'answer_subjects_offered': answer_subjects_offered,
    'answer_cheapest': answer_cheapest,
})
