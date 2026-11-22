"""A record of what people asked.

Kept for one reason above all: the questions the assistant FAILED to answer
are the only reliable guide to what belongs in the knowledge base next.
Guessing at what users will ask is how FAQ pages end up answering questions
nobody has.

Only the question, the matched intent and the confidence are stored — never
the answer, which may contain the asker's own financial details.
"""
from django.db import models

from accounts.models import User


class ChatMessage(models.Model):
    # Null for an anonymous visitor; their session still groups the exchange.
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                             blank=True, related_name='chat_messages')
    session_key = models.CharField(max_length=40, blank=True, default='',
                                   db_index=True)

    question = models.TextField()
    intent = models.CharField(max_length=40, blank=True, default='', db_index=True)
    confidence = models.FloatField(default=0.0)
    answered = models.BooleanField(default=False, db_index=True)

    asked_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-asked_at']
        indexes = [models.Index(fields=['answered', '-asked_at'])]

    def __str__(self):
        state = 'answered' if self.answered else 'unanswered'
        return f'{self.question[:48]!r} ({state})'
