import hashlib
import hmac
import os
import secrets
import time

DEFAULT_OTP_TTL = 600


def generate_otp(length: int = 6) -> str:
    upper_bound = 10 ** length
    return f"{secrets.randbelow(upper_bound):0{length}d}"


def _otp_secret() -> bytes:
    secret = os.getenv("OTP_SECRET") or os.getenv("SECRET_KEY") or "ppu-local-otp-secret"
    return secret.encode("utf-8")


def hash_otp(code: str, salt: str | None = None) -> dict[str, str]:
    otp_salt = salt or secrets.token_hex(16)
    digest = hmac.new(
        _otp_secret(),
        f"{otp_salt}:{code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"otp_salt": otp_salt, "otp_hash": digest}


def make_otp_fields(code: str, ttl: int = DEFAULT_OTP_TTL) -> dict:
    return {
        **hash_otp(code),
        "created_at": time.time(),
        "expires_at": time.time() + ttl,
        "attempts": 0,
    }


def verify_otp(record: dict, code: str) -> bool:
    if record.get("otp_hash") and record.get("otp_salt"):
        expected = hash_otp(code, record["otp_salt"])["otp_hash"]
        return hmac.compare_digest(expected, record["otp_hash"])
    legacy_code = str(record.get("otp", ""))
    return bool(legacy_code) and hmac.compare_digest(legacy_code, code)


def otp_expired(ts: float, ttl: int = 300) -> bool:
    if isinstance(ts, dict):
        expires_at = ts.get("expires_at")
        if expires_at:
            return time.time() > float(expires_at)
        ts = ts.get("ts", 0)
    return time.time() - ts > ttl
