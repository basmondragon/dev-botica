# Handoff: Botica — plataforma de gestión para droguerías (4 pantallas)

## Overview
Botica es una plataforma web de gestión para redes de droguerías en Colombia (multi-sede). Este paquete contiene cuatro pantallas de escritorio ya diseñadas:

1. **Panel** — resumen de la red (KPIs, venta diaria, venta por sede, aceptación de sugerencias del asistente, tabla por sede).
2. **Inventario / Existencias** — tabla maestra de existencias por producto, laboratorio, sede, lote y vencimiento, con estados de stock.
3. **Mostrador (venta asistida)** — captura del síntoma del cliente, recomendación del asistente, sugerencias basadas en existencias de la sede y ticket en curso.
4. **Compras — Orden sugerida** — orden de reposición generada por el modelo, con KPIs de la orden y tabla editable de cantidades sugeridas.

Toda la interfaz está en **español (Colombia)**: moneda COP con punto de miles (`$15.600`), decimales con coma (`24,8%`), millones abreviados como `$9,4 M`, fechas `MM/AAAA`.

## About the Design Files
Los archivos `.dc.html` de este paquete son **referencias de diseño creadas en HTML** — prototipos que muestran la apariencia y el comportamiento previstos, **no código de producción para copiar y pegar**. La tarea es **recrear estas pantallas en el entorno del codebase destino** (React, Vue, etc.) usando sus patrones y librerías establecidas (sistema de componentes, tabla, tokens, iconografía). Si aún no existe un entorno, elegir el stack más apropiado (sugerencia razonable: React + TypeScript + Tailwind o CSS Modules, TanStack Table para las tablas) e implementar ahí.

Notas técnicas sobre los archivos: usan estilos **inline** exclusivamente (decisión de la herramienta de prototipado, no una guía de implementación — extraiga tokens y clases/componentes al implementar) y requieren `support.js` (incluido) para renderizar. Ábralos en un navegador desde la carpeta del paquete.

## Fidelity
**Alta fidelidad (hifi).** Colores, tipografía, espaciado, alturas de fila y radios son finales y deben reproducirse con precisión, adaptándolos a los componentes existentes del codebase. Lo que **no** está resuelto: estados de carga, vacío y error, validación de formularios, comportamiento responsive (el diseño asume escritorio ≥1440 px) y accesibilidad de foco/teclado. Ver "Pendientes" al final.

## Shell común (las cuatro pantallas)
Cada pantalla es un lienzo de **1600 × 1000 px**, `display:flex`, `overflow:hidden`, fondo `#fbfbfb`, color base `#171717`, `font-size:14px`.

### Barra lateral (`nav`)
- Ancho fijo **280 px**, `flex-shrink:0`, fondo `#f4f4f4`, borde derecho `1px solid rgba(0,0,0,0.06)`.
- **Cabecera de organización**: alto 64 px, padding `0 20px`, borde inferior `1px solid rgba(0,0,0,0.06)`, `gap:10px`. Contiene: cuadro de marca 24×24 px, `border-radius:4px`, fondo `#171717`, letra "B" `#fbfbfb` 10 px/500; nombre de la organización "Droguerías La 45" 14 px/500 `#171717`; icono de colapsar panel 16×16 (`stroke #555555`, `stroke-width:1.5`).
- **Lista de navegación**: `padding:12px 0`, `gap:2px`. Cada ítem: `margin:0 12px`, alto 38 px, `padding:0 12px`, `border-radius:9px`, `gap:10px`, icono 16×16 `currentColor` 1.5, etiqueta 14 px.
  - Inactivo: color `#555555`, sin fondo.
  - Activo: fondo `#ffffff`, color `#171717`, `font-weight:500`.
  - Contador opcional alineado a la derecha (`margin-left:auto`), 11 px, `tabular-nums`; `#727272` normalmente, `#171717`/500 si el ítem está activo.
  - Ítems, en orden: Panel, Inventario, Compras (contador 12), Precios, Mostrador (contador 3), Sedes, Reportes.
- **Versión**: al final del bloque de navegación, `padding:0 20px`, "Botica 2.4.1" en Geist Mono 10 px, `letter-spacing:0.18em`, mayúsculas, `#909090`.
- **Pie de usuario**: alto 64 px, `padding:0 16px`, borde superior `1px solid rgba(0,0,0,0.06)`. Nombre 12 px `#171717`, rol/sede 11 px `#727272`; botón de ajustes 34×34 px, `border-radius:9px`, icono 16×16 `#555555`.
  - Panel / Inventario / Compras: "Marcela Ríos · Administradora".
  - Mostrador: "Andrés Peña · Mostrador · Chapinero".

