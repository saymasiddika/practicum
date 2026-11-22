# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack

Django 6.0.4 + SQLite, server-rendered HTML (Django templates + Tailwind via CDN).
Python dependencies are pinned in `requirements.txt`; the virtualenv lives in `.venv/`.

## Development Commands

Run everything through the project virtualenv (`.venv\Scripts\python.exe` on Windows).

### Run the development server
`python manage.py runserver`

### Migrations
`python manage.py makemigrations` then `python manage.py migrate`

### Testing
`python manage.py test` — 283 tests across six apps, the project config and the ERD, all passing.
To run one app: `python manage.py test payments`.
To run one case: `python manage.py test payments.tests.CommissionCalculationTests`.

The suite concentrates on the three areas where a silent regression costs the
most, rather than chasing coverage:

- **`payments/tests.py`** — the commission engine. The rule (50% on the first
  month, 10% after) lives in `Payment.calculate_commission`, and the admin
  aggregation derives its broker/platform split from the same
  `commission_portions`. These tests pin both.
- **`bookings/tests.py`** — the booking lifecycle. Every transition is checked
  from every state, because four separate defects turned out to be the same
  bug: a transition with no guard on the current status.
- **`accounts/tests.py`** — the admin permission gates, split between the staff
  tier and the superuser tier, plus registration and profile data rules.
- `reviews/tests.py` and `tutors/tests.py` cover rating bounds, review
  eligibility, listing filters and schedule validation.

### Other
- `python manage.py createsuperuser` - create an admin account
- `python manage.py fix_commissions [--dry-run]` - recalculate commission/tutor income
  on existing payment rows

## Code Architecture

A standard Django project (`quick_tutor/`) with six apps. There is no service or
repository layer: views talk to models directly, and derived money values live as
model properties rather than stored columns.

- **accounts** - custom `User` (`AUTH_USER_MODEL`) with a `role` of student or tutor;
  session auth, registration, profile, password, account deletion.
- **tutors** - `TutorProfile` (subject, bio, rating, `monthly_rate`) and
  `TutorSchedule` (comma-separated weekday string + time + `months`). The
  schedule's FK points at `User`, not `TutorProfile`.
- **bookings** - `Booking` links a student and a tutor for N months, optionally
  against a schedule slot. Exposes `total_amount` / `total_paid` / `balance_due` /
  `is_fully_paid` as properties. `BookingRequest` is either party asking to end a
  booking early (`complete` or `terminate`) for an admin to verify.
- **reviews** - `Review`, one per booking; saving one recalculates the tutor's rating.
- **payments** - `Payment` is a **ForeignKey** to Booking, so a booking can be paid in
  instalments. Payments are submitted with a mobile-banking transaction ID and
  approved manually by an admin.
- **chatbot** - a retrieval assistant, not a language model. TF-IDF over a
  knowledge base in `chatbot/knowledge.py`, plus handlers that read the asker's
  own records. Adds no dependency; everything is standard library.

### Things that are easy to get wrong

- **Django's `/admin/` is disabled** (commented out in `quick_tutor/urls.py`). The real
  admin is the hand-rolled panel in `quick_tutor/admin_views.py` plus
  `payments.views.payment_admin_view`, reached via `/dashboard/` and `/admin-panel/...`.
  Templates under `templates/admin/` are leftovers for the disabled Django admin and
  never render.
- **`/tutors/<pk>/` looks up by *user* id**, not by `TutorProfile.id`
  (see `tutors.views.tutor_detail_view`). Link with `tutor.user.id` from a profile object.
- **Commission** is charged against how much of the first month is still uncovered:
  50% on that slice (broker 40% + platform 10%), 10% on everything beyond it. The logic
  lives in `Payment.calculate_commission` / `Payment.commission_portions` and nowhere
  else. The admin breakdown in `payment_admin_view` splits the same
  `commission_portions` result into broker/platform buckets, so there is one
  implementation to change. (Commission is deliberately not shown to students, so
  there is no longer a JS copy in `templates/payments/create.html`.)
- **Templates come in three generations**: `base/luminary_base.html` (current, Tailwind),
  `base/base.html` (older plain-CSS theme, still used by `reviews/list.html` and
  `payments/detail.html`), and a few standalone full-HTML pages (`home.html`,
  `dashboard.html`, `reviews/detail.html`). Prefer `luminary_base.html` for new pages.
