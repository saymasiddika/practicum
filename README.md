# Online Tutor Management System

A Django-based REST API for managing online tutoring sessions, bookings, reviews, and payments.

## Project Structure

```
quick_tutor/
├── accounts/          # User authentication and profiles
├── tutors/            # Tutor profiles and management
├── bookings/          # Session booking management
├── reviews/           # Review and rating system
├── payments/          # Payment processing
└── quick_tutor/       # Main project settings
```

## Features

- **User Management**: Register as student or tutor
- **Tutor Profiles**: Create and manage tutor profiles with subjects and ratings
- **Session Booking**: Students can book sessions with tutors
- **Review System**: Students can review and rate completed sessions
- **Payment Processing**: Track payments for sessions

## Installation

1. Create and activate virtual environment:
```bash
python -m venv .venv
.venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```
`pillow` (avatar uploads) and `python-dateutil` (course-duration maths) are
required at runtime, so install from `requirements.txt` rather than by hand.

3. Run migrations:
```bash
python manage.py migrate
```

4. Create superuser:
```bash
python manage.py createsuperuser
```

## Running the Application

Start the development server:
```bash
python manage.py runserver
```

The application will be available at `http://localhost:8000`

## Routes

This is a server-rendered Django application — every route below returns HTML,
not JSON. (DRF serializers and `APIView` classes exist in the tree but are not
routed; they are currently unused scaffolding.)

### Accounts
- `GET|POST /accounts/register/` - Register as a student or tutor (tutors must supply an NID number)
- `GET|POST /accounts/login/` - Log in
- `GET       /accounts/logout/` - Log out
- `GET       /accounts/profile/` - View your profile
- `GET|POST /accounts/edit-profile/` - Edit profile and avatar
- `GET|POST /accounts/change-password/` - Change password
- `GET|POST /accounts/notification-settings/` - Notification preferences (placeholder)
- `GET|POST /accounts/delete-account/` - Permanently delete your account

### Tutors
- `GET       /tutors/` - Browse tutors (filters: `search`, `subject`, `rating`)
- `GET       /tutors/<id>/` - Tutor detail, where `<id>` is the tutor's **user** id
- `GET|POST /tutors/create-profile/` - Create a tutor profile (tutors only)
- `GET|POST /tutors/edit-profile/` - Edit tutor profile (tutors only)
- `POST      /tutors/schedule/add/` - Add an availability slot
- `GET       /tutors/schedule/<id>/delete/` - Remove an availability slot

### Bookings
- `GET       /bookings/` - Your bookings (students) or students (tutors)
- `GET       /bookings/<id>/` - Booking detail
- `GET|POST /bookings/create/` - Create a booking (students only)
- `GET       /bookings/<id>/confirm/` - Confirm a request (tutor only)
- `GET       /bookings/<id>/reject/` - Reject a request (tutor only)
- `GET       /bookings/<id>/cancel/` - Cancel (student only, before any payment)
- `GET       /bookings/<id>/complete/` - Mark complete once the course period has elapsed
- `POST      /bookings/<id>/tutor-update/` - Set the meet link and message (tutor only)

### Reviews
- `GET       /reviews/` - All reviews
- `GET       /reviews/<id>/` - Review detail
- `GET|POST /reviews/create/?booking=<id>` - Review a completed booking (student only)

### Payments
- `GET       /payments/` - Your payments, grouped by booking
- `GET       /payments/<id>/` - Payment detail
- `GET|POST /payments/create/?booking_id=<id>` - Submit a payment (student only)
- `GET       /payments/manage/` - Payment review dashboard (admin/staff only)
- `GET       /payments/approve/<id>/` - Approve a payment (admin/staff only)
- `GET       /payments/reject/<id>/` - Reject a payment (admin/staff only)

### Admin panel
- `GET       /dashboard/` - Role-aware dashboard; admin overview for staff/superusers
- `GET       /admin-panel/users/` - User management (detail, edit, toggle, delete)
- `GET       /admin-panel/bookings/` - Booking management
- `GET       /admin-panel/tutors/` - Tutor profile management
- `GET       /admin-panel/reviews/` - Review moderation

## Admin Panel

Django's built-in `/admin/` is **disabled** (commented out in `quick_tutor/urls.py`)
and replaced by a custom panel. Sign in as a staff or superuser account and go to
`http://localhost:8000/dashboard/`, which serves the admin overview.

Create your own superuser with `python manage.py createsuperuser` — do not ship
shared credentials.

## Database Models

### User (`accounts`)
- Custom user model extending `AbstractUser`, set as `AUTH_USER_MODEL`
- Fields: username, email, password, role (student/tutor), first_name, last_name,
  avatar, phone, bio, nid_number
- `nid_number` is required when registering as a tutor

### TutorProfile (`tutors`)
- OneToOne with User
- Fields: subject, bio, rating, monthly_rate (default BDT 4000), created_at, updated_at

### TutorSchedule (`tutors`)
- ForeignKey to User (`schedules`)
- Fields: days (comma-separated, e.g. `"0,2,4"` for Sun/Tue/Thu), time, months
- `is_booked()` reports whether an active booking already holds the slot

