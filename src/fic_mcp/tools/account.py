
from typing import Annotated, Any

from pydantic import Field

from ..registry import Registrar


def register(reg: Registrar) -> None:
    rt = reg.rt
    c = rt.client

    @reg.tool(toolset="account", title="Utente e aziende")
    async def fic_whoami() -> dict[str, Any]:
        """Mostra l'utente collegato, le aziende accessibili con il token e quella selezionata.
        Usalo all'inizio per capire su quale azienda stai lavorando."""
        user = await c.request("GET", "/user/info", company=False)
        companies = await c.companies(refresh=True)
        try:
            selected = await c.company_id()
        except Exception:  # più aziende e nessuna scelta
            selected = None
        return {
            "utente": (user or {}).get("data"),
            "aziende": [{"id": x.id, "nome": x.name, "tipo": x.type} for x in companies],
            "azienda_selezionata": selected,
        }

    @reg.tool(toolset="account", title="Seleziona azienda")
    async def fic_select_company(
        company_id: Annotated[int, Field(description="ID azienda da fic_whoami.")],
    ) -> dict[str, Any]:
        """Seleziona l'azienda su cui lavorare quando il token ne gestisce più di una."""
        companies = await c.companies()
        match = next((x for x in companies if x.id == company_id), None)
        if not match:
            raise ValueError(f"Azienda {company_id} non accessibile con questo token.")
        c.select_company(company_id)
        return {"azienda_selezionata": {"id": match.id, "nome": match.name}}

    @reg.tool(toolset="account", title="Dati azienda")
    async def fic_company_info() -> dict[str, Any]:
        """Dati dell'azienda selezionata: ragione sociale, P.IVA, piano, impostazioni, permessi dell'app."""
        return await c.get("/company/info")

    @reg.tool(toolset="account", title="Utilizzo del piano")
    async def fic_plan_usage(
        category: Annotated[str | None, Field(description="Categoria di utilizzo, se richiesta dall'API (es. 'documents').")] = None,
    ) -> dict[str, Any]:
        """Utilizzo del piano Fatture in Cloud (limiti di documenti/anagrafiche del piano sottoscritto)."""
        return await c.get("/company/plan_usage", category=category)

    @reg.tool(toolset="account", title="Quote API e stato server")
    async def fic_server_status() -> dict[str, Any]:
        """Stato del server MCP senza consumare richieste: quote API residue (dagli header delle ultime
        risposte), modalità sola lettura, privacy, toolset attivi, ultime scritture registrate."""
        s = rt.settings
        return {
            "quote_api": c.quota.as_dict(),
            "limite_breve": "300 richieste ogni 5 minuti per azienda",
            "sola_lettura": s.read_only,
            "privacy": {"modalita": s.privacy_mode, "segnaposto_attivi": rt.tokenizer.vault.stats()},
            "toolset": list(s.toolsets),
            "accantonamento_pct": s.set_aside_pct,
            "ultime_scritture": rt.audit.tail(10),
        }
