from decimal import Decimal, ROUND_DOWN

from .time_utils import get_current_app_date_str, get_current_app_datetime


def calculate_adjusted_price_with_fixed_nine(value):
    try:
        amount = Decimal(str(value))
    except Exception:
        return Decimal('0')

    if amount <= 0:
        return Decimal('0')

    integer_part = amount.quantize(Decimal('1'), rounding=ROUND_DOWN)
    return integer_part + Decimal('0.9')


def calculate_adjusted_price_with_freight_fixed_nine(base_price, freight, ratio):
    try:
        base_amount = Decimal(str(base_price))
    except Exception:
        base_amount = Decimal('0')

    try:
        ratio_amount = Decimal(str(ratio))
    except Exception:
        ratio_amount = Decimal('0')

    calculated_amount = base_amount * ratio_amount
    return calculate_adjusted_price_with_fixed_nine(calculated_amount)


def get_auto_adjust_ratio_by_sell_price(sell_price):
    try:
        sell_amount = Decimal(str(sell_price))
    except Exception:
        return None

    if sell_amount > Decimal('100'):
        return Decimal('1.2')
    if sell_amount >= Decimal('30'):
        return Decimal('1.25')
    if sell_amount > Decimal('0'):
        return Decimal('1.4')
    return None


def calculate_auto_adjusted_price_with_sell_price_tiers(base_price, freight, sell_price):
    ratio = get_auto_adjust_ratio_by_sell_price(sell_price)
    if ratio is None:
        return Decimal('0')
    return calculate_adjusted_price_with_freight_fixed_nine(base_price, freight, ratio)
