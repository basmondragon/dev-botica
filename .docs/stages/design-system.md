---
type: design-system
doc: botica-v1-design-system
company: "[[particula-tech]]"
product: "Botica"
market: "Colombia"
captured: "2026-09-01"
status: authoritative
source: "../handoff/README.md; ../handoff/Botica - Pantallas.dc.html; ../handoff/Botica - Pantallas 2.dc.html; [[botica-v1-architecture]]"
---

# Botica — Design System

The visual and interaction authority for every Botica surface. Two parts, kept separate on purpose:

- **Part A — What the handoff fixes.** Values transcribed from `../handoff/`: the README's summary, checked line by line against the two `.dc.html` prototypes, with everything the README rounds off or omits recovered from the markup. Part A is a record. Where it and a stage document disagree, Part A wins, because Part A is what was drawn.
- **Part B — The app component layer.** Everything a four-screen marketing-grade handoff does not contain and a multi-tenant operating platform needs: a closed type scale, elevation, density modes, form controls, status semantics against the real domain enums, the shell's role gating, the sync and staleness conventions, the counter density mode, loading/empty/error, keyboard, and motion. Every value in Part B either comes from Part A or is derived from it and says so.

**Section numbers are a public contract.** Eleven stage documents cite this file by number. `../architecture.md` §5 already cites **§B.9** for the words the interface uses when a local figure may be behind the server. Renumber nothing. Add subsections; never renumber a section that exists.

**Where this document's authority comes from.** `../architecture.md` §1 states that the handoff's _"tokens, densities and component specifications are transcribed into `stages/design-system.md`, which governs every surface, including the ones the handoff does not draw."_ That is the mandate. On any visual or interaction question the architecture does not settle, this document governs. On any question the architecture does settle — the stack (§9), the grid contract (§9), tenancy and roles (§2), the offline contract (§5), the assistant's safety rails (§7), the fiscal state machine (§8) — the architecture wins and this document conforms to it.

## Reference standard

**Linear and Notion.** Calm, dense, restrained colour, small consistent radii, fast functional motion, content-first. Colour carries meaning — a stock state, an expiry, a handoff the client's invoicing system refused — and never decoration. Chrome recedes until needed. Information density is high and never cramped. The handoff is already drawn to this standard; Part B extends it rather than reinterpreting it.

**There is no dark theme.** `../architecture.md` §12 lists it under _deliberately not building_, alongside white-label theming and SSO. Do not scaffold a dark token block, do not write a `dark:` variant, and do not build a theme toggle. A dark ramp is a separate project that starts by authoring a dark neutral ramp from zero, and speculative `dark:` variants would be wrong in every one of them.

## Language

Prose in this document is English. **Every interface string is Spanish (Colombia)**, and every string the handoff draws is reproduced here verbatim — including its punctuation, its middle dots and its accents. Strings this document authors for undrawn surfaces are written in the same register: plain, short, second person formal avoided in favour of impersonal constructions, no exclamation marks, no product voice. `../architecture.md` §1 is binding on the code side: identifiers, tables, columns and API paths are English; domain nouns with no honest English equivalent stay Spanish (`sede`, `mostrador`, `lote`, `turno`, `documento equivalente`). Design tokens are therefore named in English.

## What was read, and how

| File                                                                      | What it gave                                                                                                                       |
| ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `../handoff/README.md` (192 lines, read in full)               | The token summary, the four screens' content, the interaction notes, the state-management sketch and the list of open questions    |
| `../handoff/Botica - Pantallas.dc.html` (63KB, read in full)   | Inventario (`#pantalla-inventario`) and Panel (`#pantalla-panel`) — every inline style, recovered                                  |
| `../handoff/Botica - Pantallas 2.dc.html` (41KB, read in full) | Mostrador (`#pantalla-mostrador`) and Compras (`#pantalla-compras`) — every inline style, recovered                                |
| `../architecture.md` (433 lines, read in full)                            | What the product is, the eleven stages, the seven surfaces the handoff never drew, the domain enums Part B's status system maps to |
| `ownership.md`                                                            | The stage boundaries the component layer is built against; §157 defers the Cobertura colour thresholds to this document            |

**The prototypes use inline styles exclusively.** The README says so and it is right: that is a prototyping-tool artefact, not an implementation guide. Nothing in this document tells anyone to ship an inline style. What the markup is good for is exactness — the README rounds, and the markup does not. Everything below marked _recovered_ is a value the markup states and the README does not.

### Findings from that read — these shape Part B

1. **The handoff has four semantic families, and an operating platform needs five.** Positive, warning, informative and critical are drawn (§A.6). There is no treatment for _nothing has happened yet_ — a purchase order nobody has looked at, a fiscal document not yet handed over, a checklist item nobody has answered. §B.7 adds **neutral** and introduces no new colour to do it: the ink label `#727272` on the symptom chip's own fill `#e8e8e8`, both already in the system.
2. **The hollow dot has a rule, and the handoff states it only by example.** Expiry badges draw a ringed dot; every other state draws a filled one. The rule behind that example — _solid means true now, hollow means not yet true or true only under a condition_ — is what makes the treatment extensible to `in_process`, `sent` and `offline` without inventing a colour. §B.7.2.
3. **The blue ramp is not a global threshold function.** The three drawn charts bucket the same ten steps differently: 70% is `#1a7fe5` on a stock bar, 68% is `#4c9bea` on a per-sede bar, 71% is `#7fb9f0` on the histogram. Depth is normalised _within a series_, not against an absolute scale. §A.5 records the three mappings exactly; §B.12 fixes the rule.
4. **The badge label is `#171717` on every tint, and that is the right call.** Measured: a status colour on its own tint runs 4.03:1 (positive), 4.04 (warning), 4.29 (critical), 4.55 (informative) — three of four fail AA for a 12px label. `#171717` on the same four tints runs 14.66:1 to 15.04:1. The handoff already decided this; §B.7.3 states the numbers so nobody re-opens it.
5. **`#727272` fails AA on the chrome plane.** It measures 4.81:1 on `#ffffff` and 4.65:1 on `#fbfbfb` — both fine — and **4.38:1 on `#f4f4f4`**, which is where the table footer, the `thead` and the section-card counter put it. §B.15 fixes it with a value the system already contains and no new token: on `#f4f4f4`, tertiary text steps to `#6b6b6b` (4.85:1).
6. **The prototype's Mostrador screen shows a `cashier` the full seven-item nav.** `../architecture.md` §2 says a `cashier` sees Mostrador and a read-only Inventario. The prototype is wrong and the architecture governs; §B.8.3 states the gating explicitly, because this is exactly the detail a build agent copies from a screenshot.
7. **The handoff already ships the overlay shadow it says it does not have.** `0 1px 2px rgba(20,20,20,0.04), 0 18px 44px rgba(20,20,20,0.08)` is labelled _"solo presentación, no en la app"_ because it dresses the 1600×1000 canvas. It is exactly the shadow a modal and a dropdown need, and §B.2 adopts it as L3 rather than authoring a new one.
8. **There is one editable control in the whole handoff and no form.** The `Sugerido` cell (§A.18.2) is the only input drawn, the search field is a placeholder, and the `Filas 25` select is a static span. Every text input, textarea, select, checkbox, radio, stepper and validation treatment in §B.5 is authored from zero against the drawn geometry.

---

# PART A — WHAT THE HANDOFF FIXES

Every value below was read out of `../handoff/`. Where the README and the markup differ, the markup wins and the difference is noted.

## A.1 Type family

```css
font-family: "Geist", system-ui, sans-serif;
font-family: "Geist Mono", monospace; /* eyebrows only — see A.2 */
-webkit-font-smoothing: antialiased;
```

**Geist** carries the interface at weights **300 / 400 / 500 / 600**; only 400 and 500 appear in the four screens. **Geist Mono** carries eyebrows at **400 / 500**; only 400 appears. Both are SIL OFL and may be self-hosted; the prototype loads them from Google Fonts with the single request `https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600&family=Geist+Mono:wght@400;500&display=swap`.

**Geist Mono is not the code face.** In this system it is the eyebrow face: always 10px, always uppercase, always `letter-spacing: 0.18em`, always `#727272` (see §B.15 for the chrome-plane correction), and it appears in exactly four places in the handoff — table column headers, section-card titles, the sidebar version string, and the acceptance panel's _"Combinaciones más aceptadas"_ heading. If a fifth place is added it obeys the same four properties or it is not this face.

**Titles are weight 400, not 300.** The `h1` is drawn `font-size:28px; letter-spacing:-0.025em; font-weight:400`. This is a real difference from the sibling system and it is deliberate: at 28px on a dense operating surface, 300 goes thin and grey against 14px body text at 400.

## A.2 Type scale, as drawn

Seven steps, with the leading and tracking each is drawn with. _Recovered:_ the 11px step is drawn with two different leadings, and which one applies is decided by whether the line can wrap.

| px     | Leading     | Weight                   | Tracking              | Numerals              | Drawn at                                                                                                                                                                                                                                                                                                |
| ------ | ----------- | ------------------------ | --------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **36** | 40          | 400                      | −0.025em              | tabular               | KPI figure — `$412,8 M`, `24,8%`, `37`, `$18,9 M`, `42`, `$9,4 M`, `11`, `−$2,1 M`                                                                                                                                                                                                                      |
| **28** | — (h1) / 32 | 400                      | −0.025em              | tabular where numeric | Page title `Existencias`; acceptance figure `58,6%` at 28/32; ticket total `$15.600`                                                                                                                                                                                                                    |
| **20** | —           | 400                      | −0.025em              | tabular               | Data subtitle `$13,7 M promedio por día`; order total `$9.412.600` in the table footer                                                                                                                                                                                                                  |
| **14** | 20 / —      | 400, 500 for emphasis    | normal                | tabular where numeric | Body copy at 14/20 (assistant text, suggestion names, ticket line names); table cell at 14 with the row height carrying the leading; transcript box; nav label; editable cell; organisation name at 500                                                                                                 |
| **12** | 18 / 16 / — | 400, **500 on controls** | normal                | tabular where numeric | 12/18 secondary copy (KPI label, assistant secondary line, suggestion context, `Por qué` cells); 12/16 badge label; 12 flat for button and chip labels at 500, breadcrumb, pagination numbers, ticket total rows, sidebar user name, `≥ 22%`                                                            |
| **11** | 16 / 18 / — | 400                      | normal                | tabular where numeric | 11/16 in a fixed-height line (pill without a dot, symptom chip, per-sede row, `3.412 de 5.824 sugerencias`, the combination list); **11/18 when the line wraps** (KPI footnotes); 11 flat for the filter-bar status, the table footer, nav counters, the sidebar role line, the ticket subtext, `Filas` |
| **10** | —           | 400                      | **0.18em, uppercase** | —                     | Geist Mono only: `thead`, section-card titles, the version string                                                                                                                                                                                                                                       |

**Letter-spacing rules.** `−0.025em` on 20, 28 and 36 — every display-weight numeral and every title — and nowhere else. `0.18em` on the 10px mono step and nowhere else. Every other step is `normal`. There is no third tracking value in the system.

**Tabular numerals are not optional.** `font-variant-numeric: tabular-nums` is drawn on every numeric in all four screens without exception: table cells, KPI figures, badge text containing a number (`Sobrestock · 94 días`, `142 lotes`), nav counters, pagination numbers, the row-size select, axis labels, ticket amounts, per-sede values, the acceptance figure and its sub-line, and the lot codes (`A-2291`) and expiry dates (`03/2027`) — which are alphanumeric and still tabular, because they are compared down a column.

## A.3 Neutrals

| Token            | Value     | Role, as drawn                                                                                                                                            |
| ---------------- | --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--canvas`       | `#fbfbfb` | App background; filter-bar fill; the transcript box's fill; the zero-state editable cell's fill                                                           |
| `--surface`      | `#ffffff` | Card and table fill; search-field fill; editable-cell fill; active nav item; active segment                                                               |
| `--chrome`       | `#f4f4f4` | Sidebar; `thead`; section-card headers; table footer; the counter panel's own footer; the segmented control's track; a filter chip's value pill           |
| `--active`       | `#e8e8e8` | Selected table row; the current pagination page; the neutral symptom chip                                                                                 |
| `--ink`          | `#171717` | Primary text; primary button fill; the brand square; the selected row's inset marker; the margin-bar's own fill is _not_ this — see §A.5                  |
| `--ink-body`     | `#555555` | Secondary text: non-first table cells, inactive nav items, inactive chips, the sidebar's icons, the advisory notice                                       |
| `--ink-label`    | `#727272` | Tertiary text: KPI labels, `thead`, breadcrumb, nav counters, the sync line, footers, section-card counters, suggestion context lines, `Por qué` cells    |
| `--ink-note`     | `#6b6b6b` | KPI footnotes and deltas, and the `312 requieren acción` line — one step darker than `--ink-label` and drawn only where the line is a wrapping annotation |
| `--ink-soft`     | `#909090` | Placeholder; the ticket line index; a zero editable value; the margin target marker; the version string; chevrons                                         |
| `--ink-disabled` | `#c8c8c8` | The breadcrumb separator `/`; a disabled pagination arrow; the pagination ellipsis                                                                        |

**`#e9e9e7` is not an app token.** It is the presentation canvas the 1600×1000 screens are laid on. It never appears inside a screen and must not enter the token set.

**Two greys are two greys.** `#727272` and `#6b6b6b` are eleven units apart and both are drawn, in different roles: `#727272` is a label beside its value, `#6b6b6b` is an annotation under it. Keep both; §B.15 gives `#6b6b6b` a second job.

## A.4 Borders

Four alpha-black steps. All four are `rgba(0, 0, 0, α)` — never an opaque grey — so they tint whatever plane they sit on.

| Token           | Value              | Role, as drawn                                                                                                                                                                                                                              |
| --------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--hairline`    | `rgba(0,0,0,0.06)` | Every divider: sidebar right edge, header underline, filter-bar underline, sidebar header and footer rules, table row separators, table footer top rule, card-header underline, the in-card section rules, the suggestion card's own border |
| `--edge-soft`   | `rgba(0,0,0,0.08)` | Card and table frame borders                                                                                                                                                                                                                |
| `--edge`        | `rgba(0,0,0,0.11)` | Input borders; the rule under `thead`; the row-size select; the transcript box                                                                                                                                                              |
| `--edge-strong` | `rgba(0,0,0,0.16)` | Secondary button; active filter chip; the editable cell                                                                                                                                                                                     |

_Recovered:_ the suggestion card inside the Mostrador's section card uses `--hairline`, not `--edge-soft`. That is not an oversight — it is the handoff obeying the rule §B.2 states: a plane nested inside a plane drops a step rather than repeating the frame.

## A.5 The blue accent scale and the data rail

Ten steps plus a rail. Blue is the quantity colour. In the four drawn screens it appears in exactly five places: the stock bar, the margin progress bar, the daily-sales histogram, the per-sede bars, the acceptance donut — and one sixth, the assistant's 26×26 icon tile, which is the only non-quantitative use in the system.

| Token          | Value     |
| -------------- | --------- |
| `--data-100`   | `#0071e3` |
| `--data-90`    | `#1a7fe5` |
| `--data-80`    | `#2683e5` |
| `--data-70`    | `#3389e6` |
| `--data-60`    | `#4c9bea` |
| `--data-50`    | `#5fa8ed` |
| `--data-40`    | `#6cb0ef` |
| `--data-30`    | `#7fb9f0` |
| `--data-20`    | `#87bff1` |
| `--data-10`    | `#9ec9f4` |
| `--data-track` | `#e0eefc` |

**The rail is `#e0eefc` everywhere.** The stock bar's track, the margin bar's track, the per-sede bar's track and the donut's ring are one value.

**Depth is normalised per series, not against a global threshold** — _recovered_, and it is the single most important thing the markup says that the README does not. The three charts map the same ten steps onto three different bucket sets:

| Series                               | Drawn mapping (fill % → step)                                                                                                                                    |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Stock bar** (Inventario)           | 100, 88 → `#0071e3` · 70 → `#1a7fe5` · 62 → `#3389e6` · 58, 54 → `#4c9bea` · 46 → `#5fa8ed` · 38 → `#7fb9f0` · 34 → `#87bff1` · 30, 26, 20, 12, 8, 4 → `#9ec9f4` |
| **Per-sede bar** (Panel)             | 100 → `#0071e3` · 79 → `#2683e5` · 68 → `#4c9bea` · 53 → `#6cb0ef` · 43 → `#87bff1` · 26 → `#9ec9f4`                                                             |
| **Daily histogram** (Panel, 30 bars) | 100 → `#0071e3` · 88, 86 → `#3389e6` · 84 → `#4c9bea` · 81 → `#5fa8ed` · 76, 73, 72, 71 → `#7fb9f0` · ≤ 69 → `#9ec9f4`                                           |

