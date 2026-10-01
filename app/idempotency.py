import asyncio
import hashlib
import time
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# The frontend retries writes that got no response (on mobile networks the
# request often never reaches the API, but sometimes only the response is
# lost). It sends the same Idempotency-Key on every attempt, so a retry of a
# write that already went through gets the original response back instead of
# running twice (e.g. adding the same product to the cart twice).
# The store is in memory: the API runs as a single process on Render, and a
# retry only needs to be recognised for a few minutes.
IDEMPOTENCY_HEADER = "idempotency-key"
IDEMPOTENT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
TTL_SECONDS = 600
MAX_KEY_LENGTH = 128
PENDING_WAIT_SECONDS = 60


@dataclass
class _Entry:
    expires_at: float
    done: asyncio.Event = field(default_factory=asyncio.Event)
    status_code: int | None = None
    headers: list[tuple[bytes, bytes]] | None = None
    body: bytes | None = None


class IdempotencyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._entries: dict[str, _Entry] = {}

    def _prune(self, now: float) -> None:
        expired = [k for k, e in self._entries.items() if e.expires_at < now and e.done.is_set()]
        for k in expired:
            del self._entries[k]

    @staticmethod
    def _cache_key(request: Request, key: str) -> str:
        # Scope the key to the caller and the endpoint, without keeping tokens.
        raw = "|".join(
            [request.headers.get("authorization", ""), request.method, request.url.path, key]
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _replay(entry: _Entry) -> Response:
        response = Response(content=entry.body, status_code=entry.status_code)
        response.raw_headers = list(entry.headers)
        return response

    async def dispatch(self, request: Request, call_next):
        key = request.headers.get(IDEMPOTENCY_HEADER)
        if request.method not in IDEMPOTENT_METHODS or not key or len(key) > MAX_KEY_LENGTH:
            return await call_next(request)

        now = time.monotonic()
        self._prune(now)
        cache_key = self._cache_key(request, key)

        entry = self._entries.get(cache_key)
        if entry is not None:
            if not entry.done.is_set():
                # The first attempt is still running: wait for its result.
                try:
                    await asyncio.wait_for(entry.done.wait(), PENDING_WAIT_SECONDS)
                except asyncio.TimeoutError:
                    return Response(status_code=409, content=b'{"detail":"Request still in progress"}', media_type="application/json")
            if entry.body is not None:
                return self._replay(entry)
            # The first attempt failed without a stored response: run it again.

        entry = _Entry(expires_at=now + TTL_SECONDS)
        self._entries[cache_key] = entry
        try:
            response = await call_next(request)
            body = b"".join([chunk async for chunk in response.body_iterator])
        except Exception:
            self._entries.pop(cache_key, None)
            entry.done.set()
            raise

        if response.status_code < 500:
            entry.status_code = response.status_code
            entry.headers = list(response.raw_headers)
            entry.body = body
        else:
            self._entries.pop(cache_key, None)
        entry.done.set()

        replayed = Response(content=body, status_code=response.status_code)
        replayed.raw_headers = list(response.raw_headers)
        return replayed
