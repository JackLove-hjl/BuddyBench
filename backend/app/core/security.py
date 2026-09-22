"""认证工具:密码哈希(pbkdf2_sha256) + JWT 签发/校验。"""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid

# 生产环境应改为环境变量注入
JWT_SECRET = os.getenv("JWT_SECRET", "llm-webui-dev-secret-change-me")
JWT_EXPIRE_SECONDS = 7 * 24 * 3600  # 7 天

_ALGO = "sha256"
_ITER = 100_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac(_ALGO, password.encode("utf-8"), salt, _ITER)
    return f"pbkdf2${_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_b64, hash_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac(_ALGO, password.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:  # noqa: BLE001
        return False


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_token(user_id: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": user_id, "iat": int(time.time()), "exp": int(time.time()) + JWT_EXPIRE_SECONDS}
    head_b64 = _b64encode(json.dumps(header, separators=(",", ":")).encode())
    body_b64 = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(JWT_SECRET.encode(), f"{head_b64}.{body_b64}".encode(), hashlib.sha256).digest()
    return f"{head_b64}.{body_b64}.{_b64encode(sig)}"


def verify_token(token: str) -> str | None:
    """校验 JWT,返回 user_id;失败返回 None。"""
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        expected = hmac.new(JWT_SECRET.encode(), f"{head_b64}.{body_b64}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(sig_b64)):
            return None
        payload = json.loads(_b64decode(body_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return str(payload.get("sub") or "")
    except Exception:  # noqa: BLE001
        return None


def new_user_id() -> str:
    return str(uuid.uuid4())