### Cabecera de página (`header`)
Alto **64 px**, `padding:0 40px`, `gap:16px`, borde inferior `1px solid rgba(0,0,0,0.06)`, fondo `rgba(251,251,251,0.85)`.
- Izquierda: migaja 12 px `#727272` + separador `/` `#c8c8c8`, y título `h1` **28 px, weight 400, letter-spacing −0.025em**, `#171717`, alineados por `align-items:baseline`, `gap:8px`.
- Derecha (`margin-left:auto`, `gap:8px`): acciones de 34 px de alto (ver "Botones").

### Barra de filtros (Inventario y Compras)
Alto **52 px**, `padding:0 40px`, `gap:10px`, borde inferior `1px solid rgba(0,0,0,0.06)`, fondo `#fbfbfb`. A la derecha, texto de estado 11 px `#727272` ("Sincronizado hace 4 s" / "Modelo entrenado con 18 meses de venta · actualizado hoy 06:00").

## Componentes compartidos

### Botones
- **Primario**: alto 34 px (40 px en "Cobrar", 30 px en "Agregar" de fila), `padding:0 16px`, `border-radius:9px`, sin borde, fondo `#171717`, texto `#fbfbfb` 12 px/500 (14 px en "Cobrar").
- **Secundario**: mismas métricas, `border:1px solid rgba(0,0,0,0.16)`, fondo transparente, texto `#171717`.
- Ambos heredan la fuente (`font-family:inherit`).

### Chip de filtro
Alto 34 px, `border-radius:9px`, `padding:0 6px 0 14px` cuando lleva valor, 12 px/500.
- Activo (con valor): `border:1px solid rgba(0,0,0,0.16)`, texto `#171717`, y píldora de valor: alto 20 px, `padding:0 8px`, `border-radius:999px`, fondo `#f4f4f4`, 11 px/400 `#555555`.
- Inactivo: sin borde, `padding:0 14px`, texto `#555555`.

### Campo de búsqueda (Inventario)
Alto 34 px, `min-width:250px`, `padding:0 10px 0 12px`, `border-radius:9px`, `border:1px solid rgba(0,0,0,0.11)`, fondo `#ffffff`, icono lupa 15×15 `#909090`, placeholder 12 px `#909090`: "Buscar producto, laboratorio o lote".

### Badge de estado (píldora con punto)
`padding:4px 10px`, `border-radius:999px`, 12 px/16 px, texto `#171717`, `white-space:nowrap`. Punto 8×8 px, `border-radius:999px`, `margin-right:7px`, `vertical-align:1px`. Variantes:
| Semántica | Fondo | Punto |
|---|---|---|
| Positivo ("Suficiente", "En meta") | `#e3e9e3` | `#4e7a52` lleno |
| Atención ("Punto de reorden", "Margen bajo meta", "142 lotes") | `#ece7df` | `#8c6a33` lleno |
| Informativo ("Sobrestock · 94 días") | `#e3e7eb` | `#4c6a86` lleno |
| Crítico ("Quiebre · hay 96 en Suba") | `#f1e2e1` | `#b04a3f` lleno |
| Vencimiento próximo | `#ece7df` / `#f1e2e1` según urgencia | punto **hueco**: `border:1px solid` del color, fondo transparente |

Píldoras sin punto (en tarjetas de sugerencia): `padding:3px 8px`, `border-radius:999px`, 11 px/16 px — "Primera opción" `#e3e9e3`, "Con condición" `#ece7df`, "Se lleva junto" `#e3e7eb`. Chips de síntoma: alto 24 px, `padding:4px 10px`, fondo `#e8e8e8`, 11 px `#555555`; el chip de tratamiento activo usa fondo `#e3e7eb` y texto `#171717`.

