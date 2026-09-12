"""Auth0 access-token verification for the agent HTTP boundary."""

import os
from urllib.parse import urlparse

import jwt


class Unauthorized(Exception):
    pass


class Forbidden(Exception):
    pass


class Auth0Verifier:
    def __init__(self):
        domain = os.getenv("AUTH0_DOMAIN", "").strip()
        self.audience = os.getenv("AUTH0_AUDIENCE", "").strip()
        if not domain or not self.audience or urlparse("https://" + domain).netloc != domain or "/" in domain:
            raise ValueError("Configure AUTH0_DOMAIN as a hostname and AUTH0_AUDIENCE as the API identifier")
        self.issuer = "https://" + domain + "/"
        self.keys = jwt.PyJWKClient(self.issuer + ".well-known/jwks.json", timeout=10)

    def verify(self, authorization, permission):
        if not authorization.startswith("Bearer "):
            raise Unauthorized("A Bearer access token is required")
        token = authorization[7:]
        try:
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.audience,
                                issuer=self.issuer, options={"require": ["exp", "iss", "aud", "sub"]})
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                raise ValueError("Invalid subject")
        except Exception:
            raise Unauthorized("Access token could not be verified") from None
        permissions = claims.get("permissions", [])
        scope = claims.get("scope", "")
        if not isinstance(permissions, list) or not isinstance(scope, str):
            raise Forbidden("Invalid token permissions")
        if permission not in permissions and permission not in scope.split():
            raise Forbidden("Required permission: " + permission)
        return claims["sub"]
