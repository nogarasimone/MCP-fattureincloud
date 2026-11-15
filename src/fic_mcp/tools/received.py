
from typing import Annotated, Any

from pydantic import Field

from ..registry import Registrar
from ._common import (Confirm, Data, DateStr, Fields, Fieldset, Page, PerPage, Query, ReceivedType, Sort,
                      attachment_files, combine_q, date_range_q, list_params, not_confirmed, wrap)

DocId = Annotated[int, Field(description="ID del documento ricevuto.")]

RECEIVED_DOC = (
    "Campi principali di 'data': type ('expense'|'passive_credit_note'|'passive_delivery_note'|'self_invoice'), "
    "entity (fornitore: {id} oppure {name, vat_number, ...}), date, category (testo, vedi "
    "fic_info('received_document_categories')), description, amount_net, amount_vat, amount_gross, "
    "amount_withholding_tax, amount_other_withholding_tax, tax_deductibility (% deducibilità), "
    "vat_deductibility (% detraibilità IVA), is_detailed (true se passi items_list), items_list, "
    "payments_list [{amount, due_date, status ('not_paid'|'paid'), paid_date, payment_account:{id}}], "
    "invoice_number, attachment_token."
)


def register(reg: Registrar) -> None:
    c = reg.rt.client

    @reg.tool(toolset="received", title="Elenco spese e documenti ricevuti")
    async def fic_list_received_documents(
        type: ReceivedType = "expense",
        date_from: DateStr = None, date_to: DateStr = None,
        supplier_id: Annotated[int | None, Field(description="Solo documenti di questo fornitore.")] = None,
        q: Query = None, fieldset: Fieldset = None, fields: Fields = None,
        sort: Sort = "-date", page: Page = 1, per_page: PerPage = 20,
    ) -> dict[str, Any]:
        """Elenca spese (fatture passive), note di credito ricevute, DDT ricevuti e autofatture registrate."""
        query = combine_q(q, date_range_q(date_from, date_to),
                          f"entity.id = {supplier_id}" if supplier_id else None)
        return await c.request("GET", "/received_documents", params=list_params(
            q=query, fields=fields, fieldset=fieldset, sort=sort, page=page, per_page=per_page, type=type))

    @reg.tool(toolset="received", title="Dettaglio documento ricevuto")
    async def fic_get_received_document(doc_id: DocId, fieldset: Fieldset = "detailed") -> dict[str, Any]:
        """Dettaglio di una spesa o documento ricevuto (fornitore, importi, deducibilità, pagamenti)."""
        return await c.request("GET", f"/received_documents/{doc_id}", params={"fieldset": fieldset})

    @reg.tool(toolset="received", title="Info per nuova spesa")
    async def fic_received_document_pre_create_info(type: ReceivedType = "expense") -> dict[str, Any]:
        """Default e opzioni per registrare un documento ricevuto: categorie, conti, aliquote, centri di costo."""
        return await c.get("/received_documents/info", type=type)

    @reg.tool(toolset="received", title="Calcola totali spesa")
    async def fic_received_document_totals(
        data: Data,
        doc_id: Annotated[int | None, Field(description="Per un documento esistente modificato.")] = None,
    ) -> dict[str, Any]:
        """Calcola i totali di un documento ricevuto senza salvarlo."""
        path = f"/received_documents/{doc_id}/totals" if doc_id else "/received_documents/totals"
        return await c.request("POST", path, json_body=wrap(data))

    @reg.tool(toolset="received", title="Fatture SdI da registrare")
    async def fic_list_pending_received_documents() -> dict[str, Any]:
        """Fatture elettroniche passive arrivate dallo SdI e non ancora registrate come spesa."""
        return await c.get("/received_documents/pending")

    @reg.tool(toolset="received", title="Dettaglio fattura SdI da registrare")
    async def fic_get_pending_received_document(doc_id: DocId) -> dict[str, Any]:
        """Dettaglio di una fattura passiva in attesa di registrazione."""
        return await c.get(f"/received_documents/pending/{doc_id}")

    async def fic_create_received_document(data: Data) -> dict[str, Any]:
        return await c.request("POST", "/received_documents", json_body=wrap(data))

    fic_create_received_document.__doc__ = "Registra una spesa o un documento ricevuto. " + RECEIVED_DOC
    reg.tool(toolset="received", write=True, title="Registra spesa")(fic_create_received_document)

    @reg.tool(toolset="received", write=True, idempotent=True, title="Modifica spesa")
    async def fic_update_received_document(doc_id: DocId, data: Data) -> dict[str, Any]:
        """Modifica un documento ricevuto (es. segnare un pagamento come pagato in payments_list,
        cambiare categoria o deducibilità). Passa solo i campi da cambiare."""
        return await c.request("PUT", f"/received_documents/{doc_id}", json_body=wrap(data))

    @reg.tool(toolset="received", write=True, destructive=True, idempotent=True)
    async def fic_delete_received_document(doc_id: DocId, confirm: Confirm = False) -> dict[str, Any]:
        """Sposta nel cestino un documento ricevuto (richiede confirm=true)."""
        if not confirm:
            current = await c.request("GET", f"/received_documents/{doc_id}", params={"fieldset": "basic"})
            return not_confirmed(f"eliminazione del documento ricevuto {doc_id}", anteprima=(current or {}).get("data"))
        await c.request("DELETE", f"/received_documents/{doc_id}")
        return {"eseguito": True, "eliminato": doc_id}

    @reg.tool(toolset="received", write=True, title="Carica allegato spesa")
    async def fic_upload_received_document_attachment(
        file_path: Annotated[str | None, Field(description="Percorso locale del file.")] = None,
        content_base64: Annotated[str | None, Field(description="In alternativa: contenuto base64.")] = None,
        filename: Annotated[str | None, Field(description="Nome file (obbligatorio con base64).")] = None,
    ) -> dict[str, Any]:
        """Carica il PDF/immagine di una spesa e restituisce attachment_token da usare nel documento."""
        files, form = attachment_files(file_path, content_base64, filename)
        return await c.request("POST", "/received_documents/attachment", files=files, data=form)

    @reg.tool(toolset="received", write=True, destructive=True, idempotent=True)
    async def fic_delete_received_document_attachment(doc_id: DocId, confirm: Confirm = False) -> dict[str, Any]:
        """Rimuove l'allegato di un documento ricevuto (richiede confirm=true)."""
        if not confirm:
            return not_confirmed(f"rimozione allegato dal documento ricevuto {doc_id}")
        await c.request("DELETE", f"/received_documents/{doc_id}/attachment")
        return {"eseguito": True, "documento": doc_id}

    @reg.tool(toolset="received", title="Cestino documenti ricevuti")
    async def fic_list_bin_received_documents() -> dict[str, Any]:
        """Elenca i documenti ricevuti nel cestino."""
        return await c.get("/bin/received_documents")

    @reg.tool(toolset="received", write=True, idempotent=True)
    async def fic_recover_bin_received_document(doc_id: DocId) -> dict[str, Any]:
        """Ripristina dal cestino un documento ricevuto."""
        await c.request("POST", f"/bin/received_documents/{doc_id}/recover")
        return {"eseguito": True, "ripristinato": doc_id}

    @reg.tool(toolset="received", write=True, destructive=True, idempotent=True)
    async def fic_delete_bin_received_document(doc_id: DocId, confirm: Confirm = False) -> dict[str, Any]:
        """Elimina definitivamente dal cestino un documento ricevuto (irreversibile, confirm=true)."""
        if not confirm:
            return not_confirmed(f"eliminazione DEFINITIVA dal cestino del documento ricevuto {doc_id}")
        await c.request("DELETE", f"/bin/received_documents/{doc_id}")
        return {"eseguito": True, "eliminato_definitivamente": doc_id}