### Tabla
Contenedor: `border-radius:16px`, `border:1px solid rgba(0,0,0,0.08)`, fondo `#ffffff`, `box-shadow:0 1px 2px rgba(20,20,20,0.02)`, `overflow:hidden`.
- `table`: `width:100%`, `border-collapse:collapse`, `table-layout:fixed`, `text-align:left`.
- `th`: alto 40 px, `padding:0 22px`, fondo `#f4f4f4`, borde inferior `1px solid rgba(0,0,0,0.11)`, **Geist Mono 10 px, weight 400, letter-spacing 0.18em, mayúsculas, `#727272`**, `white-space:nowrap`. Columnas numéricas: `text-align:right`.
- `tr`: alto **48 px** (44 px en la tabla por sede del Panel), borde inferior `1px solid rgba(0,0,0,0.06)`; la última fila sin borde.
- Fila seleccionada: fondo `#e8e8e8` + `box-shadow:inset 2px 0 0 #171717`.
- `td`: `padding:0 22px`, 14 px; primera columna `#171717`, resto `#555555`; números con `font-variant-numeric:tabular-nums`. Celdas de "Por qué"/razón: 12 px `#727272`.
- Pie de tabla: alto 48 px, `padding:0 22px`, borde superior `1px solid rgba(0,0,0,0.06)`, fondo `#f4f4f4`, texto 11 px `#727272`.

### Barra de existencias en celda (Inventario)
Riel 56×4 px, `border-radius:999px`, fondo `#e0eefc`, `vertical-align:3px`; relleno con ancho porcentual y color del azul según nivel (`#0071e3` alto → `#9ec9f4` bajo). A su derecha, número con `min-width:44px`, `text-align:right`, 14 px `#555555`, `tabular-nums`.

### Tarjeta KPI
`border-radius:12px`, `border:1px solid rgba(0,0,0,0.08)`, fondo `#ffffff`, `padding:16px`, `box-shadow:0 1px 2px rgba(20,20,20,0.02)`. Etiqueta 12 px/18 px `#727272` (con `min-height:32px` en el Panel para alinear etiquetas de dos líneas); cifra **36 px/40 px, letter-spacing −0.025em, tabular-nums, `#171717`**; nota al pie 11 px/18 px `#6b6b6b`. Delta: 11 px `#6b6b6b` con flecha SVG 12×12 (arriba/abajo).

### Tarjeta de sección (Mostrador)
`border-radius:16px`, `border:1px solid rgba(0,0,0,0.08)`, fondo `#ffffff`, `box-shadow:0 1px 2px rgba(20,20,20,0.02)`, `overflow:hidden`. Cabecera de tarjeta: alto 40 px, `padding:0 20px`, fondo `#f4f4f4`, borde inferior `1px solid rgba(0,0,0,0.06)`, título en Geist Mono 10 px / 0.18em / mayúsculas / `#727272`; a la derecha, contador 11 px `#727272`.

## Pantalla 1 — Panel · "Resumen de red"
**Propósito**: la administradora revisa la salud de las 6 sedes en un periodo.
- Cabecera: migaja "Panel", título "Resumen de red". Derecha: segmentado de periodo (contenedor alto 34 px, `padding:3px`, `border-radius:9px`, fondo `#f4f4f4`, `gap:2px`; segmento alto 28 px, `padding:0 14px`, `border-radius:7px`; activo fondo `#ffffff`, 12 px/500 `#171717`, `box-shadow:0 1px 2px rgba(20,20,20,0.04)`; inactivo 12 px `#555555`) con "7 días / **30 días** / 90 días", y botón secundario "Exportar".
- `main`: `padding:32px 40px`, `gap:16px`, columna, `overflow:hidden`.
- **Fila 1 — 4 KPIs** (`grid-template-columns:repeat(4,1fr)`, `gap:16px`):
  1. "Venta de la red" · `$412,8 M` · ▲ 6,4% · "contra $388,0 M en los 30 días previos".
  2. "Margen bruto" · `24,8%` + referencia `≥ 22%` (12 px `#727272`) · barra de progreso alto 6 px, fondo `#e0eefc`, relleno 70% `#1a7fe5`, marcador de meta: 2 px de ancho, `top:-4px;bottom:-4px`, `#909090`, en `left:calc(62% - 1px)` · "+1,9 pp desde el ajuste de precios".
  3. "Quiebres de stock" · `37` · ▼ 41% · "contra 63 en los 30 días previos".
  4. "Inventario por vencer · 90 días" · `$18,9 M` · badge atención "142 lotes" · "4,6% del inventario valorizado".
