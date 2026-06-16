from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import APP_TIMEZONE


@lru_cache(maxsize=1)
def get_app_timezone():
    try:
        return ZoneInfo(APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo('Asia/Shanghai')


def get_current_app_datetime(now=None):
    if now is None:
        return datetime.now(get_app_timezone())

    if now.tzinfo is None:
        return now.replace(tzinfo=get_app_timezone())

    return now.astimezone(get_app_timezone())


def get_current_app_date_str(now=None):
    return get_current_app_datetime(now).strftime('%Y-%m-%d')
