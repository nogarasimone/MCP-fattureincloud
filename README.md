# fic-mcp — server MCP per Fatture in Cloud

Server MCP (Model Context Protocol) che dà a Claude, o a qualsiasi client MCP, accesso completo
all'API v2 di Fatture in Cloud: anagrafiche, prodotti, documenti emessi, fattura elettronica/SdI,
spese e fatture passive, F24, prima nota, corrispettivi, archivio, impostazioni, webhook, più
analisi da CFO calcolate dal server.

- **93 tool** organizzati in 15 gruppi attivabili (75 nel set di default, 41 in sola lettura).
- **Sicurezza per costruzione**: modalità sola lettura, `confirm=true` obbligatorio per
  eliminazioni, invii SdI ed email al cliente, `dry_run` di default sulla fattura guidata,
  registro delle scritture.
- **Privacy**: tokenizzazione reversibile dei dati personali (`FIC_PRIVACY_MODE=tokenize`).
- **Limiti API rispettati**: retry su 429 con `Retry-After` e backoff, rinnovo automatico del
  token OAuth2, quote residue lette dagli header senza consumare richieste.
- Trasporti **stdio** (Claude Desktop, Claude Code) e **Streamable HTTP** con bearer token.

## Installazione

Serve Python ≥ 3.11.

```bash
git clone https://github.com/nogarasimone/MCP-fattureincloud.git
cd MCP-fattureincloud
uv tool install .            # oppure: pipx install .   oppure: pip install .
fic-mcp --version
```

L'unica dipendenza è `mcp>=2.3` (SDK ufficiale; porta con sé httpx2, pydantic, starlette, uvicorn).

## Accesso a Fatture in Cloud

**Opzione A – token manuale (uso personale).** In Fatture in Cloud: *Impostazioni → Applicazioni
collegate → Sviluppatori*, crea un'app privata, scegli i permessi e genera il **token manuale**.

```bash
mkdir -p ~/.config/fic-mcp
cp .env.example ~/.config/fic-mcp/.env   # oppure .env nella cartella da cui lanci il server
# imposta FIC_ACCESS_TOKEN=...
fic-mcp doctor                           # verifica token, aziende, quote
```

**Opzione B – OAuth2 (app pubblica / SaaS).** Registra l'app con Redirect URL
`http://localhost:8765/callback`, imposta `FIC_CLIENT_ID` e `FIC_CLIENT_SECRET`, poi:

```bash
fic-mcp auth              # apre il browser, salva i token in ~/.config/fic-mcp/tokens.json (permessi 600)
fic-mcp auth --read-only  # chiede solo gli scope di lettura
```

L'access token dura 24 ore e viene rinnovato da solo con il refresh token (valido un anno).

Se il token gestisce più aziende imposta `FIC_COMPANY_ID` oppure usa il tool `fic_select_company`.