- **Fila 2 — 3 paneles** (`grid-template-columns:2fr 1fr 1fr`, `gap:16px`, `flex:1`):
  - **Venta diaria de la red**: subtítulo `$13,7 M promedio por día` (20 px, −0.025em, tabular). Histograma de 30 barras: `display:flex; align-items:flex-end; gap:1px`, `min-height:120px`, borde inferior `1px solid rgba(0,0,0,0.06)`; cada barra `flex:1` con altura porcentual; color por intensidad (`#9ec9f4` base, `#7fb9f0`, `#5fa8ed`, `#4c9bea`, `#3389e6`, `#0071e3` en el máximo). Eje: "27 jul" ↔ "25 ago", 11 px `#727272`.
  - **Venta por sede** (`dl`, filas `space-between`): etiqueta 76 px 11 px `#727272`, barra `flex:1` alto 6 px `#e0eefc` con relleno, valor 46 px alineado a la derecha 11 px `#555555`. Chapinero 112 M (100%, `#0071e3`), Kennedy 88 M (79%, `#2683e5`), Suba 76 M (68%, `#4c9bea`), Restrepo 59 M (53%, `#6cb0ef`), Bosa 48 M (43%, `#87bff1`), Usme 29 M (26%, `#9ec9f4`).
  - **Sugerencias del asistente aceptadas**: donut SVG 64×64 (`viewBox 0 0 64 64`, `transform:rotate(-90deg)`, r=28.5, `stroke-width:7`, riel `#e0eefc`, arco `#0071e3` con `stroke-dasharray="105 179"`, `stroke-linecap:round`), cifra `58,6%` (28 px/32 px, −0.025em) y "3.412 de 5.824 sugerencias" 11 px `#6b6b6b`. Bloque inferior separado por `border-top:1px solid rgba(0,0,0,0.06)`, título "Combinaciones más aceptadas" (Geist Mono 10 px/0.18em/mayúsculas): "Suero oral + antidiarreico 412", "Analgésico + protector gástrico 386", "Antigripal + vitamina C 341"; y comparativa: "Ticket con sugerencia $41.200" / "Ticket sin sugerencia $28.700".
- **Fila 3 — tabla por sede** (filas de 44 px). Columnas: Sede 22% · Venta 30 d 18% (der.) · Margen 14% (der.) · Quiebres 14% (der.) · Días de stock 16% (der.) · Estado 16%.
  Chapinero $112.480.900 / 26,1% / 4 / 38 / En meta · Kennedy $88.104.300 / 25,4% / 9 / 31 / En meta · Suba $76.290.100 / 23,8% / 6 / 44 / Sobrestock · Restrepo $59.870.400 / 24,2% / 5 / 35 / En meta · Bosa $48.115.700 / 21,3% / 11 / 22 / Margen bajo meta · Usme $29.006.200 / 22,7% / 2 / 29 / En meta.

## Pantalla 2 — Inventario · "Existencias"
**Propósito**: buscar y auditar existencias de las 6 sedes; detectar quiebres, reorden, sobrestock y vencimientos.
- Cabecera: migaja "Inventario", título "Existencias"; acciones: secundaria "Cargar mercancía", primaria "Nuevo traslado".
- Filtros: búsqueda; chip "Sede · Todas · 6"; chip inactivo "Categoría"; chip "Estado · Requiere acción"; chip inactivo "Vencimiento"; derecha "Sincronizado hace 4 s".
- `main`: `padding:32px 40px`. Tabla a altura completa; columnas: Producto 24% · Laboratorio 13% · Sede 12% · Lote 9% · Vence 9% · Existencias 13% (der., con barra) · Estado 20%.
- Filas (15; la primera seleccionada): Acetaminofén 500 mg × 100 / Genfar / Chapinero / A-2291 / 03/2027 / 412 (88%) / Suficiente · Sales de rehidratación oral / Tecnoquímicas / Chapinero / R-0148 / 11/2026 / 14 (12%) / Punto de reorden · Losartán 50 mg × 30 / MK / Kennedy / L-7730 / 08/2027 / 246 (54%) / Suficiente · Amoxicilina 500 mg × 20 / La Santé / Suba / M-3312 / 02/2026 / 178 (38%) / Vence en 5 meses · Omeprazol 20 mg × 30 / Procaps / Chapinero / O-5027 / 06/2028 / 468 (100%) / Sobrestock · 94 días · Ibuprofeno 400 mg × 50 / Genfar / Kennedy / I-9004 / 09/2027 / 0 (4%) / Quiebre · hay 96 en Suba · Metformina 850 mg × 30 / MK / Restrepo / F-1180 / 01/2028 / 289 (62%) / Suficiente · Loratadina 10 mg × 10 / Tecnoquímicas / Suba / T-4419 / 04/2027 / 63 (20%) / Punto de reorden · Enalapril 20 mg × 30 / La Santé / Chapinero / E-6602 / 12/2027 / 214 (46%) / Suficiente · Atorvastatina 20 mg × 30 / Procaps / Restrepo / V-2075 / 05/2026 / 121 (30%) / Vence en 8 meses · Suero fisiológico 500 ml / Baxter / Bosa / S-8891 / 10/2028 / 327 (70%) / Suficiente · Naproxeno 500 mg × 20 / Genfar / Suba / N-1136 / 07/2027 / 89 (26%) / Punto de reorden · Dipirona 500 mg × 10 / Tecnoquímicas / Usme / D-3308 / 11/2027 / 263 (58%) / Suficiente · Salbutamol inhalador 100 mcg / Bayer / Kennedy / B-7741 / 03/2026 / 7 (8%) / Vence en 6 meses · Hidroclorotiazida 25 mg × 30 / MK / Bosa / H-9928 / 02/2028 / 142 (34%) / Suficiente.
- Pie: izquierda "1-15 de 4.284" + "312 requieren acción" (11 px `#6b6b6b`). Derecha: selector "Filas 25" (alto 28 px, `border-radius:9px`, `border:1px solid rgba(0,0,0,0.11)`, fondo `#ffffff`, chevron 12×12 `#909090`) y paginación: flechas 28×28 px (`border-radius:9px`; deshabilitada `#c8c8c8`), páginas `min-width:32px`, alto 28 px, 12 px tabular; página actual fondo `#e8e8e8`, `#171717`/500; secuencia 1 · 2 · 3 · … · 172.