- **Payments come in two flavours.** The manual flow (`payments/views.py`)
  records a transaction ID for an admin to check; the SSLCommerz flow
  (`payments/gateway.py`, `payments/gateway_views.py`) confirms automatically.
  The gateway's success callback is public and forgeable, so a payment is only
  ever approved on the strength of `gateway.verify_settlement()`, which calls
  SSLCommerz's validation API server-side. Never approve from callback POST
  data. Settlement is idempotent because the redirect and the IPN both run it.
- **Refund policy is a table, not logic.** `REFUND_TIERS` in `bookings/refunds.py`
  is the whole rule (100% before the start, then 75/50/25/0 by week, counted from
  the course start date). Change the table, not the conditionals. `quote_refund()`
  is pure — no database writes — so a figure can be shown on a form before anyone
  commits to it.
- **A tutor-initiated termination ignores the ladder** and refunds the untaught
  portion pro rata — *or* the ladder figure, whichever is larger. The ladder
  prices a student changing their mind, and a student whose tutor walks away
  has not changed their mind, so they must never end up worse off. Pro rata
  usually wins, but late in a short course it does not: day 26 of one month is
  16% pro rata against the ladder's 25%.
- **Money comes in through `payments.views.parse_amount` and nowhere else.**
  `"NaN"` is valid input to both `Decimal()` and `float()`: a Decimal NaN
  raises on the first comparison, and a float nan answers False to
  `nan <= 0`, sliding past the check meant to stop it. The rule is that the
  value must BE a finite positive number within the column's range.
- **Refund figures are snapshotted onto the `BookingRequest`**, not recomputed when
  an admin approves. Recomputing would silently move a student to a worse tier
  because the admin was slow.
- **Only the student is asked for payout details.** A refund is owed to them, so a
  tutor ending a booking is never asked for an account number — the admin queue
  flags "Not given yet" instead.
- **Ending early is admin-gated; ending free is not.** `is_cancellable` stays the
  fast path when no money has moved. `can_request_early_end` governs the
  request-and-verify path once it has.
- **The assistant answers from data, never from a model.** `chatbot/knowledge.py`
  is the whole of what it knows; adding a topic is a data change. It refuses
  rather than guesses — a question whose words the knowledge base mostly does
  not recognise can never reach a confident answer, which is what keeps
  "what is the weather" from matching "what can you do".
- **The assistant never does its own arithmetic.** Every figure it quotes comes
  from `quote_refund`, `balance_due` or a stored `tutor_income`. If it computed
  its own, it could disagree with the page the student is reading and there
  would be no way to tell which was right.
- **Commission must never reach a student through the chat.** The rule holds
  site-wide, and a chatbot is the easiest place to break it — `audience` on each
  knowledge entry is what enforces it, and `chatbot/tests.py` pins it.
- **`my` is not a stopword, and neither is `when`.** Possession is what
  separates "how do I book" from "what are MY bookings", and dropping it
  collapsed the two. Interrogatives (`what`, `how`, `who`) *are* stopwords:
  leaving them in made "what can you do" a single-token magnet that any
  question containing "what" matched perfectly.
- **DRF is installed and `APIView` classes exist** in `tutors`, `bookings` and `reviews`,
  but none of them are routed. Treat them as unused scaffolding, not as live endpoints.

## Security Notes

Everything security-critical in `settings.py` is read from the environment, with
development defaults. `.env.example` documents the variables; `.env` holds the real
values and is gitignored.

- **Development needs no configuration.** Unset variables give `DEBUG = True`, the
  published fallback key, and localhost in `ALLOWED_HOSTS`.
- **Production is `DJANGO_DEBUG=0`.** That makes `DJANGO_SECRET_KEY` and
  `DJANGO_ALLOWED_HOSTS` mandatory — the project raises `ImproperlyConfigured` at
  startup rather than running on the key that is committed to this repository. The
  HTTPS settings (SSL redirect, secure cookies, HSTS, nosniff) switch on with it, so
  `manage.py check --deploy` is clean. `quick_tutor/tests.py` pins all of this.
- **Gateway credentials come only from the environment.** Never hardcode them, never
  commit `.env`, and never put them in the README.
