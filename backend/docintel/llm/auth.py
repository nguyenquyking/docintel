"""Credentials for the model endpoint.

Databricks AI Gateway issues short-lived bearer tokens. The Databricks SDK resolves
them from, in order of preference here:

* a **service principal** (OAuth M2M: client id + secret) — for containers, where
  nobody can click through a browser login;
* a **profile** in ``~/.databrickscfg`` — e.g. the developer's own CLI login, so
  calls are made under a personal identity that can be revoked individually and
  is attributed correctly in audit logs; no shared secret ever sits on a laptop;
* a **personal access token**.

The SDK caches and refreshes tokens; ``authenticate()`` is cheap after the first call.
"""
from __future__ import annotations

import asyncio
import re

from ..settings import Settings


class AuthError(Exception):
    pass


class Auth:
    async def headers(self) -> dict[str, str]:
        return {}


class StaticKeyAuth(Auth):
    def __init__(self, key: str):
        self._key = key

    async def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"} if self._key else {}


class DatabricksAuth(Auth):
    def __init__(self, settings: Settings):
        self.settings = settings
        self._config = None   # built lazily: an expired login must not break start-up

    def _cfg(self):
        if self._config is None:
            try:
                from databricks.sdk.core import Config
            except ImportError as exc:
                raise AuthError("install the 'databricks' extra: uv sync --extra databricks") from exc
            s = self.settings
            kwargs: dict = {}
            if s.databricks_host:
                kwargs["host"] = s.databricks_host
            if s.databricks_client_id and s.databricks_client_secret:
                kwargs.update(client_id=s.databricks_client_id,
                              client_secret=s.databricks_client_secret.get_secret_value(), auth_type="oauth-m2m")
            elif s.databricks_token:
                kwargs.update(token=s.databricks_token.get_secret_value(), auth_type="pat")
            elif s.databricks_profile:
                kwargs["profile"] = s.databricks_profile
            self._config = Config(**kwargs)
        return self._config

    def base_url(self) -> str:
        host = (self._cfg().host or "").rstrip("/")
        if not host:
            raise AuthError("no Databricks host: set DOCINTEL_DATABRICKS_HOST or a profile with a host")
        path = "/" + self.settings.databricks_gateway_path.strip("/")
        base = host + path
        return base if re.search(r"/v\d+$", base) else base + "/v1"

    async def headers(self) -> dict[str, str]:
        try:
            return await asyncio.to_thread(self._cfg().authenticate)
        except Exception as exc:  # expired CLI login, revoked token, missing profile…
            raise AuthError(f"could not obtain a Databricks token ({type(exc).__name__}: {exc}). "
                            "For a CLI profile run: databricks auth login --profile <profile>") from exc
