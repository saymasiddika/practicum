"""SSLCommerz hosted-checkout client.

Two calls make up the integration:

1. `create_session()` asks SSLCommerz for a checkout page and returns its URL.
   We send the customer's browser there.
2. `validate()` asks SSLCommerz whether a given transaction really succeeded.

The second one is not optional. SSLCommerz posts the result back to our
success URL, but that POST arrives over the open internet and anyone can forge
it — the only trustworthy answer comes from calling the validation API
ourselves with our own store credentials. Everything in this module is built
around that: `validate()` is the sole authority on whether money arrived.

Uses urllib from the standard library so the project gains no new dependency.
"""
import json
import logging
from decimal import Decimal
from urllib import error, parse, request

from django.conf import settings

logger = logging.getLogger(__name__)

SANDBOX_HOST = 'https://sandbox.sslcommerz.com'
LIVE_HOST = 'https://securepay.sslcommerz.com'

SESSION_PATH = '/gwprocess/v4/api.php'
VALIDATION_PATH = '/validator/api/validationserverAPI.php'

TIMEOUT_SECONDS = 20

# SSLCommerz reports a successful, settled transaction as one of these.
VALID_STATUSES = ('VALID', 'VALIDATED')


class GatewayError(Exception):
    """The gateway could not be reached, or answered with something unusable."""


def is_configured():
    """True when store credentials are present, so the UI can hide the option."""
    return bool(settings.SSLCOMMERZ_STORE_ID and settings.SSLCOMMERZ_STORE_PASSWORD)


def host():
    return SANDBOX_HOST if settings.SSLCOMMERZ_SANDBOX else LIVE_HOST


def _post(url, payload):
    body = parse.urlencode(payload).encode()
    req = request.Request(url, data=body, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode())
    except (error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        raise GatewayError(f'SSLCommerz did not answer usefully: {exc}') from exc


def _get(url):
    try:
        with request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode())
    except (error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        raise GatewayError(f'SSLCommerz did not answer usefully: {exc}') from exc


def create_session(payment, return_urls):
    """Open a checkout session and return the URL to send the customer to.

    `return_urls` carries absolute success/fail/cancel/ipn URLs — they must be
    absolute because SSLCommerz redirects the browser to them.
    """
    if not is_configured():
        raise GatewayError('SSLCommerz store credentials are not configured.')

    booking = payment.booking
    student = booking.student

    payload = {
        'store_id': settings.SSLCOMMERZ_STORE_ID,
        'store_passwd': settings.SSLCOMMERZ_STORE_PASSWORD,
        'total_amount': f'{Decimal(payment.amount):.2f}',
        'currency': 'BDT',
        'tran_id': payment.gateway_tran_id,

        'success_url': return_urls['success'],
        'fail_url': return_urls['fail'],
        'cancel_url': return_urls['cancel'],
        'ipn_url': return_urls['ipn'],

        # SSLCommerz rejects the session if these are blank, so fall back to
        # something truthful rather than inventing customer details.
        'cus_name': student.get_full_name() or student.username,
        'cus_email': student.email or 'no-reply@example.com',
        'cus_phone': student.phone or '01700000000',
        'cus_add1': 'N/A',
        'cus_city': 'Dhaka',
        'cus_country': 'Bangladesh',

        'shipping_method': 'NO',
        'product_name': f'Tutoring — booking #{booking.id}',
        'product_category': 'Education',
        'product_profile': 'non-physical-goods',

        'value_a': str(payment.id),          # echoed back to us untouched
        'value_b': str(booking.id),
    }

    data = _post(host() + SESSION_PATH, payload)

    if data.get('status') != 'SUCCESS' or not data.get('GatewayPageURL'):
        reason = data.get('failedreason') or data.get('status') or 'unknown reason'
        raise GatewayError(f'SSLCommerz refused the session: {reason}')

    return data['GatewayPageURL'], data.get('sessionkey', '')


def validate(val_id):
    """Ask SSLCommerz what really happened. This is the only trusted answer."""
    if not is_configured():
        raise GatewayError('SSLCommerz store credentials are not configured.')

    query = parse.urlencode({
        'val_id': val_id,
        'store_id': settings.SSLCOMMERZ_STORE_ID,
        'store_passwd': settings.SSLCOMMERZ_STORE_PASSWORD,
        'format': 'json',
    })
    return _get(f'{host()}{VALIDATION_PATH}?{query}')


def verify_settlement(payment, val_id):
    """Confirm a payment really settled, for the right amount, in BDT.

    Returns (ok, detail). A False here means: do not mark this payment
    approved, whatever the browser POST claimed.
    """
    result = validate(val_id)

    status = (result.get('status') or '').upper()
    if status not in VALID_STATUSES:
        return False, f'gateway reported status {status or "unknown"}'

    # The transaction must be the one we opened, not another store's.
    if result.get('tran_id') != payment.gateway_tran_id:
        logger.warning(
            'SSLCommerz tran_id mismatch: expected %s, got %s',
            payment.gateway_tran_id, result.get('tran_id'),
        )
        return False, 'transaction id does not match this payment'

    if (result.get('currency') or '').upper() != 'BDT':
        return False, f'unexpected currency {result.get("currency")}'

    # Amount is the one that matters: a forged callback would understate it.
    try:
        settled = Decimal(str(result.get('amount', '0')))
    except (TypeError, ValueError):
        return False, 'gateway returned an unreadable amount'

    if settled < Decimal(str(payment.amount)):
        logger.warning(
            'SSLCommerz amount short: expected %s, settled %s',
            payment.amount, settled,
        )
        return False, f'settled amount ৳{settled} is less than ৳{payment.amount}'

    return True, result
