# ista Online (DK) — Home Assistant integration

> ⚠️ **Beta / work in progress.** Denne integration er under udvikling og er
> kun testet i begrænset omfang. Den scraper en uofficiel ASP.NET-side, som
> kan ændre sig uden varsel. Forvent bugs, breaking changes og manglende
> features — brug på eget ansvar, og opret gerne et issue hvis noget fejler.

Henter dit **varmeforbrug** (HCA-enheder) fra [istaonline.dk](https://www.istaonline.dk)
ind i Home Assistant. Home Assistant har ingen "varme"-enhedstype, så forbruget
indlæses som **gas**, så det kan vises i Energi-dashboardet.

Data hentes via ista's CSV-eksport og lægges direkte i Home Assistants
**langtidsstatistik** på de rigtige aflæsningsdatoer (også historik bagud).

## Funktioner

- Login med dine istaonline.dk-oplysninger (brugernavn, adgangskode, `cons_id`).
- Eksterne statistikker:
  - `ista_online:total_energy` — samlet forbrug (vises som gas/m³ til Energi-dashboardet).
  - `ista_online:total_cost` — samlet omkostning i DKK, beregnet med korrekt pris pr. varmeår.
  - `ista_online:meter_<id>_energy` — ét pr. HCA-måler, navngivet efter rummet.
- Pris pr. enhed indtastes ved opsætning; priser for tidligere/kommende **varmeår**
  (1. maj–30. april) styres via integrationens **Konfigurer**-menu, så historisk
  omkostning bliver korrekt.
- Diagnostiske sensorer: seneste aflæsningsdato og samlet forbrug.

## Installation (HACS)

1. HACS → **Integrations** → tre-prikker-menu → **Custom repositories**.
2. Tilføj `https://github.com/vondk/ista_hacs` med kategori **Integration**.
3. Installér **ista Online (DK)** og genstart Home Assistant.
4. **Indstillinger → Enheder & tjenester → Tilføj integration → ista Online (DK)**.

## Opsætning

| Felt | Beskrivelse |
| --- | --- |
| Brugernavn / Adgangskode | Dine istaonline.dk-oplysninger |
| Forbruger-id (`cons_id`) | Findes i din istaonline-konto/URL |
| Målertype | Normalt `HCA` |
| Pris pr. enhed | DKK pr. enhed for indeværende varmeår |

## Energi-dashboard

**Indstillinger → Energi → Gasforbrug → Tilføj gaskilde** og vælg statistikken
`ista Online samlet forbrug`. HCA-tallene vises da som m³. Tilføj eventuelt en fast
pris for at få omkostning i dashboardet — eller brug `ista_online:total_cost`, som
allerede regner med den korrekte pris pr. varmeår.

## Priser pr. varmeår

Gå til integrationens **Konfigurer** for at tilføje/rette prisen for et varmeår.
Angiv startåret (fx `2024` for 1. maj 2024 – 30. april 2025) og prisen. Omkostnings-
statistikken genberegnes automatisk.

## Bemærk

Integrationen scraper ista's ASP.NET-side (uofficielt API). Ændrer ista deres
loginside eller eksport, kan det kræve en opdatering.
