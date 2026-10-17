"""Local BI experiment configuration, not a production deployment template."""

import os
from urllib.parse import quote_plus


def required_secret(name):
    value = os.environ[name]
    if value == "generate-locally" or len(value) < 32:
        raise ValueError(f"{name} must be generated locally (at least 32 characters)")
    return value


SECRET_KEY = required_secret("SUPERSET_SECRET_KEY")
SQLALCHEMY_DATABASE_URI = (
    "postgresql+psycopg2://superset:"
    + quote_plus(required_secret("POSTGRES_PASSWORD"))
    + "@metadata:5432/superset"
)

# First experiment uses synchronous small queries and no query-result cache.
CACHE_CONFIG = {"CACHE_TYPE": "NullCache"}
DATA_CACHE_CONFIG = {"CACHE_TYPE": "NullCache"}
FEATURE_FLAGS = {"GLOBAL_ASYNC_QUERIES": False}
WTF_CSRF_ENABLED = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# HTTP on the explicitly selected experiment LAN address.
SESSION_COOKIE_SECURE = False
