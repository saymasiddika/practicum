"""Turning what someone typed into tokens the retriever can match on.

Three problems to solve before any matching happens:

  1. People type the same thing many ways — "cost", "costs", "costing",
     "price", "how much". A light stemmer and a synonym table collapse those
     onto one token.
  2. People make typos. "commision", "refnd", "tutior" should still find the
     right answer, so unmatched words fall back to character n-grams.
  3. This platform's users write Banglish. "koto taka", "kivabe", "taka
     ferot" are how a real student asks, and a bot that only understands
     formal English is a bot they will not use. Bengali script is handled
     too, since a phone keyboard will produce it.

Nothing here needs a library. That is deliberate: the project has eight small
dependencies and this adds none, and every line is explainable in a viva.
"""
import re
import unicodedata

# ── Words that carry no meaning for matching ─────────────────────────────
STOPWORDS = {
    'a', 'an', 'the', 'is', 'are', 'am', 'was', 'were', 'be', 'been', 'being',
    'do', 'does', 'did', 'doing', 'have', 'has', 'had', 'having',
    'i', 'me', 'you', 'your', 'yours', 'we', 'us', 'our',
    # NOT 'my' or 'mine': possession is what separates "how do I book"
    # from "what are MY bookings", and the second wants live data.
    'it', 'its', 'this', 'that', 'these', 'those', 'there', 'here',
    'to', 'of', 'in', 'on', 'at', 'by', 'for', 'with', 'from', 'as',
    'and', 'or', 'but', 'if', 'then', 'so', 'than', 'too', 'very',
    'can', 'could', 'will', 'would', 'shall', 'should', 'may', 'might',
    'please', 'kindly', 'want', 'need', 'like', 'know', 'tell', 'give',
    # Interrogatives carry no topic. "What is the price" and "how much is the
    # price" are the same question; the signal is entirely in 'price'. Left in,
    # a phrasing like "what can you do" collapses to the single token 'what'
    # and becomes a magnet that any question containing "what" matches
    # perfectly.
    'what', 'how', 'which', 'where', 'why', 'who', 'whom', 'whose',
    # NOT 'when': it marks a question about time, which is a topic.
    'much', 'many', 'get', 'got', 'go', 'goes', 'make', 'made',
}

# ── Synonyms, collapsed onto one canonical token ─────────────────────────
# Left side is what people type; right side is what the knowledge base is
# written in. Banglish and Bengali sit here alongside English, because to the
# retriever they are simply more ways of saying the same thing.
SYNONYMS = {
    # money
    'cost': 'price', 'costs': 'price', 'costing': 'price', 'charge': 'price',
    'charges': 'price', 'fee': 'price', 'fees': 'price', 'rate': 'price',
    'rates': 'price', 'pricing': 'price', 'taka': 'price', 'tk': 'price',
    'money': 'price', 'amount': 'price', 'koto': 'price', 'kto': 'price',
    'dam': 'price', 'daam': 'price', 'mullo': 'price',

    # paying
    'pay': 'payment', 'paying': 'payment',
    # NOT 'paid': the past tense is a question about history
    # ("how much have I paid"), not about method ("how do I pay").
    'payments': 'payment', 'bill': 'payment', 'billing': 'payment',
    'transaction': 'payment', 'porishod': 'payment', 'poyment': 'payment',
    'payemnt': 'payment', 'paymnt': 'payment',

    # owing
    'owe': 'balance', 'owing': 'balance', 'due': 'balance', 'dues': 'balance',
    'outstanding': 'balance', 'remaining': 'balance', 'baki': 'balance',
    'bakhi': 'balance', 'balence': 'balance',

    # refunds
    'refund': 'refund', 'refunds': 'refund', 'refunded': 'refund',
    'refnd': 'refund', 'refun': 'refund', 'money-back': 'refund',
    'ferot': 'refund', 'ferat': 'refund', 'fert': 'refund',
    'return': 'refund', 'returned': 'refund',

    # quitting
    'cancel': 'terminate', 'cancelling': 'terminate', 'cancellation': 'terminate',
    'quit': 'terminate', 'quitting': 'terminate', 'stop': 'terminate',
    'stopping': 'terminate', 'leaving': 'terminate',
    'drop': 'terminate', 'withdraw': 'terminate', 'bondho': 'terminate',
    # NOT 'end': "when does my course end" is a question about a date, and
    # "end my booking" is a termination. The surrounding words separate them
    # perfectly well, and forcing 'end' onto 'terminate' broke the first.

    # tutors
    'teacher': 'tutor', 'teachers': 'tutor', 'tutors': 'tutor',
    'tuitor': 'tutor', 'tutior': 'tutor', 'tuter': 'tutor',
    'instructor': 'tutor', 'master': 'tutor', 'sir': 'tutor',
    'shikkhok': 'tutor', 'ustad': 'tutor',

    # students
    'students': 'student', 'learner': 'student', 'pupil': 'student',
    'chatro': 'student', 'shikkharthi': 'student',

    # bookings
    'book': 'booking', 'books': 'booking', 'booked': 'booking',
    'bookings': 'booking', 'reserve': 'booking', 'hire': 'booking',
    'engage': 'booking', 'boking': 'booking',

    # courses
    'course': 'course', 'courses': 'course', 'class': 'course',
    'classes': 'course', 'lesson': 'course', 'lessons': 'course',
    'tuition': 'course', 'session': 'course', 'sessions': 'course',
    'porasona': 'course', 'porashuna': 'course',

    # commission
    'commission': 'commission', 'commision': 'commission',
    'comission': 'commission', 'cut': 'commission', 'percentage': 'commission',
    'percent': 'commission', 'broker': 'commission', 'brokerage': 'commission',

    # reviews
    'review': 'review', 'reviews': 'review', 'rating': 'review',
    'ratings': 'review', 'feedback': 'review', 'rate-tutor': 'review',
    'mothamot': 'review',

    # finishing
    'complete': 'complete', 'completed': 'complete', 'completing': 'complete',
    'finish': 'complete', 'finished': 'complete', 'finishing': 'complete',
    'done': 'complete', 'over': 'complete', 'shesh': 'complete',

    # how / what — Banglish question words
    'kivabe': 'how', 'kivhabe': 'how', 'kibhabe': 'how', 'kemne': 'how',
    'ki': 'what', 'kii': 'what', 'kon': 'which', 'kothay': 'where',
    'kokhon': 'when', 'kobe': 'when', 'keno': 'why',

    # signing up
    'register': 'signup', 'registration': 'signup', 'signup': 'signup',
    'join': 'signup', 'account': 'signup', 'enroll': 'signup',
    'enrol': 'signup', 'khulbo': 'signup',

    # status
    'status': 'status', 'approved': 'approved', 'approve': 'approved',
    'pending': 'pending', 'confirm': 'confirm', 'confirmed': 'confirm',
    'accept': 'confirm', 'accepted': 'confirm',
}

