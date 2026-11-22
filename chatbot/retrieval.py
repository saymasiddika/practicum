"""Matching a question to something the assistant knows.

Classic information retrieval, implemented directly rather than pulled in
from a library, so the whole thing is readable and adds no dependency.

  · Every phrasing in the knowledge base is a document.
  · Documents and the incoming question become TF-IDF vectors — a word
    counts for more when it is rare across the knowledge base, so 'refund'
    carries far more signal than 'how'.
  · The best match is the document with the highest cosine similarity.

Two refinements matter as much as the maths:

  · **A typo should not be a dead end.** Words the vocabulary does not know
    are matched approximately by character trigrams before scoring.
  · **Being unsure is an answer.** Below CONFIDENT the assistant says so
    rather than serving the nearest thing it found, and when the top two
    matches are close it asks which was meant. A retrieval system that
    always answers is a retrieval system that is often wrong.
"""
import math
import re
from collections import Counter
from dataclasses import dataclass

from chatbot.knowledge import VOCABULARY, entries_for
from chatbot.normalise import closest_word, normalise

# Cosine similarity thresholds, chosen against the test set in tests.py.
CONFIDENT = 0.42        # answer it
AMBIGUOUS_GAP = 0.08    # if second place is this close, ask which was meant
FLOOR = 0.18            # below this, do not even offer a guess

# First-person constructions, which mark a question as being about the asker.
# "my" is the obvious one, but "do I have any bookings" carries the same
# meaning without it, and both must beat the general leaflet on the same
# words. Matching the RAW question, because most of these are stopwords by
# the time normalisation is done with them.
PERSONAL = re.compile(
    r"\b(my|mine|do i have|have i|am i|i have|i owe|for me|"
    r"amar|amake|ami)\b",
    re.IGNORECASE,
)

# How much of the question the knowledge base must recognise before the score
# is trusted at face value. Below it the score is damped in proportion, which
# is what keeps out-of-scope questions out of confident answers.
MIN_COVERAGE = 0.6


@dataclass
class Match:
    intent: str
    entry: object
    score: float
    runner_up: object = None
    runner_up_score: float = 0.0

    @property
    def is_confident(self):
        return self.score >= CONFIDENT

    @property
    def is_ambiguous(self):
        """Two plausible readings — better to ask than to pick."""
        return (self.runner_up is not None
                and self.score >= FLOOR
                and (self.score - self.runner_up_score) < AMBIGUOUS_GAP)

    @property
    def is_worth_offering(self):
        return self.score >= FLOOR


class Retriever:
    """A TF-IDF index over the phrasings in the knowledge base.

    Built once per set of entries and cached, because the knowledge base does
    not change between requests.
    """

    def __init__(self, entries):
        self.entries = entries
        self.documents = []      # (entry, token list)
        for entry in entries:
            for phrase in entry.phrasings:
                tokens = normalise(phrase)
                if tokens:
                    self.documents.append((entry, tokens))
        self.idf = self._build_idf()
        self.vectors = [(entry, self._vector(tokens))
                        for entry, tokens in self.documents]

    def _build_idf(self):
        """How rare each word is across the knowledge base.

        Smoothed, so a word appearing in every document still has a small
        positive weight rather than vanishing.
        """
        total = len(self.documents) or 1
        appearances = Counter()
        for _entry, tokens in self.documents:
            for token in set(tokens):
                appearances[token] += 1
        return {token: math.log((total + 1) / (count + 1)) + 1
                for token, count in appearances.items()}

    def _vector(self, tokens):
        """A length-normalised TF-IDF vector, as {token: weight}."""
        if not tokens:
            return {}
        counts = Counter(tokens)
        longest = max(counts.values())
        vector = {}
        for token, count in counts.items():
            # Sub-linear term frequency: saying 'refund' five times does not
            # make the question five times more about refunds.
            tf = 0.5 + 0.5 * (count / longest)
            vector[token] = tf * self.idf.get(token, 1.0)
        norm = math.sqrt(sum(w * w for w in vector.values())) or 1.0
        return {token: w / norm for token, w in vector.items()}

    def _repair(self, tokens):
        """Give unknown words their most likely intended spelling.

        Also reports how much of the question the knowledge base actually
        recognised, which is what separates a typo from a question about
        something else entirely.
        """
        repaired, recognised = [], 0.0
        for token in tokens:
            if token in self.idf:
                repaired.append(token)
                recognised += 1.0
                continue
            guess = closest_word(token, VOCABULARY)
            if guess:
                repaired.append(guess)
                recognised += 0.5      # probably a typo, but only probably
            else:
                repaired.append(token)  # genuinely foreign; contributes nothing
        coverage = recognised / len(tokens) if tokens else 0.0
        return repaired, coverage

    def search(self, question):
        """The best entry for this question, with its confidence."""
        tokens, coverage = self._repair(normalise(question))
        if not tokens:
            return Match(intent='', entry=None, score=0.0)

        query = self._vector(tokens)

        # Cosine similarity: both vectors are already length-normalised, so
        # the dot product IS the cosine.
        best = {}
        for entry, vector in self.vectors:
            score = sum(weight * query.get(token, 0.0)
                        for token, weight in vector.items())
            # An entry has several phrasings; keep its strongest.
            if score > best.get(entry, 0.0):
                best[entry] = score

        if not best:
            return Match(intent='', entry=None, score=0.0)

        # Ties go to the grounded entry ONLY when the question is personal.
        # "What are MY bookings" wants the live lookup; "how do I pay" wants
        # the leaflet, and letting a grounded entry win that one hides the
        # general answer entirely.
        personal = bool(PERSONAL.search(question or ''))
        ranked = sorted(
            best.items(),
            key=lambda pair: (round(pair[1], 3),
                              bool(pair[0].handler) and personal),
            reverse=True,
        )
        top_entry, top_score = ranked[0]
        second_entry, second_score = (ranked[1] if len(ranked) > 1 else (None, 0.0))

        # A question built mostly from words this knowledge base has never
        # seen is a question about something else. Cosine similarity alone
        # cannot tell: it only looks at the words that DID overlap, so
        # "what is the weather in Dhaka" matches "what can you do" on the
        # strength of "what". Damping by coverage is what stops the
        # assistant answering confidently about the weather.
        if coverage < MIN_COVERAGE:
            damping = (coverage / MIN_COVERAGE) ** 2
            top_score *= damping
            second_score *= damping
            # And a hard ceiling: however well the recognised words happened
            # to match, a question the knowledge base mostly did not
            # understand never earns a confident answer. "Write me a poem"
            # shares one word with "write a review"; that is not knowing.
            top_score = min(top_score, CONFIDENT - 0.01)
            second_score = min(second_score, CONFIDENT - 0.01)

        return Match(
            intent=top_entry.intent,
            entry=top_entry,
            score=round(top_score, 4),
            runner_up=second_entry,
            runner_up_score=round(second_score, 4),
        )


# One retriever per audience, built lazily and kept — the knowledge base is
# static, so rebuilding it on every request would be pure waste.
_CACHE = {}


def retriever_for(role, authenticated):
    key = (role, bool(authenticated))
    if key not in _CACHE:
        _CACHE[key] = Retriever(entries_for(role, authenticated))
    return _CACHE[key]


def clear_cache():
    """Tests build knowledge bases of their own."""
    _CACHE.clear()
