# kobo-deck

Genera ogni 15 minuti un'immagine 758×1024 in scala di grigi con meteo (Open-Meteo) e produzione fotovoltaica (FusionSolar Kiosk), pubblicata su GitHub Pages come `deck.png` per essere mostrata su un Kobo Glo.

- `render.py`: scarica i dati e disegna l'immagine
- `.github/workflows/deck.yml`: esecuzione programmata e pubblicazione
- Secret richiesto: `KIOSK_URL` (URL Kiosk FusionSolar)
