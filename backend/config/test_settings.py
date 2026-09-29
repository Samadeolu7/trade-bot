import os

os.environ.setdefault("DJANGO_DEBUG", "1")
os.environ.pop("DATABASE_URL", None)
os.environ.pop("REDIS_URL", None)

from config.settings import *  # noqa: E402,F401,F403

REQUIRE_2FA = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
