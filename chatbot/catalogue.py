"""Finding out what is actually on offer.

"Are there any maths teachers?" is the first question a student asks, and the
assistant had no way to answer it — no knowledge-base entry says who teaches
what, because that changes every time a tutor publishes a course.

So this reads the courses. Two things follow from that:

  · The subject vocabulary comes from the DATA, not from a hardcoded list.
    Publish a Chemistry course and "any chemistry tutors?" starts working
    with no code change.
  · The answer is specific — names, prices, times — which is the whole
    advantage of an assistant wired into the database rather than a general
    model guessing at what a tutoring site probably charges.
"""
from chatbot.normalise import fuzzy_score, stem, tokenise

# How alike a typed word and a real subject must be to count as naming it.
SUBJECT_MATCH = 0.5
MIN_STEM = 3

# School subjects that might be ASKED about but are not necessarily taught
# here. Without this the assistant cannot tell "you named no subject" from
# "you named one nobody teaches", and answers a question about Chemistry by
# listing the Maths courses — which reads as though it did not understand.
# Only ever used to say no; what CAN be booked still comes from the data.
ASKABLE_SUBJECTS = (
    'mathematics', 'physics', 'chemistry', 'biology', 'english', 'bangla',
    'accounting', 'economics', 'statistics', 'geography', 'history',
    'science', 'computer', 'programming', 'ict', 'religion', 'civics',
)


def known_subjects():
    """Every subject currently being taught, as written by the tutors."""
    from tutors.models import Course
    return list(
        Course.objects.filter(is_active=True)
        .values_list('subject', flat=True).distinct()
    )


def _names_match(word, subject_word):
    """Whether a typed word refers to a word in a subject name.

    Prefix matching is what makes "math" reach "Mathematics": the two share
    almost no trigrams, so fuzzy scoring alone misses it, but one is plainly
    the start of the other once both are stemmed.
    """
    a, b = stem(word), stem(subject_word)
    if a == b:
        return True
    if len(a) >= MIN_STEM and len(b) >= MIN_STEM and (a.startswith(b) or b.startswith(a)):
        return True
    return fuzzy_score(a, b) >= SUBJECT_MATCH


def subject_in(question, subjects=None):
    """The subject this question names, or None.

    Matched against what is really on offer, so it stays correct as the
    catalogue changes.
    """
    subjects = known_subjects() if subjects is None else subjects
    words = [w for w in tokenise(question) if len(w) > 2]
    if not words:
        return None

    for subject in subjects:
        subject_words = [w for w in tokenise(subject) if len(w) > 2]
        if any(_names_match(word, sw) for word in words for sw in subject_words):
            return subject
    return None


def unavailable_subject(question):
    """A subject this question names that nobody is teaching, or None.

    Answering "any chemistry tutors?" with a list of maths courses looks like
    the assistant misread the question. Naming the gap is both more honest
    and more useful, so this is checked before falling back to everything.
    """
    if subject_in(question):
        return None                     # something on offer was named
    named = subject_in(question, subjects=ASKABLE_SUBJECTS)
    return named.title() if named else None


def available_courses(subject=None, limit=None):
    """Courses a student could book right now.

    Bookable means published, by a real tutor with a profile, and with nobody
    already in the slot — the same test the booking form applies, so the
    assistant never offers something the form would refuse.
    """
    from tutors.models import Course

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
    if subject:
        courses = courses.filter(subject__iexact=subject)

    free = [c for c in courses if c.is_bookable]
    free.sort(key=lambda c: c.monthly_rate)
    return free[:limit] if limit else free


def subject_summary():
    """Every subject on offer, with how many free courses and the cheapest."""
    summary = {}
    for course in available_courses():
        entry = summary.setdefault(course.subject, {'count': 0, 'cheapest': None})
        entry['count'] += 1
        if entry['cheapest'] is None or course.monthly_rate < entry['cheapest']:
            entry['cheapest'] = course.monthly_rate
    return dict(sorted(summary.items()))
