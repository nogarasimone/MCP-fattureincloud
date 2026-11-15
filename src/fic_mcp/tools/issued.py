
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from ..registry import Registrar
from ._common import (Confirm, Data, DateStr, Fields, Fieldset, IssuedType, Page, PerPage, Query, Sort,
                      attachment_files, combine_q, date_range_q, list_params, not_confirmed, quote_value, r2,
                      wrap)

DocId = Annotated[int, Field(description="ID del documento emesso.")]

ISSUED_DOC = (
    "Campi principali di 'data': type, entity (cliente: {id, name, vat_number, tax_code, address_*, country, "
    "certified_email, ei_code}), date, number/numeration (se omessi li assegna FIC), subject, visible_subject, "
    "notes, currency, language, use_gross_prices, rivalsa (% rivalsa INPS), cassa (% cassa previdenziale), "
    "withholding_tax (% ritenuta), withholding_tax_taxable (% imponibile soggetto a ritenuta), "
    "items_list [{product_id, code, name, description, qty, measure, net_price, gross_price, vat:{id}, "
    "discount, not_taxable, apply_withholding_taxes}], payments_list [{amount, due_date, status "
    "('not_paid'|'paid'|'reversed'), paid_date, payment_account:{id}}] (la somma deve essere uguale a "
    "amount_due: calcolala prima con fic_issued_document_totals), e_invoice (true per FE), "
    "ei_data {payment_method: 'MP05' bonifico, ...}, payment_method {id}."
)


class InvoiceLine(BaseModel):
    name: str = Field(description="Descrizione breve della riga (es. 'Consulenza sicurezza informatica').")
    qty: float = Field(1, description="Quantità (es. giornate).")
    net_price: float = Field(description="Prezzo unitario imponibile, IVA esclusa.")
    vat_rate: float | None = Field(None, description="Aliquota IVA in % (es. 22). Ignorata se c'è vat_id.")
    vat_id: int | None = Field(None, description="ID aliquota IVA da fic_info('vat_types'). Obbligatorio per aliquote 0 con natura.")
    description: str | None = Field(None, description="Descrizione estesa.")
    product_id: int | None = Field(None, description="ID prodotto a catalogo, opzionale.")
    measure: str | None = Field(None, description="Unità di misura (es. 'gg', 'ore').")
    discount: float | None = Field(None, description="Sconto % sulla riga.")
    apply_withholding_taxes: bool = Field(True, description="Se la riga è soggetta a ritenuta d'acconto.")
    not_taxable: bool = Field(False, description="Riga esclusa dall'imponibile (es. spese anticipate art. 15).")


ENTITY_COPY = ("id", "name", "type", "vat_number", "tax_code", "address_street", "address_postal_code",
               "address_city", "address_province", "address_extra", "country", "certified_email", "ei_code",
               "first_name", "last_name", "email")


