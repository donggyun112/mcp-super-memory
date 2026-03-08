import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
SCOPE = "org:create_api_key user:profile user:inference"
AUTH_URL = "https://claude.ai/oauth/authorize"


@dataclass
class TokenResponse:
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None


@dataclass
class PendingFlow:
    login_id: str
    code_verifier: str


@dataclass
class OAuthManager:
    _pending: dict[str, PendingFlow] = field(default_factory=dict)

    @staticmethod
    def _generate_code_verifier() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def _generate_code_challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def start(self) -> tuple[str, str]:
        """Start OAuth flow. Returns (login_id, auth_url)."""
        login_id = str(uuid.uuid4())
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)
        state = code_verifier

        self._pending[state] = PendingFlow(login_id=login_id, code_verifier=code_verifier)

        qs = urlencode({
            "code": "true",
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        })

        return login_id, f"{AUTH_URL}?{qs}"

    async def complete(self, login_id: str, code: str) -> TokenResponse:
        """Exchange authorization code for tokens."""
        state_key: str | None = None
        code_verifier: str | None = None

        for state, flow in self._pending.items():
            if flow.login_id == login_id:
                state_key = state
                code_verifier = flow.code_verifier
                break

        if not state_key or not code_verifier:
            raise ValueError("Login session not found or expired")

        del self._pending[state_key]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TOKEN_URL,
                json={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "code": code,
                    "state": code_verifier,
                    "redirect_uri": REDIRECT_URI,
                    "code_verifier": code_verifier,
                },
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "claude-cli/1.0",
                },
            )

        data = resp.json()
        if "access_token" not in data:
            raise RuntimeError(data.get("error", f"Token exchange failed: {resp.text}"))

        return TokenResponse(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in"),
        )

    def cancel(self, login_id: str) -> None:
        for state, flow in list(self._pending.items()):
            if flow.login_id == login_id:
                del self._pending[state]
                break
