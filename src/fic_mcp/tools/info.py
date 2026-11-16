"""Tabelle di riferimento, impostazioni, email, webhook e chiamata API generica."""


from typing import Annotated, Any, Literal

from pydantic import Field

from ..registry import Registrar
from ._common import Confirm, Data, PerPage, not_confirmed, wrap

COMPANY_INFO = {
    "vat_types": "/info/vat_types",
    "payment_accounts": "/info/payment_accounts",
    "payment_methods": "/info/payment_methods",
    "product_categories": "/info/product_categories",
    "received_document_categories": "/info/received_document_categories",
    "revenue_centers": "/info/revenue_centers",
    "cost_centers": "/info/cost_centers",
    "archive_categories": "/info/archive_categories",
    "templates_company": "/settings/templates",
    "tax_profile": "/settings/tax_profile",
}
GLOBAL_INFO = {
    "countries": "/info/countries",
    "detailed_countries": "/info/detailed_countries",
    "currencies": "/info/currencies",
    "languages": "/info/languages",
    "measures": "/info/measures",
    "default_templates": "/info/templates",
    "delivery_note_causals": "/info/dn_causals",
    "cities": "/info/cities",
}
InfoKind = Literal[
    "vat_types", "payment_accounts", "payment_methods", "product_categories", "received_document_categories",
    "revenue_centers", "cost_centers", "archive_categories", "templates_company", "tax_profile",
    "countries", "detailed_countries", "currencies", "languages", "measures", "default_templates",
    "delivery_note_causals", "cities",
]

SETTINGS_PATHS = {
    "payment_account": "/settings/payment_accounts",
    "payment_method": "/settings/payment_methods",
    "vat_type": "/settings/vat_types",
}


