
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from ..client import FicApiError
from ..registry import Registrar
from ._common import Confirm

DocId = Annotated[int, Field(description="ID della fattura/nota di credito elettronica.")]


def register(reg: Registrar) -> None:
    rt = reg.rt
    c = rt.client

    @reg.tool(toolset="einvoice", title="Verifica XML fattura elettronica")
    async def fic_verify_e_invoice(doc_id: DocId) -> dict[str, Any]:
        """Controlla che l'XML FatturaPA del documento sia valido prima dell'invio allo SdI. Non invia nulla."""
        return await c.get(f"/issued_documents/{doc_id}/e_invoice/xml_verify")

    @reg.tool(toolset="einvoice", title="XML fattura elettronica")
    async def fic_get_e_invoice_xml(
        doc_id: DocId,
        save_to: Annotated[str | None, Field(description="Percorso locale dove salvare l'XML (opzionale).")] = None,
        include_attachment: Annotated[bool, Field(description="Include gli allegati codificati nell'XML.")] = False,
    ) -> dict[str, Any]:
        """Scarica l'XML FatturaPA del documento. Se indichi save_to lo salva su disco e restituisce solo il
        percorso; altrimenti restituisce il testo (troncato se molto lungo)."""
        resp = await c.request("GET", f"/issued_documents/{doc_id}/e_invoice/xml",
                               params={"include_attachment": int(include_attachment)}, raw=True)
        xml = resp.text
        if rt.tokenizer.enabled and not save_to:
            raise ValueError("Con la privacy attiva l'XML (che contiene tutti i dati del cliente) non viene "
                             "mostrato al modello: indica save_to per salvarlo su disco.")
        if save_to:
            path = Path(save_to).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(xml, encoding="utf-8")
            return {"salvato_in": str(path), "byte": len(xml.encode())}
        return {"xml": xml[:60_000], "troncato": len(xml) > 60_000}

    @reg.tool(toolset="einvoice", write=True, destructive=True, idempotent=False,
              title="Invia fattura elettronica allo SdI")
    async def fic_send_e_invoice(doc_id: DocId, confirm: Confirm = False) -> dict[str, Any]:
        """Trasmette la fattura elettronica allo SdI. IRREVERSIBILE: una volta accettata si corregge solo
        con nota di credito. Senza confirm esegue verifica XML e invio di prova (dry run) e mostra il
        riepilogo; con confirm=true invia davvero. Chiedi sempre conferma esplicita all'utente."""
        doc = ((await c.request("GET", f"/issued_documents/{doc_id}", params={"fieldset": "detailed"})) or {}).get("data") or {}
        summary = {"id": doc_id, "tipo": doc.get("type"), "numero": doc.get("number"), "data": doc.get("date"),
                   "cliente": (doc.get("entity") or {}).get("name"), "totale": doc.get("amount_gross"),
                   "stato_fe": doc.get("ei_status"), "fattura_elettronica": doc.get("e_invoice")}
        if not doc.get("e_invoice"):
            raise ValueError(f"Il documento {doc_id} non è una fattura elettronica (e_invoice=false).")
        if not confirm:
            checks: dict[str, Any] = {}
            try:
                checks["verifica_xml"] = await c.get(f"/issued_documents/{doc_id}/e_invoice/xml_verify")
                checks["dry_run"] = await c.request("POST", f"/issued_documents/{doc_id}/e_invoice/send",
                                                    json_body={"options": {"dry_run": True}})
                checks["esito"] = "pronta per l'invio"
            except FicApiError as exc:
                checks["esito"] = "NON pronta"
                checks["errore"] = exc.render()
            return {"eseguito": False, "documento": summary, "controlli": checks,
                    "messaggio": "Nessun invio effettuato. Per trasmettere allo SdI richiama con confirm=true "
                                 "dopo il via libera esplicito dell'utente."}
        result = await c.request("POST", f"/issued_documents/{doc_id}/e_invoice/send", json_body={})
        return {"eseguito": True, "documento": summary, "risposta": result}

    @reg.tool(toolset="einvoice", title="Motivo scarto SdI")
    async def fic_get_e_invoice_rejection_reason(doc_id: DocId) -> dict[str, Any]:
        """Motivo dello scarto (notifica di scarto SdI) di una fattura elettronica, con codice e descrizione."""
        return await c.get(f"/issued_documents/{doc_id}/e_invoice/error_reason")
