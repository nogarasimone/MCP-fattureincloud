"""Tokenizzazione reversibile dei dati personali (pseudonimizzazione, art. 4(5) GDPR).

Con FIC_PRIVACY_MODE=tokenize il modello non vede nomi, P.IVA, codici fiscali, email,
PEC, telefoni, indirizzi e IBAN: riceve segnaposto stabili come [[NOME_3]] o
[[PIVA_1]]. Quando il modello usa un segnaposto negli argomenti di un tool, il server
lo sostituisce con il valore reale prima di chiamare Fatture in Cloud.

Limiti da dichiarare al cliente:
- importi, date, numeri documento e descrizioni delle righe restano in chiaro;
- nei testi liberi si riconoscono solo pattern (email, IBAN, CF, P.IVA, telefoni):
  un nome di persona scritto a mano in una descrizione non viene intercettato;
- la tabella di corrispondenza vive solo in memoria nel processo del server.
"""

from __future__ import annotations

import re
import threading
from typing import Any

TOKEN_RE = re.compile(r"\[\[([A-Z]+)_(\d+)\]\]")

# Campi tokenizzati ovunque compaiano.
ALWAYS_FIELDS: dict[str, str] = {
    "vat_number": "PIVA",
    "tax_code": "CF",
    "email": "EMAIL",
    "certified_email": "PEC",
    "recipient_email": "EMAIL",
    "sender_email": "EMAIL",
    "phone": "TEL",
    "fax": "TEL",
    "address_street": "IND",
    "address_extra": "IND",
    "shipping_address": "IND",
    "bank_iban": "IBAN",
    "iban": "IBAN",
    "bank_beneficiary": "NOME",
    "entity_name": "NOME",
    "contact_person": "NOME",
    "first_name": "NOME",
    "last_name": "NOME",
    "cliente": "NOME",      # output dei tool di analisi
    "fornitore": "NOME",
    "default_payment_terms_name": None,  # esplicitamente non sensibile
}

# Campi tokenizzati solo dentro un oggetto anagrafica (cliente/fornitore/entity).
ENTITY_FIELDS: dict[str, str] = {"name": "NOME", "referent": "NOME", "notes": "TESTO"}

ENTITY_KEYS = {"entity", "client", "supplier", "clients", "suppliers"}

FREE_TEXT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("IBAN", re.compile(r"\bIT\d{2}[A-Z]\d{10}[0-9A-Z]{12}\b", re.IGNORECASE)),
    ("CF", re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPR-T]\d{2}[A-Z]\d{3}[A-Z]\b", re.IGNORECASE)),
    ("PIVA", re.compile(r"(?<![\d/.-])(?:IT)?\d{11}(?![\d/.-])")),
    ("TEL", re.compile(r"(?<!\d)(?:\+39[ .]?)?3\d{2}[ .]?\d{6,7}(?!\d)")),
]

# Chiavi i cui valori non vanno mai toccati (identificativi tecnici, importi, date).
SKIP_KEYS = {"id", "type", "date", "number", "numeration", "currency", "language", "status",
             "ei_code", "code", "url", "attachment_url", "attachment_token", "created_at", "updated_at"}


class Vault:
    """Mappa bidirezionale valore reale <-> segnaposto, thread-safe."""

    def __init__(self) -> None:
        self._forward: dict[tuple[str, str], str] = {}
        self._reverse: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self._lock = threading.Lock()

    def token_for(self, kind: str, value: str) -> str:
        norm = value.strip()
        key = (kind, norm.casefold())
        with self._lock:
            token = self._forward.get(key)
            if token is None:
                self._counters[kind] = self._counters.get(kind, 0) + 1
                token = f"[[{kind}_{self._counters[kind]}]]"
                self._forward[key] = token
                self._reverse[token] = norm
            return token

    def reveal(self, token: str) -> str | None:
        return self._reverse.get(token)

    def stats(self) -> dict[str, int]:
        return dict(self._counters)

    def __len__(self) -> int:
        return len(self._reverse)


class Tokenizer:
    def __init__(self, vault: Vault | None = None, enabled: bool = True):
        self.vault = vault or Vault()
        self.enabled = enabled

    # ------------------------------------------------------------ outbound
    def mask(self, value: Any, entity_root: bool = False) -> Any:
        """Maschera una risposta. entity_root=True per risposte di clienti/fornitori."""
        if not self.enabled:
            return value
        return self._walk(value, entity_root)

    def _walk(self, value: Any, entity_ctx: bool) -> Any:
        if isinstance(value, dict):
            entity_like = entity_ctx or "vat_number" in value or "tax_code" in value
            out = {}
            for k, v in value.items():
                if isinstance(v, (dict, list)):
                    # "data" e liste ereditano il contesto (es. elenco clienti);
                    # gli oggetti annidati diventano anagrafica solo sotto chiavi entity.
                    child_ctx = k in ENTITY_KEYS or (entity_ctx and k == "data")
                    out[k] = self._walk(v, child_ctx)
                else:
                    out[k] = self._field(k, v, entity_like)
            return out
        if isinstance(value, list):
            return [self._walk(v, entity_ctx) for v in value]
        if isinstance(value, str):
            return self.mask_text(value)
        return value

    def _field(self, key: str, value: Any, entity_like: bool) -> Any:
        if not isinstance(value, str) or not value.strip():
            return value
        if key in SKIP_KEYS:
            return value
        if TOKEN_RE.fullmatch(value.strip()):
            return value
        kind = ALWAYS_FIELDS.get(key, "")
        if kind is None:
            return value
        if not kind and entity_like:
            kind = ENTITY_FIELDS.get(key, "")
        if kind == "TESTO":
            return self.mask_text(value)
        if kind:
            return self.vault.token_for(kind, value)
        return self.mask_text(value)

    def mask_text(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        out = text
        for kind, pattern in FREE_TEXT_PATTERNS:
            out = pattern.sub(lambda m, k=kind: self.vault.token_for(k, m.group(0)), out)
        return out

    # ------------------------------------------------------------- inbound
    def unmask(self, value: Any) -> Any:
        """Sostituisce i segnaposto con i valori reali negli argomenti dei tool."""
        if isinstance(value, dict):
            return {k: self.unmask(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.unmask(v) for v in value]
        if isinstance(value, str) and "[[" in value:
            def repl(match: re.Match[str]) -> str:
                real = self.vault.reveal(match.group(0))
                if real is None:
                    raise UnknownTokenError(match.group(0))
                return real
            return TOKEN_RE.sub(repl, value)
        return value


class UnknownTokenError(ValueError):
    def __init__(self, token: str):
        super().__init__(f"Segnaposto sconosciuto {token}: non appartiene a questa sessione. "
                         "Rileggi il dato con un tool di lettura per ottenere il segnaposto corretto.")
