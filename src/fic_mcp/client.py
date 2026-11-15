"""Client asincrono per l'API v2 di Fatture in Cloud.

- Bearer token con rinnovo automatico via refresh token (OAuth2).
- Rispetto dei limiti: 429 (300 richieste/5 min per azienda) e 403 con Retry-After
  (1.000/ora, 40.000/mese per coppia azienda-app): attesa e nuovo tentativo con backoff.
- I contatori RateLimit-* letti dalle risposte sono esposti senza consumare richieste.
- Le scritture non vengono mai ripetute su errori ambigui (timeout, 5xx): solo su 429,
  dove la richiesta è stata rifiutata prima dell'elaborazione.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx2 as httpx

from . import __version__
from .auth import TokenSet, TokenStore, refresh_tokens
from .config import Settings

log = logging.getLogger("fic_mcp.client")

MAX_PER_PAGE = 100


class FicError(Exception):
    """Errore leggibile restituito al modello."""


class FicApiError(FicError):
    def __init__(self, status: int, message: str, details: Any = None, method: str = "", path: str = ""):
        self.status = status
        self.message = message
        self.details = details
        self.method = method
        self.path = path
        super().__init__(self.render())

    def render(self) -> str:
        hint = {
            400: "Richiesta non valida.",
            401: "Token non valido o scaduto: rigenera il token o rilancia `fic-mcp auth`.",
            403: "Permesso negato (scope dell'app, permessi del sottoutente o quota API esaurita).",
            404: "Risorsa non trovata: verifica l'ID e l'azienda selezionata.",
            409: "Conflitto: la risorsa è in uno stato che non permette l'operazione.",
            422: "Dati non validi: correggi i campi indicati.",
            429: "Troppe richieste: limite di 300 richieste ogni 5 minuti.",
        }.get(self.status, "")
        text = f"Fatture in Cloud ha risposto {self.status} su {self.method} {self.path}: {self.message}"
        if hint:
            text += f" — {hint}"
        if self.details:
            text += f"\nDettagli: {json.dumps(self.details, ensure_ascii=False)[:2000]}"
        return text


@dataclass
class Quota:
    hourly_remaining: int | None = None
    hourly_limit: int | None = None
    monthly_remaining: int | None = None
    monthly_limit: int | None = None
    updated_at: float | None = None

    def update(self, headers: httpx.Headers) -> None:
        def _int(name: str) -> int | None:
            value = headers.get(name)
            try:
                return int(value) if value is not None else None
            except ValueError:
                return None

        found = False
        for attr, header in (("hourly_remaining", "RateLimit-HourlyRemaining"),
                             ("hourly_limit", "RateLimit-HourlyLimit"),
                             ("monthly_remaining", "RateLimit-MonthlyRemaining"),
                             ("monthly_limit", "RateLimit-MonthlyLimit")):
            value = _int(header)
            if value is not None:
                setattr(self, attr, value)
                found = True
        if found:
            self.updated_at = time.time()

    def as_dict(self) -> dict[str, Any]:
        return {
            "orarie_rimanenti": self.hourly_remaining, "limite_orario": self.hourly_limit,
            "mensili_rimanenti": self.monthly_remaining, "limite_mensile": self.monthly_limit,
            "aggiornato_al": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.updated_at))
            if self.updated_at else None,
        }


@dataclass
class Company:
    id: int
    name: str
    type: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class FicClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.quota = Quota()
        self.store = TokenStore(settings.token_file)
        self._tokens = self._initial_tokens()
        self._company_id: int | None = settings.company_id
        self._companies: list[Company] | None = None
        self._refresh_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.timeout,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": f"fic-mcp/{__version__}"},
        )

    # ------------------------------------------------------------------ auth
    def _initial_tokens(self) -> TokenSet | None:
        stored = self.store.load()
        if self.settings.access_token:
            # Il token da ambiente vince; il refresh token può venire da env o dal file.
            refresh = self.settings.refresh_token or (stored.refresh_token if stored else None)
            return TokenSet(self.settings.access_token, refresh, None)
        return stored

    @property
    def can_refresh(self) -> bool:
        return bool(self._tokens and self._tokens.refresh_token
                    and self.settings.client_id and self.settings.client_secret)

    async def _refresh(self) -> None:
        async with self._refresh_lock:
            if not self.can_refresh:
                raise FicError("Token scaduto e rinnovo automatico non configurato "
                               "(servono FIC_CLIENT_ID, FIC_CLIENT_SECRET e un refresh token).")
            assert self._tokens and self._tokens.refresh_token
            self._tokens = await refresh_tokens(self.settings.base_url, self.settings.client_id or "",
                                                self.settings.client_secret or "",
                                                self._tokens.refresh_token, self._http)
            self.store.save(self._tokens)
            log.info("Access token rinnovato")

    async def _auth_header(self) -> dict[str, str]:
        if not self._tokens:
            raise FicError("Nessun token configurato: imposta FIC_ACCESS_TOKEN oppure esegui `fic-mcp auth`.")
        if self._tokens.expired() and self.can_refresh:
            await self._refresh()
        return {"Authorization": f"Bearer {self._tokens.access_token}"}

    # -------------------------------------------------------------- company
    async def companies(self, refresh: bool = False) -> list[Company]:
        if self._companies is None or refresh:
            data = await self.request("GET", "/user/companies", company=False)
            items = ((data or {}).get("data") or {}).get("companies") or []
            self._companies = [Company(int(c["id"]), c.get("name", ""), c.get("type"), c) for c in items]
        return self._companies

    async def company_id(self) -> int:
        if self._company_id:
            return self._company_id
        companies = await self.companies()
        if len(companies) == 1:
            self._company_id = companies[0].id
            return self._company_id
        if not companies:
            raise FicError("Il token non dà accesso a nessuna azienda.")
        listing = ", ".join(f"{c.id} ({c.name})" for c in companies)
        raise FicError(f"Il token gestisce più aziende: {listing}. Usa fic_select_company "
                       "oppure imposta FIC_COMPANY_ID.")

    def select_company(self, company_id: int) -> None:
        self._company_id = company_id

    # -------------------------------------------------------------- request
    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                      json_body: Any = None, files: dict[str, Any] | None = None,
                      data: dict[str, Any] | None = None, company: bool = True,
                      raw: bool = False) -> Any:
        method = method.upper()
        if company:
            path = f"/c/{await self.company_id()}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        idempotent = method in ("GET", "HEAD")
        refreshed = False
        attempt = 0

        while True:
            attempt += 1
            headers = await self._auth_header()
            try:
                resp = await self._http.request(method, path, params=clean_params, json=json_body,
                                                files=files, data=data, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if idempotent and attempt <= self.settings.max_retries:
                    await asyncio.sleep(self._backoff(attempt))
                    continue
                kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "errore di rete"
                extra = (" La scrittura NON è stata ripetuta: verifica in Fatture in Cloud se è andata a buon "
                         "fine prima di riprovare.") if not idempotent else ""
                raise FicError(f"{kind} su {method} {path}: {exc}.{extra}") from exc

            self.quota.update(resp.headers)

            if resp.status_code == 401 and not refreshed and self.can_refresh:
                refreshed = True
                await self._refresh()
                continue

            if resp.status_code in (429, 403) and "Retry-After" in resp.headers:
                wait = self._retry_after(resp)
                if resp.status_code == 429 and attempt <= self.settings.max_retries and wait <= 120:
                    log.warning("429 ricevuto, attendo %.1fs", wait)
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code == 403:
                    raise FicApiError(403, f"quota API esaurita, riprova tra {int(wait)} secondi",
                                      None, method, path)

            if resp.status_code >= 500 and idempotent and attempt <= self.settings.max_retries:
                await asyncio.sleep(self._backoff(attempt))
                continue

            if resp.status_code >= 400:
                raise self._error(resp, method, path)

            if raw:
                return resp
            if resp.status_code == 204 or not resp.content:
                return None
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype:
                return resp.json()
            return {"content_type": ctype, "text": resp.text}

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float:
        try:
            return max(1.0, float(resp.headers.get("Retry-After", "5")))
        except ValueError:
            return 5.0

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(30.0, (2 ** (attempt - 1)) + random.uniform(0, 0.5))

    @staticmethod
    def _error(resp: httpx.Response, method: str, path: str) -> FicApiError:
        message, details = resp.reason_phrase or "errore", None
        try:
            body = resp.json()
            err = body.get("error", body) if isinstance(body, dict) else body
            if isinstance(err, dict):
                message = err.get("message") or message
                details = err.get("validation_result") or err.get("validation_errors") or err.get("extra") or None
                if details is None:
                    rest = {k: v for k, v in err.items() if k not in ("message", "code")}
                    details = rest or None
            elif isinstance(err, str):
                message = err
        except ValueError:
            if resp.text:
                message = resp.text[:500]
        return FicApiError(resp.status_code, message, details, method, path)

    # ----------------------------------------------------------- helpers
    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params)

    async def list_all(self, path: str, params: dict[str, Any] | None = None,
                       max_items: int = 2000) -> list[dict[str, Any]]:
        """Scorre tutte le pagine (per_page=100) fino a max_items."""
        params = dict(params or {})
        params["per_page"] = MAX_PER_PAGE
        page, items = 1, []
        while True:
            params["page"] = page
            body = await self.request("GET", path, params=params) or {}
            chunk = body.get("data") or []
            items.extend(chunk)
            last = body.get("last_page") or 1
            if page >= last or not chunk or len(items) >= max_items:
                break
            page += 1
        return items[:max_items]

    async def aclose(self) -> None:
        await self._http.aclose()
