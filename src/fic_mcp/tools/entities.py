
from typing import Annotated, Any

from pydantic import Field

from ..registry import Registrar
from ._common import (Confirm, Data, Fields, Fieldset, Page, PerPage, Query, Sort, combine_q, list_params,
                      not_confirmed, quote_value, wrap)

ENTITY_DOC = (
    "Campi principali: name (obbligatorio), type ('company'|'person'|'pa'|'condo'), first_name, last_name, "
    "vat_number, tax_code, address_street, address_postal_code, address_city, address_province (sigla), "
    "country (es. 'Italia'), email, certified_email (PEC), phone, ei_code (codice destinatario SdI, 7 caratteri; "
    "'0000000' se solo PEC), default_payment_terms (giorni), default_vat ({id}), notes."
)


def _make(reg: Registrar, kind: str, it_one: str, it_many: str) -> None:
    c = reg.rt.client
    base = f"/entities/{kind}s"

    async def list_fn(
        search: Annotated[str | None, Field(description="Testo da cercare nella ragione sociale/nome.")] = None,
        vat_number: Annotated[str | None, Field(description="Filtra per partita IVA esatta.")] = None,
        q: Query = None, fieldset: Fieldset = None, fields: Fields = None, sort: Sort = None,
        page: Page = 1, per_page: PerPage = 20,
    ) -> dict[str, Any]:
        query = combine_q(q, f"name contains {quote_value(search)}" if search else None,
                          f"vat_number = {quote_value(vat_number)}" if vat_number else None)
        return await c.request("GET", base, params=list_params(q=query, fields=fields, fieldset=fieldset,
                                                               sort=sort, page=page, per_page=per_page))

    async def get_fn(
        entity_id: Annotated[int, Field(description=f"ID del {it_one}.")],
        fieldset: Fieldset = "detailed", fields: Fields = None,
    ) -> dict[str, Any]:
        return await c.request("GET", f"{base}/{entity_id}", params={"fieldset": fieldset, "fields": fields})

    async def create_fn(data: Data) -> dict[str, Any]:
        return await c.request("POST", base, json_body=wrap(data))

    async def update_fn(
        entity_id: Annotated[int, Field(description=f"ID del {it_one}.")], data: Data,
    ) -> dict[str, Any]:
        return await c.request("PUT", f"{base}/{entity_id}", json_body=wrap(data))

    async def delete_fn(
        entity_id: Annotated[int, Field(description=f"ID del {it_one}.")], confirm: Confirm = False,
    ) -> dict[str, Any]:
        if not confirm:
            current = await c.request("GET", f"{base}/{entity_id}", params={"fieldset": "basic"})
            return not_confirmed(f"eliminazione del {it_one} {entity_id}", anteprima=(current or {}).get("data"))
        await c.request("DELETE", f"{base}/{entity_id}")
        return {"eseguito": True, "eliminato": entity_id}

    list_fn.__doc__ = f"Elenca i {it_many} in anagrafica, con ricerca per nome/P.IVA e filtri."
    get_fn.__doc__ = f"Dettaglio completo di un {it_one}."
    create_fn.__doc__ = f"Crea un {it_one}. {ENTITY_DOC}"
    update_fn.__doc__ = f"Modifica un {it_one}: passa solo i campi da cambiare. {ENTITY_DOC}"
    delete_fn.__doc__ = f"Elimina un {it_one} (richiede confirm=true)."

    reg.tool(toolset="entities", entity_output=True, name=f"fic_list_{kind}s")(list_fn)
    reg.tool(toolset="entities", entity_output=True, name=f"fic_get_{kind}")(get_fn)
    reg.tool(toolset="entities", write=True, entity_output=True, name=f"fic_create_{kind}")(create_fn)
    reg.tool(toolset="entities", write=True, idempotent=True, entity_output=True,
             name=f"fic_update_{kind}")(update_fn)
    reg.tool(toolset="entities", write=True, destructive=True, idempotent=True,
             name=f"fic_delete_{kind}")(delete_fn)


def register(reg: Registrar) -> None:
    _make(reg, "client", "cliente", "clienti")
    _make(reg, "supplier", "fornitore", "fornitori")
