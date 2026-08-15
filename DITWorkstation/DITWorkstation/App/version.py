"""应用版本信息。"""
from datetime import date


VERSION_PREFIX = "alpha"
APP_VERSION = f"{VERSION_PREFIX}.{date.today():%Y%m%d}"
