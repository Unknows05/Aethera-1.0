"""
Relay Proxy — VPS relay for Binance API requests.
Forwards authenticated requests through a static-IP VPS endpoint.

WARNING: This client forwards API keys in plaintext to the relay server.
The relay server must be fully trusted — it receives your Binance API key
and secret in the request body. Only use a relay server you control.
API keys are stripped from debug logging to avoid accidental exposure.
"""
import os
import logging
from typing import Optional

import requests
import httpx

logger = logging.getLogger(__name__)

RELAY_URL = os.getenv("RELAY_URL", "https://relay.aethera.io")


class RelayClient:
    def __init__(self, base_url: str = None):
        self._relay_url = (base_url or RELAY_URL).rstrip("/")
        self._enabled = bool(self._relay_url)
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    @property
    def enabled(self) -> bool:
        return self._enabled

    def forward(self, method: str, path: str, params: dict = None,
                api_key: str = "", sign_data: dict = None) -> Optional[dict]:
        if not self._enabled:
            return None

        payload = {
            "method": method.upper(),
            "path": path,
            "params": params or {},
            "api_key": api_key,
        }
        if sign_data:
            payload["sign_data"] = sign_data

        try:
            r = self._session.post(
                f"{self._relay_url}/relay",
                json=payload,
                timeout=15,
            )
            if r.status_code == 200:
                return r.json()
            logger.debug(f"[Relay] Forward failed: {r.status_code}")
        except Exception as e:
            logger.debug(f"[Relay] Request error: {e}")

        return None

    def forward_signed(self, method: str, path: str, params: dict,
                       api_key: str, api_secret: str) -> Optional[dict]:
        import hmac
        import hashlib
        import time
        from urllib.parse import urlencode

        params = params or {}
        params["timestamp"] = int(time.time() * 1000)
        query_string = urlencode(sorted(params.items()))
        signature = hmac.new(
            api_secret.encode(), query_string.encode(), hashlib.sha256
        ).hexdigest()

        return self.forward(
            method=method,
            path=path,
            params=params,
            api_key=api_key,
            sign_data={"timestamp": params["timestamp"], "signature": signature},
        )

    async def forward_async(self, method: str, path: str, params: dict = None,
                             api_key: str = "") -> Optional[dict]:
        if not self._enabled:
            return None

        payload = {
            "method": method.upper(),
            "path": path,
            "params": params or {},
            "api_key": api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"{self._relay_url}/relay",
                    json=payload,
                )
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            logger.debug(f"[Relay] Async forward error: {e}")

        return None

    def direct_request(self, method: str, base_url: str, path: str,
                       params: dict = None, headers: dict = None) -> Optional[dict]:
        url = f"{base_url.rstrip('/')}{path}"
        try:
            if method.upper() == "GET":
                r = requests.get(url, params=params, headers=headers, timeout=10)
            elif method.upper() == "POST":
                r = requests.post(url, json=params, headers=headers, timeout=10)
            else:
                r = requests.request(method, url, json=params, headers=headers, timeout=10)

            if r.status_code == 200:
                return r.json()
            logger.debug(f"[Relay] Direct request failed: {r.status_code}")
        except Exception as e:
            logger.debug(f"[Relay] Direct request error: {e}")

        return None

    def request_with_fallback(self, method: str, base_url: str, path: str,
                              params: dict = None, api_key: str = "",
                              headers: dict = None) -> Optional[dict]:
        result = self.forward(method, path, params, api_key)
        if result is not None:
            return result
        return self.direct_request(method, base_url, path, params, headers)

    def health_check(self) -> bool:
        try:
            r = self._session.get(f"{self._relay_url}/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False


_relay: Optional[RelayClient] = None


def get_relay() -> RelayClient:
    global _relay
    if _relay is None:
        _relay = RelayClient()
    return _relay
