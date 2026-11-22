"""The chat endpoint and the full-page view.

The widget talks to `ask` with JSON; the full page posts a plain form to the
same view, so the assistant works with JavaScript switched off. That is not
decoration — a chat window that is the only way to reach an answer, and which
breaks silently, is worse than a link.
"""
import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from chatbot.engine import ask
from chatbot.models import ChatMessage

# What one visitor may send per minute. Generous for a person, tedious for a
# script.
RATE_LIMIT = 25
RATE_WINDOW_SECONDS = 60

MAX_QUESTION = 400
HISTORY_LIMIT = 20


def _session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key or ''


def _rate_limited(request):
    """A crude per-session limit — enough to stop a loop hammering the box."""
    from django.utils import timezone
    now = timezone.now().timestamp()
    hits = [t for t in request.session.get('chat_hits', [])
            if now - t < RATE_WINDOW_SECONDS]
    if len(hits) >= RATE_LIMIT:
        request.session['chat_hits'] = hits
        return True
    hits.append(now)
    request.session['chat_hits'] = hits
    return False


def _answer(request, question):
    """Shared by the JSON and form paths."""
    question = (question or '').strip()[:MAX_QUESTION]
    user = request.user if request.user.is_authenticated else None

    last = request.session.get('chat_last_question', '')
    response = ask(question, user=user, last_question=last)

    # Remember only what a follow-up needs, and only when the question stood
    # on its own — chaining fragments onto fragments goes nowhere.
    if response.answered and response.intent not in ('greeting', 'thanks'):
        request.session['chat_last_question'] = question

    ChatMessage.objects.create(
        user=user,
        session_key=_session_key(request),
        question=question,
        intent=response.intent,
        confidence=response.confidence,
        answered=response.answered,
    )
    return response


@require_POST
def chat_ask(request):
    """JSON endpoint for the widget."""
    if request.content_type == 'application/json':
        try:
            payload = json.loads(request.body.decode() or '{}')
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': 'Could not read that.'}, status=400)
        question = payload.get('question', '')
    else:
        question = request.POST.get('question', '')

    if not question.strip():
        return JsonResponse({'error': 'Ask me something first.'}, status=400)

    if _rate_limited(request):
        return JsonResponse(
            {'answer': 'You are asking faster than I can think. Give me a moment.',
             'actions': [], 'follow_ups': [], 'answered': False},
            status=429,
        )

    response = _answer(request, question)
    return JsonResponse({
        'answer': response.text,
        'intent': response.intent,
        'confidence': round(response.confidence, 3),
        'answered': response.answered,
        'needs_login': response.needs_login,
        'actions': [{'label': label, 'url': url} for label, url in response.actions],
        'follow_ups': response.follow_ups,
    })


def chat_page(request):
    """The full-page assistant, which works without JavaScript."""
    history = request.session.get('chat_history', [])

    if request.method == 'POST':
        question = request.POST.get('question', '').strip()
        if question and not _rate_limited(request):
            response = _answer(request, question)
            history = history + [
                {'who': 'you', 'text': question},
                {'who': 'bot', 'text': response.text,
                 'actions': [{'label': l, 'url': u} for l, u in response.actions],
                 'follow_ups': response.follow_ups},
            ]
            request.session['chat_history'] = history[-HISTORY_LIMIT:]
        elif question:
            history = history + [
                {'who': 'bot', 'text': 'You are asking faster than I can think. '
                                       'Give me a moment.'},
            ]

    if request.GET.get('clear'):
        request.session['chat_history'] = []
        request.session['chat_last_question'] = ''
        history = []

    return render(request, 'chatbot/page.html', {
        'history': history,
        'starters': _starters(request.user),
    })


def _starters(user):
    """Opening suggestions, tailored to who is looking."""
    if not user.is_authenticated:
        return ('How much does a tutor cost?',
                'How do I book a tutor?',
                'How do refunds work?',
                'How do I become a tutor?')
    if user.is_staff or user.is_superuser:
        return ('What needs reviewing?',
                'How many payments are waiting?',
                'How much is owed in refunds?')
    if user.role == 'tutor':
        return ('How much have I earned?',
                'What are my bookings?',
                'How much commission do you take?',
                'What needs my attention?')
    return ('How much do I owe?',
            'What refund would I get if I stop now?',
            'When does my course end?',
            'Has my payment been approved?')