The margin bar uses `#1a7fe5` at a 70% fill; the donut arc uses `#0071e3`. §B.12 turns these three observations into one rule.

_Recovered oddity, recorded and then overruled:_ the stock row at quantity `0` (Ibuprofeno, `Quiebre`) draws a 4% fill at `#9ec9f4` — a visible sliver under a zero. §B.12.3 rules that a zero draws no fill at all.

## A.6 Semantic families, tints and dot treatments

Four families in the handoff. §B.7 adds a fifth without adding a colour.

| Family          | Colour    | Tint      | Drawn on                                                                                                                            |
| --------------- | --------- | --------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **Positive**    | `#4e7a52` | `#e3e9e3` | `Suficiente`, `En meta`, `Primera opción`                                                                                           |
| **Warning**     | `#8c6a33` | `#ece7df` | `Punto de reorden`, `Margen bajo meta`, `142 lotes`, `Con condición`, the advisory-notice dot, the `Cobertura` numeral at 6–18 days |
| **Informative** | `#4c6a86` | `#e3e7eb` | `Sobrestock · 94 días`, the active-treatment chip, `Se lleva junto`, the `Cobertura` numeral at 94 days                             |
| **Critical**    | `#b04a3f` | `#f1e2e1` | `Quiebre · hay 96 en Suba`, the `Cobertura` numeral at 0–4 days, a `Stock` value of `0`                                             |

**The badge label is `#171717` on all four tints, and the dot carries the meaning at full strength.** Measured against each tint: the family colour on its own tint runs 4.03 / 4.04 / 4.29 / 4.55 : 1, so three of four fail AA for a 12px label; `#171717` on the same four runs 14.66 to 15.04 : 1. The dot at full strength runs 4.03 to 4.55 : 1 against its tint, comfortably over the 3:1 floor for a graphical object that carries meaning. This is settled; do not revisit it.

**Dot treatment.** Solid `8×8px`, `border-radius:999px`, `margin-right:7px`, `vertical-align:1px`. The hollow variant is the same box with `border:1px solid <family>`, `background:transparent`, `box-sizing:border-box`. The handoff draws hollow only on expiry — `Vence en 5 meses` (warning tint, warning ring), `Vence en 6 meses` and `Vence en 8 meses` (critical tint, critical ring). The tint escalates with urgency while the ring stays; §B.7.2 states the rule that generalises this.

**The `Cobertura` numeral is coloured by the same four families and carries no dot** — _recovered thresholds_, which `ownership.md` explicitly defers to this document: `0 días`, `3 días`, `4 días` → `#b04a3f` · `6 días`, `15 días`, `18 días` → `#8c6a33` · `30`, `32`, `33`, `46 días` → `#555555` · `94 días` → `#4c6a86`. §B.7.4 fixes the edges.

## A.7 Shadows

| Token              | Value                                                            | Role                                                                                                  |
| ------------------ | ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `--shadow-plane`   | `0 1px 2px rgba(20,20,20,0.02)`                                  | Every card and table frame at rest                                                                    |
| `--shadow-segment` | `0 1px 2px rgba(20,20,20,0.04)`                                  | The active segment of a segmented control                                                             |
| `--shadow-overlay` | `0 1px 2px rgba(20,20,20,0.04), 0 18px 44px rgba(20,20,20,0.08)` | Drawn on the 1600×1000 presentation canvas and labelled _"solo presentación"_. §B.2 promotes it to L3 |

Three shadows, all on a `20, 20, 20` base at 2%, 4% and 8%. Nothing in the system blooms in from `none`: the plane's shadow exists at rest, so a hover changes its border and never introduces a shadow that was not there.

## A.8 Radius scale

| Token              | Value     | Applied to                                                                                                                                                     |
| ------------------ | --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--radius-mark`    | **4px**   | The 24×24 brand square. §B.5.5 gives it a second job                                                                                                           |
| `--radius-icon`    | **6px**   | The assistant's 26×26 icon tile                                                                                                                                |
| `--radius-segment` | **7px**   | A segment inside the segmented control                                                                                                                         |
| `--radius-control` | **9px**   | Buttons, chips, inputs, the search field, nav items, icon buttons, pagination cells, the row-size select, the transcript box, the editable cell. The workhorse |
| `--radius-card`    | **12px**  | KPI card, per-panel card, suggestion row card                                                                                                                  |
| `--radius-panel`   | **16px**  | Section card, table frame, the counter panel                                                                                                                   |
| `--radius-pill`    | **999px** | Badges, chips' value pills, symptom chips, every bar and every dot                                                                                             |

Seven values, and they nest correctly: a 12px suggestion card inside a 16px section card, a 7px segment inside a 9px track, a 999px pill inside a 12px card. Nothing else is in bounds.

## A.9 Spacing scale

A 2/4-based scale: **2 · 4 · 6 · 8 · 10 · 12 · 14 · 16 · 20 · 22 · 28 · 32 · 40 · 48**. Every gap, padding and margin in the four screens is one of these fourteen values. The load-bearing three:

- **40px** — horizontal page inset, on the header, the filter bar and `main`, on all four screens without exception.
- **22px** — table cell padding-x, on `th`, `td` and the table footer, on all three tables.
- **16px** — grid gap, between KPI cards, between panel rows, between the KPI row and the panel row, and the card padding of a KPI tile.

The vertical page inset is **32px** on Panel and Inventario and **28px** on Mostrador and Compras. _Recovered:_ the difference is not noise — the two screens whose `main` is a single full-height working panel take the tighter inset and give the 8px back to the panel. §B.3 states it as a rule.

Other drawn values, for reference: nav list `padding:12px 0`, `gap:2px`, item `margin:0 12px`; sidebar header/footer `padding:0 20px` / `0 16px`; header `gap:16px`, right group `gap:8px` (Panel: `10px`); filter bar `gap:10px`; section-card header `padding:0 20px`, body `padding:16px 20px`; assistant card `padding:20px`, `gap:14px`; suggestion card `padding:14px 16px`, `gap:16px`; ticket body `padding:16px 20px`, `gap:14px`; ticket footer `padding:16px 20px`, `gap:8px`; KPI card `padding:16px`.

## A.10 The height system

| Element                                                            | Value                                                            |
| ------------------------------------------------------------------ | ---------------------------------------------------------------- |
| Sidebar width                                                      | **280px**, `flex-shrink:0`                                       |
| Page header                                                        | **64px**                                                         |
| Sidebar organisation header                                        | **64px**                                                         |
| Sidebar user footer                                                | **64px**                                                         |
| Filter bar                                                         | **52px**                                                         |
| Nav item                                                           | **38px**                                                         |
| Control (button, chip, search field, icon button, segmented track) | **34px**                                                         |
| Table row                                                          | **48px** standard, **44px** compact (the Panel's per-sede table) |
| `thead`                                                            | **40px**                                                         |
| Section-card header                                                | **40px**                                                         |
| Table footer                                                       | **48px**                                                         |
| Counter panel (Mostrador, right column)                            | **380px** wide                                                   |
| Screen canvas                                                      | **1600 × 1000px**                                                |

_Recovered secondary heights, all on the 2/4 scale:_ brand square 24 · segment inside the segmented track 28 · pagination arrow and page cell 28 (page cell `min-width:32px`) · row-size select 28 · a filter chip's value pill 20 · symptom chip 24 · in-row `Agregar` button 30 · `Cobrar` button 40 · badge dot 8 · stock rail 56 × 4 · progress bar 6 · margin target marker 2 wide, `top:-4px; bottom:-4px` · donut 64 (r 28.5, `stroke-width:7`) · assistant icon tile 26 · sparkle icon 15 · search icon 15 · nav and settings icons 16 · chevrons 12 · KPI delta arrow 12 · pagination arrow icons 14 · numeric column `min-width:44px` · per-sede label 76 · per-sede value 46 · ticket line index 20 wide · histogram `min-height:120px`.

## A.11 Numbers, currency and dates — Colombia

This is a token-level rule, not a formatting preference. There is no `i18n` runtime and no second locale (`../architecture.md` §1), so the locale is a constant and every number in the product goes through one formatter module. No call site calls `toLocaleString` on its own.

| Rule                        | Form                                                     | Drawn                                                                    |
| --------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------ |
| Thousands separator         | `.`                                                      | `$15.600`, `$112.480.900`, `4.284`, `3.412`, `5.824`, `1.184`            |
| Decimal separator           | `,`                                                      | `24,8%`, `58,6%`, `6,4%`, `27,5 g`, `38,5 °C`                            |
| Currency                    | `$` prefixed, no space, **no decimals**, no `COP` suffix | `$3.900`, `$41.200`, `$9.412.600`                                        |
| Millions                    | one decimal, a space before `M`, only above 1.000.000    | `$412,8 M`, `$9,4 M`, `$18,9 M`, `112 M`                                 |
| Percent                     | no space before `%`                                      | `24,8%`, `4,6%`                                                          |
| Percentage points           | `pp`, with a space                                       | `+1,9 pp`                                                                |
| Negative                    | **U+2212 MINUS SIGN**, never a hyphen                    | `−$2,1 M`                                                                |
| Presentation and quantity   | **U+00D7**, spaced                                       | `500 mg × 100`, `2 × $3.900`                                             |
| Clause separator in a label | **U+00B7**, spaced                                       | `Sobrestock · 94 días`, `Mostrador · Chapinero`, `1 × $5.200 · sugerido` |
| Range                       | ASCII hyphen, unspaced                                   | `1-15 de 4.284`                                                          |
| Ellipsis                    | **U+2026**                                               | the pagination gap `…`                                                   |
| Lot expiry                  | `MM/AAAA`                                                | `03/2027`, `11/2026`                                                     |
| Time of day                 | 24-hour, `HH:mm`                                         | `09:14`, `hoy 06:00`                                                     |
| Chart axis date             | day + three-letter lowercase month, no period            | `27 jul`, `25 ago`                                                       |
| Units                       | a space before the unit                                  | `27,5 g`, `38,5 °C`, `100 mcg`, `500 ml`                                 |

Two implementation notes. **The `$` is prefixed by our formatter, not by `Intl`'s currency style** — ICU emits a locale- and version-dependent space between symbol and figure for `COP`, and a figure whose spacing changes with a Node upgrade is a figure that breaks a column. Use `Intl.NumberFormat('es-CO')` for the grouping and the decimal only. **The space between a number and its unit or its `M` is a non-breaking space (U+00A0)**, so `$9,4 M` and `hace 4 s` never wrap between the figure and what it means.

`$412,8 M` abbreviates because it is a KPI figure with 36px of type and a card's width. `$112.480.900` in the per-sede table does not, because a table cell is where the exact figure is read. **Abbreviate only in a chart label or a display figure; never in a table cell.** `112 M` in the per-sede bar drops the `$` because the panel title supplies it.

## A.12 Iconography

Stroke SVG on a `24 24` viewBox, `stroke-width:1.5` (`2` on chevrons and the KPI delta arrows, `1.8` on the assistant's sparkle), `stroke-linecap:round`, `stroke-linejoin:round`, `fill:none`, rendered at 12–16px. The set is Lucide-equivalent in style. **Use Lucide; do not copy the prototype's paths.** Drawn: `panel-left-close`, `layout-dashboard`, `package`, `shopping-cart`, `tag`, `message-circle`, `store`, `bar-chart`, `settings`, `search`, `chevron-down`, `chevron-left`, `chevron-right`, `arrow-up`, `arrow-down`, `sparkle`.

Icons inside a nav item, a button or a chip inherit `currentColor`. Icons that are decoration of a fixed colour — the search glass at `#909090`, the sidebar collapse control at `#555555` — state it. There are **no images and no third-party logos** in the system; the brand is the 24×24 `#171717` square carrying a 10px/500 `#fbfbfb` `B`, and the organisation's name beside it.

## A.13 The shell, as drawn

The shell repeats identically inside all four screens: `display:flex`, `overflow:hidden`, `background:#fbfbfb`, `color:#171717`, `font-size:14px`.

### A.13.1 Sidebar

`width:280px`, `flex-shrink:0`, `background:#f4f4f4`, `border-right:1px solid rgba(0,0,0,0.06)`, full height, never scrolls with content.

- **Organisation header** — 64px, `padding:0 20px`, `gap:10px`, `border-bottom` hairline. Brand square 24×24, `--radius-mark`, `#171717`, letter `B` in `#fbfbfb` 10px/500. Organisation name `Droguerías La 45` at 14px/500 `#171717`, `flex:1`. Collapse control: a 16px `panel-left-close` at `#555555`.
- **Nav list** — `flex:1`, `padding:12px 0`, `gap:2px`. Item: `margin:0 12px`, `height:38px`, `padding:0 12px`, `border-radius:9px`, `gap:10px`, 16px icon at `currentColor`, 14px label. Inactive `#555555`, no fill. Active `background:#ffffff`, `color:#171717`, `font-weight:500` — the item lifts to the content plane's colour, the only place white appears inside the chrome plane.
- **Counter** — optional, `margin-left:auto`, 11px, tabular. `#727272` normally; `#171717` at weight 500 when the item is active. Drawn: `Compras 12`, `Mostrador 3`. _Recovered inconsistency:_ on the Compras screen the active Compras item keeps its `12` in ink/500; on the Mostrador screen the active Mostrador item drops its `3` entirely. §B.8.2 resolves it in favour of keeping it.
- **Order** — Panel, Inventario, Compras, Precios, Mostrador, Sedes, Reportes. Flat; no group labels.
- **Version** — at the bottom of the nav block, `padding:0 20px`, `Botica 2.4.1` in Geist Mono 10px, `0.18em`, uppercase, `#909090`.
- **User footer** — 64px, `padding:0 16px`, `gap:4px`, `border-top` hairline. Name 12px `#171717`, role and sede 11px `#727272`. Settings button 34×34, `--radius-control`, 16px icon `#555555`. Drawn: `Marcela Ríos · Administradora` on Panel, Inventario and Compras; `Andrés Peña · Mostrador · Chapinero` on Mostrador.

### A.13.2 Page header

64px, `padding:0 40px`, `gap:16px`, `border-bottom` hairline, `background:rgba(251,251,251,0.85)` — the canvas at 85%, which is the veil a blurred sticky header sits on.

Left: breadcrumb at 12px `#727272` with a `/` separator in `#c8c8c8`, then the `h1` at 28px/400/−0.025em `#171717`, the two aligned on `align-items:baseline` with `gap:8px`. Right: `margin-left:auto`, `gap:8px` (Panel: `10px`), 34px controls. Drawn pairs — `Cargar mercancía` / `Nuevo traslado`; `Descartar` / `Aprobar y enviar`; the period segmented control / `Exportar`; the 11px `Turno abierto 09:14` note / `Buscar producto`.

### A.13.3 Filter bar

52px, `padding:0 40px`, `gap:10px`, `border-bottom` hairline, `background:#fbfbfb`. Drawn on Inventario and Compras only. Left to right: the search field (Inventario only), then filter chips. Right, `margin-left:auto`, an 11px `#727272` provenance line — `Sincronizado hace 4 s` on Inventario, `Modelo entrenado con 18 meses de venta · actualizado hoy 06:00` on Compras.

## A.14 Buttons, as drawn

| Variant       | Geometry                                                                                                    | Fill        | Border                       | Text                |
| ------------- | ----------------------------------------------------------------------------------------------------------- | ----------- | ---------------------------- | ------------------- |
| **Primary**   | `height:34px`, `padding:0 16px`, `--radius-control`, `display:inline-flex`, `align-items:center`, `gap:8px` | `#171717`   | none                         | `#fbfbfb`, 12px/500 |
| **Secondary** | identical                                                                                                   | transparent | `1px solid rgba(0,0,0,0.16)` | `#171717`, 12px/500 |

Both carry `font-family:inherit` — the prototype states it because a bare `<button>` would not inherit Geist. Two size departures are drawn and are deliberate: the in-row `Agregar` is **30px** at `padding:0 12px`, and `Cobrar` is **40px** with **14px** text and no horizontal padding, because it spans the counter panel's footer.

