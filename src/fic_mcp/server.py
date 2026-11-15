"""Costruzione del server MCP."""

from __future__ import annotations

import httpx2 as httpx
from mcp.server.mcpserver import MCPServer

from . import __version__
from .audit import AuditLog
from .client import FicClient
from .config import Settings
from .privacy import Tokenizer
from .registry import Registrar, Runtime
from .tools import register_all

INSTRUCTIONS = """Server MCP per Fatture in Cloud (API v2) dell'azienda selezionata.

Regole d'uso:
- Inizia con fic_whoami se non sai quale azienda è selezionata.
- Per gli ID (aliquote IVA, conti, metodi di pagamento, categorie spese) usa fic_info.
- Per creare una fattura usa fic_create_invoice: prima con dry_run=true, mostra l'anteprima
  all'utente, poi crea con dry_run=false solo dopo conferma esplicita.
- Le operazioni irreversibili o verso terzi (invio SdI, email al cliente, eliminazioni) richiedono
  confirm=true: chiedi sempre il via libera all'utente prima di passarlo.
- Per totali, incassi, crediti, spese e IVA usa i tool fic_*_summary / fic_receivables /
  fic_vat_estimate invece di sommare a mano lunghe liste.
- I valori tra doppie parentesi quadre (es. [[NOME_2]], [[PIVA_1]]) sono segnaposto di dati
  personali: riusali tali e quali negli argomenti, non chiedere né inventare il valore reale.
- Le cifre analitiche sono stime gestionali: le decisioni fiscali le valida il commercialista.
"""


def build_server(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> tuple[MCPServer, Registrar]:
    client = FicClient(settings, transport=transport)
    rt = Runtime(settings=settings, client=client, tokenizer=Tokenizer(enabled=settings.tokenize),
                 audit=AuditLog(settings.audit_log))

    server = MCPServer(
        name="fatture-in-cloud",
        title="Fatture in Cloud",
        description="Fatturazione, spese, FE/SdI, F24, prima nota e analisi CFO su Fatture in Cloud.",
        instructions=INSTRUCTIONS,
        version=__version__,
    )
    reg = Registrar(server, rt)
    register_all(reg)
    return server, reg
