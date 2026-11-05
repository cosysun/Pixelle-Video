"""Single-account server sessions and secret redaction."""

import hashlib
import hmac
import secrets

SECRET_FIELDS = {
    "api_key",
    "secret_key",
    "access_key",
    "password",
    "password_hash",
    "token",
    "secret_id",
}


def redact(value, mask=False):
    if isinstance(value, dict):
        return {
            key: ("••••••••" if mask and item else "")
            if key.lower() in SECRET_FIELDS or key.lower().endswith("_api_key")
            else redact(item, mask)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, mask) for item in value]
    return value


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return "scrypt$" + salt.hex() + "$" + digest.hex()


def verify_password(password, encoded):
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def safe_error(error):
    """Provider errors may echo URLs/credentials; strip configured secret values."""
    message = str(error)
    import os

    from pixelle_video.config import config_manager

    def secrets_from(value):
        found = []
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in SECRET_FIELDS or key.lower().endswith("_api_key"):
                    if isinstance(item, str) and len(item) > 3:
                        found.append(item)
                else:
                    found.extend(secrets_from(item))
        return found

    sensitive = secrets_from(config_manager.config.to_dict())
    sensitive.extend(
        v
        for k, v in os.environ.items()
        if k in {"COS_SECRET_ID", "COS_SECRET_KEY", "COS_SESSION_TOKEN", "PIXELLE_PASSWORD"}
        and v
    )
    for secret in sorted(sensitive, key=len, reverse=True):
        message = message.replace(secret, "[已隐藏]")
    return message[:2000]