## A.15 Filter chips, search field, segmented control, pagination

### A.15.1 Filter chip

34px, `--radius-control`, 12px/500.

- **Active** (carrying a value): `padding:0 6px 0 14px`, `gap:8px`, `border:1px solid rgba(0,0,0,0.16)`, text `#171717`; the value is a pill — `height:20px`, `padding:0 8px`, `--radius-pill`, `background:#f4f4f4`, 11px/400 `#555555`. Drawn: `Sede · Todas · 6`, `Estado · Requiere acción`, `Proveedor · Coopidrogas`, `Sede · Chapinero`.
- **Inactive**: `padding:0 14px`, `gap:6px`, no border, no fill, text `#555555`. Drawn: `Categoría`, `Vencimiento`, `Confianza del modelo`.

### A.15.2 Search field

34px, `min-width:250px`, `padding:0 10px 0 12px`, `gap:8px`, `--radius-control`, `border:1px solid rgba(0,0,0,0.11)`, `background:#ffffff`. 15px search glass at `#909090`. Placeholder 12px `#909090`: `Buscar producto, laboratorio o lote`.

### A.15.3 Segmented control

Track: 34px, `padding:3px`, `--radius-control`, `background:#f4f4f4`, `gap:2px`. Segment: 28px, `padding:0 14px`, `--radius-segment`. Active: `background:#ffffff`, 12px/500 `#171717`, `box-shadow:0 1px 2px rgba(20,20,20,0.04)`. Inactive: 12px/400 `#555555`. Drawn: `7 días` / **`30 días`** / `90 días`.

### A.15.4 Pagination

In the table footer, right group, `gap:8px`.

- **Row-size select** — `Filas` label at 11px `#727272`, then a 28px control: `padding:0 8px`, `gap:6px`, `--radius-control`, `border:1px solid rgba(0,0,0,0.11)`, `background:#ffffff`, value `#171717` tabular, 12px chevron `#909090` at `stroke-width:2`.
- **Arrows** — 28×28, `--radius-control`, 14px icon at `stroke-width:2`. Disabled `#c8c8c8`; enabled `#555555`.
- **Page cells** — `min-width:32px`, `height:28px`, `padding:0 8px`, `--radius-control`, 12px tabular. Current: `background:#e8e8e8`, `#171717`, weight 500. Others `#555555`.
- **Gap** — `padding:0 6px`, 11px `#c8c8c8`, the character `…`.
- Drawn sequence: `1 · 2 · 3 · … · 172`.

## A.16 The status badge, as drawn

```
display:inline-block; padding:4px 10px; border-radius:999px;
background:<tint>; font-size:12px; line-height:16px; color:#171717; white-space:nowrap
dot: display:inline-block; width:8px; height:8px; border-radius:999px;
     margin-right:7px; vertical-align:1px;
     solid  → background:<family>
     hollow → border:1px solid <family>; background:transparent; box-sizing:border-box
```

**A pill without a dot** is the suggestion-type label: `padding:3px 8px`, `--radius-pill`, 11px/16px, `#171717`, on the family tint — `Primera opción` (positive), `Con condición` (warning), `Se lleva junto` (informative). It carries no dot because the three sit side by side in one list where the tint alone separates them and a dot on each would be three dots in a row saying nothing.

**A symptom chip** is the neutral case: `height:24px`, `padding:4px 10px`, `--radius-pill`, `background:#e8e8e8`, 11px/16px `#555555` — `diarrea`, `fiebre`, `adulto`. The active-treatment chip escalates to the informative tint with `#171717` text: `tratamiento activo · losartán`.

## A.17 The table, as drawn

**Frame** — `--radius-panel`, `border:1px solid rgba(0,0,0,0.08)`, `background:#ffffff`, `box-shadow:0 1px 2px rgba(20,20,20,0.02)`, `overflow:hidden`, and the body region carries its own `overflow:hidden` so the frame clips rather than the page.

**Table** — `width:100%`, `border-collapse:collapse`, `table-layout:fixed`, `text-align:left`. Every column has an explicit percentage width.

**`th`** — 40px, `padding:0 22px`, `background:#f4f4f4`, `border-bottom:1px solid rgba(0,0,0,0.11)`, Geist Mono 10px/400, `letter-spacing:0.18em`, uppercase, `#727272`, `white-space:nowrap`. Numeric columns `text-align:right`.

**`tr`** — 48px standard, 44px on the Panel's per-sede table, `border-bottom:1px solid rgba(0,0,0,0.06)`, and **the last row carries no border**. Selected: `background:#e8e8e8` **and** `box-shadow: inset 2px 0 0 #171717`.

**`td`** — `padding:0 22px`, 14px. First column `#171717`; the rest `#555555`. Numeric columns `text-align:right` with `tabular-nums`. A reason cell (`Por qué`) steps down to 12px `#727272`.

**Footer** — 48px, `padding:0 22px`, `border-top:1px solid rgba(0,0,0,0.06)`, `background:#f4f4f4`, 11px `#727272`. Left, the range and its annotation: `1-15 de 4.284` and, 8px after it at 11px `#6b6b6b`, `312 requieren acción`. Right, either the pagination group (Inventario) or a summary figure (Compras: the label `Total de la orden` at 11px `#727272` and, 10px after it on a shared baseline, `$9.412.600` at 20px/−0.025em `#171717`).

**No zebra striping anywhere.** The hairline separator and the row fill carry the eye.

**Drawn column widths.** Inventario: Producto 24 · Laboratorio 13 · Sede 12 · Lote 9 · Vence 9 · Existencias 13 (right) · Estado 20. Panel per-sede: Sede 22 · Venta 30 d 18 (right) · Margen 14 (right) · Quiebres 14 (right) · Días de stock 16 (right) · Estado 16. Compras: Producto 26 · Stock 11 (right) · Venta / sem 14 (right) · Cobertura 13 (right) · Sugerido 13 (right) · Por qué 23.

## A.18 In-cell controls

### A.18.1 The stock bar

`display:inline-block`, rail **56 × 4px**, `--radius-pill`, `background:#e0eefc`, `vertical-align:3px`; the fill is a block child at a percentage width, `--radius-pill`, coloured by §A.5's stock-bar mapping. To its right, the figure: `display:inline-block`, `min-width:44px`, `text-align:right`, 14px `#555555`, tabular. **The bar never appears without its number.**

### A.18.2 The editable cell

`display:inline-block`, `padding:5px 12px`, `--radius-control`, `border:1px solid rgba(0,0,0,0.16)`, `background:#ffffff`, 14px `#171717`, tabular. **At zero** it recedes: `background:#fbfbfb`, `color:#909090` — drawn on the two lines the model says not to order (`Cobertura suficiente, no pedir`, `Sobrestock, liberar capital`). It is the only editable control in the handoff.

## A.19 Cards, as drawn

### A.19.1 KPI card

`--radius-card`, `border:1px solid rgba(0,0,0,0.08)`, `background:#ffffff`, `padding:16px`, `--shadow-plane`. Label 12px/18px `#727272` — on the Panel with `min-height:32px`, so a two-line label (`Inventario por vencer · 90 días`) and a one-line label align their figures. Figure 36px/40px, `−0.025em`, tabular, `#171717`, at `margin-top:12px` on the Panel and `margin:10px 0 0` on Compras. Footnote 11px/18px `#6b6b6b` at `margin:6px 0 0`.

**The delta is never coloured.** `▲ 6,4%` on rising sales and `▼ 41%` on falling stock-outs are both 11px `#6b6b6b` with a 12px arrow at `stroke-width:2` and `gap:3px`. A direction is not a status, and the arrow says which way it went.

**The reference and progress variant** (Margen bruto): the figure is followed on the baseline by `≥ 22%` at 12px `#727272`; below at `margin-top:14px` a 6px bar, `--radius-pill`, `background:#e0eefc`, with a `70%` fill in `#1a7fe5` and a target marker — `position:absolute`, `width:2px`, `top:-4px`, `bottom:-4px`, `background:#909090`, `left:calc(62% - 1px)`. Footnote at `margin:10px 0 0`.

**The badge variant** (Inventario por vencer): the figure is followed by a full status badge — `142 lotes` on the warning tint.

### A.19.2 Section card

`--radius-panel`, `border:1px solid rgba(0,0,0,0.08)`, `background:#ffffff`, `--shadow-plane`, `overflow:hidden`. Header: 40px, `padding:0 20px`, `background:#f4f4f4`, `border-bottom` hairline, title in Geist Mono 10px/0.18em/uppercase/`#727272`, and optionally a right-aligned counter at 11px `#727272` (`3 de 12 referencias`, `3 ítems`). Body: `padding:16px 20px`.

### A.19.3 Suggestion card

`--radius-card`, `border:1px solid rgba(0,0,0,0.06)`, `padding:14px 16px`, `gap:16px`, no fill of its own and **no shadow** — a plane inside a plane drops a step. Left: the product name at 14px/20px `#171717` with the type pill on the same baseline at `gap:10px`, and the context line under it at `margin:6px 0 0`, 12px/18px `#727272`. Right: the price at 14px `#171717`, tabular, `white-space:nowrap`, then the 30px `Agregar` button — primary on the first option, secondary on the rest.

**The advisory notice.** At the foot of the suggestions card: `margin-top:auto`, `padding-top:12px`, `border-top` hairline, `gap:10px`; an 8px solid warning dot, then 12px/18px `#555555`: **`Con fiebre de más de dos días, remitir a consulta médica. Botica no diagnostica.`** The README calls it mandatory; `../architecture.md` A8 makes it a property of the component rather than configurable content. It ships inside the suggestions component and there is no prop that removes it.

### A.19.4 The counter panel

`width:380px`, `flex-shrink:0`, `--radius-panel`, `border:1px solid rgba(0,0,0,0.08)`, `background:#ffffff`, `--shadow-plane`, `overflow:hidden`. Section-card header (`Venta en curso`, counter `3 ítems`). Body `padding:16px 20px`, `gap:14px`; each line is a baseline row with a 20px-wide index at 12px `#909090` tabular, the name at 14px/20px `#171717`, the subtext at 11px `#727272` tabular (`2 × $3.900`, and `1 × $5.200 · sugerido` where the line came from a suggestion), and the amount at 14px `#171717` tabular. Totals: `margin-top:auto`, `padding-top:16px`, `border-top` hairline, `gap:10px`, rows at 12px `#555555` with values in `#171717`, and the total's value at **28px/−0.025em**. Footer: `padding:16px 20px`, `background:#f4f4f4`, `border-top` hairline, `gap:8px`, a full-width 40px primary `Cobrar` at 14px/500, and a centred 11px `#727272` note `Ticket promedio del punto: $28.700`.

### A.19.5 The assistant card

No header. `padding:20px`, `gap:14px`. A 26×26 tile, `--radius-icon`, `background:#0071e3`, holding a 15px white sparkle at `stroke-width:1.8`. Primary text 14px/20px `#171717`; secondary at `margin:8px 0 0`, 12px/18px `#555555`. This tile is the one place blue appears as an identity rather than as a quantity, and it is the assistant's mark.

## A.20 Charts, as drawn

- **Histogram** — `display:flex`, `align-items:flex-end`, `gap:1px`, `flex:1`, `min-height:120px`, `border-bottom:1px solid rgba(0,0,0,0.06)`. Thirty bars, each `flex:1` with a percentage height and a colour from §A.5's histogram mapping. Axis under it at `margin-top:8px`, `justify-content:space-between`, 11px `#727272` tabular: `27 jul` and `25 ago`.
- **Ranked bars** — a `dl` whose rows are `space-between` at `gap:12px`: `dt` 76px, 11px/16px `#727272`; a `flex:1` rail at 6px on `#e0eefc` with a percentage fill; `dd` 46px, right-aligned, 11px/16px `#555555`, tabular.
- **Donut** — a 64px `viewBox="0 0 64 64"` rotated `-90deg`; two circles at `cx/cy 32`, `r 28.5`, `stroke-width:7`; the track `#e0eefc`, the arc `#0071e3` with `stroke-linecap:round` and `stroke-dasharray="105 179"` — the circumference is 179.07, so the arc is the 58,6% the figure states. Beside it, the figure at 28px/32px/−0.025em and the sub-line at 11px/16px `#6b6b6b`.
- **Progress with a target** — §A.19.1.

Every chart in the handoff prints its own figure in text beside it. None has a legend, an axis grid, a tooltip or an animation.

## A.21 Interaction states the handoff states

The prototypes are static. The README states the intended behaviour, and these values are binding:

- **Hover** — table rows `#f4f4f4`; inactive nav items `rgba(0,0,0,0.04)`; primary button `#000000`; secondary button border `rgba(0,0,0,0.28)`.
- **Focus** — a visible ring: **2px `#0071e3` with `outline-offset:2px`**.
- **Transitions** — **120–160ms `ease-out`** on colour and background. **No entrance animations.**
- **Selection** — clicking a table row selects it (`#e8e8e8` + the inset ink marker) and opens the product/lot detail. Headers are sortable. The body scrolls internally with `thead` pinned.
- **Filters** — a chip opens a menu; choosing a value moves it to the active state with its value pill and refilters. `Sede` is multi-select.
- **Mostrador** — the customer's words are captured by dictation or typing; confirming extracts the symptom chips and requests suggestions. `Agregar` adds the line, increments the counter and recomputes the totals; a suggestion-originated line is marked `· sugerido`. `Cobrar` opens the payment flow and closes the sale.
- **Compras** — editing `Sugerido` recomputes the order total and must record the deviation from the model's proposal. Zero dims the field. `Aprobar y enviar` sends and locks; `Descartar` asks for confirmation.
- **Panel** — the period control reloads every KPI, series and table. `Exportar` downloads the summary.
- **Sidebar** — the header icon collapses the sidebar to icons only. Counters are pending work per module.

## A.22 The four screens, as layout recipes

The README carries the sample data. What follows is the structure, so a fifth screen can be built to match.

- **Panel · `Resumen de red`** — header with a segmented period control and a secondary `Exportar`. `main`: `padding:32px 40px`, column, `gap:16px`, `overflow:hidden`. Row 1: `grid-template-columns:repeat(4,1fr)`, `gap:16px`, `flex-shrink:0` — four KPI cards. Row 2: `grid-template-columns:2fr 1fr 1fr`, `gap:16px`, `flex:1`, `min-height:0` — histogram card, ranked-bar card, donut card with two hairline-separated sub-blocks. Row 3: `flex-shrink:0` — a 44px-row table in a 16px frame.
- **Inventario · `Existencias`** — header with a secondary and a primary action. Filter bar with a search field and four chips, sync line right. `main`: `padding:32px 40px`, one table at full height with an internal scroll region and a 48px footer carrying range, annotation, row-size select and pagination.
- **Mostrador · `Venta 4821`** — header with an 11px turno note and a secondary action; no filter bar. `main`: `display:flex`, `gap:20px`, `padding:28px 40px`, `overflow:hidden`. Left `flex:1`, `gap:16px`: a section card (transcript + symptom chips), a headerless assistant card, and a section card of suggestions at `flex:1` with the advisory notice pinned to its foot. Right: the 380px counter panel.
- **Compras · `Orden sugerida 248`** — header with a secondary and a primary action. Filter bar with four chips, model-provenance line right. `main`: `padding:28px 40px`, `gap:16px`. Four KPI cards, then a table at `flex:1` with an editable column and a 48px footer carrying a count left and a display total right.

---

# PART B — THE APP COMPONENT LAYER

Everything below is authored for Botica. The handoff draws four desktop screens for two roles at one viewport. Botica is eleven stages, four roles, two read models, seven undrawn modules, an offline till and a regulated document. Part B is what closes that gap, and it composes from Part A: **the complete delta is two derived colour values, one type step, one density mode and four heights** (§B.15).

## B.1 The closed type scale — eight steps

Part A's seven drawn steps, plus one. App surfaces draw from **these eight and nothing else**, including via arbitrary values.

