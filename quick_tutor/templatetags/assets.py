"""A static tag that changes when the file does.

Django's `{% static %}` emits a bare path, and the development server sends
only Last-Modified with no Cache-Control. Browsers then cache heuristically,
so an edited stylesheet can keep serving the old bytes for a while — which is
exactly what happened after the chat panel's display rule was corrected: the
fix was live on the server and invisible in the browser.

Appending the file's modification time gives every change a fresh URL, so a
stale copy can never be used and nobody has to be told to hard-refresh.
"""
import os

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

# Resolving a path hits the filesystem, so remember what we find. In DEBUG the
# cache is skipped, because that is precisely when files are changing.
_versions = {}


def _version(path):
    try:
        absolute = finders.find(path)
        if absolute and os.path.exists(absolute):
            return str(int(os.path.getmtime(absolute)))
    except Exception:                      # pragma: no cover - never break a page
        pass
    return ''


@register.simple_tag
def static_v(path):
    """`{% static_v 'css/eduai.css' %}` -> /static/css/eduai.css?v=1757... """
    url = static(path)
    if settings.DEBUG:
        stamp = _version(path)
    else:
        if path not in _versions:
            _versions[path] = _version(path)
        stamp = _versions[path]
    return f'{url}?v={stamp}' if stamp else url
