
from typing import Annotated, Any

from pydantic import Field

from ..registry import Registrar
from ._common import (Confirm, Data, Fieldset, Fields, Page, PerPage, Query, Sort, combine_q, list_params,
                      not_confirmed, quote_value, wrap)


def register(reg: Registrar) -> None:
    c = reg.rt.client

    @reg.tool(toolset="products", title="Elenco prodotti")
    async def fic_list_products(
        search: Annotated[str | None, Field(description="Testo da cercare nel nome del prodotto/servizio.")] = None,
        q: Query = None, fieldset: Fieldset = None, fields: Fields = None, sort: Sort = None,
        page: Page = 1, per_page: PerPage = 20,
    ) -> dict[str, Any]:
        """Elenca prodotti e servizi a catalogo (codice, nome, prezzo, IVA di default)."""
        query = combine_q(q, f"name contains {quote_value(search)}" if search else None)
        return await c.request("GET", "/products", params=list_params(
            q=query, fields=fields, fieldset=fieldset, sort=sort, page=page, per_page=per_page))

    @reg.tool(toolset="products")
    async def fic_get_product(
        product_id: Annotated[int, Field(description="ID prodotto.")], fieldset: Fieldset = "detailed",
    ) -> dict[str, Any]:
        """Dettaglio di un prodotto/servizio."""
        return await c.request("GET", f"/products/{product_id}", params={"fieldset": fieldset})

    @reg.tool(toolset="products", write=True)
    async def fic_create_product(data: Data) -> dict[str, Any]:
        """Crea un prodotto o servizio a catalogo. Campi principali: name (obbligatorio), code,
        description, net_price, gross_price, use_gross_price, default_vat ({id}), net_cost, measure,
        category, in_stock, notes."""
        return await c.request("POST", "/products", json_body=wrap(data))

    @reg.tool(toolset="products", write=True, idempotent=True)
    async def fic_update_product(
        product_id: Annotated[int, Field(description="ID prodotto.")], data: Data,
    ) -> dict[str, Any]:
        """Modifica un prodotto: passa solo i campi da cambiare."""
        return await c.request("PUT", f"/products/{product_id}", json_body=wrap(data))

    @reg.tool(toolset="products", write=True, destructive=True, idempotent=True)
    async def fic_delete_product(
        product_id: Annotated[int, Field(description="ID prodotto.")], confirm: Confirm = False,
    ) -> dict[str, Any]:
        """Elimina un prodotto (richiede confirm=true)."""
        if not confirm:
            return not_confirmed(f"eliminazione del prodotto {product_id}")
        await c.request("DELETE", f"/products/{product_id}")
        return {"eseguito": True, "eliminato": product_id}

    @reg.tool(toolset="products", title="Listini")
    async def fic_list_price_lists() -> dict[str, Any]:
        """Elenca i listini prezzi dell'azienda."""
        return await c.get("/price_lists")

    @reg.tool(toolset="products", title="Voci di listino")
    async def fic_get_price_list_items(
        price_list_id: Annotated[str, Field(description="ID del listino.")],
    ) -> dict[str, Any]:
        """Prezzi dei prodotti in un listino."""
        return await c.get(f"/price_lists/{price_list_id}/items")
