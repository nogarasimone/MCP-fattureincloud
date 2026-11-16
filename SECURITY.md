# Sicurezza

fic-mcp accede ai dati contabili e fiscali di un'azienda: le vulnerabilità vanno segnalate in
privato.

## Come segnalare

Usa la segnalazione privata di GitHub:
[**Report a vulnerability**](https://github.com/nogarasimone/MCP-fattureincloud/security/advisories/new)
(scheda *Security* del repository). Non aprire issue pubbliche e non includere token o dati reali.

Indica, se puoi: versione, trasporto (stdio o HTTP), passi per riprodurre e impatto.
Riceverai una prima risposta entro 7 giorni; la correzione viene pubblicata prima della
divulgazione, concordando i tempi con chi ha segnalato.

## Versioni supportate

Le correzioni di sicurezza escono solo sull'ultima versione pubblicata.

## Cosa rientra

- esposizione di token (FIC, OAuth, bearer HTTP) o di dati personali nonostante
  `FIC_PRIVACY_MODE=tokenize`;
- scritture su Fatture in Cloud senza `confirm=true` o con `FIC_READ_ONLY=true`;
- aggiramento del bearer in modalità HTTP.

Non rientrano: problemi dell'API o dell'account Fatture in Cloud, configurazioni che espongono il
server senza HTTPS o con un bearer debole.