| Step   | px / leading                               | Weight                   | Tracking                          | Role                                                                                                                                                            |
| ------ | ------------------------------------------ | ------------------------ | --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `t-36` | 36 / 40                                    | 400                      | −0.025em                          | **Metric figure.** KPI tiles, the counter's total at counter density. Numerals only — never a word                                                              |
| `t-28` | 28 / 32                                    | 400                      | −0.025em                          | **Page title.** Exactly one per route. Also the ticket total and a single hero figure inside a panel                                                            |
| `t-20` | 20 / 26                                    | 400                      | −0.025em                          | **Data subtitle and panel title.** A card's own headline figure, a table footer's total, a dialog title                                                         |
| `t-16` | 16 / 24                                    | 400                      | normal                            | **Reading step. The one step Part B adds.** See below                                                                                                           |
| `t-14` | 14 / 20                                    | 400, 500 for emphasis    | normal                            | **UI default.** Table cell, form value, button label at counter density, nav label, ticket line, suggestion name, editable value. If you are unsure, it is this |
| `t-12` | 12 / 18, or 12 / 16 in a fixed-height line | 400, **500 on controls** | normal                            | **Label.** Form label, button label at desktop density, chip, badge, breadcrumb, secondary copy, reason cell                                                    |
| `t-11` | 11 / 16, or 11 / 18 when the line wraps    | 400                      | normal                            | **Caption.** Counters, footnotes, axis labels, the sync line, provenance                                                                                        |
| `t-10` | 10 / 14                                    | 400                      | 0.18em, uppercase, **Geist Mono** | **Eyebrow.** `thead`, section-card title, version, keyboard hint                                                                                                |

**Why 16 is added, and why it is the only addition.** The handoff draws no surface that is _read_ rather than scanned — four dense dashboards for a seated administrator, where 14 is the largest body step and it is right. Botica has three surfaces that are read: the ticket at counter density, seen across a counter at arm's length by a customer who is about to pay; the assistant's recommendation at counter density, which is a sentence a cashier acts on under time pressure; and the settings and compliance prose, which is paragraphs. A 14px ticket at a counter is a squint. `t-16` is that step and it does exactly three jobs. Nothing else moves up to it.

**Out of bounds:** 13, 15, 17, 18, 19, 22, 24, 26, 30, 32, 34, 40, 48 and everything above. There is no display step above 36 in this product — a 48px heading inside a 48px table row is a category error, and the largest number Botica ever shows is a ticket total.

**Numerals.** `font-variant-numeric: tabular-nums` on every figure, every code and every date, per §A.2. This is not per-column judgement; it is the default for anything numeric and it has no exceptions.

## B.2 Surface elevation — four levels

| Level            | Fill                  | Border                          | Shadow             | Radius                                                    | Used for                                                                                                                                                   |
| ---------------- | --------------------- | ------------------------------- | ------------------ | --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **L0 — Chrome**  | `--chrome` `#f4f4f4`  | a hairline on the edge it meets | none               | 0                                                         | Sidebar, `thead`, section-card header, table footer, the counter panel's footer, the segmented track, a chip's value pill, an inset block, a skeleton fill |
| **L1 — Canvas**  | `--canvas` `#fbfbfb`  | none                            | none               | 0                                                         | The content region and the filter bar. The default background of every route                                                                               |
| **L2 — Panel**   | `--surface` `#ffffff` | `--edge-soft`                   | `--shadow-plane`   | `--radius-panel` 16px, or `--radius-card` 12px for a tile | Table frame, KPI tile, section card, counter panel, side panel, form section                                                                               |
| **L3 — Overlay** | `--surface` `#ffffff` | `--edge-soft`                   | `--shadow-overlay` | `--radius-card` 12px                                      | Dropdown, popover, combobox list, the sync panel, modal, settings dialog, the reserved command palette                                                     |

**L3's shadow is not new.** It is `--shadow-overlay` from §A.7, the value the handoff labels _"solo presentación"_ because it dresses the 1600×1000 canvas. A floating plane over a page and a page floating over a presentation board are the same optical problem, and the handoff already solved it. No new shadow is authored.

Rules:

- **Never nest L2 inside L2.** A plane inside a plane drops to a `--hairline` border with no shadow and no fill of its own — which is exactly what the handoff's suggestion card does inside its section card (§A.19.3). A second border-and-shadow reads as a box in a box.
- **L0 is a static plane, never a state.** `#f4f4f4` is also the table-row hover fill (§A.21), and those two uses never meet: a row sits on L2 white and hovers to `#f4f4f4`; the chrome that _is_ `#f4f4f4` has no hover of its own.
- **Three fills, and their order is load-bearing.** Hover is `#f4f4f4`, selected is `#e8e8e8`. Selection sits below hover, always, so a hovered row never looks more committed than a selected one.
- **Modal scrim:** `rgba(0, 0, 0, 0.32)` — the border ramp's own base at the alpha an opaque overlay implies. One of Part B's two new values.

## B.3 Spacing, insets and layout

The §A.9 scale is closed: **2 · 4 · 6 · 8 · 10 · 12 · 14 · 16 · 20 · 22 · 28 · 32 · 40 · 48**. No arbitrary padding, no value above 48 on any app surface.

**The content inset is `32px 40px`.** Every route. The one exception the handoff draws and this document keeps: **a route whose `main` is a single full-height working panel takes `28px 40px`**, and the 8px goes to the panel. Mostrador and Compras qualify; Panel and Inventario do not. This is not a per-screen judgement — a route with one panel takes 28, a route with a grid takes 32.

**No maximum width on the content region.** A table of 4.284 rows on a 2560px monitor uses the monitor. Prose surfaces cap their own measure at **720px** inside a full-width panel — the compliance vault's notes, a report's description, an error dialog's body — never the region.

**Grid gap is 16px** between tiles, panels and rows of tiles. **List gap is 12px** between cards in a scrolling list. **Inline gap is 8px** between a control and the next control, **10px** in a filter bar, **6px** between an icon and a tightly-bound label.

**Form rhythm:** 16px between fields, 28px between field groups, and a `border-top: 1px solid rgba(0,0,0,0.06)` with 28px above it between form sections.

## B.4 Tables — density modes and row states

The server-paginated grid is the primary surface of the office read model, and §9 of the architecture fixes its contract: `manualPagination`, `manualSorting`, `manualFiltering`, `rowCount` from the API, and page index, page size, sort and filter state in TanStack Router's typed search params so any view is a link. **Counter surfaces are exempt** — they query the local store, they are not paginated, and their filter state is component-local because there is no server to keep in step.

### B.4.1 Density modes

| Mode         | Row height | Cell padding-x | When                                                                                 |
| ------------ | ---------- | -------------- | ------------------------------------------------------------------------------------ |
| **Compact**  | 40px       | 22px           | Reference pickers, the audit log, the compliance checklist, a picker inside a dialog |
| **Panel**    | 44px       | 22px           | A table inside a dashboard card. Drawn: the Panel's per-sede table                   |
| **Standard** | 48px       | 22px           | Every primary table. Drawn: Existencias, Orden sugerida                              |
| **Counter**  | 56px       | 20px           | Till surfaces only. §B.11                                                            |

Density is a property of the surface, decided in its spec. **Do not build a density toggle.** Cell padding-x stays 22px in the first three modes because it is the handoff's own inset and a narrower cell does not buy a row.

Cells are `align-middle` on a fixed row height, never `padding-y` on a single-line row — padding-derived height drifts the moment one cell wraps. Single-line cells truncate; the full value is in the row's detail panel, never in a `title` attribute alone.

### B.4.2 Header

40px, `sticky top-0`, L0 fill, `border-bottom: 1px solid rgba(0,0,0,0.11)`, `th` per §A.17. The fill is **opaque** — a translucent blurred header smears row text as it scrolls beneath it.

A sortable header makes the whole `th` the hit target, goes to `#171717` on hover, and shows a 12px chevron in `#909090` **only on the active sort column**. No hover-revealed chevron: it reflows 12px on every column the pointer crosses. One sort column at a time; clicking a different column replaces the sort. Sorting is server-side and lands in the URL.

### B.4.3 Row states

**No zebra striping.** The 6% hairline gives the eye its rail, and zebra plus hover plus cursor plus selection is four fills competing on one row.

| State                                  | Treatment                                                                        |
| -------------------------------------- | -------------------------------------------------------------------------------- |
| Rest                                   | transparent, `border-bottom: 1px solid rgba(0,0,0,0.06)`, last row none          |
| Hover                                  | `background:#f4f4f4`                                                             |
| Keyboard cursor (`j`/`k`)              | `background:#f4f4f4` + `box-shadow: inset 2px 0 0 #909090`                       |
| **Current** — its detail panel is open | `background:#e8e8e8` + `box-shadow: inset 2px 0 0 #171717` — **the drawn state** |
| Checked — in a bulk-action set         | `background:#e8e8e8`, checkbox checked, **no marker**                            |
| Checked + current                      | `background:#e8e8e8` + the ink marker                                            |
| Checked + hover                        | `background:#e8e8e8`, unchanged. There is no fifth fill                          |

**Two fills and two markers, and each says one thing.** The fill is the weight of attention: `#f4f4f4` means the pointer or the cursor is here, `#e8e8e8` means this row is committed to something. The marker says which pointer: `#909090` is the keyboard cursor, `#171717` is the open record. A row can be checked, current and under the cursor at once, and the ink marker wins over the grey one.

**The keyboard cursor, DOM focus and selection are three separate states.** Do not collapse them into one boolean; the checkbox set a bulk action operates on and the row whose panel is open are different questions, and answering them with one prop is how a screen reader ends up announcing an opened record as selected.

Row click opens the detail panel. The checkbox column and any inline control stop propagation. Shift-click extends the checked set from the last click.

### B.4.4 Columns

- Widths are percentages under `table-layout: fixed`, per §A.17. **Never write a column width as a `calc()` containing a percentage** — Chrome does not resolve one and divides the table equally instead, so the shares read as deliberate and are inert.
- Every table states a **minimum width**; below it the frame scrolls horizontally inside its own `overflow-x:auto` container. The page body never scrolls horizontally.
- Checkbox column: 44px fixed, `padding:0`, `sticky left-0`, inheriting the row fill. A gutter column that keeps the 22px inset cannot hold its own content box and spills into its neighbour.
- Numeric columns: `text-align:right`, `tabular-nums`, header right-aligned to match.
- Status column: the badge, at a width fixed per enum — never `auto`, because the widest label would otherwise move the column on every page.
- Actions column: 48px fixed, `sticky right-0`, an icon-only ghost control revealed on row hover or focus-within, with its width reserved at rest so nothing reflows.

### B.4.5 Footer and pagination

48px, L0, `padding:0 22px`, per §A.17. Left: the range at 11px, then the filtered annotation at 11px `#6b6b6b` where the surface has one (`312 requieren acción`). Right: the row-size select and the page group per §A.15.4, or a display total (`Total de la orden`).

The page group renders first page, last page, current and one neighbour either side; skipped spans collapse to `…`. Never more than nine cells. **Reserve the group's width in `ch`**, computed from the widest arrangement `rowCount` permits — the numerals are tabular, so one digit is exactly `1ch` — rather than measuring the rendered group, which is a layout read on every paint and wrong the moment the viewport changes.

Until `rowCount` arrives the range is a skeleton bar at its resting width and the page group is not rendered. Never `… de muchos`, never a guessed page count. Any filter change resets to page 1. A checked set survives paging, is announced as a count in the bulk bar, and is cleared by `Esc` and by any filter change — and the filter-change clear is announced.

**Bulk-action bar.** When a set is checked, a 48px L0 strip pins to the bottom edge inside the table frame, above the footer: the count at 12px `#171717` tabular (`3 seleccionadas`), a ghost `Quitar selección`, then the actions right-aligned at `sm` size with at most one primary.

## B.5 Form controls and the focus ring

The handoff draws one input, one placeholder and one static select. Everything here is authored against that geometry.

### B.5.1 The focus ring — one definition, no exceptions

```css
:focus-visible {
  outline: 2px solid #0071e3;
  outline-offset: 2px;
}
```

The handoff states it and it is the whole rule. Notes that make it survive:

- **`:focus-visible` only, never bare `:focus`.** A `focus:outline-none` with no `focus-visible` replacement is a defect, not a style choice.
- **There is exactly one ring.** No destructive variant, no per-surface variant, no ring inside a dialog that differs from the ring outside it. A second focus colour is a second thing to learn for no gain.
- **This is the one place `#0071e3` is an interface signal rather than a quantity** (§B.12), and that is precisely why it works: a colour reserved for data can never be misread as a status family. It is not an accent — no button, tab, link, badge or row marker draws from it.
- Measured 4.70:1 against `#ffffff` and 4.54:1 against `#fbfbfb`, well over the 3:1 floor for a focus indicator.
- The ring is never animated and never suppressed on `:active`. The 2px offset means a control needs 2px of clear space around it; a control flush against a table cell's edge gets it from the cell's padding.
- On a control that already carries a border, the ring sits outside the border and the border does not change. On a borderless control — a ghost button, a nav item, a table row — the ring is the whole treatment.

### B.5.2 Text input

```
height:34px; width:100%; border-radius:9px; border:1px solid rgba(0,0,0,0.11);
background:#ffffff; padding:0 12px; font-size:14px; color:#171717;
transition: border-color 140ms ease-out
placeholder: 12px #909090
```

| State     | Treatment                                                                                                                                                                               |
| --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Rest      | as above — the search field's own geometry from §A.15.2                                                                                                                                 |
| Hover     | `border-color: rgba(0,0,0,0.16)`                                                                                                                                                        |
| Focus     | the §B.5.1 ring; the border does not change                                                                                                                                             |
| Disabled  | `background:#f4f4f4`, `color:#909090`, `border-color:rgba(0,0,0,0.06)`, `cursor:not-allowed`, no hover. **Never `opacity`** — opacity on an input fades its label and its value with it |
| Read-only | `background:#f4f4f4`, `color:#171717`, `border-color:rgba(0,0,0,0.06)`. Selectable, not editable, and distinguished from disabled by keeping full-strength text                         |
| Invalid   | `border-color:#b04a3f`, `aria-invalid="true"`, `aria-describedby` pointing at the message                                                                                               |

A field with a leading icon takes the search field's `padding:0 10px 0 12px` with a 15px `#909090` glyph and an 8px gap.

### B.5.3 Textarea

The input's border and state logic, at `padding:10px 12px`, `t-16` with 24px leading, `resize:none`, minimum 3 rows, auto-growing to 12 then scrolling. `t-16` because a textarea holds prose — a compliance note, a discard reason, a manual movement's justification.

### B.5.4 Select and combobox

Trigger: the input's geometry at `padding:0 34px 0 12px`, with a 12px chevron in `#909090` at `right:12px`, `pointer-events:none`. States identical to the input. An unset select shows its placeholder in `#909090`; when the value is optional the placeholder is a real option, so a choice can be cleared without resetting the form.

Listbox: L3, `padding:6px`, up to 320px tall with vertical scrolling, portalled above clipping containers, at least as wide as its trigger, flipping above when there is more room there. Option rows are 34px, `padding:0 10px`, `--radius-control`, `t-14`, hover and keyboard-active both `#f4f4f4`, with a 14px check at the selected row's end. Arrow keys, Home, End, typeahead, Enter, Escape and focus restoration are all required.

Use a searchable combobox wherever the collection is a catalog — `items`, `manufacturers`, `suppliers`, `customers` — because a droguería's catalog is thousands of rows and a select is not a search.

### B.5.5 Checkbox and radio

```
width:18px; height:18px; flex-shrink:0; border-radius:4px;
border:1px solid rgba(0,0,0,0.16); background:#ffffff
```

**`4px` is `--radius-mark`** — the brand square's radius, already in §A.8. Nothing new is introduced; the sibling system had to author a token for this and Botica does not, because the handoff's radius scale already reaches 4.

| State         | Treatment                                                                                        |
| ------------- | ------------------------------------------------------------------------------------------------ |
| Rest          | as above                                                                                         |
| Hover         | `border-color:#171717`                                                                           |
| Checked       | `background:#171717`, `border-color:#171717`, a 12px check in `#fbfbfb`                          |
| Indeterminate | `background:#171717`, a 10 × 1.5px bar in `#fbfbfb` — a header select-all over a partial page    |
| Focus         | the §B.5.1 ring                                                                                  |
| Disabled      | `background:#f4f4f4`, `border-color:rgba(0,0,0,0.06)`; when also checked, the glyph in `#c8c8c8` |