## Pantalla 3 — Mostrador · "Venta 4821"
**Propósito**: el auxiliar registra lo que dice el cliente y el asistente sugiere productos disponibles en la sede mientras arma el ticket.
- Cabecera: migaja "Mostrador", título "Venta 4821"; derecha: "Turno abierto 09:14" (11 px `#727272`, `margin-right:6px`) y botón secundario "Buscar producto".
- `main`: `display:flex`, `gap:20px`, `padding:28px 40px`, `overflow:hidden`. Columna izquierda `flex:1` (`gap:16px`), columna derecha fija **380 px**.
- **Izquierda A — "Qué dice el cliente"** (tarjeta de sección): cuadro de transcripción `border-radius:9px`, `border:1px solid rgba(0,0,0,0.11)`, fondo `#fbfbfb`, `padding:12px 14px`, 14 px `#171717`: "Lleva dos días con diarrea y algo de fiebre. Adulto, toma losartán." Debajo, chips (`gap:8px`, `flex-wrap`): "diarrea", "fiebre", "adulto", "tratamiento activo · losartán".
- **Izquierda B — recomendación del asistente** (tarjeta sin cabecera, `padding:20px`, `gap:14px`): icono cuadrado 26×26 px, `border-radius:6px`, fondo **`#0071e3`**, chispa SVG 15×15 blanca `stroke-width:1.8`. Texto principal 14 px/20 px `#171717`: "Prioriza rehidratación. Ofrece sales de rehidratación oral primero; el antidiarreico solo si no hay fiebre alta." Secundario 12 px/18 px `#555555`: "En este punto el 64% de los clientes que compran antidiarreico lo llevan junto con suero. La sede tiene las tres referencias disponibles."
- **Izquierda C — "Sugerencias sobre existencias de la sede"** (cabecera con contador "3 de 12 referencias"). Cada tarjeta de sugerencia: `border-radius:12px`, `border:1px solid rgba(0,0,0,0.06)`, `padding:14px 16px`, `gap:16px`, con nombre 14 px + píldora, línea de contexto 12 px/18 px `#727272`, precio 14 px tabular `white-space:nowrap`, botón "Agregar" de 30 px (primario en la primera opción, secundario en las demás).
  1. "Sales de rehidratación oral · sobre 27,5 g" · Primera opción · "14 unidades en Chapinero · repone la pérdida de líquidos, que es lo que más pesa en estos casos" · $3.900.
  2. "Loperamida 2 mg × 12" · Con condición · "8 unidades en Chapinero · no ofrecer si la fiebre pasa de 38,5 °C o si hay sangre" · $8.400.
  3. "Electrolitos bebida 500 ml" · Se lleva junto · "22 unidades en Chapinero · aparece en el 41% de los tickets con suero oral" · $5.200.
  Al pie de la tarjeta (`margin-top:auto`, `border-top:1px solid rgba(0,0,0,0.06)`): punto 8×8 `#8c6a33` + aviso 12 px/18 px `#555555`: "Con fiebre de más de dos días, remitir a consulta médica. Botica no diagnostica." **Este aviso es obligatorio y no debe eliminarse.**
