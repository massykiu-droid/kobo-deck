# kobo-deck

Genera ogni 15 minuti due immagini 758×1024 in scala di grigi, pubblicate su GitHub Pages per essere mostrate su un Kobo Glo:

- `deck.png`: meteo (Open-Meteo) e produzione fotovoltaica (FusionSolar Kiosk)
- `solar.png`: schermata dedicata al fotovoltaico (produzione di oggi, curva con confronto con ieri, ultimi 14 giorni, mese/anno/totale, dati ambientali). Il Kiosk non fornisce il consumo né lo storico: lo storico (`pv-history.json`) viene raccolto a ogni esecuzione e pubblicato su Pages; se non si riesce a leggerlo la pubblicazione viene annullata, per non perderlo
- `stocks.png`: andamento dei titoli (Yahoo Finance), variazione rispetto al giorno precedente. È disegnata in orizzontale e ruotata di 90° per stare nello schermo verticale: si legge ruotando il Kobo (con `STOCKS_ROTATE=270` si ruota nel verso opposto)

Per il client trmnl-kobo ci sono quattro indirizzi: `api/display` (alterna le due schermate a ogni quarto d'ora), `api-meteo/display` (solo meteo e fotovoltaico) e `api-titoli/display` (solo titoli) e `api-fotovoltaico/display` (solo fotovoltaico).

- `render.py`: scarica i dati e disegna le immagini. L'elenco dei titoli è la lista `STOCKS` in cima alla sezione "titoli"
- `.github/workflows/deck.yml`: esecuzione programmata e pubblicazione
- Secret richiesto: `KIOSK_URL` (URL Kiosk FusionSolar)
