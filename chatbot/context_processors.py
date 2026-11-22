"""Makes the assistant's opening suggestions available on every page.

The widget lives in the base template, so its starters have to come from
somewhere that runs for every view rather than from one view's context.
"""
import json

from chatbot.views import _starters


def assistant(request):
    return {'chat_starters': json.dumps(list(_starters(request.user)))}