- **Derecha — "Venta en curso"** (contador "3 ítems"): líneas con índice 20 px `#909090` tabular, nombre 14 px, subtexto 11 px `#727272` (`2 × $3.900`), importe 14 px tabular a la derecha.
  1. Sales de rehidratación oral · 2 × $3.900 · $7.800. 2. Acetaminofén 500 mg × 10 · 1 × $2.600 · $2.600. 3. Electrolitos bebida 500 ml · 1 × $5.200 · sugerido · $5.200.
  Totales (`margin-top:auto`, borde superior): Subtotal $15.600 · Descuento $0 · Total **$15.600** (28 px, −0.025em). Pie de la columna: `padding:16px 20px`, fondo `#f4f4f4`, borde superior; botón primario de 40 px "Cobrar" y nota centrada 11 px `#727272`: "Ticket promedio del punto: $28.700".

## Pantalla 4 — Compras · "Orden sugerida 248"
**Propósito**: la administradora revisa, ajusta y aprueba la orden de reposición propuesta por el modelo.
- Cabecera: migaja "Compras", título "Orden sugerida 248"; acciones: secundaria "Descartar", primaria "Aprobar y enviar".
- Filtros: chip "Proveedor · Coopidrogas"; chip "Sede · Chapinero"; chips inactivos "Categoría" y "Confianza del modelo"; derecha "Modelo entrenado con 18 meses de venta · actualizado hoy 06:00".
- `main`: `padding:28px 40px`, `gap:16px`.
- **4 KPIs**: "Referencias sugeridas" 42 / "de 1.184 activas en la sede" · "Valor de la orden" $9,4 M / "cubre 34 días de venta proyectada" · "Quiebres que evita" 11 / "referencias que se agotan en 7 días" · "Recortes vs. pedido manual" −$2,1 M / "en referencias de rotación lenta".
- **Tabla**: Producto 26% · Stock 11% (der.) · Venta / sem 14% (der.) · Cobertura 13% (der.) · Sugerido 13% (der.) · Por qué 23%.
  - "Cobertura" se colorea por urgencia: crítico `#b04a3f` (≤ ~4 días), atención `#8c6a33` (~5–20 días), normal `#555555`, sobrestock `#4c6a86` (> ~90 días).
  - "Sugerido" es un **campo editable**: `display:inline-block`, `padding:5px 12px`, `border-radius:9px`, `border:1px solid rgba(0,0,0,0.16)`, fondo `#ffffff`, 14 px `#171717` tabular. Cuando el valor es 0: fondo `#fbfbfb`, texto `#909090`.
  - Filas (11; la primera seleccionada): Sales de rehidratación oral 14 / 38 / 3 días / 220 / "Pico de temporada + quiebre reciente" · Acetaminofén 500 mg × 100 412 / 96 / 30 días / 180 / "Rotación estable, mantiene 45 días" · Ibuprofeno 400 mg × 50 **0** (rojo) / 61 / 0 días / 300 / "En quiebre, hay 96 en Suba" · Loratadina 10 mg × 10 63 / 29 / 15 días / 140 / "Sube con la temporada de polen" · Losartán 50 mg × 30 246 / 52 / 33 días / 120 / "Crónico, demanda predecible" · Metformina 850 mg × 30 289 / 44 / 46 días / 0 / "Cobertura suficiente, no pedir" · Salbutamol inhalador 100 mcg 7 / 12 / 4 días / 60 / "Lote actual vence en 6 meses" · Omeprazol 20 mg × 30 468 / 35 / 94 días / 0 / "Sobrestock, liberar capital" · Naproxeno 500 mg × 20 89 / 33 / 18 días / 100 / "Sustituto del ibuprofeno en quiebre" · Dipirona 500 mg × 10 263 / 58 / 32 días / 150 / "Rotación estable en las 6 sedes" · Electrolitos bebida 500 ml 22 / 27 / 6 días / 160 / "Se vende junto con suero oral".