### Booking (`bookings`)
- ForeignKey to User twice (`student_bookings`, `tutor_bookings`)
- ForeignKey to TutorSchedule (nullable, `SET_NULL`)
- Fields: date, months, status (pending/confirmed/completed/rejected/cancelled),
  meet_link, tutor_message, created_at, updated_at
- Computed properties: `total_amount`, `total_paid`, `balance_due`, `is_fully_paid`

### Payment (`payments`)
- **ForeignKey** to Booking (`payments`) — a booking may have several instalments
- Fields: amount, commission, tutor_income, is_first_payment, payment_method
  (bkash/nagad/rocket), sender_number, transaction_id,
  status (pending/approved/rejected), created_at, updated_at
- Payments are verified manually by an admin from the transaction ID

### Review (`reviews`)
- OneToOne with Booking
- Fields: rating (1-5), comment, created_at
- Submitting a review recalculates the tutor's average rating

## Payments

Two routes, side by side. The student picks one on the payment page.

**Online — SSLCommerz.** Card, bKash, Nagad or Rocket through SSLCommerz's
hosted checkout. The payment is confirmed automatically; no transaction ID to
type and no admin step.

**By hand.** The student sends money themselves and enters the transaction ID.
An admin checks it against the bank record and approves or rejects it. This is
the original flow and still works whether or not the gateway is configured.

### Configuring SSLCommerz

Credentials are read from the environment and are never committed. The
simplest way is a `.env` file in the project root, which `settings.py` loads
at startup and `.gitignore` excludes:

```ini
SSLCOMMERZ_STORE_ID=your_store_id
SSLCOMMERZ_STORE_PASSWORD=your_store_password
SSLCOMMERZ_SANDBOX=true                  # false for the live gateway
SSLCOMMERZ_BASE_URL=http://127.0.0.1:8000
```

A real environment variable always beats the file, so a deployment can
override any of these without editing it.

Sandbox is the default deliberately — going live has to be an explicit act,
not the result of an unset variable. With no credentials set, the online
option simply does not appear and the manual flow carries on as before.

`SSLCOMMERZ_BASE_URL` is where SSLCommerz sends the customer back to. It must
be the address a browser can reach.

### How a payment is verified

SSLCommerz posts the result to `/payments/gateway/success/`, but that request
arrives over the open internet and can be forged. It is treated only as a hint
about *which* payment to check. The application then calls SSLCommerz's
validation API itself, with its own store credentials, and approves the
payment only if that answer says the transaction is valid, carries the
matching transaction id, is in BDT, and settled for at least the amount owed.

Settlement is idempotent: the browser redirect and the server-to-server IPN
both call the same code, and whichever arrives first is the one that counts.

### A limitation worth knowing on localhost

The success, fail and cancel callbacks work locally, because the customer's
own browser is redirected to them. The **IPN does not**: it is a
server-to-server call from SSLCommerz, and they cannot reach `127.0.0.1`.

In practice that means a payment completed on localhost is confirmed by the
browser redirect. If the customer closes the tab before being redirected, the
payment stays `pending` until someone resolves it. To exercise the IPN, expose
the server with a tunnel (ngrok, Cloudflare Tunnel) and set
`SSLCOMMERZ_BASE_URL` to that public address.

## Commission Model

Commission is charged against how much of the **first month** is still uncovered
by approved payments on that booking:

- the slice of a payment covering the remaining first month: **50%**
  (broker 40% + platform 10%)
- everything beyond the first month: **10%** (platform only)

Because the split is based on the running total rather than a first-payment flag,
paying the first month in instalments is charged the same 50% overall as paying
it in one go. `python manage.py fix_commissions [--dry-run]` recalculates
commission and tutor income for existing payment rows.

## Deployment notes

Before deploying, the following must change — the project ships with
development defaults:

- `DEBUG = False`, a `SECRET_KEY` read from the environment, and a real
  `ALLOWED_HOSTS` list.
- Run `python manage.py collectstatic`; the web server serves `STATIC_ROOT`
  (`staticfiles/`) at `STATIC_URL`.
- **Media files are only routed by Django while `DEBUG` is true.** In
  production the web server must serve `MEDIA_ROOT` (`media/`) at
  `MEDIA_URL`, or every uploaded avatar will 404. Django deliberately does
  not serve user uploads itself.
- SQLite runs in WAL mode with a 20-second busy timeout, which is enough for
  light concurrent use. For real traffic move to PostgreSQL — SQLite allows
  only one writer at a time.
- `PAYMENT_RECIPIENT_NUMBER` / `PAYMENT_RECIPIENT_DISPLAY` in `settings.py`
  hold the mobile-banking account students pay into. Update both together.

## Development

The project uses Django's default SQLite database for development. For production, configure a PostgreSQL or MySQL database in `quick_tutor/settings.py`.

## Testing

```bash
python manage.py test
```

99 tests covering the commission engine, the booking lifecycle, the admin
permission gates, review validation and tutor listing filters. They run against
a throwaway database, so the development data is untouched.

Run a single app with `python manage.py test payments`.

## Security Notes

- Change the SECRET_KEY in production
- Set DEBUG=False in production
- Use HTTPS in production
- Implement CORS if needed (install django-cors-headers)

