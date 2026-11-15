"""Finta API Fatture in Cloud per i test (httpx2.MockTransport)."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs

import httpx2 as httpx

CID = 1001


def _json(status: int, body: object | None = None, headers: dict | None = None) -> httpx.Response:
    h = {"Content-Type": "application/json", "RateLimit-HourlyRemaining": "990", "RateLimit-HourlyLimit": "1000",
         "RateLimit-MonthlyRemaining": "39000", "RateLimit-MonthlyLimit": "40000"}
    h.update(headers or {})
    return httpx.Response(status, headers=h, content=b"" if body is None else json.dumps(body).encode())


CLIENT = {"id": 10, "name": "Rossi Consulting Srl", "type": "company", "vat_number": "01234567890",
          "tax_code": "01234567890", "address_street": "Via Roma 1", "address_postal_code": "22100",
          "address_city": "Como", "address_province": "CO", "country": "Italia",
          "email": "amministrazione@rossi.it", "certified_email": "rossi@pec.it", "ei_code": "M5UXCR1",
          "default_payment_method": {"id": 3, "name": "Bonifico bancario"}}

VAT_TYPES = [
    {"id": 0, "value": 22, "description": "Aliquota ordinaria", "is_disabled": False, "ei_type": None},
    {"id": 1, "value": 10, "description": "Ridotta", "is_disabled": False},
    {"id": 6, "value": 0, "description": "Esente art. 10", "is_disabled": False, "ei_type": "N4"},
    {"id": 7, "value": 0, "description": "Non sogg. art. 7-ter", "is_disabled": False, "ei_type": "N2.1"},
]


def _invoice(i: int, date: str, net: float, paid: bool, paid_date: str | None = None, entity=None) -> dict:
    vat = round(net * 0.22, 2)
    gross = net + vat
    wht = round(net * 0.20, 2)
    due = gross - wht
    return {"id": i, "type": "invoice", "number": i, "date": date, "amount_net": net, "amount_vat": vat,
            "amount_gross": gross, "amount_withholding_tax": wht, "amount_rivalsa": 0, "amount_cassa": 0,
            "amount_due": due, "entity": entity or {"id": 10, "name": "Rossi Consulting Srl", "vat_number": "01234567890"},
            "payments_list": [{"amount": due, "due_date": date[:8] + "28",
                               "status": "paid" if paid else "not_paid", "paid_date": paid_date}]}


class MockFic:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, object]] = []
        self.fail_429_once = {"/c/1001/info/vat_types"}
        self.expired_token = False
        self.invoices = [_invoice(100 + n, f"2026-{n:02d}-01", 6000.0, paid=n < 9, paid_date=f"2026-{n:02d}-20")
                         for n in range(1, 11)]
        self.credit_notes = [{"id": 900, "type": "credit_note", "date": "2026-03-15", "amount_net": 1000,
                              "amount_vat": 220, "amount_gross": 1220, "amount_withholding_tax": 200,
                              "entity": {"id": 10, "name": "Rossi Consulting Srl"}, "payments_list": []}]
        self.expenses = [{"id": 700 + n, "type": "expense", "date": f"2026-{n:02d}-10", "category": "Software",
                          "amount_net": 100.0, "amount_vat": 22.0, "amount_gross": 122.0, "vat_deductibility": 100,
                          "entity": {"name": "Anthropic"},
                          "payments_list": [{"amount": 122.0, "status": "paid"}]} for n in range(1, 4)]
        self.created: list[dict] = []
        self.sent: list[dict] = []

    # -------------------------------------------------------------- handler
    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = {k: v[0] for k, v in parse_qs(request.url.query.decode() if isinstance(request.url.query, bytes)
                                               else str(request.url.query)).items()}
        body = None
        if request.content and request.headers.get("content-type", "").startswith("application/json"):
            body = json.loads(request.content)
        self.calls.append((request.method, path, params, body))

        if request.headers.get("authorization") != "Bearer test-token" and path != "/oauth/token":
            return _json(401, {"error": {"message": "Unauthenticated"}})
        if path in self.fail_429_once:
            self.fail_429_once.discard(path)
            return _json(429, {"error": {"message": "Too Many Requests"}}, {"Retry-After": "1"})

        m, p = request.method, path
        if p == "/user/info":
            return _json(200, {"data": {"id": 1, "name": "Simone Test", "email": "x@y.it"}})
        if p == "/user/companies":
            return _json(200, {"data": {"companies": [{"id": CID, "name": "Intarmour di Test", "type": "company"}]}})
        pre = f"/c/{CID}"
        if not p.startswith(pre):
            return _json(404, {"error": {"message": "Not found"}})
        p = p[len(pre):]

        if p == "/company/info":
            return _json(200, {"data": {"id": CID, "name": "Intarmour di Test"}})
        if p == "/entities/clients" and m == "GET":
            return _json(200, {"current_page": 1, "last_page": 1, "per_page": 20, "total": 1, "data": [CLIENT]})
        if p == "/entities/clients/10" and m == "GET":
            return _json(200, {"data": CLIENT})
        if p == "/entities/clients" and m == "POST":
            if not (body or {}).get("data", {}).get("name"):
                return _json(422, {"error": {"message": "Invalid request.",
                                             "validation_result": {"name": ["Il campo nome è obbligatorio."]}}})
            return _json(200, {"data": {"id": 11, **body["data"]}})
        if p == "/entities/clients/10" and m == "DELETE":
            return _json(200)
        if p == "/info/vat_types":
            return _json(200, {"data": VAT_TYPES})
        if p == "/issued_documents/totals" and m == "POST":
            d = body["data"]
            net = sum(i["qty"] * i["net_price"] for i in d["items_list"])
            riv = round(net * d.get("rivalsa", 0) / 100, 2)
            vat = round((net + riv) * 0.22, 2)
            wht = round(net * d.get("withholding_tax", 0) / 100, 2)
            gross = net + riv + vat
            return _json(200, {"data": {"amount_net": net, "amount_rivalsa": riv, "amount_cassa": 0,
                                        "amount_vat": vat, "amount_gross": gross,
                                        "amount_withholding_tax": wht, "amount_due": round(gross - wht, 2)}})
        if p == "/issued_documents" and m == "POST":
            self.created.append(body["data"])
            return _json(200, {"data": {"id": 555, "number": 42, "url": "https://fic/pdf/555", **body["data"]}})
        if p == "/issued_documents" and m == "GET":
            docs = self.invoices if params.get("type") == "invoice" else (
                self.credit_notes if params.get("type") == "credit_note" else [])
            q = params.get("q", "")
            lo = re.search(r"date >= '([\d-]+)'", q)
            hi = re.search(r"date <= '([\d-]+)'", q)
            docs = [d for d in docs if (not lo or d["date"] >= lo.group(1)) and (not hi or d["date"] <= hi.group(1))]
            per = int(params.get("per_page", 20))
            page = int(params.get("page", 1))
            if per == 100:  # simula due pagine da 6 e 4
                per = 6
            chunk = docs[(page - 1) * per: page * per]
            last = max(1, -(-len(docs) // per))
            return _json(200, {"current_page": page, "last_page": last, "per_page": per, "total": len(docs),
                               "data": chunk})
        if p == "/issued_documents/555" and m == "GET":
            return _json(200, {"data": {"id": 555, "type": "invoice", "number": 42, "date": "2026-11-15",
                                        "e_invoice": True, "ei_status": "attempt", "amount_gross": 6100,
                                        "entity": {"name": "Rossi Consulting Srl", "vat_number": "01234567890"}}})
        if p == "/issued_documents/555/e_invoice/xml_verify":
            return _json(200, {"data": {"success": True}})
        if p == "/issued_documents/555/e_invoice/send":
            self.sent.append(body or {})
            return _json(200, {"data": {"name": "IT01234567890_00001.xml", "date": "2026-11-15"}})
        if p == "/issued_documents/555/e_invoice/xml":
            return httpx.Response(200, headers={"Content-Type": "application/xml"}, content=b"<FatturaElettronica/>")
        if p == "/received_documents" and m == "GET":
            docs = self.expenses if params.get("type") == "expense" else []
            return _json(200, {"current_page": 1, "last_page": 1, "data": docs})
        return _json(404, {"error": {"message": f"Mock: endpoint non gestito {m} {p}"}})