The hit target is 34px square via padding on the wrapping `<label>` (44px at counter density), regardless of the 18px box. A radio is the same geometry at `--radius-pill` with an 8px `#fbfbfb` centre dot, in a `role="radiogroup"` with a 12px `#727272` legend; arrow keys move within the group and Tab moves past it. Use a radio group only for two to four mutually exclusive options that must all be visible; anything longer is a select.

### B.5.6 The quantity stepper

Botica has two places a number is edited against a proposal — the `Sugerido` cell in Compras and a ticket line's quantity at the counter — and they are one control.

**Desktop:** the drawn editable cell (§A.18.2) at a 34px box, `text-align:right`, tabular, with the `−` and `+` controls appearing only on hover or focus as 24px ghost glyphs inside the field's own padding. **Counter:** 44px tall with 44 × 44 `−` and `+` buttons flanking a 60px field, because a cashier adjusts a quantity with a thumb.

Commit on `Enter` or blur; revert on `Esc`. A pending write shows its value at `#909090` until the server confirms; a rejected write reverts and raises an inline error naming the operation.

**The model's number is never overwritten.** `purchase_order_lines` carries `suggested_quantity` and `approved_quantity` as two columns precisely so the deviation survives (`../architecture.md` §3), and the interface must make that legible: an edited cell renders the new value and its row's `Por qué` cell gains ` · ajustado de 220` at 12px `#727272`. No new colour, no new geometry, and the one measurement that says whether the model is trusted stays readable on the screen where it is made.

### B.5.7 Labels, help and validation

```
label: 12px #727272, margin-bottom:8px, block
help:  11px #909090, margin-top:6px
error: 12px #b04a3f, margin-top:6px
```

Required fields carry the word `Obligatorio` in the help slot, never an asterisk. In a mostly-required form, optional fields carry `Opcional`. Validation fires **on blur and on submit, never on keystroke**. Help and error occupy the same slot and the error replaces the help, so validating shifts no layout.

## B.6 Buttons

Colour and geometry are §A.14 verbatim. Part B adds two sizes, a ghost and a destructive variant, the disabled treatment, and the busy state.

### B.6.1 Sizes

| Size | Height | Padding-x | Icon-only | Type       | Use                                                                                           |
| ---- | ------ | --------- | --------- | ---------- | --------------------------------------------------------------------------------------------- |
| `xs` | 30px   | 12px      | 30px      | `t-12`     | Inside a table row or a card row. Drawn: `Agregar`                                            |
| `sm` | 34px   | 16px      | 34px      | `t-12`/500 | **Default.** Page header, filter bar, panel footer, dialog footer. Drawn: every header action |
| `md` | 40px   | 20px      | 40px      | `t-14`/500 | A form's submit, an empty state's action, a full-width panel action. Drawn: `Cobrar`          |
| `lg` | 52px   | 24px      | 52px      | `t-16`/500 | **Counter density only.** §B.11                                                               |

All sizes: `display:inline-flex`, `align-items:center`, `justify-content:center`, `gap:8px`, `white-space:nowrap`, `--radius-control`, `font-family:inherit`, and an enumerated transition on `background-color, border-color, color`. Icons are 16px Lucide (14px at `xs`).

Pagination's 28px arrows and the 28px row-size select are not buttons in this table; they are the pagination component's own geometry (§A.15.4) and exist nowhere else.

### B.6.2 Variants and states

| Variant         | Rest                                                                  | Hover                                          | Active               | Disabled                               |
| --------------- | --------------------------------------------------------------------- | ---------------------------------------------- | -------------------- | -------------------------------------- |
| **Primary**     | `background:#171717`, `color:#fbfbfb`                                 | `background:#000000`                           | back to `#171717`    | `opacity:0.45`, `pointer-events:none`  |
| **Secondary**   | `border:1px solid rgba(0,0,0,0.16)`, `color:#171717`, transparent     | `border-color:rgba(0,0,0,0.28)`                | `background:#f4f4f4` | `opacity:0.45`, `pointer-events:none`  |
| **Ghost**       | `color:#555555`, transparent, no border                               | `color:#171717`, `background:rgba(0,0,0,0.04)` | `background:#e8e8e8` | `color:#c8c8c8`, `pointer-events:none` |
| **Destructive** | `border:1px solid rgba(176,74,63,0.32)`, `color:#b04a3f`, transparent | `border-color:#b04a3f`, `background:#f1e2e1`   | `background:#f1e2e1` | `opacity:0.45`                         |

**The press returns to rest.** Primary hovers _up_ to `#000000` and presses back down to `#171717`, so the direction of travel reverses under the finger — which is what a press should feel like. No new value, and nothing translates: the plane holds its position on hover and on press, always.

**Destructive is a bordered variant, not a filled one.** A filled red button beside `Cobrar` on a till is a mis-tap that voids a sale. The **only** place destructive is filled — `background:#b04a3f`, `color:#fbfbfb` — is the confirm button inside a confirmation dialog, where it is the thing being confirmed and there is nothing beside it to hit by accident. Its focus ring is still `#0071e3`; there is one ring (§B.5.1).

**Two disabled treatments, and the drawing decides which.** Filled and bordered controls take `opacity:0.45` — the whole control fades together. Ghost and icon-only controls take a colour change to `#c8c8c8`, which is what the handoff draws for a disabled pagination arrow. Inputs take neither (§B.5.2).

**Busy.** The label goes to its present participle — `Aprobando…`, `Cobrando…`, `Enviando…`, `Guardando…` — with `aria-busy="true"` and `pointer-events:none`; opacity is unchanged, and a 14px inline indicator is permitted. **This is the only spinner in the entire product** (§B.10.1), and it exists only inside a control the user has already pressed.

**Exactly one primary per surface.** A panel or dialog footer is `[Cancelar: secondary] [Confirmar: primary]`, right-aligned at `gap:8px`. Drawn precedent: `Cargar mercancía` / `Nuevo traslado`, `Descartar` / `Aprobar y enviar`.

## B.7 Status colour and the domain enums

### B.7.1 Five families

Part A's four, plus **neutral**, which the handoff has no treatment for and an operating platform cannot do without: a purchase order nobody has opened, a fiscal document not yet handed over, a checklist item nobody has answered. Neutral introduces **no new colour** — it is the ink label on the symptom chip's own fill, both already in the system.

| Family          | Colour    | Tint      | Contrast, colour on tint | Reading                                                                        |
| --------------- | --------- | --------- | ------------------------ | ------------------------------------------------------------------------------ |
| **Neutral**     | `#727272` | `#e8e8e8` | 3.44                     | Nothing has happened yet. Not a problem                                        |
| **Informative** | `#4c6a86` | `#e3e7eb` | 4.55                     | The system is working on it, or a human committed and the system has not acted |
| **Positive**    | `#4e7a52` | `#e3e9e3` | 4.03                     | The good terminal state                                                        |
| **Warning**     | `#8c6a33` | `#ece7df` | 4.04                     | Usable but degraded, or a human has to decide                                  |
| **Critical**    | `#b04a3f` | `#f1e2e1` | 4.29                     | Terminal failure, or the thing this product exists to prevent                  |

All five dots clear the 3:1 floor for a graphical object carrying meaning. All five labels are `#171717` at 14.66–15.04:1 (§A.6, §B.7.3).

**A person who learns the colours on Existencias reads every other table.** The five meanings are constant across all five enums below, and no enum bends them.

### B.7.2 Solid and hollow

The handoff draws a hollow dot on expiry and a solid dot on everything else. The rule behind that example, stated so it extends:

> **Solid means the state is true now. Hollow means it is not yet true, or it is true only under a condition, or it is waiting on something outside this system.**

**Vence en 5 meses** is hollow because the lot has not expired. **Vencido** is solid because it has. **En trámite** is hollow because INVIMA has it. **Pendiente de envío** is hollow because the handoff has not happened yet. **Sin conexión** is hollow because the network is not ours. This costs no token and it is what lets two states share a family without becoming the same badge.

### B.7.3 The two treatments

| Surface                          | Treatment                                                                |
| -------------------------------- | ------------------------------------------------------------------------ |
| **The status column of a table** | The full tinted badge from §A.16 — as drawn, on every row of Existencias |
| **Status shown incidentally**    | Dot + label at `t-12` `#555555`, no fill, no pill                        |

The sibling system forbids a tinted pill in a table row on the grounds that fifty of them are noise. Botica departs, because on Existencias the `Estado` column is 20% of the table and it is what the screen exists for — a droguería opens it to find the quiebres. The rule that keeps this from spreading: **at most one column per table may use the badge, and it is the column the surface is about.** A second status on the same row is a dot and a label. A status in a detail panel's field list, in a nav item, in a card header or in a dense secondary list is a dot and a label.

**A status is never encoded by colour alone.** The label is always present, in every treatment, on every surface.

### B.7.4 The enum map

Every value comes from `../architecture.md` §3. Labels are the interface strings; where the handoff draws one it is reproduced verbatim.

**Stock state** — derived per item, sede and lot in S3 from `stock_on_hand`, `demand_forecasts` and the lot's expiry. Not a column; a computed state. **S3 owns the derivation — which state a lot is in, and on what thresholds; this document owns the family, the dot and the label grammar each state renders in.** Two documents, two questions, and neither answers the other's.

| State             | Family      | Dot        | Label                                                                                                                                       |
| ----------------- | ----------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `sufficient`      | Positive    | solid      | **Suficiente**                                                                                                                              |
| `reorder_point`   | Warning     | solid      | **Punto de reorden**                                                                                                                        |
| `overstock`       | Informative | solid      | **Sobrestock · 94 días** — the cover figure is part of the label                                                                            |
| `stockout`        | Critical    | solid      | **Quiebre**, and **Quiebre · hay 96 en Suba** when another sede holds it. The second clause is a §B.9.2 stale figure and carries its marker |
| `expiring`        | Warning     | **hollow** | **Vence en n meses** — the rendered month count, inside S3's `expiry_notice_days` window                                                    |
| `expiring_urgent` | Critical    | **hollow** | **Vence en n meses** on the critical tint — inside S3's `expiry_alert_days` window                                                          |
| `expired`         | Critical    | solid      | **Vencido**                                                                                                                                 |

**The two expiry states are one visual family with two tints, and their label is rendered rather than fixed.** What this document settles is the treatment: warning with a hollow dot in the notice window, critical with a hollow dot in the alert window, solid critical once the lot is `expired` — the ring stays while the tint escalates (§B.7.2). Where the two edges fall is not this document's: they are S3's `expiry_notice_days` and `expiry_alert_days`, in the `/api/settings/inventory` group (`ownership.md`), and the label is the month count computed from the lot's own `expires_at` at render time. A horizon written into the string is how a badge ends up announcing a window a tenant moved months ago, and an expiry badge that states the wrong horizon is worse than no badge, because a pharmacist acts on it.

**Cobertura thresholds**, which `ownership.md` defers here, recovered from §A.6 and fixed at the edges the drawing implies: **≤ 4 días critical `#b04a3f` · 5–20 días warning `#8c6a33` · 21–90 días normal `#555555` · > 90 días informative `#4c6a86`.** The `Cobertura` numeral takes the family's colour and no dot; the `Por qué` cell always restates it in words, so the colour is never the only signal.

**`fiscal_documents.status`** — `pending → sent → acknowledged`, with `failed`. **These four describe Botica's handoff to the client's invoicing system, not the DIAN's filing** (`../architecture.md` §8, A9): Botica issues nothing, transmits nothing and never learns what the DIAN did with the document. **`S5-handoff.md` coined the four labels and owns their text; this document owns the family and the dot treatment each one renders in** — a wording change is an edit there, a treatment change is an edit here, and neither document may make the other's.

| Value          | Family      | Dot        | Label                  | Reading                                                                                                                                       |
| -------------- | ----------- | ---------- | ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `pending`      | Neutral     | hollow     | **Pendiente de envío** | Built and queued for the client's invoicing system. Nothing has failed                                                                        |
| `sent`         | Informative | **hollow** | **Enviado**            | Delivered under its `document_key`; the target has not confirmed yet. Hollow because we are waiting on something outside this system (§B.7.2) |
| `acknowledged` | Positive    | solid      | **Confirmado**         | The client's system holds it. Terminal success, and an empty response still counts                                                            |
| `failed`       | Critical    | solid      | **Falló el envío**     | The target refused the delivery. An administrator's work list, never the cashier's                                                            |

**No label may claim a DIAN outcome.** **Aceptado por la DIAN** on a row Botica delivered to an API is a lie about a filing Botica did not perform and cannot see, and it is the one badge in the system that would be read as legal cover. **Confirmado** states that the client's invoicing system took the document — the surface it renders on is `Envíos a facturación` — and stops there.

**Where no invoicing system is configured, none of these badges ever renders.** That is the default and it is not an error: no `fiscal_documents` row exists, so the sale's fiscal slot is **absent, not `Pendiente de envío`**, and no Panel tile, work list or badge mentions the subject anywhere (`../architecture.md` §8). §B.10.2's empty state is for a configured target with an empty queue — an unconfigured one has no surface at all.

**`purchase_orders.status`** — `suggested → approved → sent → partially_received | received | discarded`.

| Value                | Family      | Dot    | Label                    |
| -------------------- | ----------- | ------ | ------------------------ |
| `suggested`          | Neutral     | hollow | **Sugerida**             |
| `approved`           | Informative | solid  | **Aprobada**             |
| `sent`               | Informative | solid  | **Enviada al proveedor** |
| `partially_received` | Warning     | solid  | **Recibida parcial**     |
| `received`           | Positive    | solid  | **Recibida**             |
| `discarded`          | Neutral     | solid  | **Descartada**           |

`discarded` is **neutral, not critical**. Discarding a suggestion the model made is the product working — it is the measurement `suggested_quantity` versus `approved_quantity` exists to capture — and colouring it as a failure would tell an administrator that using their judgement is an error.

**`items.invima_status`** — `valid | in_process | expired | not_applicable`.

| Value            | Family   | Dot        | Label                |
| ---------------- | -------- | ---------- | -------------------- |
| `valid`          | Positive | solid      | **Registro vigente** |
| `in_process`     | Warning  | **hollow** | **En trámite**       |
| `expired`        | Critical | solid      | **Registro vencido** |
| `not_applicable` | Neutral  | hollow     | **No aplica**        |

Botica surfaces the state and records the pharmacy's own decision; it does not block a sale on it and does not validate against INVIMA's register (`../architecture.md` §3, §12). So `expired` is a badge and a filter, and never a disabled row.

**Sync state** — §B.9, which is the fifth enum and gets its own section because it appears on every surface a till uses.

**`price_proposals.status`** — `proposed` neutral hollow **Propuesta** · `above_cap` critical solid **Sobre el tope regulado** · `taken` positive solid **Tomada** · `modified` positive solid **Modificada** · `dismissed` neutral solid **Descartada** · `superseded` neutral solid **Reemplazada**. **`S7-pricing.md` coined these labels and owns their text; this document owns the family and the dot each one renders in** — the same split this section takes with S5 over the fiscal four. **No value in this enum means a price moved.** A proposal is an analysis and never an instruction (`../architecture.md` A11): nothing applies a price, so there is no **Aplicada**, and nothing reverts one, so there is no **Revertida**. A price changes only when a person changes it in the catalog's price editor, and what that produces is an `item_prices` row at `source = manual` carrying their name. `proposed` is hollow because nothing about it is true yet (§B.7.2); the other five are solid because each states something that is true now — including `above_cap`, which is not a proposal at all but a compliance finding about the price the till is charging today.

`dismissed` is **neutral, not critical**, for exactly the reason this section gives `purchase_orders.discarded`: dismissing a suggestion is an owner exercising judgement, and colouring judgement as a failure teaches them not to exercise it. *If this is wrong*, an owner learns that the honest resolution is the one that turns their row red and stops recording it — they close the screen instead of dismissing, `resolved_by` and `resolved_price` stay null, and the table goes quiet on precisely the rows where a person disagreed with the model.

`modified` is **positive, not warning**, and deliberately not a shade of partial success. It is the most interesting outcome in the set: the person acted on the analysis and disagreed with the number, and the gap between `suggested_price` and `resolved_price` is the only honest measure of whether the model is worth trusting — the same measurement `purchase_order_lines.suggested_quantity` versus `approved_quantity` and `sale_lines.from_suggestion` exist to preserve. It shares Positive with `taken` and is told apart by its label, which §B.7.3 requires of every badge anyway. *If this is wrong*, an amber badge on the one row that proves the analysis was used teaches an owner that typing their own number is a deviation from the intended path, and the pressure is toward taking the suggested figure as drawn — a model steering prices by badge colour, having been denied the write path to steer them directly (A11).

