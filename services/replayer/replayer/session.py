"""Сообщения backend о сим-сессии: ``POST {BACKEND_HTTP}/internal/sim/session``."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("replayer.session")
SESSION_PATH = "/internal/sim/session"
POST_TIMEOUT_S = 3.0


class SessionReporter:
    """Отправляет последнее состояние сессии одним фоновым воркером.

    ``push`` не блокирует: если backend медленный или лежит, промежуточные
    состояния схлопываются до последнего, ошибки только логируются.
    """

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.url = base_url.rstrip("/") + SESSION_PATH
        self._client = client or httpx.AsyncClient(timeout=POST_TIMEOUT_S)
        self._latest: dict[str, Any] | None = None
        self._wake = asyncio.Event()
        self.sent = 0
        self.errors = 0
        self._healthy = True

    def push(self, payload: dict[str, Any]) -> None:
        """Ставит состояние на отправку (заменяя неотправленное)."""
        self._latest = payload
        self._wake.set()

    async def run(self) -> None:
        """Цикл воркера: ждёт ``push`` и шлёт последнее состояние."""
        while True:
            await self._wake.wait()
            self._wake.clear()
            payload, self._latest = self._latest, None
            if payload is not None:
                await self._post(payload)

    async def _post(self, payload: dict[str, Any]) -> None:
        """Один POST; ошибки сети/HTTP — в лог (warning только при первом сбое подряд)."""
        try:
            resp = await self._client.post(self.url, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self.errors += 1
            level = logging.WARNING if self._healthy else logging.DEBUG
            log.log(level, "session POST %s не прошёл: %s", self.url, exc)
            self._healthy = False
            return
        if not self._healthy:
            log.info("session POST снова проходит")
        self._healthy = True
        self.sent += 1

    async def aclose(self) -> None:
        """Закрывает HTTP-клиент."""
        await self._client.aclose()
