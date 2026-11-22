"""Working out which booking someone means.

"How much do I owe?" is easy when a student has one booking. With three, the
assistant has to either pick the right one or admit it does not know which —
and picking wrong is worse than asking, because the number it reports would
be confidently incorrect.

So: look for a subject or a tutor's name in what they typed, and if that does
not single out one booking, say which ones there are and let them choose.
"""
from chatbot.normalise import fuzzy_score, tokenise

# How alike a typed word and a subject/name must be to count as a reference.
NAME_MATCH = 0.55


def _candidate_names(booking):
    """Every word that could be used to refer to this booking.

    The course carries the subject and the name now, so both are ways a
    student might point at it — "my Physics course", "the HSC one".
    """
    names = set()
    if booking.course is not None:
        names.update(tokenise(booking.course.subject))
        names.update(tokenise(booking.course.name))
    for part in (booking.tutor.first_name, booking.tutor.last_name,
                 booking.tutor.username):
        names.update(tokenise(part or ''))
    return {n for n in names if len(n) > 2}


def mentioned(bookings, question):
    """The bookings the question appears to be about.

    Returns every booking whose subject or tutor is named. An empty result
    means the question named nothing, not that nothing matched.
    """
    words = [w for w in tokenise(question) if len(w) > 2]
    if not words:
        return []

    hits = []
    for booking in bookings:
        names = _candidate_names(booking)
        if any(word == name or fuzzy_score(word, name) >= NAME_MATCH
               for word in words for name in names):
            hits.append(booking)
    return hits


def resolve(bookings, question):
    """Pick the one booking this question is about.

    Returns (booking, alternatives). When `booking` is None and
    `alternatives` is non-empty, the caller should ask which one — never
    guess, because every grounded answer is a number about someone's money.
    """
    bookings = list(bookings)
    if not bookings:
        return None, []

    if len(bookings) == 1:
        return bookings[0], []

    named = mentioned(bookings, question)
    if len(named) == 1:
        return named[0], []
    if len(named) > 1:
        return None, named

    return None, bookings


def describe(booking):
    """A short label a person would recognise, for a disambiguation prompt."""
    subject = booking.course.subject if booking.course else 'tutoring'
    who = booking.tutor.get_full_name() or booking.tutor.username
    return f'{subject} with {who}'
