"""Firebase Cloud Messaging (HTTP v1) data pushes, signed with a service account."""
import asyncio
import json
from pathlib import Path

import aiohttp
from google.auth.transport.requests import Request
from google.oauth2 import service_account

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class FcmSender:
    def __init__(self, service_account_file: Path) -> None:
        info = json.loads(service_account_file.read_text(encoding="utf-8"))
        self.project_id = info["project_id"]
        self.credentials = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])

    async def _token(self) -> str:
        if not self.credentials.valid:
            await asyncio.to_thread(self.credentials.refresh, Request())
        return self.credentials.token

    async def send(self, device_token: str, data: dict) -> None:
        message = {"message": {
            "token": device_token,
            "data": {k: str(v) for k, v in data.items()},
            # High priority wakes the phone from Doze; a stale ring is useless, so short TTL.
            "android": {"priority": "HIGH", "ttl": "30s"},
        }}
        url = f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=message, timeout=aiohttp.ClientTimeout(total=15),
                                    headers={"Authorization": f"Bearer {await self._token()}"}) as response:
                if response.status != 200:
                    raise RuntimeError(f"FCM {response.status}: {(await response.text())[:300]}")