- Pie de tabla: "Mostrando 11 de 42 referencias sugeridas" y, a la derecha, "Total de la orden" con `$9.412.600` (20 px, −0.025em, `#171717`).

## Interactions & Behavior
Los prototipos son estáticos; el comportamiento previsto es:
- **Navegación lateral**: cambia de módulo; el ítem activo toma fondo blanco y peso 500. Contadores = pendientes por módulo (Compras 12 órdenes sugeridas, Mostrador 3 ventas abiertas). El icono de la cabecera colapsa la barra a solo iconos.
- **Filtros**: cada chip abre un menú; al elegir valor pasa a estado activo con píldora de valor y refiltra la tabla. Multi-sede en "Sede".
- **Tablas**: clic en fila la selecciona (fondo `#e8e8e8` + barra interior izquierda `#171717`) y debería abrir el detalle del producto/lote; encabezados ordenables; scroll vertical interno con `thead` fijo (el contenedor ya recorta con `overflow:hidden`).
- **Paginación** (Inventario): cambio de página y de tamaño de página; recarga los datos.
- **Mostrador**: el texto del cliente se captura por dictado o escritura; al confirmarlo se extraen los chips de síntomas y se piden sugerencias. "Agregar" añade el ítem al ticket, incrementa el contador de ítems y recalcula subtotal/total; los ítems provenientes de sugerencia se marcan con "· sugerido" (para medir la tasa de aceptación del Panel). "Cobrar" abre el flujo de pago y cierra la venta.
- **Compras**: editar una cantidad en "Sugerido" recalcula el total de la orden y debe registrar la desviación frente a la propuesta del modelo. Poner 0 atenúa el campo. "Aprobar y enviar" envía al proveedor y bloquea la orden; "Descartar" pide confirmación.
- **Periodo del Panel** (7/30/90 días): recarga todos los KPIs, series y tabla. "Exportar" descarga el resumen.
- **Estados de hover** (no representados en el prototipo, recomendación): filas de tabla `#f4f4f4`; ítems de navegación inactivos `rgba(0,0,0,0.04)`; botón primario `#000000`; botón secundario borde `rgba(0,0,0,0.28)`. Foco visible: anillo 2 px `#0071e3` con `outline-offset:2px`.
- **Transiciones**: 120–160 ms `ease-out` en color/fondo; sin animaciones de entrada.

## State Management
- Global: organización, usuario y rol (Administradora vs. Mostrador — el rol define los módulos visibles), sede activa, versión de la app.
- Panel: `periodo` (7|30|90), KPIs, serie diaria (30 puntos), ranking por sede, métricas de aceptación de sugerencias.
- Inventario: `busqueda`, `filtros{sede[],categoria[],estado[],vencimiento}`, `orden{columna,direccion}`, `pagina`, `filasPorPagina`, `filaSeleccionada`, total de resultados y conteo de "requieren acción".
- Mostrador: `ventaId`, `turnoAbiertoDesde`, `transcripcion`, `chipsExtraidos[]`, `recomendacion`, `sugerencias[]` (con existencias por sede), `ticket[]` (producto, cantidad, precio, origenSugerencia), totales derivados.
- Compras: `ordenId`, `proveedor`, `sede`, `filtros`, `lineas[]` (stock, ventaSemanal, coberturaDias, sugeridoModelo, sugeridoEditado, razon), KPIs de la orden, total derivado, estado de la orden.
- Datos: todo viene del backend; la venta del Mostrador y la orden de Compras necesitan escritura optimista con reconciliación. El asistente (recomendación + sugerencias) es una llamada asíncrona: prever estado de carga en las tarjetas B y C.