# Bengali script → the English token the knowledge base uses.
BENGALI = {
    'শিক্ষক': 'tutor', 'শিক্ষার্থী': 'student', 'ছাত্র': 'student',
    'টাকা': 'price', 'দাম': 'price', 'মূল্য': 'price',
    'ফেরত': 'refund', 'বাতিল': 'terminate', 'বন্ধ': 'terminate',
    'কত': 'price', 'কিভাবে': 'how', 'কোথায়': 'where', 'কখন': 'when',
    'পেমেন্ট': 'payment', 'বুকিং': 'booking', 'কোর্স': 'course',
    'রিভিউ': 'review', 'কমিশন': 'commission', 'বাকি': 'balance',
}

# ── Light stemming ───────────────────────────────────────────────────────
# Not a real Porter stemmer: just the suffixes that actually cause misses
# here, applied longest-first so 'bookings' loses 'ings' not 's'.
SUFFIXES = ('ingly', 'edly', 'ings', 'ing', 'ies', 'ied', 'es', 'ed', 'ly', 's')

TOKEN_RE = re.compile(r"[a-z0-9ঀ-৿]+")


def strip_accents(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text)
                   if not unicodedata.combining(c))


def stem(word):
    """Trim a common suffix, but never down to a stub."""
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


def tokenise(text):
    """Words only, lowercased, accents removed."""
    return TOKEN_RE.findall(strip_accents((text or '').lower()))


def canonical(word):
    """Map one word onto the vocabulary the knowledge base is written in."""
    if word in BENGALI:
        return BENGALI[word]
    if word in SYNONYMS:
        return SYNONYMS[word]
    stemmed = stem(word)
    if stemmed in SYNONYMS:
        return SYNONYMS[stemmed]
    return stemmed


def normalise(text):
    """The full pipeline: text in, matchable tokens out.

    Stopwords are dropped last, so a synonym that maps onto a stopword-like
    token still survives.
    """
    raw = tokenise(text)
    tokens = []
    for word in raw:
        if word in STOPWORDS and word not in SYNONYMS and word not in BENGALI:
            continue
        mapped = canonical(word)
        if mapped and mapped not in STOPWORDS:
            tokens.append(mapped)

    # "who are you" is every one of it a stopword, yet it is a real question.
    # Rather than lose it, fall back to the unfiltered words.
    if not tokens and raw:
        tokens = [stem(w) for w in raw]
    return tokens


def char_ngrams(word, n=3):
    """Character n-grams, used to rescue typos the synonym table missed.

    'commision' and 'commission' share most of their trigrams, so a word the
    vocabulary does not recognise can still be matched approximately.
    """
    padded = f'  {word} '
    return {padded[i:i + n] for i in range(len(padded) - n + 1)}


def fuzzy_score(word, candidate):
    """How alike two words are, 0..1, by shared character trigrams."""
    a, b = char_ngrams(word), char_ngrams(candidate)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def closest_word(word, vocabulary, threshold=0.45):
    """The vocabulary word a typo most likely meant, or None."""
    best, best_score = None, threshold
    for candidate in vocabulary:
        score = fuzzy_score(word, candidate)
        if score > best_score:
            best, best_score = candidate, score
    return best
