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
- **Valgfri historik-dybde ved opsætning**: du får ista's egen liste over perioder
  for netop dit login og vælger hvor langt tilbage der skal importeres.
- Eksterne statistikker:
  - `ista_online:total_energy` — samlet forbrug (vises som gas/m³ til Energi-dashboardet).
  - `ista_online:total_cost` — samlet omkostning i DKK, beregnet med korrekt pris pr. varmeår.
  - `ista_online:meter_<id>_energy` og `ista_online:meter_<id>_cost` — ét sæt pr. HCA-måler.
- **Én enhed pr. måler** i Home Assistant med forbrug, omkostning og seneste
  aflæsningsdato, så du kan følge hvert rum for sig.
- Pris pr. enhed angives for hvert importeret **varmeår** (1. maj–30. april), så
  historisk omkostning bliver korrekt. Prisen er den samme for alle målere.
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
| Forbruger-id (`cons_id`) | Findes automatisk efter login; kan indtastes manuelt |
| Målertype | Normalt `HCA` |
| Start fra periode | Hvor langt tilbage der importeres — vælg den ældste for at hente alt |
| Pris pr. varmeår | DKK pr. enhed for hvert importeret varmeår (lad stå på 0 hvis ukendt) |

Kan periodelisten ikke læses fra ista, springes valget over, og eksporten bruger
ista's egen standardperiode som før.

## Energi-dashboard

**Indstillinger → Energi → Gasforbrug → Tilføj gaskilde** og vælg statistikken
`ista Online samlet forbrug`. HCA-tallene vises da som m³. Tilføj eventuelt en fast
pris for at få omkostning i dashboardet — eller brug `ista_online:total_cost`, som
allerede regner med den korrekte pris pr. varmeår.

## Konfigurer

Integrationens **Konfigurer**-menu har tre punkter:

- **Priser pr. varmeår** — tilføj/ret prisen for et varmeår. Angiv startåret
  (fx `2024` for 1. maj 2024 – 30. april 2025) og prisen. Omkostningsstatistikken
  genberegnes automatisk.
- **Navngiv målere** — giv hver måler et navn. Navnet bruges både til målerens
  enhed og til dens statistikker. Uden et navn bruges rummet fra ista's CSV, og
  målernummeret sættes på hvis to målere deler rumnavn.
- **Hent mere historik** — vælg en tidligere startperiode. Allerede hentede data
  bevares; der lægges kun til.

## Bemærk

Integrationen scraper ista's ASP.NET-side (uofficielt API). Ændrer ista deres
loginside eller eksport, kan det kræve en opdatering.