## Design Tokens
**Neutros**: `#fbfbfb` fondo de app · `#ffffff` superficie de tarjeta/tabla · `#f4f4f4` barra lateral, cabeceras de tabla/tarjeta, pies · `#e8e8e8` fila seleccionada / chip neutro / página activa · `#e9e9e7` fondo del lienzo de presentación · `#171717` texto principal y botón primario · `#555555` texto secundario · `#727272` texto terciario · `#6b6b6b` notas al pie de KPI · `#909090` texto deshabilitado / placeholder · `#c8c8c8` separadores y flechas deshabilitadas.
**Bordes**: `rgba(0,0,0,0.06)` divisores · `rgba(0,0,0,0.08)` bordes de tarjeta · `rgba(0,0,0,0.11)` inputs y borde bajo `thead` · `rgba(0,0,0,0.16)` botón secundario y chips activos.
**Acento (azul)**: `#0071e3` primario de datos · escala descendente `#1a7fe5`, `#2683e5`, `#3389e6`, `#4c9bea`, `#5fa8ed`, `#6cb0ef`, `#7fb9f0`, `#87bff1`, `#9ec9f4` · `#e0eefc` riel de barras.
**Semánticos**: positivo `#4e7a52` / fondo `#e3e9e3` · atención `#8c6a33` / fondo `#ece7df` · crítico `#b04a3f` / fondo `#f1e2e1` · informativo `#4c6a86` / fondo `#e3e7eb`.
**Sombras**: tarjeta `0 1px 2px rgba(20,20,20,0.02)` · segmento activo `0 1px 2px rgba(20,20,20,0.04)` · lienzo de pantalla `0 1px 2px rgba(20,20,20,0.04), 0 18px 44px rgba(20,20,20,0.08)` (solo presentación, no en la app).
**Radios**: 4 px marca · 6 px icono cuadrado · 7 px segmento · **9 px** botones, chips, inputs, celdas editables · 12 px tarjeta KPI / tarjeta de fila · 16 px tarjeta de sección y tabla · 999 px píldoras y barras.
**Espaciado** (escala de 2/4): 2 · 4 · 6 · 8 · 10 · 12 · 14 · 16 · 20 · 22 · 28 · 32 · 40 · 48. Padding horizontal de página 40 px; padding de celda 22 px; gap de rejilla 16 px.
**Alturas**: barra lateral 280 px de ancho · cabecera 64 px · barra de filtros 52 px · ítem de nav 38 px · control 34 px · fila de tabla 48 px (44 px compacta) · `thead` 40 px · pie de tabla 48 px · panel derecho del Mostrador 380 px.
**Tipografía**: **Geist** (300/400/500/600) para UI y **Geist Mono** (400/500) para encabezados de tabla, etiquetas de sección y la versión. Escala: 36/40 cifra KPI · 28 título de página y total · 20 subtítulo de dato · 14/20 cuerpo · 12/18 secundario · 11/16 terciario · 10 mono con `letter-spacing:0.18em` y mayúsculas. Títulos con `letter-spacing:-0.025em` y weight 400. Todo dato numérico usa `font-variant-numeric:tabular-nums`.

## Assets
- **Fuentes**: Geist y Geist Mono desde Google Fonts (`https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600&family=Geist+Mono:wght@400;500&display=swap`). Ambas son de licencia abierta (SIL OFL) y pueden autoalojarse.
- **Iconos**: SVG de trazo 24×24, `stroke-width:1.5` (2 en chevrons), `stroke-linecap/linejoin:round`, dibujados a 15–16 px. Son equivalentes en estilo a Lucide — **usar Lucide (o el set de iconos del codebase) en la implementación** en lugar de copiar los path del prototipo. Iconos usados: panel-left-close, layout-dashboard, package, shopping-cart, tag, message-circle, building/store, bar-chart, settings, search, chevron-down/left/right, arrow-up, arrow-down, sparkle.
- **Imágenes**: ninguna. No hay logos de terceros; la marca es el cuadro "B" y el nombre de la organización.

## Files
En esta carpeta:
- `Botica - Pantallas.dc.html` — Pantalla **Inventario** (`#pantalla-inventario`) y **Panel** (`#pantalla-panel`).
- `Botica - Pantallas 2.dc.html` — Pantalla **Mostrador** (`#pantalla-mostrador`) y **Compras** (`#pantalla-compras`).
- `support.js` — runtime necesario para abrir los dos archivos en el navegador.

Cada pantalla es el `div` con el `id` indicado; el shell (barra lateral + cabecera) se repite dentro de cada una.

## Pendientes (decidir con diseño antes de implementar)
1. Estados vacío, de carga y de error de cada tabla y de las tarjetas del asistente.
2. Comportamiento responsive por debajo de 1440 px (colapso de la barra lateral, columnas prescindibles, panel del Mostrador).
3. Permisos por rol: qué ve y qué puede aprobar el perfil Mostrador frente a Administradora.
4. Módulos aún sin diseñar: Precios, Sedes, Reportes.
5. Flujo de pago tras "Cobrar" y confirmación tras "Aprobar y enviar".
6. Accesibilidad: contraste de los textos `#727272`/11 px sobre `#f4f4f4`, orden de tabulación en tablas, etiquetas ARIA de las barras y el donut.