**Every other enum takes the same grammar.** `transfers` (`draft` neutral hollow · `dispatched` informative · `received` positive · `partial` warning), `sales` (`open` informative · `closed` positive · `voided` neutral solid). A new enum picks a family per value against the five readings in §B.7.1 and adds nothing.

## B.8 The shell, the nav and the undrawn surfaces

```
┌────────────┬──────────────────────────────────────────────────┐
│            │  Header                                    64px  │  L1 @ 85% + blur
│  Sidebar   ├──────────────────────────────────────────────────┤
│   280px    │  Filter bar (server-paginated surfaces)     52px  │  L1
│    L0      ├──────────────────────────────────┬───────────────┤
│            │  Content  32px 40px              │   Panel       │
│            │  L1 canvas, L2 panels            │   440px  L2   │
├────────────┤                                  │               │
│ user  64px │                                  │               │
└────────────┴──────────────────────────────────┴───────────────┘
```

### B.8.1 The sidebar

§A.13.1 verbatim, plus:

- **280px expanded, 64px collapsed** to an icon rail. The handoff draws the collapse control and the architecture does not constrain it. Collapsed, an item is its 16px icon centred in a 38px square with the label as its accessible name and a tooltip on hover; the counter becomes a 6px `#727272` dot in the item's top-right rather than a number, because a number in a 64px rail is unreadable.
- **The nav is one flat list with no group labels.** Seven items is the ceiling for a flat list and Botica is at it. Everything a network _configures_ or _audits_ — people, devices, the invoicing handoff, price policy, the assistant's settings, the compliance vault, the activity log, exports — is a section of the settings dialog (§B.8.4) and appears nowhere in this list. Eight items in a list a cashier also uses costs attention every day to save an administrator a click a month.
- Hover on an inactive item: `background:rgba(0,0,0,0.04)`. Focus: the §B.5.1 ring.
- A role that reaches no item gets a sentence saying where its surfaces are, never an empty rail. An empty container reads as a failure to load.
- **The organisation name is a label, not a control.** There is no workspace switcher in v1; an affordance promising one is worse than none.

### B.8.2 Nav counters

The counter is **work waiting in that module**, never a total. Drawn: `Compras 12` (órdenes sugeridas awaiting a decision) and `Mostrador 3` (ventas abiertas). Rules:

- 11px, tabular, `#727272`, `margin-left:auto`. On the active item it goes `#171717` at weight 500 — and **it stays.** The prototype drops the `3` when Mostrador is active and keeps the `12` when Compras is active; the Compras precedent is the correct one, because "three sales are open" is still true while you are inside one of them.
- **Zero renders nothing at all.** Not `0`, not a dot, not a dimmed badge. A module with no work waiting is a module with no counter.
- It is never a colour and never a pill. A red badge on a nav item is an alarm, and none of these are alarms.
- One exception, and it is the only one: `Compras` and any module holding a **critical** state shows its number in `#b04a3f` — registros vencidos — because that is the case where the count is a problem rather than a queue. **No fiscal count is ever one of them:** the handoff's work list has no nav item at all, and no counter anywhere claims a DIAN outcome (§B.7.4). Everything else is `#727272`.

### B.8.3 Role gating

`../architecture.md` §2 governs, and the prototype does not: the Mostrador screen draws a `cashier` — `Andrés Peña · Mostrador · Chapinero` — the full seven-item administrator nav. **That is a prototype artefact. Do not reproduce it.**

| Role             | Sees                                                                                                             |
| ---------------- | ---------------------------------------------------------------------------------------------------------------- |
| `owner`          | All seven, plus every settings section                                                                           |
| `admin`          | All seven, plus settings minus hard delete, role changes and billing/API keys                                    |
| `cashier`        | **Mostrador** (the landing route) and a **read-only Inventario**. Nothing else                                   |
| `platform_admin` | Reaches a tenant only through Django admin (§B.8.5). Inside the app on a pinned tenant it sees what `owner` sees |

**An item a role cannot reach is not rendered. It is never rendered disabled.** A greyed nav item advertises a capability that will never arrive and invites a support ticket a month. The same rule applies inside a surface: a `cashier` on Inventario sees no `Cargar mercancía` and no `Nuevo traslado` in the header, not two dimmed buttons.

A route reached by a link the role cannot have **refuses inside the content region**, naming the role it needs, and does not redirect silently — a link that shows nothing is indistinguishable from a broken one.

### B.8.4 The seven surfaces the handoff never drew

Each of these is buildable from Part A's components plus the rules above. What follows fixes the layout and the decisions, not the copy of every field.

**1 · Precios (S7).** Filter bar + table + record panel, exactly Existencias' shape. Columns: `Producto 24 · Laboratorio 12 (left) · Costo 10 (right) · Precio 10 (right) · Margen 10 (right) · Propuesto 12 (right, editable) · Impacto mensual 12 (right) · Estado 10`. The `Propuesto` cell is **read-only** — the suggested price is an analysis, and the only action on a row opens S1's price editor pre-filled (A11). It is not a stepper, and this screen has no write path to a price or to a suggestion's outcome. An item carrying `regulated_max_price` shows a hollow warning dot after its `Precio` value and states the cap in the panel; a proposal above the cap renders `Sobre el tope regulado` as a critical badge and its row cannot be included in an approval — the checkbox is absent, not disabled, and the reason is in the cell. Header: `Exportar` secondary and no primary — **there is no `Aplicar propuestas` and no bulk bar**, because nothing on this screen applies anything (A11). Four KPI tiles above the table: `Referencias con propuesta`, `Impacto mensual estimado`, `Margen proyectado`, `Propuestas sobre el tope`.

**2 · Sedes (S3/S9).** Two regions. Top: `grid-template-columns:repeat(3,1fr)`, `gap:16px` of L2 cards at `--radius-card`, each carrying the sede's name at `t-14`/500, its `code` at `t-10` mono, a four-row `dl` of venta / margen / quiebres / días de stock at 11px on the per-sede-bar geometry (§A.20), and its status badge. Bottom: the Panel's own per-sede table at 44px rows, so the two screens agree. A sede's detail is a record panel, not a route: dirección, teléfono, tipo, devices with their `last_synced_at` per §B.9, **that sede's handoff state where an invoicing target is configured — and no fiscal block at all where none is** (`../architecture.md` §8, A6; S9 draws it), and the people whose home sede it is. There is no resolution and no numbering range to show, because Botica allocates no fiscal numbers. Creating a sede is a settings action (§B.8.4·4), not a button here.

**3 · Reportes (S9).** A 240px L0 rail of report definitions grouped under `t-10` mono labels — `Venta`, `Inventario`, `Compras`, `Asistente`, `Cumplimiento` — and a pane holding the report. Every report is: a `t-20` title, a filter bar (period segmented control, sede multi-select, category), one chart from §B.12, one table, and a `sm` secondary `Exportar`. **Every report states its source and its freshness in one 11px line under the title** — `Calculado sobre métricas diarias · al 31/08 06:00` — because a report whose provenance is invisible is a report someone will dispute in a meeting. Export produces CSV and XLSX and never a PDF of a table.

**4 · Ajustes (S0, S10).** One dialog, not routes. `1120 × 720`, capped at the viewport, L3, `--radius-panel`, centred over the `rgba(0,0,0,0.32)` scrim. A 240px L0 rail with a 34px search field at its top and sections under `t-10` mono group labels: **Organización** (General, Personas, Sedes y dispositivos) · **Operación** (Facturación electrónica, Precios y topes, Asistente) · **Registros** (Cumplimiento, Actividad, Exportaciones). Rail item: 34px, `padding:0 10px`, `--radius-control`, `t-14`, 16px icon, `gap:10px`; active `background:#ffffff`, `#171717`/500, `--shadow-segment` — the same lift-to-L2 the sidebar's active route uses. Pane: a 44px strip holding only a ghost close, then one scrolling region at `padding:4px 24px 24px`. Block titles inside the pane are `t-16`/500, one step below the dialog's own rank; blocks are separated by space and hairlines, never by nested cards. The open section is a search param on whatever route is showing — `/inventory?settings=people` — so the dialog never takes the page out from under anyone, a section is a link, and `Escape` returns exactly where you were. Opens on the sidebar's gear and on `⌘,`. Filter and pagination state inside a section is component-local, deliberately departing from §B.4: a dialog does not own the address bar it floats over.

**5 · Ingreso (S0).** No shell. A 380px L2 card — the counter panel's own width — centred on `#fbfbfb`, `--radius-panel`, `padding:32px`, `--shadow-plane`. The 24px brand square above the organisation-less wordmark `Botica` at `t-14`/500, a `t-20` heading `Iniciar sesión`, a 34px email field, and a 40px full-width primary `Continuar`. **Access is invite-only** (`../architecture.md` §3), so there is no sign-up link and no password-reset that creates an account; instead an 11px `#727272` line: `El acceso a Botica es por invitación. Pida el enlace a la administradora de su droguería.` Errors are §B.10.3's field or region treatment and name the failure: `No encontramos una cuenta con ese correo.` — never `Credenciales inválidas` on the email step, which tells an attacker nothing and tells a cashier nothing either. A till registering itself for the first time uses the same card to claim a `device`: its `label`, its sede, and then the persistence prompt from §B.9.4.

**6 · Cumplimiento — the vault (S10).** A settings section, not a nav item, because it is opened monthly and putting an eighth item in a nav a cashier uses costs more than it returns. **The measurement that would change that:** if a pilot's regente opens it more than weekly, it earns a nav item and this paragraph is the diff to apply. Layout: the checklist as a `Compact` table at 40px rows — `Requisito 40 · Periodicidad 14 · Documento 20 · Vence 12 · Estado 14` — with the checklist state as a badge (`done` positive · `pending` neutral hollow · `not_applicable` neutral hollow) and the expiry column using §B.7.2's hollow-dot expiry treatment on the same windows as a lot. Attaching a document is a `xs` secondary in the row; an attached document is its filename at `t-12` with a 14px download glyph. A document expiring inside 30 days raises the Panel's compliance tile; nothing here files anything with anyone, and the section says so once, at the top, at `t-12` `#727272`: `Botica guarda los soportes y avisa cuando vencen. No presenta reportes ante ninguna autoridad.`

**7 · Aprovisionamiento (S10).** Django admin. It is outside this design system entirely and gets no styling budget. Two requirements only: nothing in it is ever shown to a tenant user, and **a newly provisioned tenant lands on an empty state with an action, not on a blank Panel** — `Todavía no hay catálogo` with `Cargar catálogo` as the primary, per §B.10.2. A first-run Panel drawn with six zero-value KPI tiles is indistinguishable from a broken one.

### B.8.5 The header, the filter bar and the record panel

**Header** — §A.13.2. Sticky, `z-30`, and the veil (`rgba(251,251,251,0.85)` with a 14px backdrop blur) is correct here because nothing scrolls under it at speed. Exactly one `t-28` title per route.

**Filter bar** — §A.13.3, `sticky` under the header, and it renders the URL's typed search params rather than local state: a filter the URL does not carry is not set, and clearing the bar clears the params. The search field writes its param debounced and as a history _replace_, so `Back` returns to the previous view rather than walking backwards one keystroke at a time. Its right slot is the provenance line: §B.9's sync state on any surface a till touches, the model's training line on Compras, the rollup's freshness on Reportes.

**Record panel** — 440px, L2, `border-left: 1px solid rgba(0,0,0,0.08)`, no radius on its leading edge. It **pushes** the content region rather than overlaying it, and takes no scrim, so the table behind it stays navigable — which is the whole point of `j`/`k`. Header 64px, `padding:0 20px`, hairline under it, a `t-20` title and a ghost close. Body scrolls independently at `padding:20px`. A footer, when the panel has actions, sits below the scrolling body on the panel's own surface under a hairline at `padding:12px 20px` — it must never scroll away from the content it acts on. Enter and exit at 160ms `ease-out`; under `prefers-reduced-motion` both collapse to opacity.

**Modal** — 560px for a confirmation, 720px for a form, L3, always scrimmed, always focus-trapped, restoring focus to its trigger on close. A confirmation names the consequence in its body and puts the consequence in the button: `Descartar orden` rather than `Aceptar`.

## B.9 Sync state, staleness and the offline vocabulary

**Cited by `../architecture.md` §5, rule 1.** This section fixes the words and the treatment the till uses when a local figure may differ from the server's. It is the most-seen indicator in the product: it appears on every surface a till touches, and a cashier looks at it more often than at any badge in §B.7.

### B.9.1 The sync state component

One component, `SyncStatus`, rendered from one state machine. No surface re-implements it and no surface omits it.

| State      | Dot                 | String                                                 | When                                         |
| ---------- | ------------------- | ------------------------------------------------------ | -------------------------------------------- |
| `synced`   | **none**            | **Sincronizado hace 4 s**                              | Nothing pending, last pull inside the window |
| `pending`  | Informative, solid  | **Sincronizando · 3 pendientes**                       | Operations queued, the connection is up      |
| `offline`  | Warning, **hollow** | **Sin conexión · 12 por enviar**                       | No connection. Selling normally              |
| `degraded` | Warning, solid      | **Sincronización con problemas · sesión vencida**      | Pushes are failing for a stated reason       |
| `blocked`  | Critical, solid     | *unwritten — see below*                                | The one interruption. **Nothing raises it at v1** (§B.9.4) |

**At rest it is text and nothing else.** The handoff draws `Sincronizado hace 4 s` as a bare 11px `#727272` line with no dot, and that is the correct resting form: a green dot on every screen all day is decoration, and a decoration that is always there is a decoration nobody reads when it changes. **The dot appears only when the state leaves `synced`.** That is what makes it noticeable.

**Family choices, and why.** `pending` is informative because the system is working — it is not a problem and must never be dressed as one on a network that drops several times a week. `offline` is warning with a **hollow** dot: the system is waiting on something outside itself, which is exactly §B.7.2's ring. `degraded` is warning with a **solid** dot: the system itself is failing, now. `blocked` is critical and is the only state that escalates out of the status line.

**Rules:**

- **It is never a spinner and never animates.** Progress is a count. A pulsing dot on a till is a distraction at a counter with a customer waiting, and §B.14 forbids it anyway.
- **The count is operations, never a percentage.** `12 por enviar` is a number a cashier can act on; `73%` is not.
- **Only `blocked` interrupts.** Every other state is a line in the chrome. `blocked` additionally renders a banner at the top of the content region: L2 with `border-color:#b04a3f` and the critical tint as its fill, `--radius-card`, `padding:16px`, a `t-14`/500 title, a `t-12` `#555555` body, and one `sm` action. **The geometry is fixed here; the words are not written here.** Nothing raises `blocked` at v1 — its only producer was a device exhausting a fiscal numbering lease, and Botica allocates no fiscal numbers (`../architecture.md` §8, A6, §B.9.4) — so **the state string and the banner's title, body and action are written by the first stage that ever raises it**, against the condition it actually raises. Live copy specified for a condition that cannot occur is how a stale string ships: nothing renders it, so no review catches it, and it is read as a promise the product still makes. Whatever that stage writes says what happened, why, and the one thing that fixes it, and claims nothing about what may still be sold — that is `../architecture.md` §8's decision, not this document's.
- **It never says `Error de sincronización`.** Every degraded reason is named: `sesión vencida`, `el servidor rechazó los datos`, `almacenamiento lleno`, `versión desactualizada`.
- **A state must dwell 2 seconds before it is replaced.** A connection that flickers must not make the line flicker; a cashier who sees `Sin conexión` blink four times in a second stops believing any of it.
- **Placement.** Office surfaces: the filter bar's right slot at 11px `#727272` (§A.13.3), as drawn. Counter surfaces: the header's right slot at `t-12` inside a 44px hit target that opens the panel below. Every surface that reads the local store renders it; a surface without one is a surface where a cashier cannot tell whether what they are reading is current.
- **Accessibility.** `role="status"`, `aria-live="polite"`. **A state change announces; the ticking clock does not.** Recompute the relative time on a 5-second interval under a minute and a 30-second interval above it, and re-render only the string — never the surrounding surface, and never the live region unless the state itself changed.

