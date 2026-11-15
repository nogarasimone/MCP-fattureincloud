"""Gestione token OAuth2 di Fatture in Cloud.

Due modalità:
- token manuale (app privata, "Applicazioni collegate" in FIC): FIC_ACCESS_TOKEN;
- OAuth2 Authorization Code (app pubblica o SaaS): `fic-mcp auth` apre il browser,
  riceve il codice su http://localhost:<porta>/callback e salva access/refresh token
  in un file con permessi 600. L'access token dura 24 h e viene rinnovato da solo.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx2 as httpx

AUTHORIZE_URL = "https://api-v2.fattureincloud.it/oauth/authorize"
TOKEN_PATH = "/oauth/token"

# Scope completi (lettura+scrittura). `fic-mcp auth --read-only` usa le varianti :r.
SCOPE_AREAS = [
    "entity.clients", "entity.suppliers", "products", "stock",
    "issued_documents.invoices", "issued_documents.credit_notes", "issued_documents.quotes",
    "issued_documents.proformas", "issued_documents.receipts", "issued_documents.delivery_notes",
    "issued_documents.orders", "issued_documents.work_reports", "issued_documents.supplier_orders",
    "issued_documents.self_invoices", "received_documents", "receipts", "calendar", "archive",
    "taxes", "cashbook", "settings",
]
READ_ONLY_AREAS = ["situation", "emails"]


def build_scopes(read_only: bool = False) -> str:
    level = "r" if read_only else "a"
    scopes = [f"{area}:{level}" for area in SCOPE_AREAS]
    scopes += [f"{area}:r" for area in READ_ONLY_AREAS]
    return " ".join(scopes)


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None  # epoch seconds

    def expired(self, margin: float = 300) -> bool:
        return self.expires_at is not None and time.time() > self.expires_at - margin

    @classmethod
    def from_response(cls, data: dict) -> "TokenSet":
        expires_in = data.get("expires_in")
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=time.time() + float(expires_in) if expires_in else None,
        )


class TokenStore:
    """Salva i token su file JSON con permessi 0600."""

    def __init__(self, path: Path | None):
        self.path = path

    def load(self) -> TokenSet | None:
        if not self.path or not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return TokenSet(data["access_token"], data.get("refresh_token"), data.get("expires_at"))
        except (OSError, ValueError, KeyError):
            return None

    def save(self, tokens: TokenSet) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = json.dumps(
            {"access_token": tokens.access_token, "refresh_token": tokens.refresh_token,
             "expires_at": tokens.expires_at},
            indent=2,
        )
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, self.path)


async def refresh_tokens(base_url: str, client_id: str, client_secret: str, refresh_token: str,
                         http: httpx.AsyncClient | None = None) -> TokenSet:
    body = {"grant_type": "refresh_token", "client_id": client_id,
            "client_secret": client_secret, "refresh_token": refresh_token}
    own = http is None
    http = http or httpx.AsyncClient(timeout=30)
    try:
        resp = await http.post(f"{base_url}{TOKEN_PATH}", json=body)
        resp.raise_for_status()
        tokens = TokenSet.from_response(resp.json())
        if not tokens.refresh_token:
            tokens.refresh_token = refresh_token
        return tokens
    finally:
        if own:
            await http.aclose()


def run_code_flow(client_id: str, client_secret: str, store: TokenStore, *, port: int = 8765,
                  read_only: bool = False, base_url: str = "https://api-v2.fattureincloud.it",
                  open_browser: bool = True) -> TokenSet:
    """Esegue il flusso Authorization Code con callback locale. Bloccante (CLI)."""
    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(24)
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            result.update(qs)
            ok = qs.get("state") == state and "code" in qs
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = ("Autorizzazione completata. Puoi chiudere questa finestra."
                   if ok else "Autorizzazione non valida (state o code mancanti).")
            self.wfile.write(f"<html><body><h3>{msg}</h3></body></html>".encode())
            done.set()

        def log_message(self, *args):  # silenzia il log su stderr
            return

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
              "scope": build_scopes(read_only), "state": state}
    url = f"{AUTHORIZE_URL}?{urlencode(params)}"
    print("Apri questo URL per autorizzare l'accesso a Fatture in Cloud:\n" + url, flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        if not done.wait(timeout=300):
            raise TimeoutError("Nessuna risposta dal browser entro 5 minuti.")
    finally:
        server.shutdown()

    if result.get("state") != state:
        raise RuntimeError("Parametro state non corrispondente: possibile CSRF, operazione annullata.")
    if "code" not in result:
        raise RuntimeError(f"Autorizzazione negata: {result.get('error', 'codice assente')}")

    body = {"grant_type": "authorization_code", "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": redirect_uri, "code": result["code"]}
    with httpx.Client(timeout=30) as http:
        resp = http.post(f"{base_url}{TOKEN_PATH}", json=body)
        resp.raise_for_status()
        tokens = TokenSet.from_response(resp.json())
    store.save(tokens)
    return tokens
