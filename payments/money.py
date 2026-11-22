"""How an amount of money is written for a person to read."""
from decimal import Decimal, InvalidOperation


def taka(amount):
    """৳ with thousands separators, and paise only when there are some.

    Messages were built with f'৳{amount}', which prints a Decimal as it is
    stored — '৳8000.0' — beside pages that say '৳8,000'.
    """
    try:
        value = Decimal(str(amount if amount is not None else 0))
    except (InvalidOperation, ValueError):
        return f'৳{amount}'
    text = f'{value:,.2f}'
    return '৳' + (text[:-3] if text.endswith('.00') else text)
