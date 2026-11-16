"""Test end-to-end: client MCP in-process -> server -> finta API FIC."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx2 as httpx
import pytest
from mcp import Client

from fic_mcp.config import ALL_TOOLSETS, Settings
from fic_mcp.privacy import Tokenizer
from fic_mcp.server import build_server

from mock_fic import MockFic


def make(tmp_path: Path, **overrides):
    mock = MockFic()
    settings = Settings(access_token="test-token", audit_log=tmp_path / "audit.jsonl",
                        token_file=tmp_path / "tokens.json", toolsets=ALL_TOOLSETS, max_retries=2)
    for k, v in overrides.items():
        setattr(settings, k, v)
    server, reg = build_server(settings, transport=httpx.MockTransport(mock))
    return server, reg, mock, settings


def payload(result) -> dict:
    sc = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if sc:
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    text = "".join(getattr(c, "text", "") for c in result.content)
    try:
        return json.loads(text)
    except ValueError:
        return {"_text": text}


def is_error(result) -> bool:
    return bool(getattr(result, "is_error", None) or getattr(result, "isError", None))


def run(coro):
    return asyncio.run(coro)


def call(server, name, args=None):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, args or {})
    return run(go())


def test_tools_listed_with_annotations(tmp_path):
    server, reg, _, _ = make(tmp_path)

    async def go():
        async with Client(server) as client:
            return await client.list_tools()
    tools = {t.name: t for t in run(go()).tools}
    assert len(tools) == len(reg.registered) >= 90
    send = tools["fic_send_e_invoice"]
    assert send.annotations.destructive_hint is True or send.annotations.destructiveHint is True
    assert "confirm" in json.dumps(send.input_schema if hasattr(send, "input_schema") else send.inputSchema)


def test_read_only_hides_writes(tmp_path):
    _, reg, _, _ = make(tmp_path, read_only=True)
    names = {t.name for t in reg.registered}
    assert "fic_list_clients" in names
    assert not {"fic_create_invoice", "fic_send_e_invoice", "fic_delete_client"} & names


def test_whoami_and_auto_company(tmp_path):
    server, _, mock, _ = make(tmp_path)
    data = payload(call(server, "fic_whoami"))
    assert data["azienda_selezionata"] == 1001
    assert data["aziende"][0]["nome"] == "Intarmour di Test"


def test_list_clients_search_builds_query(tmp_path):
    server, _, mock, _ = make(tmp_path)
    data = payload(call(server, "fic_list_clients", {"search": "Ross'i"}))
    assert data["data"][0]["name"] == "Rossi Consulting Srl"
    q = [c for c in mock.calls if c[1].endswith("/entities/clients")][0][2]["q"]
    assert q == "(name contains 'Ross i')"  # apice neutralizzato


def test_validation_error_is_readable(tmp_path):
    server, _, _, _ = make(tmp_path)
    res = call(server, "fic_create_client", {"data": {"vat_number": "123"}})
    assert is_error(res)
    text = "".join(c.text for c in res.content)
    assert "422" in text and "obbligatorio" in text


def test_retry_on_429(tmp_path):
    server, _, mock, _ = make(tmp_path)
    data = payload(call(server, "fic_info", {"kind": "vat_types"}))
    assert len(data["data"]) == 4
    assert sum(1 for c in mock.calls if c[1].endswith("/info/vat_types")) == 2


def test_create_invoice_dry_run_then_create(tmp_path):
    server, _, mock, settings = make(tmp_path)
    args = {"client_id": 10, "doc_date": "2026-11-15", "due_days": 30, "rivalsa_pct": 4, "withholding_pct": 20,
            "lines": [{"name": "Consulenza cybersecurity", "qty": 20, "net_price": 300, "vat_rate": 22, "measure": "gg"}]}
    preview = payload(call(server, "fic_create_invoice", args))
    assert preview["eseguito"] is False
    a = preview["anteprima"]
    assert a["imponibile"] == 6000 and a["rivalsa"] == 240 and a["iva"] == 1372.8
    assert a["ritenuta"] == 1200 and a["netto_a_pagare"] == 6412.8 and a["scadenza"] == "2026-12-15"
    assert not mock.created

    done = payload(call(server, "fic_create_invoice", {**args, "dry_run": False}))
    assert done["eseguito"] is True and done["id"] == 555
    doc = mock.created[0]
    assert doc["e_invoice"] is True and doc["ei_data"]["payment_method"] == "MP05"
    assert doc["items_list"][0]["vat"] == {"id": 0}
    assert doc["payments_list"][0]["amount"] == 6412.8
    assert doc["entity"]["ei_code"] == "M5UXCR1"
    audit = [json.loads(line) for line in settings.audit_log.read_text().splitlines()]
    assert audit[-1]["tool"] == "fic_create_invoice" and audit[-1]["result_id"] == 555


def test_zero_rate_requires_vat_id(tmp_path):
    server, _, _, _ = make(tmp_path)
    res = call(server, "fic_create_invoice", {"client_id": 10, "lines": [{"name": "Servizio UE", "net_price": 100, "vat_rate": 0}]})
    assert is_error(res)
    assert "N2.1" in "".join(c.text for c in res.content)


def test_send_e_invoice_requires_confirm(tmp_path):
    server, _, mock, _ = make(tmp_path)
    preview = payload(call(server, "fic_send_e_invoice", {"doc_id": 555}))
    assert preview["eseguito"] is False and preview["controlli"]["esito"] == "pronta per l'invio"
    assert mock.sent == [{"options": {"dry_run": True}}]
    done = payload(call(server, "fic_send_e_invoice", {"doc_id": 555, "confirm": True}))
    assert done["eseguito"] is True
    assert mock.sent[-1] == {}


def test_delete_requires_confirm(tmp_path):
    server, _, mock, _ = make(tmp_path)
    preview = payload(call(server, "fic_delete_client", {"entity_id": 10}))
    assert preview["eseguito"] is False
    assert not any(c[0] == "DELETE" for c in mock.calls)
    payload(call(server, "fic_delete_client", {"entity_id": 10, "confirm": True}))
    assert any(c[0] == "DELETE" for c in mock.calls)


def test_revenue_summary_with_pagination(tmp_path):
    server, _, mock, _ = make(tmp_path)
    data = payload(call(server, "fic_revenue_summary", {"year": 2026}))
    tc = data["totali_competenza"]
    assert tc["imponibile"] == 10 * 6000 - 1000
    assert data["documenti"]["fatture"] == 10  # due pagine lette
    cassa = data["totali_cassa"]
    # 8 fatture pagate: incasso netto = 6000*1.22 - 1200 = 6120 ciascuna
    assert cassa["incassato"] == 8 * 6120
    assert cassa["iva_incassata"] == 8 * 1320
    assert cassa["accantonamento"] == round(8 * (1320 + 6000 * 0.48), 2)
    assert data["da_incassare_fatture_anno_e_precedente"] == 2 * 6120


def test_receivables_aging(tmp_path):
    server, _, _, _ = make(tmp_path)
    data = payload(call(server, "fic_receivables", {"as_of": "2026-11-15"}))
    assert data["fasce"]["1_30"] == 6120 and data["fasce"]["31_60"] == 6120
    assert data["posizioni"][0]["giorni_ritardo"] == 48


def test_vat_estimate(tmp_path):
    server, _, _, _ = make(tmp_path)
    data = payload(call(server, "fic_vat_estimate", {"date_from": "2026-01-01", "date_to": "2026-12-31"}))
    assert data["iva_vendite"] == 10 * 1320 - 220
    assert data["iva_acquisti_detraibile"] == 66


def test_privacy_tokenize_roundtrip(tmp_path):
    server, _, mock, _ = make(tmp_path, privacy_mode="tokenize")

    async def go():
        async with Client(server) as client:
            listed = await client.call_tool("fic_list_clients", {})
            data = payload(listed)
            row = data["data"][0]
            # Il modello non vede i dati personali...
            dumped = json.dumps(data)
            for secret in ("Rossi Consulting", "01234567890", "rossi@pec.it", "Via Roma", "amministrazione@rossi.it"):
                assert secret not in dumped
            assert row["name"].startswith("[[NOME_") and row["vat_number"].startswith("[[PIVA_")
            # ...ma i campi tecnici restano leggibili
            assert row["address_city"] == "Como" and row["default_payment_method"]["name"] == "Bonifico bancario"
            # e può usare il segnaposto come argomento
            await client.call_tool("fic_list_clients", {"vat_number": row["vat_number"]})
            unknown = await client.call_tool("fic_list_clients", {"search": "[[NOME_99]]"})
            return unknown
    unknown = run(go())
    last_q = [c for c in mock.calls if c[1].endswith("/entities/clients")][-1][2]["q"]
    assert last_q == "(vat_number = '01234567890')"
    assert is_error(unknown)


def test_privacy_tokenize_analytics(tmp_path):
    server, _, _, _ = make(tmp_path, privacy_mode="tokenize")
    for name, args in (("fic_receivables", {"as_of": "2026-11-15"}), ("fic_revenue_summary", {"year": 2026}),
                       ("fic_expenses_summary", {"year": 2026})):
        dumped = json.dumps(payload(call(server, name, args)))
        assert "Rossi Consulting" not in dumped, name
    row = payload(call(server, "fic_receivables", {"as_of": "2026-11-15"}))["posizioni"][0]
    assert row["cliente"].startswith("[[NOME_")


def test_tokenizer_free_text():
    t = Tokenizer()
    out = t.mask({"items_list": [{"name": "Consulenza", "description": "Rif. mario.rossi@gmail.com IBAN IT60X0542811101000000123456"}]})
    desc = out["items_list"][0]["description"]
    assert "gmail" not in desc and "IT60X" not in desc
    assert out["items_list"][0]["name"] == "Consulenza"
    assert t.unmask(desc) == "Rif. mario.rossi@gmail.com IBAN IT60X0542811101000000123456"


def test_quota_headers_tracked(tmp_path):
    server, _, _, _ = make(tmp_path)

    async def go():
        async with Client(server) as client:
            await client.call_tool("fic_company_info", {})
            return await client.call_tool("fic_server_status", {})
    data = payload(run(go()))
    assert data["quote_api"]["orarie_rimanenti"] == 990


def test_token_refresh_on_401(tmp_path):
    mock = MockFic()
    state = {"refreshed": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            body = json.loads(request.content)
            assert body["grant_type"] == "refresh_token" and body["refresh_token"] == "r1"
            state["refreshed"] = True
            return httpx.Response(200, json={"access_token": "test-token", "refresh_token": "r2", "expires_in": 86400})
        return mock(request)

    settings = Settings(access_token="old-token", refresh_token="r1", client_id="cid", client_secret="sec",
                        audit_log=None, token_file=tmp_path / "tokens.json")
    server, _ = build_server(settings, transport=httpx.MockTransport(handler))
    data = payload(call(server, "fic_company_info"))
    assert state["refreshed"] and data["data"]["name"] == "Intarmour di Test"
    saved = json.loads((tmp_path / "tokens.json").read_text())
    assert saved["refresh_token"] == "r2"
    assert oct((tmp_path / "tokens.json").stat().st_mode)[-3:] == "600"


def test_raw_request_guard(tmp_path):
    server, _, mock, _ = make(tmp_path)
    res = call(server, "fic_api_request", {"method": "GET", "path": "/c/1/x"})
    assert is_error(res)
    preview = payload(call(server, "fic_api_request", {"method": "DELETE", "path": "/products/1"}))
    assert preview["eseguito"] is False
