"""Optional Auth0 access-token verification for approval routes."""
from functools import lru_cache
import os

import jwt
from jwt import PyJWKClient


def _settings():
    domain = os.environ.get("AUTH0_DOMAIN", "").strip().removeprefix("https://").rstrip("/")
    audience = os.environ.get("AUTH0_AUDIENCE", "").strip()
    if not domain or not audience or "/" in domain:
        raise RuntimeError("AUTH0_DOMAIN and AUTH0_AUDIENCE must be configured")
    return domain, audience


@lru_cache(maxsize=4)
def _jwks_client(domain):
    return PyJWKClient(
        f"https://{domain}/.well-known/jwks.json",
        cache_keys=True,
        cache_jwk_set=True,
        lifespan=300,
        timeout=5,
    )


def _decode_access_token(token):
    domain, audience = _settings()
    signing_key = _jwks_client(domain).get_signing_key_from_jwt(token).key
    return jwt.decode(
        token,
        signing_key,
        algorithms=["RS256"],
        audience=audience,
        issuer=f"https://{domain}/",
        options={"require": ["exp", "iss", "aud", "sub"]},
    )


def verify_request(authorization_header, required_permission):
    """Return (subject, error, status) for a bearer access token."""
    try:
        _settings()
    except RuntimeError as error:
        return None, str(error), 503

    parts = authorization_header.split() if isinstance(authorization_header, str) else []
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        return None, "A bearer access token is required", 401

    try:
        claims = _decode_access_token(parts[1])
    except (jwt.PyJWTError, OSError, ValueError):
        return None, "The access token is invalid or expired", 401

    permissions = claims.get("permissions", [])
    scopes = claims.get("scope", "").split() if isinstance(claims.get("scope", ""), str) else []
    if not isinstance(permissions, list):
        permissions = []
    if required_permission not in permissions and required_permission not in scopes:
        return None, f"Missing permission: {required_permission}", 403

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        return None, "The access token has no subject", 401
    return subject, None, 200
