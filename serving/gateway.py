"""API Gateway middleware — authentication, rate limiting, RBAC.

Provides a FastAPI middleware that intercepts every request and enforces:
- **Authentication**: API-key header or JWT Bearer token validation.
- **Rate limiting**: Per-key sliding-window counter (in-memory for now,
  Redis-ready interface for production).
- **RBAC**: Route → role mapping loaded from ``confs/gateway.yaml``.
- **Cost attribution**: Extracts ``X-App-ID`` / ``X-User-ID`` headers and
  attaches them to ``request.state`` for downstream cost tracking.

The middleware is attached to the FastAPI app in ``server.py`` when the
gateway config has ``authentication.enabled: true``.

Design notes:
    The in-memory rate-limiter resets on restart — acceptable for Cloud Run
    where each instance handles its own traffic.  For strict global limits,
    swap ``InMemoryRateLimiter`` for a Redis-backed implementation.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

_DEFAULT_GATEWAY_CONFIG: dict[str, Any] = {
    "authentication": {"enabled": False, "methods": [], "api_keys": []},
    "rate_limiting": {
        "enabled": False,
        "default": {"requests_per_minute": 60, "requests_per_day": 10000},
        "tiers": {},
    },
    "routes": {},
    "cost_attribution": {
        "app_id_header": "X-App-ID",
        "user_id_header": "X-User-ID",
    },
}


def load_gateway_config(path: str = "confs/gateway.yaml") -> dict[str, Any]:
    """Load gateway configuration from YAML.

    Falls back to permissive defaults when the file is missing.
    """
    try:
        import yaml

        p = Path(path)
        if p.exists():
            with p.open() as f:
                cfg = yaml.safe_load(f) or {}
            logger.info("Gateway config loaded from %s", path)
            # Merge with defaults so missing keys don't crash
            merged = {**_DEFAULT_GATEWAY_CONFIG}
            for key in _DEFAULT_GATEWAY_CONFIG:
                if key in cfg:
                    if isinstance(_DEFAULT_GATEWAY_CONFIG[key], dict):
                        merged[key] = {**_DEFAULT_GATEWAY_CONFIG[key], **cfg[key]}
                    else:
                        merged[key] = cfg[key]
            return merged
    except Exception as exc:
        logger.warning("Failed to load gateway config from %s: %s", path, exc)
    return dict(_DEFAULT_GATEWAY_CONFIG)


# ---------------------------------------------------------------------------
# In-memory sliding-window rate limiter
# ---------------------------------------------------------------------------


class InMemoryRateLimiter:
    """Simple in-memory sliding-window rate limiter.

    Tracks request timestamps per API key.  Resets on process restart
    (acceptable for Cloud Run single-instance scaling).
    """

    def __init__(self, rpm: int = 60, rpd: int = 10_000) -> None:
        self.rpm = rpm
        self.rpd = rpd
        self._minute_buckets: dict[str, list[float]] = defaultdict(list)
        self._day_buckets: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> tuple[bool, str]:
        """Check if *key* may proceed.  Returns ``(allowed, reason)``."""
        now = time.time()

        # Purge expired entries
        minute_ago = now - 60
        day_ago = now - 86_400

        self._minute_buckets[key] = [t for t in self._minute_buckets[key] if t > minute_ago]
        self._day_buckets[key] = [t for t in self._day_buckets[key] if t > day_ago]

        if len(self._minute_buckets[key]) >= self.rpm:
            return False, "Rate limit exceeded: too many requests per minute"
        if len(self._day_buckets[key]) >= self.rpd:
            return False, "Rate limit exceeded: too many requests per day"

        self._minute_buckets[key].append(now)
        self._day_buckets[key].append(now)
        return True, "ok"


# ---------------------------------------------------------------------------
# JWT helper (lightweight — delegates real verification to google-auth)
# ---------------------------------------------------------------------------


def _verify_jwt(token: str, issuers: list[str], audiences: list[str]) -> dict[str, Any] | None:
    """Verify a JWT Bearer token using google-auth.

    Returns decoded claims dict or ``None`` on failure.
    """
    try:
        from google.auth.transport import requests as gauth_requests
        from google.oauth2 import id_token

        claims = id_token.verify_oauth2_token(
            token,
            gauth_requests.Request(),
            audience=audiences[0] if audiences else None,
        )
        if issuers and claims.get("iss") not in issuers:
            return None
        return claims
    except Exception as exc:
        logger.debug("JWT verification failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Gateway middleware
# ---------------------------------------------------------------------------


class GatewayMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware implementing auth, rate limiting, RBAC, and cost tags.

    Attach to any FastAPI app::

        cfg = load_gateway_config()
        app.add_middleware(GatewayMiddleware, config=cfg)
    """

    def __init__(self, app: FastAPI, config: dict[str, Any] | None = None) -> None:
        super().__init__(app)
        self.config = config or load_gateway_config()
        self.auth_cfg = self.config.get("authentication", {})
        self.rl_cfg = self.config.get("rate_limiting", {})
        self.routes_cfg = self.config.get("routes", {})
        self.cost_cfg = self.config.get("cost_attribution", {})

        # Build API key set (hash for constant-time comparison)
        raw_keys: list[str] = self.auth_cfg.get("api_keys", []) or []
        self._api_key_hashes: set[str] = {hashlib.sha256(k.encode()).hexdigest() for k in raw_keys}
        self._api_key_header: str = "X-API-Key"
        for m in self.auth_cfg.get("methods", []):
            if m.get("type") == "api_key":
                self._api_key_header = m.get("header", "X-API-Key")
                break

        # JWT config
        self._jwt_issuers: list[str] = []
        self._jwt_audiences: list[str] = []
        for m in self.auth_cfg.get("methods", []):
            if m.get("type") == "jwt":
                self._jwt_issuers.append(m.get("issuer", ""))
                self._jwt_audiences = m.get("audiences", [])

        # Rate limiter
        rl_default = self.rl_cfg.get("default", {})
        self._limiter = InMemoryRateLimiter(
            rpm=rl_default.get("requests_per_minute", 60),
            rpd=rl_default.get("requests_per_day", 10_000),
        )

        logger.info(
            "Gateway middleware initialised (auth=%s, rate_limit=%s)",
            self.auth_cfg.get("enabled", False),
            self.rl_cfg.get("enabled", False),
        )

    # --- helpers ---

    def _route_config(self, path: str) -> dict[str, Any]:
        """Return route-specific config or empty dict."""
        return self.routes_cfg.get(path, {})

    def _authenticate(self, request: Request) -> tuple[bool, str, dict[str, Any]]:
        """Authenticate the request.

        Returns ``(success, identity_key, claims_dict)``.
        """
        # API key check
        api_key = request.headers.get(self._api_key_header, "")
        if api_key:
            key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            if key_hash in self._api_key_hashes:
                return True, f"apikey:{key_hash[:8]}", {"role": "user"}
            return False, "", {}

        # JWT Bearer check
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            claims = _verify_jwt(token, self._jwt_issuers, self._jwt_audiences)
            if claims is not None:
                sub = claims.get("sub", claims.get("email", "unknown"))
                role = claims.get("role", "user")
                return True, f"jwt:{sub}", {"role": role, **claims}
            return False, "", {}

        return False, "", {}

    # --- dispatch ---

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Execute the gateway checks before forwarding the request."""
        path = request.url.path
        route_cfg = self._route_config(path)

        # --- Cost attribution (always) ---
        app_id_header = self.cost_cfg.get("app_id_header", "X-App-ID")
        user_id_header = self.cost_cfg.get("user_id_header", "X-User-ID")
        request.state.app_id = request.headers.get(app_id_header, "default")
        request.state.user_id = request.headers.get(user_id_header, "anonymous")

        # --- Authentication ---
        if self.auth_cfg.get("enabled", False):
            auth_requirement = route_cfg.get("auth", "required")
            if auth_requirement == "none":
                request.state.identity = "anonymous"
                request.state.claims = {}
            else:
                success, identity, claims = self._authenticate(request)
                if not success:
                    if auth_requirement == "required":
                        return JSONResponse(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            content={"error": "Authentication required"},
                        )
                    # optional auth — proceed unauthenticated
                    identity = "anonymous"
                    claims = {}
                request.state.identity = identity
                request.state.claims = claims

                # RBAC
                allowed_roles = route_cfg.get("roles", [])
                if allowed_roles and claims.get("role") not in allowed_roles:
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={"error": "Insufficient permissions"},
                    )
        else:
            request.state.identity = "anonymous"
            request.state.claims = {}

        # --- Rate limiting ---
        if self.rl_cfg.get("enabled", False):
            rl_key = getattr(request.state, "identity", "anonymous")
            allowed, reason = self._limiter.is_allowed(rl_key)
            if not allowed:
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"error": reason},
                )

        # --- Forward ---
        response = await call_next(request)
        return response


# ---------------------------------------------------------------------------
# Helper: attach middleware to an existing app
# ---------------------------------------------------------------------------


def attach_gateway(app: FastAPI, config_path: str = "confs/gateway.yaml") -> None:
    """Load gateway config and attach the middleware to *app*.

    Called by ``server.py`` during app creation.
    """
    cfg = load_gateway_config(config_path)
    app.add_middleware(GatewayMiddleware, config=cfg)
    logger.info("Gateway middleware attached to app")
