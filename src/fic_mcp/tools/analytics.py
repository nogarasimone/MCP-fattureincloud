"""Analisi calcolate lato server: il modello riceve numeri già sommati, non migliaia di righe.

Tutte le cifre sono STIME gestionali basate sui dati registrati in Fatture in Cloud: non
sostituiscono la liquidazione IVA o i conteggi del commercialista.
"""


import asyncio
from collections import defaultdict
from datetime import date
from typing import Annotated, Any

from pydantic import Field

from ..registry import Registrar
from ._common import quote_value, r2

Year = Annotated[int, Field(ge=2000, le=2100, description="Anno, es. 2026.")]

DOC_FIELDS = "detailed"


def _year_q(year: int) -> str:
    return f"date >= {quote_value(f'{year}-01-01')} and date <= {quote_value(f'{year}-12-31')}"


def _month(d: str | None) -> str:
    return (d or "")[:7]


def register(reg: Registrar) -> None:
    rt = reg.rt
    c = rt.client

    async def issued(doc_type: str, q: str) -> list[dict[str, Any]]:
        return await c.list_all("/issued_documents", {"type": doc_type, "q": q, "fieldset": DOC_FIELDS},
                                max_items=5000)

    async def received(doc_type: str, q: str) -> list[dict[str, Any]]:
        return await c.list_all("/received_documents", {"type": doc_type, "q": q, "fieldset": DOC_FIELDS},
                                max_items=5000)

    @reg.tool(toolset="analytics", title="Fatturato e incassi")
    async def fic_revenue_summary(
        year: Year,
        set_aside_pct: Annotated[float | None, Field(ge=0, le=100, description=(
            "% dell'imponibile incassato da accantonare per imposte e contributi (oltre al 100% dell'IVA). "
            "Default da FIC_SET_ASIDE_PCT."))] = None,
    ) -> dict[str, Any]:
        """Fatturato dell'anno per mese (competenza: data documento, fatture meno note di credito) e incassi
        per mese (cassa: data di pagamento), con IVA, rivalsa, ritenute, da incassare, primi clienti e
        accantonamento: su ogni incasso resta da parte il 100% dell'IVA più la % scelta dell'imponibile;
        il resto è il disponibile."""
        pct = rt.settings.set_aside_pct if set_aside_pct is None else set_aside_pct
        q = _year_q(year)
        invoices, credit_notes = await asyncio.gather(issued("invoice", q), issued("credit_note", q))

        months: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        by_client: dict[str, float] = defaultdict(float)
        for sign, docs in ((1, invoices), (-1, credit_notes)):
            for d in docs:
                m = months[_month(d.get("date"))]
                net = r2(d.get("amount_net"))
                m["imponibile"] += sign * net
                m["rivalsa_cassa"] += sign * (r2(d.get("amount_rivalsa")) + r2(d.get("amount_cassa")))
                m["iva"] += sign * r2(d.get("amount_vat"))
                m["ritenute"] += sign * r2(d.get("amount_withholding_tax"))
                m["totale_documenti"] += sign * r2(d.get("amount_gross"))
                by_client[(d.get("entity") or {}).get("name") or "n/d"] += sign * net

        # Vista di cassa: pagamenti segnati come pagati nell'anno (anche di fatture dell'anno prima).
        prev_invoices = await issued("invoice", _year_q(year - 1))
        cash: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        open_items = 0.0
        for d in invoices + prev_invoices:
            due_total = r2(d.get("amount_due")) or sum(r2(p.get("amount")) for p in d.get("payments_list") or [])
            taxable = r2(d.get("amount_net")) + r2(d.get("amount_rivalsa")) + r2(d.get("amount_cassa"))
            for p in d.get("payments_list") or []:
                amount = r2(p.get("amount"))
                if p.get("status") == "paid":
                    paid = p.get("paid_date") or p.get("due_date") or ""
                    if not paid.startswith(str(year)):
                        continue
                    share = amount / due_total if due_total else 0
                    vat = r2(d.get("amount_vat")) * share
                    tax_base = taxable * share
                    cm = cash[_month(paid)]
                    cm["incassato"] += amount
                    cm["iva_incassata"] += vat
                    cm["imponibile_incassato"] += tax_base
                    cm["accantonamento"] += vat + tax_base * pct / 100
                elif p.get("status") == "not_paid":
                    open_items += amount

        def rounded(table: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
            return {k: {kk: r2(vv) for kk, vv in v.items()} for k, v in sorted(table.items()) if k}

        comp = rounded(months)
        cassa = rounded(cash)
        for v in cassa.values():
            v["disponibile"] = r2(v["incassato"] - v["accantonamento"])
        tot = lambda table, key: r2(sum(v.get(key, 0) for v in table.values()))  # noqa: E731
        return {
            "anno": year,
            "competenza_per_mese": comp,
            "totali_competenza": {k: tot(comp, k) for k in ("imponibile", "rivalsa_cassa", "iva", "ritenute",
                                                             "totale_documenti")},
            "cassa_per_mese": cassa,
            "totali_cassa": {k: tot(cassa, k) for k in ("incassato", "iva_incassata", "imponibile_incassato",
                                                        "accantonamento", "disponibile")},
            "da_incassare_fatture_anno_e_precedente": r2(open_items),
            "primi_clienti_per_imponibile": [
                {"cliente": k, "imponibile": r2(v)}
                for k, v in sorted(by_client.items(), key=lambda kv: -kv[1])[:10]
            ],
            "documenti": {"fatture": len(invoices), "note_di_credito": len(credit_notes)},
            "regola_accantonamento": f"100% IVA + {pct:g}% dell'imponibile incassato",
            "natura_dati": "STIMA gestionale da Fatture in Cloud; le ritenute subite sono acconti d'imposta "
                           "e riducono quanto dovuto a saldo.",
        }

    @reg.tool(toolset="analytics", title="Crediti da incassare")
    async def fic_receivables(
        as_of: Annotated[str | None, Field(description="Data di riferimento AAAA-MM-GG (default oggi).",
                                           pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
        years_back: Annotated[int, Field(ge=0, le=5, description="Anni precedenti da includere.")] = 1,
    ) -> dict[str, Any]:
        """Scadenzario crediti: rate di fatture non incassate, divise in non scadute e scadute per fascia
        (1-30, 31-60, 61-90, oltre 90 giorni), con l'elenco delle posizioni più vecchie."""
        ref = date.fromisoformat(as_of) if as_of else date.today()
        q = (f"date >= {quote_value(f'{ref.year - years_back}-01-01')} and "
             f"date <= {quote_value(ref.isoformat())}")
        invoices = await issued("invoice", q)
        buckets = {"non_scaduto": 0.0, "1_30": 0.0, "31_60": 0.0, "61_90": 0.0, "oltre_90": 0.0}
        rows = []
        for d in invoices:
            for p in d.get("payments_list") or []:
                if p.get("status") != "not_paid":
                    continue
                amount = r2(p.get("amount"))
                due = date.fromisoformat(p["due_date"]) if p.get("due_date") else ref
                late = (ref - due).days
                key = ("non_scaduto" if late <= 0 else "1_30" if late <= 30 else "31_60" if late <= 60
                       else "61_90" if late <= 90 else "oltre_90")
                buckets[key] += amount
                rows.append({"documento_id": d.get("id"), "numero": d.get("number"), "data": d.get("date"),
                             "cliente": (d.get("entity") or {}).get("name"), "scadenza": p.get("due_date"),
                             "importo": amount, "giorni_ritardo": max(0, late)})
        rows.sort(key=lambda r: -r["giorni_ritardo"])
        return {"al": ref.isoformat(), "totale_da_incassare": r2(sum(buckets.values())),
                "fasce": {k: r2(v) for k, v in buckets.items()}, "posizioni": rows[:50],
                "posizioni_totali": len(rows)}

    @reg.tool(toolset="analytics", title="Spese per categoria")
    async def fic_expenses_summary(year: Year) -> dict[str, Any]:
        """Spese registrate nell'anno (spese meno note di credito ricevute) per mese e per categoria, con
        IVA, quota pagata e da pagare, e i principali fornitori."""
        q = _year_q(year)
        expenses, credits = await asyncio.gather(received("expense", q), received("passive_credit_note", q))
        months: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        categories: dict[str, float] = defaultdict(float)
        suppliers: dict[str, float] = defaultdict(float)
        paid = unpaid = 0.0
        for sign, docs in ((1, expenses), (-1, credits)):
            for d in docs:
                m = months[_month(d.get("date"))]
                m["imponibile"] += sign * r2(d.get("amount_net"))
                m["iva"] += sign * r2(d.get("amount_vat"))
                m["totale"] += sign * r2(d.get("amount_gross"))
                categories[d.get("category") or "senza categoria"] += sign * r2(d.get("amount_net"))
                suppliers[(d.get("entity") or {}).get("name") or "n/d"] += sign * r2(d.get("amount_gross"))
                for p in d.get("payments_list") or []:
                    if p.get("status") == "paid":
                        paid += sign * r2(p.get("amount"))
                    else:
                        unpaid += sign * r2(p.get("amount"))
        return {
            "anno": year,
            "per_mese": {k: {kk: r2(vv) for kk, vv in v.items()} for k, v in sorted(months.items()) if k},
            "per_categoria_imponibile": {k: r2(v) for k, v in sorted(categories.items(), key=lambda kv: -kv[1])},
            "primi_fornitori_totale": [{"fornitore": k, "totale": r2(v)}
                                       for k, v in sorted(suppliers.items(), key=lambda kv: -kv[1])[:10]],
            "pagato": r2(paid), "da_pagare": r2(unpaid),
            "documenti": {"spese": len(expenses), "note_di_credito": len(credits)},
        }

    @reg.tool(toolset="analytics", title="Stima IVA del periodo")
    async def fic_vat_estimate(
        date_from: Annotated[str, Field(description="Inizio periodo AAAA-MM-GG.", pattern=r"^\d{4}-\d{2}-\d{2}$")],
        date_to: Annotated[str, Field(description="Fine periodo AAAA-MM-GG.", pattern=r"^\d{4}-\d{2}-\d{2}$")],
    ) -> dict[str, Any]:
        """Stima dell'IVA del periodo: IVA sulle vendite (fatture − note di credito, per data documento)
        meno IVA detraibile sugli acquisti (spese − note di credito ricevute, applicando la % di
        detraibilità registrata). È una STIMA: non gestisce split payment, reverse charge, esigibilità
        differita, date di registrazione diverse né eventuali crediti precedenti."""
        q = f"date >= {quote_value(date_from)} and date <= {quote_value(date_to)}"
        inv, cn, exp, pcn = await asyncio.gather(issued("invoice", q), issued("credit_note", q),
                                                 received("expense", q), received("passive_credit_note", q))
        sales_vat = sum(r2(d.get("amount_vat")) for d in inv) - sum(r2(d.get("amount_vat")) for d in cn)

        def deductible(d: dict[str, Any]) -> float:
            pct = d.get("vat_deductibility")
            pct = 100.0 if pct in (None, "") else float(pct)
            return r2(d.get("amount_vat")) * pct / 100

        purchase_vat = sum(deductible(d) for d in exp) - sum(deductible(d) for d in pcn)
        balance = sales_vat - purchase_vat
        return {
            "periodo": {"dal": date_from, "al": date_to},
            "iva_vendite": r2(sales_vat), "iva_acquisti_detraibile": r2(purchase_vat),
            "saldo": r2(balance), "esito": "a debito" if balance > 0 else "a credito",
            "documenti": {"fatture": len(inv), "note_credito_emesse": len(cn), "spese": len(exp),
                          "note_credito_ricevute": len(pcn)},
            "natura_dati": "STIMA: verificare con la liquidazione del commercialista.",
        }
