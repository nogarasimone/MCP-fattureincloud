"""Corrispettivi, F24, prima nota, archivio: CRUD con la stessa struttura."""


from typing import Annotated, Any, Literal

from pydantic import Field

from ..registry import Registrar
from ._common import (Confirm, Data, DateStr, Fields, Fieldset, Page, PerPage, Query, Sort, attachment_files,
                      combine_q, date_range_q, list_params, not_confirmed, wrap)

ResId = Annotated[int, Field(description="ID del documento.")]


def _crud(reg: Registrar, *, toolset: str, slug: str, base: str, one: str, create_doc: str,
          attachment: bool = False) -> None:
    """Registra get/create/update/delete (+ allegati) per una risorsa /c/{id}/<base>."""
    c = reg.rt.client

    async def get_fn(doc_id: ResId) -> dict[str, Any]:
        return await c.get(f"{base}/{doc_id}")

    async def create_fn(data: Data) -> dict[str, Any]:
        return await c.request("POST", base, json_body=wrap(data))

    async def update_fn(doc_id: ResId, data: Data) -> dict[str, Any]:
        return await c.request("PUT", f"{base}/{doc_id}", json_body=wrap(data))

    async def delete_fn(doc_id: ResId, confirm: Confirm = False) -> dict[str, Any]:
        if not confirm:
            return not_confirmed(f"eliminazione {one} {doc_id}")
        await c.request("DELETE", f"{base}/{doc_id}")
        return {"eseguito": True, "eliminato": doc_id}

    get_fn.__doc__ = f"Dettaglio di {one}."
    create_fn.__doc__ = f"Crea {one}. {create_doc}"
    update_fn.__doc__ = f"Modifica {one}: passa solo i campi da cambiare."
    delete_fn.__doc__ = f"Elimina {one} (richiede confirm=true)."
    reg.tool(toolset=toolset, name=f"fic_get_{slug}")(get_fn)
    reg.tool(toolset=toolset, write=True, name=f"fic_create_{slug}")(create_fn)
    reg.tool(toolset=toolset, write=True, idempotent=True, name=f"fic_update_{slug}")(update_fn)
    reg.tool(toolset=toolset, write=True, destructive=True, idempotent=True, name=f"fic_delete_{slug}")(delete_fn)

    if attachment:
        async def upload_fn(
            file_path: Annotated[str | None, Field(description="Percorso locale del file.")] = None,
            content_base64: Annotated[str | None, Field(description="In alternativa: contenuto base64.")] = None,
            filename: Annotated[str | None, Field(description="Nome file (obbligatorio con base64).")] = None,
        ) -> dict[str, Any]:
            files, form = attachment_files(file_path, content_base64, filename)
            return await c.request("POST", f"{base}/attachment", files=files, data=form)

        upload_fn.__doc__ = f"Carica un allegato per {one}; restituisce attachment_token da usare nel documento."
        reg.tool(toolset=toolset, write=True, name=f"fic_upload_{slug}_attachment")(upload_fn)


