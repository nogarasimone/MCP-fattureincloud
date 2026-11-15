"""CLI: fic-mcp [serve|auth|doctor|tools]."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import sys

from . import __version__
from .auth import TokenStore, run_code_flow
from .config import Settings


def _settings(args: argparse.Namespace) -> Settings:
    return Settings.from_env(getattr(args, "env_file", None))


def cmd_serve(args: argparse.Namespace) -> None:
    from .server import build_server

    settings = _settings(args)
    if args.read_only:
        settings.read_only = True
    server, reg = build_server(settings)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("fic_mcp").info(
        "fic-mcp %s: %d tool attivi, sola lettura=%s, privacy=%s", __version__, len(reg.registered),
        settings.read_only, settings.privacy_mode)

    if args.transport == "stdio":
        server.run("stdio")
        return

    import uvicorn
    from starlette.types import ASGIApp, Receive, Scope, Send

    if not settings.http_token:
        raise SystemExit("In modalità HTTP imposta FIC_MCP_HTTP_TOKEN (bearer richiesto ai client).")
    expected = f"Bearer {settings.http_token}".encode()

    def require_bearer(app: ASGIApp) -> ASGIApp:
        async def guarded(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http":
                headers = dict(scope.get("headers") or [])
                if not hmac.compare_digest(headers.get(b"authorization", b""), expected):
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"www-authenticate", b"Bearer")]})
                    await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                    return
            await app(scope, receive, send)
        return guarded

    app = server.streamable_http_app(host=args.host)
    uvicorn.run(require_bearer(app), host=args.host, port=args.port, log_level="info")


def cmd_auth(args: argparse.Namespace) -> None:
    settings = _settings(args)
    if not (settings.client_id and settings.client_secret):
        raise SystemExit("Servono FIC_CLIENT_ID e FIC_CLIENT_SECRET dell'app registrata su Fatture in Cloud "
                         f"(Redirect URL: http://localhost:{args.port}/callback).")
    tokens = run_code_flow(settings.client_id, settings.client_secret, TokenStore(settings.token_file),
                           port=args.port, read_only=args.read_only, base_url=settings.base_url,
                           open_browser=not args.no_browser)
    print(f"Token salvati in {settings.token_file} (scadenza access token tra 24 ore, rinnovo automatico).")
    del tokens


def cmd_doctor(args: argparse.Namespace) -> None:
    from .client import FicClient, FicError

    settings = _settings(args)

    async def run() -> int:
        client = FicClient(settings)
        try:
            user = await client.request("GET", "/user/info", company=False)
            companies = await client.companies()
            print(json.dumps({
                "utente": ((user or {}).get("data") or {}).get("name"),
                "aziende": [{"id": c.id, "nome": c.name} for c in companies],
                "azienda_configurata": settings.company_id,
                "quote_api": client.quota.as_dict(),
                "sola_lettura": settings.read_only, "privacy": settings.privacy_mode,
                "toolset": list(settings.toolsets),
            }, indent=2, ensure_ascii=False))
            return 0
        except FicError as exc:
            print(f"ERRORE: {exc}", file=sys.stderr)
            return 1
        finally:
            await client.aclose()

    raise SystemExit(asyncio.run(run()))


def cmd_tools(args: argparse.Namespace) -> None:
    from .server import build_server

    settings = _settings(args)
    if args.read_only:
        settings.read_only = True
    _, reg = build_server(settings)
    for t in reg.registered:
        flag = "W!" if t.destructive else ("W " if t.write else "R ")
        print(f"{flag} {t.toolset:<10} {t.name}")
    print(f"\n{len(reg.registered)} tool attivi; {len(reg.skipped)} esclusi da toolset/sola lettura.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fic-mcp", description="Server MCP per Fatture in Cloud")
    parser.add_argument("--version", action="version", version=f"fic-mcp {__version__}")
    parser.add_argument("--env-file", help="File .env da caricare")
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="Avvia il server MCP (default)")
    p_serve.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--read-only", action="store_true", help="Espone solo i tool di lettura")
    p_serve.set_defaults(func=cmd_serve)

    p_auth = sub.add_parser("auth", help="Autorizza l'app via OAuth2 e salva i token")
    p_auth.add_argument("--port", type=int, default=8765)
    p_auth.add_argument("--read-only", action="store_true", help="Richiede solo scope di lettura")
    p_auth.add_argument("--no-browser", action="store_true")
    p_auth.set_defaults(func=cmd_auth)

    p_doc = sub.add_parser("doctor", help="Verifica token, aziende e quote")
    p_doc.set_defaults(func=cmd_doctor)

    p_tools = sub.add_parser("tools", help="Elenca i tool che verrebbero esposti")
    p_tools.add_argument("--read-only", action="store_true")
    p_tools.set_defaults(func=cmd_tools)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):  # nessun sottocomando: serve su stdio
        args.func, args.transport, args.read_only = cmd_serve, "stdio", False
    args.func(args)


if __name__ == "__main__":
    main()
