"""Tipi e helper condivisi dai tool."""


import base64
import mimetypes
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

Page = Annotated[int, Field(ge=1, description="Pagina da leggere (da 1).")]
PerPage = Annotated[int, Field(ge=5, le=100, description="Elementi per pagina (da 5 a 100, limiti dell'API).")]
Fieldset = Annotated[Literal["basic", "detailed"] | None,
                     Field(description="'basic' = campi essenziali (default), 'detailed' = oggetto completo.")]
Fields = Annotated[str | None, Field(description="Elenco di campi separati da virgola da restituire, es. 'id,name,vat_number'.")]
Sort = Annotated[str | None, Field(description="Campi di ordinamento separati da virgola; '-' davanti per decrescente, es. '-date'.")]
Query = Annotated[str | None, Field(description=(
    "Filtro FIC in sintassi SQL-like: campo op valore. Operatori = <> != > >= < <=, like, contains, "
    "starts with, ends with, is null, is not null; and/or e parentesi; stringhe tra apici singoli. "
    "Es.: \"date >= '2026-01-01' and amount_gross > 1000\", \"name contains 'Rossi'\"."))]
DateStr = Annotated[str | None, Field(description="Data nel formato AAAA-MM-GG.", pattern=r"^\d{4}-\d{2}-\d{2}$")]
Confirm = Annotated[bool, Field(description=(
    "Deve essere true per eseguire davvero l'operazione. Con false il tool restituisce solo "
    "l'anteprima di cosa farebbe: chiedi conferma esplicita all'utente prima di passare true."))]
Data = Annotated[dict[str, Any], Field(description="Oggetto risorsa nel formato API v2 di Fatture in Cloud (senza l'involucro 'data').")]

ISSUED_TYPES = ("invoice", "credit_note", "quote", "proforma", "receipt", "delivery_note", "order",
                "work_report", "supplier_order", "self_own_invoice", "self_supplier_invoice")
IssuedType = Annotated[Literal["invoice", "credit_note", "quote", "proforma", "receipt", "delivery_note",
                               "order", "work_report", "supplier_order", "self_own_invoice",
                               "self_supplier_invoice"],
                       Field(description="Tipo di documento emesso: invoice=fattura, credit_note=nota di credito, "
                                         "quote=preventivo, proforma, receipt=ricevuta, delivery_note=DDT, "
                                         "order=ordine, work_report=rapportino, supplier_order=ordine a fornitore, "
                                         "self_own_invoice=autofattura per autoconsumo, "
                                         "self_supplier_invoice=autofattura (es. reverse charge)." )]
ReceivedType = Annotated[Literal["expense", "passive_credit_note", "passive_delivery_note", "self_invoice"],
                         Field(description="expense=spesa/fattura passiva, passive_credit_note=nota di credito "
                                           "ricevuta, passive_delivery_note=DDT ricevuto, self_invoice=autofattura.")]

_SAFE_Q_VALUE = re.compile(r"^[^'\\]*$")


def list_params(*, q: str | None = None, fields: str | None = None, fieldset: str | None = None,
                sort: str | None = None, page: int = 1, per_page: int = 20, **extra: Any) -> dict[str, Any]:
    params = {"q": q, "fields": fields, "fieldset": fieldset, "sort": sort, "page": page, "per_page": per_page}
    params.update(extra)
    return params


def quote_value(value: str) -> str:
    """Rende sicuro un valore per il filtro q (niente apici: blocca l'iniezione nel filtro)."""
    if not _SAFE_Q_VALUE.match(value):
        value = value.replace("\\", "").replace("'", " ")
    return f"'{value}'"


def combine_q(*parts: str | None) -> str | None:
    clean = [f"({p})" for p in parts if p]
    return " and ".join(clean) if clean else None


def date_range_q(date_from: str | None, date_to: str | None, field: str = "date") -> str | None:
    parts = []
    if date_from:
        parts.append(f"{field} >= {quote_value(date_from)}")
    if date_to:
        parts.append(f"{field} <= {quote_value(date_to)}")
    return " and ".join(parts) or None


def wrap(data: dict[str, Any]) -> dict[str, Any]:
    """Le API v2 vogliono il corpo dentro 'data'. Accetta anche un oggetto già avvolto."""
    if set(data.keys()) == {"data"} and isinstance(data["data"], dict):
        return data
    return {"data": data}


def not_confirmed(action: str, **preview: Any) -> dict[str, Any]:
    return {"eseguito": False,
            "messaggio": f"Operazione NON eseguita: {action}. Per procedere richiama il tool con confirm=true "
                         "dopo aver avuto il via libera esplicito dell'utente.",
            **preview}


def attachment_files(file_path: str | None, content_base64: str | None,
                     filename: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prepara multipart per gli upload di allegati (percorso locale o base64)."""
    if file_path:
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"File non trovato: {path}")
        content = path.read_bytes()
        name = filename or path.name
    elif content_base64:
        content = base64.b64decode(content_base64)
        if not filename:
            raise ValueError("Con content_base64 serve anche filename.")
        name = filename
    else:
        raise ValueError("Indica file_path oppure content_base64.")
    allowed = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".xls", ".xlsx", ".doc", ".docx"}
    if Path(name).suffix.lower() not in allowed:
        raise ValueError(f"Estensione non ammessa da Fatture in Cloud: {Path(name).suffix}. Ammesse: {sorted(allowed)}")
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return {"attachment": (name, content, mime)}, {"filename": name}


def r2(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0
