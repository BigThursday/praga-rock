# Praga Rock

Tutti i concerti rock, metal e punk a Praga in una pagina, con un messaggio Telegram quando ne esce uno nuovo.

Ogni 3 ore GitHub Actions lancia `update.py`, che:

1. legge le fonti (GoOut, festivaly.eu, Songkick, Obscure Promotion, Futurum, Lucerna Music Bar, Rock Café, Klub 007, Modrá Vopice, kdykam);
2. unisce i doppioni: stessa data e stessa band, oppure stesso locale e stessa ora;
3. tiene il rock, in base ai generi delle fonti. Se una fonte non li dà, cerca la band su MusicBrainz e Apple Music;
4. aggiorna `docs/concerts.json`, che la pagina `docs/index.html` legge;
5. manda su Telegram i concerti mai visti prima e li segna in `data/state.json`.

Il primo giro non manda nulla: segna tutto come già visto. Lo stesso vale per una fonte che risponde per la prima volta.

## Cose da sapere

- **Una fonte smette di rispondere.** I suoi concerti restano in pagina. Dopo un giorno intero arriva un avviso su Telegram e il piè di pagina la segna in rosso.
- **Il bot Telegram smette di consegnare.** I concerti nuovi restano da mandare, il giro su GitHub risulta fallito e GitHub manda una mail.
- **Indie e alternative.** Stanno in pagina dietro il filtro "Indie/Alt", spento di default, e non arrivano su Telegram.

## Comandi

```bash
python update.py --no-send --preview preview.html
```

```bash
python setup_telegram.py
```

Il primo fa un giro di prova senza mandare messaggi e senza toccare lo stato. Il secondo collega il bot: si lancia una volta sola.

## Dove si cambia cosa

- Frequenza dei controlli: `cron` in `.github/workflows/aggiorna.yml`.
- Nuova fonte: una funzione `fetch_*` in `update.py` e una riga in `SOURCES`.
- Un locale compare con due nomi: una riga in `VENUES`.
- Generi considerati rock: `buckets()`.