**Relative-time ladder**, and it is the same one §B.9.2 uses: under 60 s → `hace 4 s` · under 60 min → `hace 3 min` · under 12 h → `hace 2 h` · at or beyond 12 h → the absolute stamp `al 31/08 06:00`. The space between the number and its unit is a non-breaking space (§A.11).

### B.9.2 The staleness convention

The problem this solves: a cashier must be able to tell a number that may be out of date from one that is not, **without alarm fatigue** — on a till that is offline weekly, a field of amber warnings is a field nobody reads — and **without a confident figure that is wrong**, which is the failure `../architecture.md` §5 rule 1 exists to prevent.

**Three tiers, and the tier is a property of a number's provenance, not of its age alone.**

**1 · Authoritative — no marker.** The till's own sede, read from the local store, including its own pending events. This is the majority of every counter screen: this sede's stock, this sede's prices, this ticket's arithmetic. It renders at full strength with nothing added, because adding a marker to the numbers that are right is how a marker stops meaning anything.

**2 · As of a sync — the staleness marker.** Another sede's stock, a network figure shown at a counter, an office figure whose source rollup is behind. The figure **keeps its full-strength colour** and gains a marker: a **4px hollow dot, `border:1px solid #909090`, `background:transparent`, 6px after the figure, `vertical-align:3px`**. Its reading is in the row's or tile's secondary slot, or on hover and focus: `Suba · hace 3 min`.

**The marker is never a colour and never a badge.** Staleness is metadata, not status — colouring it would put it in §B.7's vocabulary, where it would compete with the states that are actually about the business. A hollow grey dot is the quietest mark in the system that is still a mark, and it is exactly one glyph away from §B.7.2's hollow status dot, which already means _not yet settled_.

**3 · Unknown — not a number at all.** Where a figure cannot be computed now, it renders as an em dash `—` in `#909090` with its reason in the secondary slot: `sin datos`, `sin conexión`, `sin ventas en el periodo`. **Never a zero. Never a last-known figure without its marker. Never a skeleton that never resolves.** A zero standing in for "we don't know" is the single most expensive lie an inventory system can tell.

**Rules:**

- **The marker is per figure, not per screen.** A banner saying _"los datos pueden estar desactualizados"_ is precisely the thing that teaches people to ignore banners. **One exception:** a surface whose entire content is one snapshot — the Panel opened at a counter, a report — carries a single 11px line under its title (`al 31/08 06:00`) instead of forty dots.
- **Thresholds are stated per surface and never guessed.** Defaults: a figure is stale at the counter when its source is over **60 seconds** old, and in the office when its source is over **5 minutes** old. Beyond **24 hours** in the office it stops being relative and prints the absolute stamp.
- **Money the customer is about to pay never carries a marker.** The ticket's arithmetic is local and exact, and `sale_lines.unit_price` records what was actually charged (`../architecture.md` §5). What can be behind is the _price list_, and its freshness is stated once, in the sync panel, not on every line of every ticket.
- **Another sede's stock is always marked.** The handoff draws `Quiebre · hay 96 en Suba` — that `96` is the canonical case this convention exists for, and it renders with the marker and the reading `Suba · hace 3 min`.
- **A stale figure is still a figure.** It is not dimmed, not italic, not struck through and not smaller. The only thing that distinguishes it is the dot, and that is the point: a cashier reading a stale number is reading the best number that exists.

### B.9.3 The sync panel

Activating the sync line opens an L3 popover, 320px, `padding:16px`, `gap:12px`, anchored to the line. It holds, at `t-12` with 11px labels: `Última descarga` and `Último envío` as relative times; the pending queue broken down by kind (`Ventas 2 · Movimientos 9 · Conteos 1`); the device's `label` and its sede; and the storage-persistence state (§B.9.4). One `sm` secondary: `Sincronizar ahora`. It is a read-out, not a control panel — there is no button here that changes what syncs. **And it carries no fiscal read-out of any kind**: Botica allocates no fiscal numbers, so there is no numbering range to report and nothing on this panel mentions the handoff (`../architecture.md` §8, A6). A track bar reserved for a figure that has no producer renders empty on every till in the pilot.

### B.9.4 Storage persistence

`navigator.storage.persist()` is requested at first run and **its state is displayed** (`../architecture.md` §5). Granted: a line in the sync panel, `Almacenamiento protegido`, positive, and nothing else. Denied: `degraded` with the reason `el navegador puede borrar los datos sin enviar`, a one-time dialog at device claim explaining what to change, and a persistent chip that stays for as long as the state does — and the till keeps selling throughout. An unsynced sale living in evictable storage is a risk the operator must be told about, and it is the only technical browser detail this product ever puts in front of a cashier.

**Persistence denied is `degraded`, never `blocked`, and this is settled** (`../architecture.md` §5). `blocked` means stopping is safer than continuing, and **at v1 nothing produces it**: its only producer was a device exhausting its fiscal numbering lease, and Botica no longer allocates fiscal numbers because it no longer issues fiscal documents (`../architecture.md` §8, A6). The state stays defined — its family, its dot and its banner geometry fixed in §B.9.1, its words deliberately unwritten — because the day a till must genuinely stop is not the day to design that treatment from scratch, and the copy belongs to the stage that first raises the state. Eviction is a risk, not an invalidity — a till that refuses to sell over it trades a possible loss for a certain one, and a droguería that cannot sell during a blackout is the failure this whole architecture is shaped to avoid.

## B.10 Loading, empty and error

### B.10.1 Loading — geometry-matched skeletons

```
background:#f4f4f4; border-radius:9px; animation: pulse 1.6s ease-in-out infinite
```

**No spinners.** The one exception in the entire product is the 14px inline indicator inside a button the user has already pressed (§B.6.2).

**A skeleton reproduces the geometry it replaces.** A loading Existencias is fifteen rows at the real 48px height with a bar in each cell at that column's real width — not a grey block. A loading KPI tile is a 12px label bar at 40% width and a 36px figure bar at 55%, in a real 12px card at real 16px padding. A loading histogram is thirty bars at a flat 40% height. A loading counter panel is three real ticket lines and a real totals block. **If the skeleton and the loaded state differ in height, the skeleton is wrong** — a table that grows 200px when it resolves has thrown away the reader's place.

Bar heights: `10px` for an 11px caption, `12px` for a 12px label, `14px` for a 14px cell, `20px` for a 20px heading, `34px` for a control. Vary widths per row by ±20% so the block does not read as a grid.

- **Skeletons are first paint only.** A table re-fetching after a filter, sort, page or page-size change keeps its previous rows at `opacity:0.6` with `pointer-events:none` and shows a 2px `#171717` progress line under the filter bar. Blanking a populated table on every keystroke is worse than the wait.
- **A background delta pull is silent.** It never dims rows, never draws the progress line, never moves the keyboard cursor and never collapses an open panel. Changed values arrive at the 140ms transition and nothing else happens. Only a fetch the user asked for earns the dim-and-progress treatment.
- **Optimistic writes show no loading state at all.** Adding a ticket line, editing a `Sugerido`, checking a checklist item: the new state renders immediately with a revert path defined (`../architecture.md` §5).
- **The assistant is the one genuinely async card, and the handoff flags it.** Card B renders its 26px blue tile immediately with two skeleton lines at the real 14/20 and 12/18 leading; card C renders three suggestion-card skeletons at the real `padding:14px 16px`. **After 2.5 seconds** the card names the wait at 12px `#727272`: `El asistente está tardando más de lo normal.` If the call fails or the device is offline, the card switches to the local fallback and labels itself — a `t-10` mono eyebrow `MODO LOCAL` in the card's top-right — which `../architecture.md` A8 requires and this document gives a treatment.
- Under `prefers-reduced-motion: reduce`, the pulse is replaced with a static `#f4f4f4` fill.

### B.10.2 Empty states — every one carries an action

Centred in its panel, `max-width:420px`, `padding:48px 0`.

```
title:  t-16 #171717
body:   t-14 #555555, margin-top:8px
action: md button, margin-top:20px
```

No illustration and no icon over 24px. Three kinds, and conflating them is the defect:

| Kind                    | Title                                        | Body                                       | Action                                                                    |
| ----------------------- | -------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------- |
| **Never populated**     | `Todavía no hay existencias`                 | Names what fills it and where that happens | `Cargar mercancía` — primary                                              |
| **Filtered to nothing** | `Ningún producto coincide con estos filtros` | Echoes the active filters back verbatim    | `Quitar filtros` — **secondary, never primary.** The intent was to filter |
| **Deliberately empty**  | `No hay documentos pendientes de envío`      | `18 documentos confirmados hoy.`           | None, or a ghost link to the list it counts                               |

An empty state whose body is `Sin datos` or `No hay nada aquí` is a defect. So is a section header with nothing under it — **a section a role or a capability can empty is gated at its header, not inside its body**, because a component cannot remove its own parent and a `null` inside a `gap:16px` stack leaves the gap.

### B.10.3 Error states — name the operation, the entity and the recovery

Botica's vendor surface at v1 — the client's own invoicing system, the model gateway, object storage — makes `Ocurrió un error` actively harmful: a person cannot tell a transient failure from a refusal, and those two need opposite responses. A transient failure is waited out and retries itself; a refusal is a payload someone has to fix, and no amount of waiting fixes it. **Botica hands documents to the client's invoicing system and never to the DIAN** (`../architecture.md` §8, A9), so no error string here reports a DIAN outcome — there is none to report.

| Scope         | Treatment                                                                                                                                                                              |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Field**     | `t-12` `#b04a3f` at `margin-top:6px`, the control at `border-color:#b04a3f` with `aria-invalid`                                                                                        |
| **Region**    | L2 at `border-color: rgba(176,74,63,0.32)` on the critical tint, `--radius-card`, `padding:16px`. Title `t-14`/500 `#171717`, detail `t-12` `#555555`, one `sm` secondary `Reintentar` |
| **Row**       | The critical badge in the status column, with the reason in the record panel. **The row never turns red** — one failed row must not dominate fifteen good ones                         |
| **Route**     | The empty-state geometry with a retry and a `t-10` mono `#727272` correlation id that is `user-select:all`                                                                             |
| **Transient** | A toast: L3, `background:#171717`, `color:#fbfbfb`, `t-14`, `--radius-control`, bottom-right, 5 seconds, dismissible, never carrying the only copy of an action                        |

Required shape:

> **`No pudimos enviar 3 documentos de Chapinero.`** El sistema de facturación respondió 503 después de 4 intentos. Los documentos quedan pendientes de envío y se reintentan automáticamente. `[Reintentar ahora]` `req_8f2a1c04`

A refusal takes the same shape and a different ending, because its recovery is a person and not a clock: `El sistema de facturación rechazó el documento: el adquiriente no tiene número de documento.` names what must be fixed and promises no automatic retry.

Prohibited: `Error`, `Ocurrió un error`, `Falló la solicitud`, and any raw exception, stack trace or vendor payload rendered to a user. A vendor's response body belongs in `fiscal_documents.response` and the platform-admin view.

**Every error a retry can fix carries a retry. Every error a retry cannot fix says what a human must do instead.** And no error at the counter ever blocks a sale: `../architecture.md` §5 rule 2 is binding, and an error treatment that stops a cashier selling is a design defect, not a safety measure.

## B.11 The counter density mode

The handoff draws a desktop administrator at 1440px and up, seated. A till is used **standing, at speed, one hand on a scanner, with a customer watching**, sometimes on a smaller and older screen. Counter density is the answer, and its constraint is that it must not become a second product.

**It is a mode of this system, not a variant of it.** Colours, tints, radii, borders, shadows, the badge geometry, the eyebrow face and its tracking, the focus ring and the motion budget are **identical**. Three things change: control heights, hit targets, and the type of the ticket. A screenshot of the two densities side by side must read as one product.

**It is selected by the surface, never by the user and never by the viewport.** Mostrador, cobro, devolución, apertura and cierre de turno, and conteo render at counter density. There is no toggle, and a `cashier` on a 27" monitor still gets it — the reason is the posture and the speed, not the pixels.

|                              | Desktop                  | Counter                       |
| ---------------------------- | ------------------------ | ----------------------------- |
| Control height               | 34px                     | **44px**                      |
| Primary action               | 34px (40px for `Cobrar`) | **52px**                      |
| List / table row             | 48px                     | **56px**                      |
| Nav item                     | 38px                     | **44px**                      |
| Icon-only control            | 34 × 34                  | **44 × 44**                   |
| Checkbox hit target          | 34px                     | **44px**                      |
| Minimum hit target, any axis | 34px                     | **44px**                      |
| Content inset                | `32px 40px`              | **`24px 28px`**               |
| Right panel                  | 380px                    | **420px**                     |
| Sidebar                      | 280px                    | **64px icon rail by default** |

**The extra height is paid for out of chrome, not out of content.** The inset drops 8px vertically and 12px horizontally, the filter bar is absent from every till surface, and the sidebar collapses because a `cashier` reaches two items — which together give back more than the taller rows take.

