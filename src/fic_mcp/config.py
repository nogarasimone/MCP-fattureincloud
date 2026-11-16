"""Configurazione da variabili d'ambiente (opzionalmente da un file .env)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

ALL_TOOLSETS = (
    "account",      # utente, aziende, piano, quote API
    "entities",     # clienti e fornitori
    "products",     # prodotti e listini
    "issued",       # documenti emessi (fatture, NC, preventivi, proforma, ordini, DDT...)
    "einvoice",     # fattura elettronica / SdI
    "received",     # documenti ricevuti (spese, NC passive, fatture SdI in attesa)
    "receipts",     # corrispettivi
    "taxes",        # F24
    "cashbook",     # prima nota
    "archive",      # archivio documenti
    "info",         # tabelle (aliquote IVA, conti, metodi di pagamento, paesi...)
    "settings",     # impostazioni (conti, metodi, aliquote, profilo fiscale, modelli)
    "webhooks",     # sottoscrizioni webhook
    "analytics",    # analisi CFO calcolate lato server
    "raw",          # chiamata API generica (escape hatch)
)
# Default pensato per un professionista/PMI: niente corrispettivi, archivio, impostazioni, webhook, raw.
DEFAULT_TOOLSETS = ("account", "entities", "products", "issued", "einvoice", "received", "taxes",
                    "cashbook", "info", "analytics")


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "si", "sì", "on")


def _num(env: Mapping[str, str], key: str, default: str, kind: type = float):
    raw = env.get(key, default).strip() or default
    try:
        return kind(raw)
    except ValueError:
        raise ValueError(f"{key} deve essere un numero, trovato {raw!r}") from None


def load_dotenv(path: Path) -> None:
    """Carica un .env minimale (KEY=VALUE) senza sovrascrivere l'ambiente."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "fic-mcp"


def default_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "fic-mcp"


@dataclass
class Settings:
    access_token: str | None = None
    refresh_token: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    company_id: int | None = None
    base_url: str = "https://api-v2.fattureincloud.it"
    read_only: bool = False
    privacy_mode: str = "off"  # off | tokenize
    toolsets: tuple[str, ...] = DEFAULT_TOOLSETS
    set_aside_pct: float = 48.0
    audit_log: Path | None = None
    token_file: Path | None = None
    http_token: str | None = None  # bearer richiesto in modalità HTTP
    allowed_hosts: tuple[str, ...] = ()  # nomi pubblici ammessi nell'header Host (dietro reverse proxy)
    timeout: float = 30.0
    max_retries: int = 4
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def tokenize(self) -> bool:
        return self.privacy_mode == "tokenize"

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "Settings":
        if env_file:
            load_dotenv(Path(env_file))
        else:
            load_dotenv(Path.cwd() / ".env")
            load_dotenv(default_config_dir() / ".env")

        env = os.environ
        toolsets_raw = env.get("FIC_TOOLSETS", "").strip()
        if not toolsets_raw or toolsets_raw == "default":
            toolsets = DEFAULT_TOOLSETS
        elif toolsets_raw == "all":
            toolsets = ALL_TOOLSETS
        else:
            toolsets = tuple(t.strip() for t in toolsets_raw.split(",") if t.strip())
            unknown = [t for t in toolsets if t not in ALL_TOOLSETS]
            if unknown:
                raise ValueError(f"FIC_TOOLSETS contiene valori sconosciuti: {unknown}. Validi: {ALL_TOOLSETS}")

        privacy = env.get("FIC_PRIVACY_MODE", "off").strip().lower()
        if privacy not in ("off", "tokenize"):
            raise ValueError("FIC_PRIVACY_MODE deve essere 'off' oppure 'tokenize'")

        company = env.get("FIC_COMPANY_ID", "").strip()
        audit_default = default_state_dir() / "audit.jsonl"
        audit_raw = env.get("FIC_AUDIT_LOG", "").strip()
        audit = None if audit_raw.lower() in ("off", "none", "0") else Path(audit_raw or audit_default).expanduser()
        token_file = Path(env.get("FIC_TOKEN_FILE", str(default_config_dir() / "tokens.json"))).expanduser()

        return cls(
            access_token=env.get("FIC_ACCESS_TOKEN") or None,
            refresh_token=env.get("FIC_REFRESH_TOKEN") or None,
            client_id=env.get("FIC_CLIENT_ID") or None,
            client_secret=env.get("FIC_CLIENT_SECRET") or None,
            company_id=_num(env, "FIC_COMPANY_ID", company, int) if company else None,
            base_url=env.get("FIC_BASE_URL", "https://api-v2.fattureincloud.it").rstrip("/"),
            read_only=_bool(env.get("FIC_READ_ONLY"), False),
            privacy_mode=privacy,
            toolsets=toolsets,
            set_aside_pct=_num(env, "FIC_SET_ASIDE_PCT", "48"),
            audit_log=audit,
            token_file=token_file,
            http_token=env.get("FIC_MCP_HTTP_TOKEN") or None,
            allowed_hosts=tuple(h.strip() for h in env.get("FIC_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()),
            timeout=_num(env, "FIC_TIMEOUT", "30"),
            max_retries=_num(env, "FIC_MAX_RETRIES", "4", int),
        )