def register(reg: Registrar) -> None:
    c = reg.rt.client

    # ------------------------------------------------------- corrispettivi
    @reg.tool(toolset="receipts", title="Elenco corrispettivi")
    async def fic_list_receipts(
        date_from: DateStr = None, date_to: DateStr = None, q: Query = None, fieldset: Fieldset = None,
        fields: Fields = None, sort: Sort = "-date", page: Page = 1, per_page: PerPage = 20,
    ) -> dict[str, Any]:
        """Elenca i corrispettivi (scontrini/ricevute fiscali registrati)."""
        return await c.request("GET", "/receipts", params=list_params(
            q=combine_q(q, date_range_q(date_from, date_to)), fields=fields, fieldset=fieldset, sort=sort,
            page=page, per_page=per_page))

    @reg.tool(toolset="receipts", title="Totali mensili corrispettivi")
    async def fic_receipts_monthly_totals(
        type: Annotated[Literal["sales_receipt", "till_receipt"], Field(description="Tipo di corrispettivo.")] = "sales_receipt",
        year: Annotated[str, Field(description="Anno, es. '2026'.", pattern=r"^\d{4}$")] = "2026",
    ) -> dict[str, Any]:
        """Totali mensili dei corrispettivi per l'anno indicato."""
        return await c.get("/receipts/monthly_totals", type=type, year=year)

    @reg.tool(toolset="receipts", title="Info per nuovo corrispettivo")
    async def fic_receipt_pre_create_info() -> dict[str, Any]:
        """Default e opzioni per registrare un corrispettivo (registri, aliquote, conti)."""
        return await c.get("/receipts/info")

    _crud(reg, toolset="receipts", slug="receipt", base="/receipts", one="un corrispettivo",
          create_doc="Campi: date, type ('sales_receipt'|'till_receipt'), number, numeration, amount_net, "
                     "amount_vat, amount_gross, use_gross_prices, rc_center, payment_account {id}, "
                     "items_list [{amount_net, amount_gross, vat {id}, category}].")

    # ----------------------------------------------------------------- F24
    @reg.tool(toolset="taxes", title="Elenco F24")
    async def fic_list_f24(
        date_from: DateStr = None, date_to: DateStr = None, q: Query = None, fields: Fields = None,
        fieldset: Fieldset = None, sort: Sort = "-due_date", page: Page = 1, per_page: PerPage = 20,
    ) -> dict[str, Any]:
        """Elenca gli F24 registrati (scadenza, importo, stato pagamento) con filtro sulla data di scadenza."""
        return await c.request("GET", "/taxes", params=list_params(
            q=combine_q(q, date_range_q(date_from, date_to, field="due_date")), fields=fields,
            fieldset=fieldset, sort=sort, page=page, per_page=per_page))

    _crud(reg, toolset="taxes", slug="f24", base="/taxes", one="un F24", attachment=True,
          create_doc="Campi: due_date, status ('paid'|'not_paid'), payment_account {id}, amount, description, "
                     "attachment_token.")

    @reg.tool(toolset="taxes", write=True, destructive=True, idempotent=True)
    async def fic_delete_f24_attachment(doc_id: ResId, confirm: Confirm = False) -> dict[str, Any]:
        """Rimuove l'allegato (quietanza/modello) da un F24 (richiede confirm=true)."""
        if not confirm:
            return not_confirmed(f"rimozione allegato dall'F24 {doc_id}")
        await c.request("DELETE", f"/taxes/{doc_id}/attachment")
        return {"eseguito": True, "f24": doc_id}

    # ---------------------------------------------------------- prima nota
    @reg.tool(toolset="cashbook", title="Prima nota")
    async def fic_list_cashbook(
        date_from: Annotated[str, Field(description="Data inizio AAAA-MM-GG (obbligatoria).", pattern=r"^\d{4}-\d{2}-\d{2}$")],
        date_to: Annotated[str, Field(description="Data fine AAAA-MM-GG (obbligatoria).", pattern=r"^\d{4}-\d{2}-\d{2}$")],
        type: Annotated[Literal["in", "out", "all"], Field(description="Entrate, uscite o tutto.")] = "all",
        payment_account_id: Annotated[int | None, Field(description="Solo movimenti di questo conto.")] = None,
    ) -> dict[str, Any]:
        """Movimenti di prima nota (incassi e pagamenti registrati) nel periodo, per conto."""
        return await c.get("/cashbook", date_from=date_from, date_to=date_to,
                           type=None if type == "all" else type, payment_account_id=payment_account_id)

    _crud(reg, toolset="cashbook", slug="cashbook_entry", base="/cashbook", one="un movimento di prima nota",
          create_doc="Campi: date, description, kind ('cashbook'|'issued_document'|'received_document'), "
                     "type ('in'|'out'|'in_out'), entity_name, amount_in / payment_account_in {id} oppure "
                     "amount_out / payment_account_out {id}, document {id} se collegato a un documento.")

    # ------------------------------------------------------------- archivio
    @reg.tool(toolset="archive", title="Archivio documenti")
    async def fic_list_archive_documents(
        date_from: DateStr = None, date_to: DateStr = None, q: Query = None, fields: Fields = None,
        fieldset: Fieldset = None, sort: Sort = "-date", page: Page = 1, per_page: PerPage = 20,
    ) -> dict[str, Any]:
        """Elenca i documenti caricati nell'archivio di Fatture in Cloud (contratti, ricevute, ecc.)."""
        return await c.request("GET", "/archive", params=list_params(
            q=combine_q(q, date_range_q(date_from, date_to)), fields=fields, fieldset=fieldset, sort=sort,
            page=page, per_page=per_page))

    _crud(reg, toolset="archive", slug="archive_document", base="/archive", one="un documento d'archivio",
          attachment=True, create_doc="Campi: date, category, description, attachment_token (obbligatorio).")