def register(reg: Registrar) -> None:
    rt = reg.rt
    c = rt.client

    @reg.tool(toolset="info", title="Tabelle di riferimento")
    async def fic_info(
        kind: Annotated[InfoKind, Field(description=(
            "Tabella: vat_types (aliquote IVA e nature), payment_accounts (conti di incasso/pagamento), "
            "payment_methods, product_categories, received_document_categories (categorie spese), "
            "revenue_centers, cost_centers, archive_categories, templates_company (modelli documento), "
            "tax_profile (profilo fiscale: regime, cassa, ritenute), countries, detailed_countries, currencies, "
            "languages, measures (unità di misura), default_templates, delivery_note_causals, cities."))],
        context: Annotated[Literal["products", "issued_documents", "received_documents"] | None,
                           Field(description="Solo per product_categories: contesto delle categorie.")] = None,
        postal_code: Annotated[str | None, Field(description="Solo per cities: CAP.")] = None,
        city: Annotated[str | None, Field(description="Solo per cities: nome città.")] = None,
        template_type: Annotated[str | None, Field(description="Solo per i modelli: tipo documento (es. 'invoice').")] = None,
    ) -> dict[str, Any]:
        """Legge una tabella di riferimento dell'azienda o generale. Serve per ottenere gli ID da usare nei
        documenti (aliquota IVA, conto, metodo di pagamento, categoria spesa...). Le tabelle sono in cache
        per la sessione: non sprecano quota API."""
        params: dict[str, Any] = {}
        if kind == "product_categories":
            params["context"] = context or "products"
        if kind == "cities":
            params.update(postal_code=postal_code, city=city)
        if kind in ("default_templates", "templates_company"):
            params["type"] = template_type
        cache_key = f"info:{kind}:{sorted(params.items())}"
        if cache_key in rt.cache:
            return rt.cache[cache_key]
        if kind in COMPANY_INFO:
            result = await c.request("GET", COMPANY_INFO[kind], params=params)
        else:
            result = await c.request("GET", GLOBAL_INFO[kind], params=params, company=False)
        rt.cache[cache_key] = result
        if kind == "vat_types":
            rt.cache["vat_types"] = (result or {}).get("data") or []
        return result

    @reg.tool(toolset="settings", write=True, title="Gestisci impostazioni")
    async def fic_manage_settings(
        resource: Annotated[Literal["payment_account", "payment_method", "vat_type"],
                            Field(description="Conto di pagamento, metodo di pagamento o aliquota IVA.")],
        action: Annotated[Literal["get", "create", "update", "delete"], Field(description="Operazione.")],
        item_id: Annotated[int | None, Field(description="ID per get/update/delete.")] = None,
        data: Annotated[dict[str, Any] | None, Field(description=(
            "Per create/update. Conto: {name, type ('standard'|'bank'), iban, sia, virtual}. Metodo: {name, "
            "type, is_default, default_payment_account {id}, details}. Aliquota: {value, description, notes, "
            "e_invoice, ei_type (natura N1..N7), ei_description}."))] = None,
        confirm: Confirm = False,
    ) -> dict[str, Any]:
        """Legge, crea, modifica o elimina conti di pagamento, metodi di pagamento e aliquote IVA.
        Le operazioni di scrittura richiedono confirm=true."""
        base = SETTINGS_PATHS[resource]
        if action != "create" and not item_id:
            raise ValueError("item_id obbligatorio per get/update/delete.")
        if action == "get":
            return await c.get(f"{base}/{item_id}")
        if not confirm:
            return not_confirmed(f"{action} {resource} {item_id or ''}".strip(), dati=data)
        rt.cache.clear()  # le tabelle info cambiano
        if action == "create":
            return await c.request("POST", base, json_body=wrap(data or {}))
        if action == "update":
            return await c.request("PUT", f"{base}/{item_id}", json_body=wrap(data or {}))
        await c.request("DELETE", f"{base}/{item_id}")
        return {"eseguito": True, "eliminato": item_id}

    @reg.tool(toolset="settings", title="Modello documento")
    async def fic_get_template(template_id: Annotated[int, Field(description="ID modello.")]) -> dict[str, Any]:
        """Dettaglio di un modello di documento dell'azienda."""
        return await c.get(f"/settings/templates/{template_id}")

    @reg.tool(toolset="issued", title="Email inviate")
    async def fic_list_emails(
        page: Annotated[int, Field(ge=1)] = 1, per_page: PerPage = 20,
        q: Annotated[str | None, Field(description="Filtro FIC (es. \"status = 'sent'\").")] = None,
    ) -> dict[str, Any]:
        """Storico delle email inviate da Fatture in Cloud (documenti, solleciti) con stato di consegna."""
        return await c.get("/emails", page=page, per_page=per_page, q=q)

    # ------------------------------------------------------------- webhook
    @reg.tool(toolset="webhooks", title="Webhook")
    async def fic_list_webhooks() -> dict[str, Any]:
        """Elenca le sottoscrizioni webhook dell'azienda."""
        return await c.get("/subscriptions")

    @reg.tool(toolset="webhooks", write=True, title="Gestisci webhook")
    async def fic_manage_webhook(
        action: Annotated[Literal["get", "create", "update", "delete", "verify"], Field(description="Operazione.")],
        subscription_id: Annotated[str | None, Field(description="ID sottoscrizione (tranne create).")] = None,
        data: Annotated[dict[str, Any] | None, Field(description=(
            "Per create/update: {sink (URL https), types ['it.fattureincloud.webhooks.issued_documents.invoices.create', ...], "
            "config {mapping: 'binary'|'structured'}}. Per verify: {verification_code}."))] = None,
        confirm: Confirm = False,
    ) -> dict[str, Any]:
        """Crea, modifica, verifica o elimina una sottoscrizione webhook (scritture con confirm=true)."""
        base = "/subscriptions"
        if action != "create" and not subscription_id:
            raise ValueError("subscription_id obbligatorio.")
        if action == "get":
            return await c.get(f"{base}/{subscription_id}")
        if not confirm:
            return not_confirmed(f"{action} webhook {subscription_id or ''}".strip(), dati=data)
        if action == "create":
            return await c.request("POST", base, json_body=wrap(data or {}))
        if action == "update":
            return await c.request("PUT", f"{base}/{subscription_id}", json_body=wrap(data or {}))
        if action == "verify":
            return await c.request("POST", f"{base}/{subscription_id}/verify", json_body=wrap(data or {}))
        await c.request("DELETE", f"{base}/{subscription_id}")
        return {"eseguito": True, "eliminato": subscription_id}

    # ---------------------------------------------------------------- raw
    @reg.tool(toolset="raw", write=True, destructive=True, idempotent=False, title="Chiamata API generica")
    async def fic_api_request(
        method: Annotated[Literal["GET", "POST", "PUT", "DELETE"], Field(description="Metodo HTTP.")],
        path: Annotated[str, Field(description="Percorso relativo all'azienda, es. '/issued_documents/123' "
                                               "(il prefisso /c/{company_id} viene aggiunto). Per percorsi "
                                               "globali (/user/..., /info/countries) usa company_scoped=false.")],
        query: Annotated[dict[str, Any] | None, Field(description="Parametri di query.")] = None,
        body: Annotated[dict[str, Any] | None, Field(description="Corpo JSON completo (incluso l'involucro 'data').")] = None,
        company_scoped: bool = True,
        confirm: Confirm = False,
    ) -> dict[str, Any]:
        """Chiama qualsiasi endpoint dell'API v2 non coperto dagli altri tool. Le richieste diverse da GET
        richiedono confirm=true. Usalo solo se nessun tool specifico fa al caso."""
        if not path.startswith("/") or ".." in path or "://" in path:
            raise ValueError("path deve essere un percorso relativo che inizia con '/'.")
        if path.startswith("/c/"):
            raise ValueError("Non includere /c/{company_id}: viene aggiunto in automatico.")
        if method != "GET" and rt.settings.read_only:
            raise ValueError("Server in sola lettura: sono ammesse solo richieste GET.")
        if method != "GET" and not confirm:
            return not_confirmed(f"{method} {path}", query=query, body=body)
        result = await c.request(method, path, params=query, json_body=body, company=company_scoped)
        return result if isinstance(result, dict) else {"eseguito": True, "risposta": result}
