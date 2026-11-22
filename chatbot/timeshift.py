"""Reading a future date out of a question.

"What refund would I get if I stop now?" is about today. "And after next
week?" is the same question about a different day, and answering it with
today's figure — which is what a naive follow-up does — is quietly wrong
rather than obviously wrong, which is worse.

So: pull an offset out of the phrasing and let the caller quote for that day
instead. Deliberately small; it recognises the handful of ways people
actually express "later" and returns None for everything else, because a
wrong date here becomes a wrong number about someone's money.
"""
import re

from dateutil.relativedelta import relativedelta

NUMBER_WORDS = {
    'a': 1, 'an': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'couple': 2,
    'few': 3, 'dui': 2, 'tin': 3, 'ek': 1,
}

UNITS = {
    'day': 'days', 'days': 'days',
    'week': 'weeks', 'weeks': 'weeks',
    'month': 'months', 'months': 'months',
    'din': 'days', 'shoptaho': 'weeks', 'mash': 'months',
}

# "next week", "next month" — an implied one.
NEXT = re.compile(r'\bnext\s+(week|month|day)\b')

# "in 2 weeks", "after three days", "10 days later"
COUNTED = re.compile(
    r'\b(?:in|after|within)?\s*([0-9]+|' + '|'.join(NUMBER_WORDS) + r')\s+'
    r'(' + '|'.join(UNITS) + r')\b'
)

# Phrases that explicitly mean today, so a follow-up can reset.
NOW = re.compile(r'\b(now|today|right now|at the moment|ekhon|aj|aaj)\b')


def offset_in(question):
    """A relativedelta for how far ahead the question looks, or None.

    None means "no future date mentioned" — the caller should use today.
    """
    text = (question or '').lower()

    if NOW.search(text) and not NEXT.search(text):
        return relativedelta(days=0)

    match = NEXT.search(text)
    if match:
        unit = UNITS[match.group(1)]
        return relativedelta(**{unit: 1})

    match = COUNTED.search(text)
    if match:
        raw, unit = match.group(1), UNITS[match.group(2)]
        count = int(raw) if raw.isdigit() else NUMBER_WORDS.get(raw, 1)
        # A sanity bound: nobody is asking about a refund in nine years, and
        # an absurd date would produce an absurd answer with a straight face.
        if 0 < count <= 60:
            return relativedelta(**{unit: count})

    return None


def describe(delta):
    """How to refer to the shifted date in a sentence."""
    if delta is None or (not delta.days and not delta.months):
        return 'today'
    if delta.months:
        n = delta.months
        return f'in {n} month{"s" if n != 1 else ""}'
    n = delta.days
    if n and n % 7 == 0:
        weeks = n // 7
        return f'in {weeks} week{"s" if weeks != 1 else ""}'
    return f'in {n} day{"s" if n != 1 else ""}'
