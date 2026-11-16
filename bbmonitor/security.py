"""Small signed-session helper for a single dashboard administrator."""
import base64
import hashlib
import hmac
import os
import time

COOKIE = 'bbs_session'
TTL_SECONDS = 7 * 24 * 3600


def password_valid(candidate):
    expected = os.environ.get('ADMIN_PASSWORD', '')
    if len(expected) < 12:
        return False
    return hmac.compare_digest(candidate.encode(), expected.encode())


def _secret():
    secret = os.environ.get('SESSION_SECRET', '')
    if len(secret) < 32:
        raise RuntimeError('SESSION_SECRET minimal 32 karakter belum dikonfigurasi.')
    return secret.encode()


def sign_session(now=None):
    expires = int(now or time.time()) + TTL_SECONDS
    payload = str(expires)
    signature = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f'{payload}.{signature}'.encode()).decode()


def session_valid(token, now=None):
    if not token or len(token) > 200:
        return False
    try:
        content = base64.urlsafe_b64decode(token).decode()
        value, signature = content.split('.', 1)
        if not value.isdigit() or int(value) < int(now or time.time()):
            return False
        correct = hmac.new(_secret(), value.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, correct)
    except (ValueError, UnicodeDecodeError, RuntimeError):
        return False
