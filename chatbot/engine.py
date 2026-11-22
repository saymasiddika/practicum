"""Turning a question into a reply.

This is the only module that knows the whole flow, and it is deliberately
readable top to bottom:

    resolve follow-ups → retrieve → check permission → answer → log

The follow-up step is what makes it a conversation rather than a search box.
"and after next week?" carries no topic of its own; it means whatever the
last question meant. Rather than guess, the engine stitches the previous
question onto the fragment and retrieves again.
"""
from dataclasses import dataclass, field

from chatbot import answers as answer_handlers
from chatbot.knowledge import find
from chatbot.retrieval import retriever_for

# Fragments that continue a previous question instead of starting a new one.
FOLLOW_UP_MARKERS = (
    'what about', 'how about', 'and ', 'also', 'then', 'after that',
    'what if', 'and then', 'ar', 'tahole', 'next week', 'later',
)

# Short replies that only make sense against the last question.
CONTINUATIONS = ('yes', 'yeah', 'ok', 'okay', 'sure', 'no', 'nope',
                 'why', 'why not', 'really', 'and', 'more', 'go on')

FALLBACK = (
    "I do not know that one. I can help with finding a tutor, what things "
    "cost, payments, refunds, and — if you are logged in — your own bookings, "
    "balance and refund amount."
)

FALLBACK_SUGGESTIONS = (
    'How much does a tutor cost?',
    'How do refunds work?',
    'How do I book a tutor?',
)


@dataclass
class Response:
    text: str
    intent: str = ''
    confidence: float = 0.0
    answered: bool = False
    actions: list = field(default_factory=list)
    follow_ups: list = field(default_factory=list)
    needs_login: bool = False


def looks_like_follow_up(question):
    """Whether this reads as a continuation rather than a fresh question."""
    lowered = (question or '').strip().lower().rstrip('?.!')
    if not lowered:
        return False
    if lowered in CONTINUATIONS:
        return True
    # Very short, and opening with a connective.
    if len(lowered.split()) <= 6:
        return any(lowered.startswith(marker) for marker in FOLLOW_UP_MARKERS)
    return False


def _role_of(user):
    if not user or not user.is_authenticated:
        return 'anonymous'
    if user.is_staff or user.is_superuser:
        return 'staff'
    return user.role


def ask(question, user=None, last_question=''):
    """Answer one question. `last_question` is what they asked before."""
    question = (question or '').strip()
    if not question:
        return Response(text='Ask me something and I will do my best.',
                        follow_ups=list(FALLBACK_SUGGESTIONS))

    authenticated = bool(user and user.is_authenticated)
    role = _role_of(user)

    # A continuation inherits the previous question's subject. Searching the
    # fragment alone would match on nothing.
    effective = question
    if last_question and looks_like_follow_up(question):
        effective = f'{last_question} {question}'

    match = retriever_for(role, authenticated).search(effective)

    if not match.is_confident:
        if match.is_ambiguous and match.is_worth_offering:
            options = [match.entry, match.runner_up]
            return Response(
                text='I am not sure which you mean. Could it be one of these?',
                intent='clarify',
                confidence=match.score,
                answered=False,
                follow_ups=[e.phrasings[0].capitalize() for e in options if e],
            )
        return Response(text=FALLBACK, intent='', confidence=match.score,
                        answered=False, follow_ups=list(FALLBACK_SUGGESTIONS))

    entry = match.entry

    # An anonymous visitor asking something personal is invited in, rather
    # than being told the question was not understood.
    if entry.needs_login and not authenticated:
        return Response(
            text='I can look that up once you are logged in — it is about your '
                 'own bookings, so I need to know who you are.',
            intent=entry.intent,
            confidence=match.score,
            answered=False,
            needs_login=True,
            actions=[('Log in', '/accounts/login/'), ('Register', '/accounts/register/')],
        )

    if entry.handler:
        handler = answer_handlers.HANDLERS.get(entry.handler)
        if handler is None:                       # pragma: no cover - guard
            return Response(text=FALLBACK, intent=entry.intent,
                            confidence=match.score, answered=False)
        reply = handler(user, question)
        return Response(
            text=reply.text,
            intent=entry.intent,
            confidence=match.score,
            answered=True,
            actions=list(reply.actions),
            follow_ups=list(reply.follow_ups) or list(entry.follow_ups),
        )

    return Response(
        text=entry.answer,
        intent=entry.intent,
        confidence=match.score,
        answered=True,
        actions=list(entry.actions),
        follow_ups=list(entry.follow_ups),
    )