**Type promotes one step on the existing ladder; no new step is added** (§B.1's eight remain eight):

| Role                               | Desktop | Counter                                                            |
| ---------------------------------- | ------- | ------------------------------------------------------------------ |
| Ticket line name                   | `t-14`  | **`t-16`**                                                         |
| Ticket line subtext (`2 × $3.900`) | `t-11`  | **`t-12`**                                                         |
| Ticket line amount                 | `t-14`  | **`t-16`**                                                         |
| Ticket total                       | `t-28`  | **`t-36`** — the largest step in the system, read across a counter |
| Assistant recommendation           | `t-14`  | **`t-16`**                                                         |
| Suggestion context line            | `t-12`  | **`t-14`**                                                         |
| Button label                       | `t-12`  | **`t-14`** at `md`, `t-16` at `lg`                                 |
| Eyebrow                            | `t-10`  | **`t-10`** — unchanged; it is already sized for its role           |

**The scanner owns the keyboard.** A till surface always has a focused capture field, focus returns to it after every action, and single-letter shortcuts are prohibited on till surfaces (§B.13). This is what lets the ticket's lines stay simple rows rather than a tab-stop obstacle course between two scans.

**Screen floor: 1280 × 720 with no horizontal scroll.** That is what an older till has, and it is the number the counter's layout is built against — 64px rail + 28px inset + a 420px panel leaves 740px for the working column, which holds the transcript, the recommendation and three suggestion cards. Below 1280 is unsupported in v1 (§B.17).

**What does not change, stated so nobody optimises it away:** the badge is still 12px/16px with an 8px dot, because it is read at a glance and it is already large enough; the eyebrow is still 10px mono at 0.18em; the hairlines are still 6%; the transition is still 120–160ms. Enlarging those would make the till a different product, which is the failure this mode is defined to avoid.

## B.12 Data display and charts

### B.12.1 Blue means quantity, and nothing else

**A deeper blue is a larger number.** On every chart, on every surface. It never marks a state, a category, a sede, a person or a direction of travel. The five status families (§B.7) carry state; the ten-step blue ramp (§A.5) carries magnitude; the two vocabularies never overlap, and that separation is what lets a screen be chromatic without any colour being ambiguous.

**Blue is not an accent.** No button, tab, link, badge, nav item or row marker reads from the ramp. It appears in charts, in the assistant's 26px identity tile, and in the focus ring (§B.5.1) — three places, all named.

### B.12.2 Depth is normalised within a series

§A.5 recovered the finding: the three drawn charts bucket the same ten steps differently, because they encode different ranges. The rule that generalises it:

> **A series maps its own values onto the ramp — the largest to `--data-100`, the smallest non-zero to `--data-10` — and nothing is snapped to a global threshold.**

A stock bar reads a row's quantity against that row's own capacity; a per-sede bar reads a sede against the leading sede; a histogram reads a day against the window's maximum. Values between two steps are mixed at read time rather than snapped, so the ramp is continuous rather than a ten-value palette.

### B.12.3 Nothing is read from colour alone

- **Depth is always redundant with geometry.** A deeper bar is a taller bar; a deeper segment is a wider one; a fuller ring is a larger arc. The handoff does this without exception and so does every chart added to it.
- **Every figure a chart draws is printed as text in the same card**, and the whole series is in the figure's `aria-label`. The stock bar never appears without its 44px right-aligned number (§A.18.1). The donut never appears without `58,6%` and `3.412 de 5.824 sugerencias`.
- **A zero draws no fill.** The handoff's zero-stock row draws a 4% sliver at the weakest step (§A.5); that is a prototype artefact and it is overruled here. A bar that shows something at zero is a bar that lies, and the row already says `Quiebre`. The track alone is the zero state.
- **The track is never the only carrier.** `#e0eefc` measures 1.18:1 against white and the weakest step 1.73:1 — both below any contrast floor, and both fine, because neither is ever the sole encoding of anything.
- **A direction is not a status.** A change indicator is `t-11` `#6b6b6b` with a 12px arrow, never green and never red — as drawn on both the rising and the falling KPI (§A.19.1). Fewer quiebres and more sales are both good; one palette cannot know which way is up for an arbitrary measure, so it does not try.

### B.12.4 One barred column per table

`Existencias` earns its bar because the level against capacity is what the screen is for. A second bar in the same table turns a column of figures into a picket fence: the bars become the loudest thing on the row, the figures get pushed into a ragged right edge, and no column is scannable because every column is shouting. Everywhere else the number stands on its own and the sort control does the comparing.

### B.12.5 The chart set

Four forms, all drawn, and they are the whole inventory: the **ranked bar list** (§A.20), the **column histogram** (§A.20), the **donut** for one part-of-whole (§A.20), and the **progress bar with an optional target marker** (§A.19.1). A new report picks one of these four. No pie chart, no stacked area, no dual axis, no legend, no gridline, and no chart animation — a chart that animates in is a chart the reader waits for.

## B.13 Keyboard and focus

### B.13.1 Focus is visible on everything

Every interactive element — button, link, input, select, checkbox, radio, table row, nav item, menu item, segment, chip, editable cell — renders the §B.5.1 ring on `:focus-visible`. No exceptions, including for elements that are "obviously" clickable.

- Tab order follows DOM order. `tabindex` above 0 is prohibited.
- Modals and overlaying sheets trap focus and restore it to their trigger. **A pushing record panel does not trap focus** — the table behind it stays navigable, which is what `j`/`k` is for.
- Every route begins with a skip-to-content link, visually hidden until focused.
- `aria-live="polite"` announces: filter result counts, selection counts, optimistic-write confirmations, sync **state** changes (never its ticking clock, §B.9.1), and toast content.
- Rows carry `scroll-margin-block: 96px` so `j`/`k` never parks a row under the sticky header or the footer.
- A list container is `tabindex="0"` with `role="grid"`, and `aria-activedescendant` tracks the cursor so a screen reader follows it without moving DOM focus per row.

### B.13.2 Office surfaces

**Every list surface supports `j`/`k`** — Existencias, Orden sugerida, Precios, Traslados, Reportes' tables, the compliance checklist, the fiscal work list. Not per-screen opt-in.

| Key                                              | Action                                                                                             |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| `j` / `↓`                                        | Cursor to the next row                                                                             |
| `k` / `↑`                                        | Cursor to the previous row                                                                         |
| `Enter`                                          | Open the row's record panel                                                                        |
| `x`                                              | Toggle the row's checkbox                                                                          |
| `Shift+J` / `Shift+K`                            | Extend the checked set while moving                                                                |
| `Esc`                                            | Close the panel; with no panel, clear the checked set; with nothing checked, blur the search field |
| `/`                                              | Focus the search field                                                                             |
| `⌘K`                                             | **Reserved.** See below                                                                            |
| `⌘,`                                             | Open the settings dialog                                                                           |
| `⌘Enter`                                         | Submit the focused form; approve the focused order                                                 |
| `g` then `p` / `i` / `c` / `r` / `m` / `s` / `e` | Panel · Inventario · Compras · pRecios · Mostrador · Sedes · rEportes                              |
| `?`                                              | The shortcut sheet                                                                                 |

`j`/`k` past the last row advances to the next page and lands on its first row. Single-letter shortcuts are suppressed whenever focus is inside a text input, a textarea or a contenteditable. Every shortcut here appears in the `?` sheet, and every menu item that has one shows it at `t-10` mono `#727272` at the row's end.

**`⌘K` is reserved and not built in v1.** It is the command palette's key and nothing else may claim it — not a search field, not a shortcut on any surface, not a browser-default override. Reserving it now costs nothing and means the palette can arrive later without retraining anyone. If it is pressed before the palette exists, nothing happens; do not bind it to the search field as a placeholder.

### B.13.3 Counter surfaces

**The scanner is a keyboard, so single-letter shortcuts are prohibited on every till surface.** A scan is a burst of characters followed by `Enter`, and any surface where `j` means something is a surface where scanning a product code navigates.

| Key       | Action                                                                     |
| --------- | -------------------------------------------------------------------------- |
| `Enter`   | Commit the capture field — a scanned code, a typed quantity, a search term |
| `F2`      | `Cobrar`                                                                   |
| `F4`      | `Buscar producto`                                                          |
| `F8`      | Open the sync panel                                                        |
| `+` / `−` | Quantity up and down on the focused ticket line                            |
| `Esc`     | Clear the capture field. **Never closes or cancels the sale**              |
| `⌘K`      | Reserved, as above                                                         |

**Focus returns to the capture field after every action** — after adding a suggestion, after adjusting a quantity, after closing a dialog. A till whose focus is somewhere else is a till where the next scan goes into the void, and that is discovered at a counter with a queue.

## B.14 Motion

- **120–160ms `ease-out`** on colour and background — every state change: hover, focus, press, row fill, badge change. The handoff's own budget (§A.21). Use 140ms as the default.
- **160ms `ease-out`** for a panel or dialog entering or leaving, and for a menu opening.
- **No entrance animations.** The handoff says so explicitly. Nothing fades in on page load, nothing staggers, nothing rises.
- **Nothing translates.** Not on hover, not on press, not on a card, not on a panel. Planes hold position; only their border, their fill and their shadow change, and the shadow already exists at rest so nothing blooms in from `none`.
- **`transition: all` is prohibited.** Enumerate the properties. Differently-timed properties arriving at different moments is what reads as "not smooth", and it is never the duration's fault.
- **The sync indicator does not animate** (§B.9.1), and neither does any chart.
- `prefers-reduced-motion: reduce` replaces the skeleton pulse with a static fill, reduces panel motion to opacity, and removes every transition over 100ms.

## B.15 Tokens introduced by Part B

Everything else in this document composes from Part A. This is the complete delta.

```css
:root {
  /* NEW — the only two colour values Part B adds, both derived alphas
     on bases the system already carries. */
  --scrim: rgba(
    0,
    0,
    0,
    0.32
  ); /* the border ramp's own base, at overlay alpha */
  --edge-destructive: rgba(
    176,
    74,
    63,
    0.32
  ); /* --critical #b04a3f at 32% — the destructive button's border */

  /* NEW — one type step. §B.1 states why it is the only one. */
  --text-16: 16px; /* leading 24px — the reading step */

  /* NEW — counter density. Four heights, all on the §A.9 2/4 scale. */
  --h-control-counter: 44px;
  --h-primary-counter: 52px;
  --h-row-counter: 56px;
  --w-panel-counter: 420px;

  /* PROMOTED — Part A values given a second job, not new values. */
  --shadow-overlay:
    0 1px 2px rgba(20, 20, 20, 0.04), 0 18px 44px rgba(20, 20, 20, 0.08);
  /* §A.7's "presentation only" shadow becomes L3 */
  --neutral: #727272; /* --ink-label, doubling as the fifth status family */
  --neutral-tint: #e8e8e8; /* --active, doubling as the neutral tint */
  --radius-check: 4px; /* --radius-mark, doubling as the checkbox radius */

  /* FROM THE HANDOFF'S OWN INTERACTION NOTES (§A.21) — recorded as tokens. */
  --hover-row: #f4f4f4;
  --hover-nav: rgba(0, 0, 0, 0.04);
  --hover-primary: #000000;
  --hover-secondary: rgba(0, 0, 0, 0.28);
  --focus-ring: 2px solid #0071e3; /* with outline-offset: 2px */
}
```

**Two new colour values, one type step, four heights.** That is the whole delta, and it is small because the handoff is unusually complete: it fixed ten blue steps, four semantic families with tints and two dot treatments, a four-step border ramp, ten neutrals, seven radii, a fourteen-value spacing scale, three shadows and a full height system. Part B's job was mostly to say what those values mean on the surfaces nobody drew.

**One correction to a Part A value, and it is an accessibility fix.**

> **On the `#f4f4f4` chrome plane, tertiary text steps from `#727272` to `#6b6b6b`.**

`#727272` measures **4.81:1** on `#ffffff` and **4.65:1** on `#fbfbfb` — both clear WCAG AA for small text — and **4.38:1 on `#f4f4f4`**, which fails. The call sites are exactly three: the `thead` label, the table footer's range and its right-hand text, and a section-card header's counter. `#6b6b6b` is already in the system (it is the KPI-footnote colour, §A.3) and measures **4.85:1** on `#f4f4f4`. No new token, no palette change, and the two greys stay eleven units apart everywhere else.

**Values below AA that are accepted, and why.** `#909090` at 3.19:1 on white is placeholders, disabled values and the ticket index — placeholder text and disabled controls are exempt under WCAG 1.4.3, and the ticket index is a positional ordinal restated by the line's own order. `#c8c8c8` is the breadcrumb separator, the pagination `…` and a disabled arrow — none of which carry information. The version string `Botica 2.4.1` at `#909090` on `#f4f4f4` is 2.90:1 and is the one informational value below AA in the system; it is accepted **only** because the settings dialog states the same version at full contrast, and if that duplication is ever removed the string steps to `#6b6b6b`. The `Cobertura` numeral in `#b04a3f` measures 5.39:1 on white and **4.40:1 on a selected row's `#e8e8e8`** — 0.1 under AA on one state of one column, accepted because the `Por qué` cell restates the reading in words and the colour is never the only signal (§B.17 keeps it on the list).

## B.16 Conformance checklist

A surface is conformant when all of the following hold. A reviewer can run this against a built screen without opening a design file.

1. **Type.** Every font-size is one of the eight steps in §B.1, with that step's leading, weight and tracking. No 13, 15, 18, 24 or anything above 36.
2. **Colour.** Every colour is a token from Part A or §B.15. No hex literal in a component. No colour outside the ten neutrals, the four border alphas, the eleven blues and the five semantic families.
3. **Radius.** Every radius is 4, 6, 7, 9, 12, 16 or 999.
4. **Spacing.** Every gap, padding and margin is a value from §A.9. Content inset is `32px 40px`, or `28px 40px` on a single-panel route, or `24px 28px` at counter density.
5. **Numbers.** Every numeric carries `tabular-nums`. Every figure is formatted by the §A.11 formatter: thousands dot, decimal comma, `$` unspaced with no decimals, `M` above a million with a non-breaking space, U+2212 for a negative, `MM/AAAA` for a lot expiry.
6. **States.** Every interactive element has rest, hover, active, `:focus-visible` and disabled, and the focus ring is exactly `2px solid #0071e3` at `outline-offset: 2px`. There is no second ring anywhere.
7. **Status.** Every status value renders a §B.7.4 family plus a text label — never colour alone — and the solid/hollow choice follows §B.7.2. At most one badge column per table.
8. **Tables.** The frame is 16px with an 8% border and the plane shadow; `thead` is 40px L0; rows are 40/44/48/56 by mode; the selected row is `#e8e8e8` with `inset 2px 0 0 #171717`; there is no zebra striping; numeric columns are right-aligned; the page never scrolls horizontally.
9. **Grid contract.** Every server-authoritative table is `manualPagination`, `manualSorting`, `manualFiltering`, with `rowCount` from the API and page, size, sort and filter state in typed search params. Counter surfaces read the local store and are exempt.
10. **Sync.** Every surface that reads the local store renders `SyncStatus` (§B.9.1), at rest as text with no dot, and only `blocked` interrupts.
11. **Staleness.** Every figure that may be behind carries the §B.9.2 marker; nothing that cannot be computed renders as a zero; the ticket's own arithmetic carries no marker.
12. **Loading.** Every loading state is a geometry-matched skeleton at the real heights. No spinner outside a pressed button. A re-fetch dims rather than blanks.
13. **Empty.** Every empty state carries an action or explains why it does not, and never says `Sin datos`.
14. **Errors.** Every error names the operation, the entity and the recovery. No raw exception or vendor payload reaches a user. No error blocks a sale at the counter.
15. **Keyboard.** Every office list answers `j`, `k`, `Enter`, `x`, `Esc` and `/`. No till surface binds a single letter. `⌘K` is bound to nothing.
16. **Motion.** Every transition is 120–160ms `ease-out` with its properties enumerated. No `transition: all`. Nothing translates. No entrance animation.
17. **Roles.** No item, action or control a role cannot use is rendered disabled — it is not rendered.
18. **Density.** A till surface renders at counter density with a 44px minimum hit target, and the same tokens, radii and palette as the office.
19. **Language.** Every string is Spanish (Colombia). Every string the handoff draws matches it verbatim, including the advisory notice, which no prop removes.
20. **No `dark:` variant exists anywhere.**

## B.17 What the handoff leaves unresolved

The README lists six open items. All six are still open, and this document resolves what it can from the drawing and names what it cannot.

|       | Item                                                                               | Status                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ----- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1** | **Loading, empty and error states**                                                | **Resolved here** — §B.10, for tables, cards, the assistant, routes and fields                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| **2** | **Responsive behaviour below 1440px**                                              | **Open.** The handoff assumes desktop ≥1440. §B.11 fixes a **1280 × 720** floor for the counter and §B.8.1 defines the 64px rail, but nothing below 1280 is designed: which Existencias columns are expendable and in what order, whether the Mostrador's two columns stack or the panel becomes a sheet, whether the Panel's `2fr 1fr 1fr` row wraps, and whether the sidebar becomes an overlay drawer with a scrim. This needs a decision with design before S3 draws Existencias at a second width                                                        |
| **3** | **Role permissions in detail**                                                     | **Partly resolved** — §B.8.3 fixes the nav gating from `../architecture.md` §2. What is still open is per-action: whether a `cashier` may log a manual movement without approval, whether an `admin` may approve a price above a regulated cap, and what a `cashier` sees on the read-only Inventario (all sedes, or their own plus a lookup). These are product decisions, not visual ones, and they block S3 and S7 rather than this document                                                                                                               |
| **4** | **Precios, Sedes, Reportes**                                                       | **Recipes given** — §B.8.4 fixes each one's layout, columns and decisions, to the level a build agent can construct from. They are not drawn, and a design review of the built screens is still worth having before the pilot                                                                                                                                                                                                                                                                                                                                 |
| **5** | **The payment flow after `Cobrar`, and the confirmation after `Aprobar y enviar`** | **Open, and it is the largest gap.** Nothing about the payment surface is drawn: the payment-method split (`cash`, `debit_card`, `credit_card`, `transfer`, `other`), the change calculation, the split-payment case, the customer-identification step the DEE POS requires, the receipt's own on-screen and QR forms, and the printing question the architecture defers by ruling out hardware in v1. §B.11 gives it a density and §B.6 gives it buttons; the flow itself is S4 and S5 work and needs a design pass, not an extrapolation from this document |
| **6** | **Accessibility**                                                                  | **Partly resolved.** §B.15 fixes the `#727272`-on-`#f4f4f4` failure with a value the system already contains, and states every remaining sub-AA value with its measurement and its justification. Still open: `#b04a3f` at 4.40:1 on a selected row's fill; tab order inside a table whose rows are clickable and whose cells contain controls; the ARIA naming of the bars, the donut and the stock rail, which currently have none; and a screen-reader pass on the counter, which is the surface where a person is least able to stop and read             |

Three more this document adds to the list, because they were found in the markup rather than in the README:

|       | Item                                  | Status                                                                                                                                                                                                   |
| ----- | ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **7** | **The nav counter on an active item** | The prototype keeps `12` on an active Compras and drops `3` on an active Mostrador. §B.8.2 resolves it in favour of keeping it. If design disagrees, it is one rule to change and this is where it lives |
| **8** | **The zero-value stock bar**          | The prototype draws a 4% sliver under a stock of `0`. §B.12.3 overrules it to no fill. Flagged so the difference from the prototype is deliberate and documented rather than discovered                  |
| **9** | **The drawn expiry labels**           | §A.6 keeps the two drawn labels — `Vence en 5 meses` warning, `Vence en 6 meses` critical — which invert their own urgency. Part A is a record of the drawing; §B.7.4 states the rule instead            |