def register(reg: Registrar) -> None:
    rt = reg.rt
    c = rt.client

    async def vat_types() -> list[dict[str, Any]]:
        if "vat_types" not in rt.cache:
            body = await c.get("/info/vat_types")
            rt.cache["vat_types"] = (body or {}).get("data") or []
        return rt.cache["vat_types"]

    async def resolve_vat(line: InvoiceLine) -> dict[str, Any]:
        if line.vat_id:
            return {"id": line.vat_id}
        if line.vat_rate is None:
            raise ValueError(f"Riga '{line.name}': indica vat_rate oppure vat_id.")
        candidates = [v for v in await vat_types()
                      if r2(v.get("value")) == r2(line.vat_rate) and not v.get("is_disabled")]
        if line.vat_rate > 0:
            plain = [v for v in candidates if not v.get("ei_type")] or candidates
            if plain:
                return {"id": plain[0]["id"]}
        options = [{"id": v["id"], "descrizione": v.get("description"), "natura": v.get("ei_type")}
                   for v in candidates]
        raise ValueError(f"Riga '{line.name}': aliquota {line.vat_rate}% ambigua o assente; scegli vat_id tra "
                         f"{options or 'le aliquote di fic_info(vat_types)'}.")

    # ------------------------------------------------------------ lettura
    @reg.tool(toolset="issued", title="Elenco documenti emessi")
    async def fic_list_issued_documents(
        type: IssuedType = "invoice",
        date_from: DateStr = None, date_to: DateStr = None,
        client_id: Annotated[int | None, Field(description="Solo documenti di questo cliente.")] = None,
        q: Query = None, fieldset: Fieldset = None, fields: Fields = None,
        sort: Sort = "-date", page: Page = 1, per_page: PerPage = 20,
    ) -> dict[str, Any]:
        """Elenca fatture, note di credito, preventivi, proforma, ordini, DDT ecc. con filtri per data,
        cliente o query libera. Per totali e incassi usa i tool di analisi (fic_revenue_summary)."""
        query = combine_q(q, date_range_q(date_from, date_to),
                          f"entity.id = {client_id}" if client_id else None)
        return await c.request("GET", "/issued_documents", params=list_params(
            q=query, fields=fields, fieldset=fieldset, sort=sort, page=page, per_page=per_page, type=type))

    @reg.tool(toolset="issued", title="Dettaglio documento emesso")
    async def fic_get_issued_document(doc_id: DocId, fieldset: Fieldset = "detailed",
                                      fields: Fields = None) -> dict[str, Any]:
        """Dettaglio completo di un documento emesso: cliente, righe, pagamenti, stato FE, link PDF."""
        return await c.request("GET", f"/issued_documents/{doc_id}",
                               params={"fieldset": fieldset, "fields": fields})

    @reg.tool(toolset="issued", title="Info per nuovo documento")
    async def fic_issued_document_pre_create_info(type: IssuedType = "invoice") -> dict[str, Any]:
        """Valori di default e opzioni per creare un documento del tipo indicato: numerazioni, aliquote,
        conti, metodi di pagamento, modelli, lingue, valute, impostazioni FE."""
        return await c.get("/issued_documents/info", type=type)

    @reg.tool(toolset="issued", title="Calcola totali")
    async def fic_issued_document_totals(
        data: Data,
        doc_id: Annotated[int | None, Field(description="Se valorizzato calcola i totali di un documento esistente modificato.")] = None,
    ) -> dict[str, Any]:
        """Calcola imponibile, IVA, rivalsa, cassa, ritenuta, totale e netto a pagare (amount_due) di un
        documento senza salvarlo. Usalo prima di creare/modificare per impostare payments_list."""
        path = f"/issued_documents/{doc_id}/totals" if doc_id else "/issued_documents/totals"
        return await c.request("POST", path, json_body=wrap(data))

    @reg.tool(toolset="issued", title="Dati email documento")
    async def fic_get_issued_document_email_data(doc_id: DocId) -> dict[str, Any]:
        """Dati precompilati per inviare il documento via email (mittenti, destinatario, oggetto, testo)."""
        return await c.get(f"/issued_documents/{doc_id}/email")

    # ----------------------------------------------------------- scrittura
    async def fic_create_issued_document(data: Data) -> dict[str, Any]:
        return await c.request("POST", "/issued_documents", json_body=wrap(data))

    fic_create_issued_document.__doc__ = (
        "Crea un documento emesso (qualsiasi tipo) passando l'oggetto completo. Per una fattura semplice "
        "preferisci fic_create_invoice, che calcola totali e scadenze da solo. " + ISSUED_DOC)
    reg.tool(toolset="issued", write=True, title="Crea documento emesso")(fic_create_issued_document)

    @reg.tool(toolset="issued", write=True, idempotent=True, title="Modifica documento emesso")
    async def fic_update_issued_document(doc_id: DocId, data: Data) -> dict[str, Any]:
        """Modifica un documento emesso: passa solo i campi da cambiare. Se cambi righe o importi,
        aggiorna anche payments_list. Una fattura elettronica già inviata allo SdI non va modificata:
        si emette una nota di credito."""
        return await c.request("PUT", f"/issued_documents/{doc_id}", json_body=wrap(data))

    @reg.tool(toolset="issued", write=True, destructive=True, idempotent=True, title="Elimina documento")
    async def fic_delete_issued_document(doc_id: DocId, confirm: Confirm = False) -> dict[str, Any]:
        """Sposta nel cestino un documento emesso (richiede confirm=true). Non usarlo per fatture già
        trasmesse allo SdI."""
        if not confirm:
            current = await c.request("GET", f"/issued_documents/{doc_id}", params={"fieldset": "basic"})
            return not_confirmed(f"eliminazione del documento {doc_id}", anteprima=(current or {}).get("data"))
        await c.request("DELETE", f"/issued_documents/{doc_id}")
        return {"eseguito": True, "eliminato": doc_id}

    @reg.tool(toolset="issued", title="Trasforma documento")
    async def fic_transform_issued_document(
        original_document_id: Annotated[int, Field(description="ID del documento di partenza.")],
        new_type: IssuedType,
        e_invoice: Annotated[bool | None, Field(description="Se il nuovo documento è una fattura elettronica.")] = None,
        keep_copy: Annotated[bool, Field(description="Mantiene il documento originale.")] = True,
    ) -> dict[str, Any]:
        """Prepara la trasformazione di un documento (es. preventivo→fattura, proforma→fattura,
        ordine→DDT). Restituisce i dati del nuovo documento da salvare con fic_create_issued_document."""
        params = {"original_document_id": original_document_id, "new_type": new_type,
                  "e_invoice": None if e_invoice is None else int(e_invoice),
                  "transform_keep_copy": int(keep_copy)}
        return await c.request("GET", "/issued_documents/transform", params=params)

    @reg.tool(toolset="issued", title="Unisci documenti")
    async def fic_join_issued_documents(
        ids: Annotated[list[int], Field(description="ID dei documenti da unire (DDT, ordini, preventivi, rapportini).")],
        type: Annotated[Literal["delivery_notes", "orders", "quotes", "work_reports"] | None,
                        Field(description="Tipo dei documenti da unire.")] = None,
        group: Annotated[bool, Field(description="Raggruppa le righe uguali.")] = False,
    ) -> dict[str, Any]:
        """Prepara un unico documento (es. fattura differita) unendo più documenti. Restituisce i dati da
        salvare con fic_create_issued_document."""
        return await c.request("GET", "/issued_documents/join",
                               params={"ids": ",".join(map(str, ids)), "type": type, "group": int(group)})

    @reg.tool(toolset="issued", write=True, destructive=True, title="Invia documento via email")
    async def fic_send_issued_document_email(
        doc_id: DocId,
        recipient_email: Annotated[str, Field(description="Email del destinatario.")],
        subject: Annotated[str, Field(description="Oggetto dell'email.")],
        body: Annotated[str, Field(description="Testo (HTML ammesso). Il segnaposto {{allegati}} inserisce i link.")],
        sender_email: Annotated[str | None, Field(description="Mittente tra quelli in fic_get_issued_document_email_data.")] = None,
        attach_pdf: bool = True, send_copy: bool = False,
        include_delivery_note: bool = False, include_attachment: bool = False,
        confirm: Confirm = False,
    ) -> dict[str, Any]:
        """Invia (o programma) l'email di un documento al cliente tramite Fatture in Cloud. È un invio
        reale verso terzi: richiede confirm=true. Non sostituisce l'invio allo SdI."""
        payload = {"recipient_email": recipient_email, "subject": subject, "body": body,
                   "attach_pdf": attach_pdf, "send_copy": send_copy,
                   "include": {"document": True, "delivery_note": include_delivery_note,
                               "attachment": include_attachment, "accompanying_invoice": False}}
        if sender_email:
            payload["sender_email"] = sender_email
        if not confirm:
            return not_confirmed(f"invio email del documento {doc_id} a {recipient_email}", email=payload)
        await c.request("POST", f"/issued_documents/{doc_id}/email", json_body={"data": payload})
        return {"eseguito": True, "documento": doc_id, "destinatario": recipient_email}

    @reg.tool(toolset="issued", write=True, title="Carica allegato documento")
    async def fic_upload_issued_document_attachment(
        file_path: Annotated[str | None, Field(description="Percorso locale del file (dove gira il server).")] = None,
        content_base64: Annotated[str | None, Field(description="In alternativa: contenuto in base64.")] = None,
        filename: Annotated[str | None, Field(description="Nome file (obbligatorio con base64).")] = None,
    ) -> dict[str, Any]:
        """Carica un allegato (pdf, immagini, office, zip) e restituisce attachment_token da mettere nel
        campo attachment_token del documento in creazione o modifica."""
        files, form = attachment_files(file_path, content_base64, filename)
        return await c.request("POST", "/issued_documents/attachment", files=files, data=form)

    @reg.tool(toolset="issued", write=True, destructive=True, idempotent=True)
    async def fic_delete_issued_document_attachment(doc_id: DocId, confirm: Confirm = False) -> dict[str, Any]:
        """Rimuove l'allegato da un documento emesso (richiede confirm=true)."""
        if not confirm:
            return not_confirmed(f"rimozione dell'allegato dal documento {doc_id}")
        await c.request("DELETE", f"/issued_documents/{doc_id}/attachment")
        return {"eseguito": True, "documento": doc_id}

    # -------------------------------------------------------------- cestino
    @reg.tool(toolset="issued", title="Cestino documenti emessi")
    async def fic_list_bin_issued_documents() -> dict[str, Any]:
        """Elenca i documenti emessi nel cestino."""
        return await c.get("/bin/issued_documents")

    @reg.tool(toolset="issued", write=True, idempotent=True)
    async def fic_recover_bin_issued_document(doc_id: DocId) -> dict[str, Any]:
        """Ripristina dal cestino un documento emesso."""
        await c.request("POST", f"/bin/issued_documents/{doc_id}/recover")
        return {"eseguito": True, "ripristinato": doc_id}

    @reg.tool(toolset="issued", write=True, destructive=True, idempotent=True)
    async def fic_delete_bin_issued_document(doc_id: DocId, confirm: Confirm = False) -> dict[str, Any]:
        """Elimina definitivamente un documento dal cestino (irreversibile, richiede confirm=true)."""
        if not confirm:
            return not_confirmed(f"eliminazione DEFINITIVA dal cestino del documento {doc_id}")
        await c.request("DELETE", f"/bin/issued_documents/{doc_id}")
        return {"eseguito": True, "eliminato_definitivamente": doc_id}

    # ------------------------------------------------- fattura semplificata
    @reg.tool(toolset="issued", write=True, title="Crea fattura (guidata)")
    async def fic_create_invoice(
        client_id: Annotated[int, Field(description="ID cliente da fic_list_clients.")],
        lines: Annotated[list[InvoiceLine], Field(min_length=1, description="Righe della fattura.")],
        doc_type: Annotated[Literal["invoice", "proforma", "quote", "credit_note"],
                            Field(description="invoice, proforma, quote (preventivo) o credit_note.")] = "invoice",
        doc_date: DateStr = None,
        due_days: Annotated[int, Field(ge=0, le=365, description="Giorni per la scadenza del pagamento.")] = 30,
        due_date: DateStr = None,
        e_invoice: Annotated[bool, Field(description="Fattura elettronica (SdI). Ignorato per preventivi/proforma.")] = True,
        ei_payment_method: Annotated[str, Field(description="Codice modalità di pagamento FE: MP05 bonifico, MP08 carta, MP01 contanti...")] = "MP05",
        payment_account_id: Annotated[int | None, Field(description="Conto di incasso da fic_info('payment_accounts').")] = None,
        rivalsa_pct: Annotated[float, Field(ge=0, le=100, description="Rivalsa INPS in % (es. 4 per Gestione Separata).")] = 0,
        cassa_pct: Annotated[float, Field(ge=0, le=100, description="Contributo cassa previdenziale in %.")] = 0,
        withholding_pct: Annotated[float, Field(ge=0, le=100, description="Ritenuta d'acconto in % (es. 20 se il cliente è sostituto d'imposta).")] = 0,
        withholding_taxable_pct: Annotated[float, Field(ge=0, le=100, description="% dell'imponibile soggetta a ritenuta.")] = 100,
        subject: Annotated[str | None, Field(description="Oggetto interno.")] = None,
        visible_subject: Annotated[str | None, Field(description="Oggetto visibile sul documento.")] = None,
        notes: Annotated[str | None, Field(description="Note in calce.")] = None,
        dry_run: Annotated[bool, Field(description="True = calcola e mostra l'anteprima senza creare nulla. "
                                                   "Passa false solo dopo conferma dell'utente.")] = True,
    ) -> dict[str, Any]:
        """Prepara e crea una fattura (o proforma, preventivo, nota di credito) da poche informazioni:
        prende l'anagrafica del cliente, risolve le aliquote IVA, applica rivalsa/cassa/ritenuta, calcola
        i totali con FIC e imposta la scadenza sul netto a pagare. Con dry_run=true (default) restituisce
        solo l'anteprima. La fattura creata resta in bozza lato SdI finché non usi fic_send_e_invoice."""
        client = (await c.request("GET", f"/entities/clients/{client_id}", params={"fieldset": "detailed"}) or {}).get("data") or {}
        entity = {k: client[k] for k in ENTITY_COPY if client.get(k) not in (None, "")}
        entity["id"] = client_id

        items = []
        for line in lines:
            item: dict[str, Any] = {"name": line.name, "qty": line.qty, "net_price": line.net_price,
                                    "vat": await resolve_vat(line),
                                    "apply_withholding_taxes": line.apply_withholding_taxes,
                                    "not_taxable": line.not_taxable}
            for key in ("description", "product_id", "measure", "discount"):
                value = getattr(line, key)
                if value not in (None, ""):
                    item[key] = value
            items.append(item)

        d = date.fromisoformat(doc_date) if doc_date else date.today()
        due = date.fromisoformat(due_date) if due_date else d + timedelta(days=due_days)
        is_fiscal = doc_type in ("invoice", "credit_note")
        data: dict[str, Any] = {"type": doc_type, "entity": entity, "date": d.isoformat(), "items_list": items,
                                "use_gross_prices": False}
        if rivalsa_pct:
            data["rivalsa"] = rivalsa_pct
        if cassa_pct:
            data["cassa"] = cassa_pct
        if withholding_pct:
            data["withholding_tax"] = withholding_pct
            data["withholding_tax_taxable"] = withholding_taxable_pct
        for key, value in (("subject", subject), ("visible_subject", visible_subject), ("notes", notes)):
            if value:
                data[key] = value
        if is_fiscal and e_invoice:
            data["e_invoice"] = True
            data["ei_data"] = {"payment_method": ei_payment_method}

        totals = ((await c.request("POST", "/issued_documents/totals", json_body={"data": data})) or {}).get("data") or {}
        amount_due = r2(totals.get("amount_due"))
        payment: dict[str, Any] = {"amount": amount_due, "due_date": due.isoformat(), "status": "not_paid"}
        if payment_account_id:
            payment["payment_account"] = {"id": payment_account_id}
        if doc_type != "quote":
            data["payments_list"] = [payment]

        summary = {
            "cliente": entity.get("name"), "tipo": doc_type, "data": d.isoformat(), "scadenza": due.isoformat(),
            "imponibile": r2(totals.get("amount_net")), "rivalsa": r2(totals.get("amount_rivalsa")),
            "cassa": r2(totals.get("amount_cassa")), "iva": r2(totals.get("amount_vat")),
            "totale_documento": r2(totals.get("amount_gross")),
            "ritenuta": r2(totals.get("amount_withholding_tax")), "netto_a_pagare": amount_due,
            "fattura_elettronica": bool(data.get("e_invoice")),
        }
        if dry_run:
            return {"eseguito": False, "anteprima": summary,
                    "messaggio": "Anteprima. Per creare il documento richiama con dry_run=false dopo conferma.",
                    "payload": data}
        created = await c.request("POST", "/issued_documents", json_body={"data": data})
        doc = (created or {}).get("data") or {}
        return {"eseguito": True, "id": doc.get("id"), "numero": doc.get("number"),
                "riepilogo": summary, "url_pdf": doc.get("url")}