## Collegarlo a Claude

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json` su macOS):

```json
{
  "mcpServers": {
    "fatture-in-cloud": {
      "command": "/Users/<utente>/.local/bin/fic-mcp",
      "env": {
        "FIC_ACCESS_TOKEN": "a/xxxxxxxx",
        "FIC_READ_ONLY": "true",
        "FIC_PRIVACY_MODE": "off"
      }
    }
  }
}
```

Usa il percorso assoluto restituito da `which fic-mcp`. Riavvia Claude Desktop dopo la modifica.

**Claude Code**:

```bash
claude mcp add fatture-in-cloud -e FIC_ACCESS_TOKEN=a/xxxx -e FIC_READ_ONLY=true -- fic-mcp
```

**Remoto (Streamable HTTP)**, per un gateway o un client web:

```bash
FIC_MCP_HTTP_TOKEN=$(openssl rand -hex 32) fic-mcp serve --transport http --host 0.0.0.0 --port 8000
# endpoint: http://host:8000/mcp  —  header: Authorization: Bearer <FIC_MCP_HTTP_TOKEN>
```

In produzione mettilo dietro HTTPS (reverse proxy) e non esporlo senza bearer.

## Configurazione

| Variabile | Default | Significato |
|---|---|---|
| `FIC_ACCESS_TOKEN` | — | Token manuale o access token OAuth2 |
| `FIC_CLIENT_ID` / `FIC_CLIENT_SECRET` | — | Per `fic-mcp auth` e il rinnovo automatico |
| `FIC_COMPANY_ID` | auto | Obbligatoria solo con più aziende |
| `FIC_READ_ONLY` | `false` | `true` nasconde tutti i tool che scrivono |
| `FIC_PRIVACY_MODE` | `off` | `tokenize` sostituisce i dati personali con segnaposto |
| `FIC_TOOLSETS` | `default` | `default`, `all` o elenco di gruppi |
| `FIC_SET_ASIDE_PCT` | `48` | % dell'imponibile incassato da accantonare, oltre all'IVA |
| `FIC_AUDIT_LOG` | `~/.local/state/fic-mcp/audit.jsonl` | Registro scritture, `off` per disattivarlo |
| `FIC_MCP_HTTP_TOKEN` | — | Bearer obbligatorio in modalità HTTP |

`fic-mcp tools` mostra i tool che verrebbero esposti con la configurazione corrente
(`R` lettura, `W` scrittura, `W!` distruttiva o verso terzi).

## Gruppi di tool

| Gruppo | Default | Contenuto |
|---|---|---|
| `account` | ✓ | utente, aziende, selezione azienda, piano, stato server e quote |
| `entities` | ✓ | clienti e fornitori: elenco con ricerca, dettaglio, crea, modifica, elimina |
| `products` | ✓ | prodotti/servizi e listini |
| `issued` | ✓ | fatture, NC, preventivi, proforma, ordini, DDT, rapportini, autofatture: CRUD, totali, trasforma, unisci, email, allegati, cestino, **fattura guidata**, storico email |
| `einvoice` | ✓ | verifica XML, scarica XML, invio SdI con dry run, motivo dello scarto |
| `received` | ✓ | spese, NC passive, DDT ricevuti, **fatture SdI da registrare**, allegati, cestino |
| `taxes` | ✓ | F24 e allegati |
| `cashbook` | ✓ | prima nota |
| `info` | ✓ | aliquote IVA/nature, conti, metodi di pagamento, categorie, centri di costo, profilo fiscale, paesi, valute, unità di misura… (in cache) |
| `analytics` | ✓ | fatturato e incassi, crediti per anzianità, spese per categoria, stima IVA |
| `receipts` | | corrispettivi |
| `archive` | | archivio documenti |
| `settings` | | conti, metodi di pagamento, aliquote IVA (CRUD), modelli |
| `webhooks` | | sottoscrizioni webhook |
| `raw` | | `fic_api_request`: qualsiasi endpoint v2 (scritture con conferma) |

## Modello di sicurezza

1. **Sola lettura** (`FIC_READ_ONLY=true`): i tool di scrittura non vengono nemmeno registrati.
   Il modo giusto per iniziare.
2. **Conferma esplicita**: eliminazioni, invio allo SdI, email ai clienti, chiamate raw non-GET
   e modifiche alle impostazioni senza `confirm=true` restituiscono solo l'anteprima. Le istruzioni
   del server dicono al modello di chiedere il via libera all'utente prima di confermare.
3. **Invio SdI in due passi**: senza conferma, `fic_send_e_invoice` esegue la verifica dell'XML e
   un invio di prova (`dry_run`) e mostra l'esito; l'invio reale parte solo con `confirm=true`.
4. **Fattura guidata**: `fic_create_invoice` lavora con `dry_run=true` di default (calcola i
   totali con FIC e mostra l'anteprima senza creare nulla).
5. **Nessuna ripetizione di scritture ambigue**: su timeout o errori di rete una POST/PUT non viene
   ripetuta; il messaggio dice di verificare in FIC prima di riprovare.
6. **Registro scritture** in JSONL (tool, ID, esito, orario): niente payload, quindi niente dati
   personali nel log.
7. **Annotazioni MCP** (`readOnlyHint`, `destructiveHint`, `idempotentHint`) su ogni tool, così il
   client può chiedere conferma in base al rischio.
8. Token salvati con permessi 600; i valori del filtro `q` costruiti dai tool vengono ripuliti
   dagli apici.

## Privacy: tokenizzazione

Con `FIC_PRIVACY_MODE=tokenize` il modello riceve segnaposto stabili al posto di nomi e ragioni
sociali delle anagrafiche, P.IVA, codici fiscali, email, PEC, telefoni, indirizzi e IBAN, ad
esempio `[[NOME_3]]` o `[[PIVA_1]]`. Quando il modello riusa un segnaposto negli argomenti di un
tool, il server lo sostituisce con il valore reale prima di chiamare Fatture in Cloud: la ricerca
`fic_list_clients(vat_number="[[PIVA_1]]")` funziona. Un segnaposto sconosciuto genera un errore,
non una chiamata con dati inventati.

È **pseudonimizzazione** (art. 4(5) GDPR), non anonimizzazione. Limiti da dichiarare:

- importi, date, numeri documento, città e descrizioni delle righe restano in chiaro;
- nei testi liberi (descrizioni, note) si intercettano solo i pattern: email, IBAN, CF, P.IVA e
  cellulari. Un nome di persona scritto a mano in una descrizione passa;
- la tabella di corrispondenza vive solo in memoria nel processo del server e si azzera al riavvio;
- con la privacy attiva l'XML della fattura elettronica non viene restituito al modello (solo
  `save_to` su disco).

## Analisi da CFO

- `fic_revenue_summary(year)`: competenza per mese (fatture meno note di credito: imponibile,
  rivalsa/cassa, IVA, ritenute) e **cassa** per mese (pagamenti segnati come pagati, anche su fatture
  dell'anno prima) con accantonamento = 100% dell'IVA incassata + `FIC_SET_ASIDE_PCT`% dell'imponibile
  incassato, e il disponibile che resta. In più: da incassare e primi 10 clienti.
- `fic_receivables()`: scadenzario crediti per fasce di ritardo (1–30, 31–60, 61–90, >90 giorni).
- `fic_expenses_summary(year)`: spese per mese, categoria e fornitore; pagato e da pagare.
- `fic_vat_estimate(dal, al)`: IVA vendite − IVA acquisti detraibile. **Stima**: non gestisce
  split payment, reverse charge, esigibilità differita né crediti precedenti.

I conteggi si fanno in Python sul server: il modello riceve totali, non migliaia di righe.
Sono stime gestionali; i numeri fiscali li valida il commercialista.

## Esempi di richieste

- «Quanto ho fatturato e incassato nel 2026, mese per mese? Quanto devo tenere da parte?»
- «Chi mi deve ancora pagare e da quanto?»
- «Prepara la fattura di novembre per Rossi Consulting: 10 giornate a 500 €, IVA 22%, rivalsa INPS 4%,
  ritenuta 20%, scadenza 30 giorni.» → anteprima, poi creazione dopo il tuo OK.
- «Ci sono fatture passive arrivate dallo SdI da registrare?»
- «Verifica e invia allo SdI la fattura 555.» → controlli e dry run, poi invio dopo conferma.

## Sviluppo e test

```bash
pip install -e ".[dev]"
pytest
```

I 18 test girano end-to-end (client MCP in-process → server → finta API FIC): registrazione e
annotazioni dei tool, sola lettura, retry su 429, rinnovo del token su 401, errori 422 leggibili,
fattura guidata (rivalsa, ritenuta, scadenza, risoluzione aliquote), invio SdI in due passi,
conferme sulle eliminazioni, paginazione, analisi, tokenizzazione andata/ritorno, guardie del tool raw.

Struttura: `client.py` (HTTP, retry, quote, refresh), `auth.py` (OAuth2), `privacy.py`
(tokenizzazione), `audit.py`, `registry.py` (registrazione e filtri dei tool), `tools/` (un modulo
per area), `server.py`, `__main__.py` (CLI).

## Da verificare al primo collegamento reale

Il server è testato contro una simulazione dell'API costruita sulla documentazione ufficiale.
Prima dell'uso in scrittura, in sola lettura:

1. `fic-mcp doctor` e `fic_whoami`;
2. filtri per cliente/fornitore (`entity.id = …` nel parametro `q`): se FIC li rifiuta, l'errore
   lo dice e si usa `q` con `entity.name`;
3. `fic_create_invoice` con `dry_run=true` su un caso reale: confronta i totali con quelli che
   vedi in FIC;
4. una prima fattura vera creata in bozza e controllata a video prima di `fic_send_e_invoice`.

## Licenza

Rilasciato con licenza [MIT](LICENSE).
