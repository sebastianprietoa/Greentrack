import hashlib
import os

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


def hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password, hashed):
    if hashed and hashed.startswith(("$2b$", "$2a$")):
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    return hashlib.sha256(password.encode("utf-8")).hexdigest() == hashed


def _api_token_serializer():
    secret = os.getenv("SECRET_KEY", "clave_segura_provisional")
    return URLSafeTimedSerializer(secret_key=secret, salt="greentrack-api-token")


def create_api_token(user_id, empresa, es_admin, email, expires_in=86400):
    payload = {
        "user_id": int(user_id),
        "empresa": empresa,
        "es_admin": int(es_admin or 0),
        "email": email,
    }
    token = _api_token_serializer().dumps(payload)
    return token, expires_in


def verify_api_token(token, max_age=86400):
    try:
        return _api_token_serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
