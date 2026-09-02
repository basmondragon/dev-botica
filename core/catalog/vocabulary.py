"""The seed's product vocabulary, and the grammar that expands it.

**The named rows are literal and the bulk is generated.** The fifteen products
the Existencias screen lists by name exist here with the laboratorio drawn
beside each, as do the eleven the Compras screen orders and the four the
Mostrador ticket and its suggestion cards name -- because a fully generated
catalog produces a demo whose every row disagrees with the screenshot the client
was shown, and the first person to compare the two stops trusting both.

Everything else is generated from a name-strength-pack grammar over the same
seven laboratorios and the same category tree, from **one fixed random seed**,
so the fixture is identical on every machine and identical from profile to
profile wherever the profile did not deliberately change it.

What makes the generated half read as a real catalog rather than as filler is
that the ingredients are real INN names at real strengths in real pack sizes.
`Etoricoxib 90 mg × 14` is a box a Colombian droguería stocks; `Producto 1183`
is not, and a page of the second is what "wrong" looks like here.
"""

import hashlib

# ---------------------------------------------------------------------------
# Laboratorios and categorías
# ---------------------------------------------------------------------------

#: The handoff's seven. Their NITs come from a reserved range no Colombian
#: registry issues, which is invariant 1's share of this fixture.
MANUFACTURERS = [
    ("Genfar", "999.100.001-1"),
    ("Tecnoquímicas", "999.100.002-2"),
    ("MK", "999.100.003-3"),
    ("La Santé", "999.100.004-4"),
    ("Procaps", "999.100.005-5"),
    ("Bayer", "999.100.006-6"),
    ("Baxter", "999.100.007-7"),
]

#: Two levels -- enough for the `Categoría` chip to be worth opening and for
#: S8's `symptom_category_map` to have something to bind to.
CATEGORIES: list[tuple[str, list[str]]] = [
    (
        "Medicamentos",
        [
            "Analgésicos",
            "Antibióticos",
            "Cardiovascular",
            "Digestivo",
            "Respiratorio",
            "Antialérgicos",
            "Metabólico",
        ],
    ),
    ("Cuidado personal", []),
    ("Bebidas y sueros", []),
    ("Dispositivos médicos", []),
    ("Servicios", []),
]

SUPPLIERS = [
    # The distributor the Compras filter chip names, plus three direct accounts.
    ("999.200.001-1", "Coopidrogas", "Línea de pedidos", "30 días", 2),
    ("999.200.002-2", "Distribuidora Farmacéutica del Norte", "Ventas", "45 días", 4),
    ("999.200.003-3", "Droguería Mayorista Andina", "Pedidos", "Contado", 3),
    ("999.200.004-4", "Insumos Médicos del Valle", "Comercial", "60 días", 6),
]

SERVICES = [
    # A7 · five services, so the mixed catalog is visible on the first screen
    # anyone opens. Two carry a `service_cost` and three leave it null, so both
    # the costed service and the 100%-margin one have a row.
    ("Toma de presión", "servicio", 5000, "excluded", None, False),
    ("Inyectología", "servicio", 8000, "excluded", 2200, True),
    ("Glucometría", "servicio", 12000, "excluded", 4800, False),
    ("Domicilio", "domicilio", 6000, "rate_19", None, False),
    ("Asesoría farmacéutica", "sesión", 15000, "excluded", None, False),
]

# ---------------------------------------------------------------------------
# The drawn rows, literal
# ---------------------------------------------------------------------------

# `(name, presentation, laboratorio, categoría, precio | None, unidad, IVA)`.
# A price of None is generated; the four the Mostrador draws are exact, so the
# ticket totals `$15.600` (§A.11).
DRAWN = [
    (
        "Acetaminofén 500 mg × 100",
        "caja × 100 tabletas",
        "Genfar",
        "Analgésicos",
        None,
        "caja",
        "excluded",
    ),
    (
        "Sales de rehidratación oral",
        "sobre 27,5 g",
        "Tecnoquímicas",
        "Bebidas y sueros",
        3900,
        "sobre",
        "rate_5",
    ),
    (
        "Losartán 50 mg × 30",
        "caja × 30 tabletas",
        "MK",
        "Cardiovascular",
        None,
        "caja",
        "excluded",
    ),
    (
        "Amoxicilina 500 mg × 20",
        "caja × 20 cápsulas",
        "La Santé",
        "Antibióticos",
        None,
        "caja",
        "excluded",
    ),
    (
        "Omeprazol 20 mg × 30",
        "caja × 30 cápsulas",
        "Procaps",
        "Digestivo",
        None,
        "caja",
        "excluded",
    ),
    (
        "Ibuprofeno 400 mg × 50",
        "caja × 50 tabletas",
        "Genfar",
        "Analgésicos",
        None,
        "caja",
        "excluded",
    ),
    (
        "Metformina 850 mg × 30",
        "caja × 30 tabletas",
        "MK",
        "Metabólico",
        None,
        "caja",
        "excluded",
    ),
    (
        "Loratadina 10 mg × 10",
        "caja × 10 tabletas",
        "Tecnoquímicas",
        "Antialérgicos",
        None,
        "caja",
        "excluded",
    ),
    (
        "Enalapril 20 mg × 30",
        "caja × 30 tabletas",
        "La Santé",
        "Cardiovascular",
        None,
        "caja",
        "excluded",
    ),
    (
        "Atorvastatina 20 mg × 30",
        "caja × 30 tabletas",
        "Procaps",
        "Cardiovascular",
        None,
        "caja",
        "excluded",
    ),
    (
        "Suero fisiológico 500 ml",
        "bolsa 500 ml",
        "Baxter",
        "Bebidas y sueros",
        None,
        "bolsa",
        "excluded",
    ),
    (
        "Naproxeno 500 mg × 20",
        "caja × 20 tabletas",
        "Genfar",
        "Analgésicos",
        None,
        "caja",
        "excluded",
    ),
    (
        "Dipirona 500 mg × 10",
        "caja × 10 tabletas",
        "Tecnoquímicas",
        "Analgésicos",
        None,
        "caja",
        "excluded",
    ),
    (
        "Salbutamol inhalador 100 mcg",
        "inhalador 200 dosis",
        "Bayer",
        "Respiratorio",
        None,
        "inhalador",
        "excluded",
    ),
    (
        "Hidroclorotiazida 25 mg × 30",
        "caja × 30 tabletas",
        "MK",
        "Cardiovascular",
        None,
        "caja",
        "excluded",
    ),
    # The Compras screen's eleventh reference, and two more the Mostrador names.
    (
        "Electrolitos bebida 500 ml",
        "botella 500 ml",
        "Tecnoquímicas",
        "Bebidas y sueros",
        5200,
        "botella",
        "rate_19",
    ),
    (
        "Loperamida 2 mg × 12",
        "caja × 12 tabletas",
        "Genfar",
        "Digestivo",
        8400,
        "caja",
        "excluded",
    ),
    (
        "Acetaminofén 500 mg × 10",
        "caja × 10 tabletas",
        "Genfar",
        "Analgésicos",
        2600,
        "caja",
        "excluded",
    ),
]

#: What the Existencias screen lists by name, in its drawn order. S3's fixture
#: hangs its lote, its vencimiento, its sede and its cantidad on these rather
#: than inventing a sixteenth product.
EXISTENCIAS_ROWS = [row[0] for row in DRAWN[:15]]

#: What the Compras screen orders. S6's fixture hangs its order lines on these.
COMPRAS_ROWS = [
    "Sales de rehidratación oral",
    "Acetaminofén 500 mg × 100",
    "Ibuprofeno 400 mg × 50",
    "Loratadina 10 mg × 10",
    "Losartán 50 mg × 30",
    "Metformina 850 mg × 30",
    "Salbutamol inhalador 100 mcg",
    "Omeprazol 20 mg × 30",
    "Naproxeno 500 mg × 20",
    "Dipirona 500 mg × 10",
    "Electrolitos bebida 500 ml",
]

#: The Mostrador ticket and its suggestion cards, with the figures §A.11 draws.
MOSTRADOR_PRICES = {
    "Sales de rehidratación oral": 3900,
    "Loperamida 2 mg × 12": 8400,
    "Electrolitos bebida 500 ml": 5200,
    "Acetaminofén 500 mg × 10": 2600,
}

# ---------------------------------------------------------------------------
# The generated half
# ---------------------------------------------------------------------------

#: The pack sizes a Colombian droguería actually stocks the same molecule in.
#: Eleven of them, because a catalog of four thousand references is mostly the
#: same hundred molecules in different boxes -- that is what a catalog *is*, and
#: a grammar with four packs would have to invent products to reach the count.
PACKS = (7, 10, 12, 14, 15, 20, 24, 30, 50, 60, 100)

#: `(ingrediente, categoría, [concentraciones], forma, receta, controlado)`.
#: Real INN names at real strengths, because the whole point of the drawn rows
#: being literal is lost if the four thousand around them read as filler.
SOLIDS: list[tuple[str, str, tuple[str, ...], str, bool, bool]] = [
    (
        "Acetaminofén",
        "Analgésicos",
        ("325 mg", "500 mg", "1 g"),
        "tabletas",
        False,
        False,
    ),
    (
        "Ibuprofeno",
        "Analgésicos",
        ("200 mg", "400 mg", "600 mg", "800 mg"),
        "tabletas",
        False,
        False,
    ),
    (
        "Naproxeno",
        "Analgésicos",
        ("250 mg", "500 mg", "550 mg"),
        "tabletas",
        False,
        False,
    ),
    (
        "Diclofenaco",
        "Analgésicos",
        ("50 mg", "75 mg", "100 mg"),
        "tabletas",
        False,
        False,
    ),
    ("Dipirona", "Analgésicos", ("300 mg", "500 mg", "1 g"), "tabletas", False, False),
    (
        "Ácido acetilsalicílico",
        "Analgésicos",
        ("81 mg", "100 mg", "500 mg"),
        "tabletas",
        False,
        False,
    ),
    ("Ketorolaco", "Analgésicos", ("10 mg", "20 mg"), "tabletas", True, False),
    ("Meloxicam", "Analgésicos", ("7,5 mg", "15 mg"), "tabletas", True, False),
    ("Celecoxib", "Analgésicos", ("100 mg", "200 mg"), "cápsulas", True, False),
    ("Tramadol", "Analgésicos", ("50 mg", "100 mg"), "cápsulas", True, True),
    ("Piroxicam", "Analgésicos", ("10 mg", "20 mg"), "cápsulas", True, False),
    ("Indometacina", "Analgésicos", ("25 mg", "50 mg"), "cápsulas", True, False),
    (
        "Etoricoxib",
        "Analgésicos",
        ("60 mg", "90 mg", "120 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Nimesulida", "Analgésicos", ("100 mg",), "tabletas", True, False),
    ("Metocarbamol", "Analgésicos", ("500 mg", "750 mg"), "tabletas", True, False),
    ("Ketoprofeno", "Analgésicos", ("50 mg", "100 mg"), "cápsulas", True, False),
    ("Clonixinato de lisina", "Analgésicos", ("125 mg",), "tabletas", False, False),
    (
        "Amoxicilina",
        "Antibióticos",
        ("250 mg", "500 mg", "875 mg"),
        "cápsulas",
        True,
        False,
    ),
    ("Azitromicina", "Antibióticos", ("250 mg", "500 mg"), "tabletas", True, False),
    ("Cefalexina", "Antibióticos", ("250 mg", "500 mg"), "cápsulas", True, False),
    (
        "Ciprofloxacina",
        "Antibióticos",
        ("250 mg", "500 mg", "750 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Claritromicina", "Antibióticos", ("250 mg", "500 mg"), "tabletas", True, False),
    ("Clindamicina", "Antibióticos", ("150 mg", "300 mg"), "cápsulas", True, False),
    ("Doxiciclina", "Antibióticos", ("50 mg", "100 mg"), "cápsulas", True, False),
    ("Eritromicina", "Antibióticos", ("250 mg", "500 mg"), "tabletas", True, False),
    (
        "Levofloxacina",
        "Antibióticos",
        ("250 mg", "500 mg", "750 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Metronidazol", "Antibióticos", ("250 mg", "500 mg"), "tabletas", True, False),
    ("Nitrofurantoína", "Antibióticos", ("50 mg", "100 mg"), "cápsulas", True, False),
    (
        "Sulfametoxazol trimetoprim",
        "Antibióticos",
        ("400/80 mg", "800/160 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Ampicilina", "Antibióticos", ("250 mg", "500 mg"), "cápsulas", True, False),
    ("Cefadroxilo", "Antibióticos", ("500 mg", "1 g"), "cápsulas", True, False),
    ("Norfloxacina", "Antibióticos", ("400 mg",), "tabletas", True, False),
    ("Fosfomicina", "Antibióticos", ("3 g",), "sobres", True, False),
    ("Losartán", "Cardiovascular", ("50 mg", "100 mg"), "tabletas", True, False),
    (
        "Enalapril",
        "Cardiovascular",
        ("5 mg", "10 mg", "20 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Amlodipino", "Cardiovascular", ("5 mg", "10 mg"), "tabletas", True, False),
    (
        "Atorvastatina",
        "Cardiovascular",
        ("10 mg", "20 mg", "40 mg", "80 mg"),
        "tabletas",
        True,
        False,
    ),
    (
        "Hidroclorotiazida",
        "Cardiovascular",
        ("12,5 mg", "25 mg", "50 mg"),
        "tabletas",
        True,
        False,
    ),
    (
        "Metoprolol",
        "Cardiovascular",
        ("25 mg", "50 mg", "100 mg"),
        "tabletas",
        True,
        False,
    ),
    (
        "Carvedilol",
        "Cardiovascular",
        ("6,25 mg", "12,5 mg", "25 mg"),
        "tabletas",
        True,
        False,
    ),
    (
        "Valsartán",
        "Cardiovascular",
        ("80 mg", "160 mg", "320 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Furosemida", "Cardiovascular", ("20 mg", "40 mg"), "tabletas", True, False),
    ("Espironolactona", "Cardiovascular", ("25 mg", "100 mg"), "tabletas", True, False),
    ("Clopidogrel", "Cardiovascular", ("75 mg",), "tabletas", True, False),
    (
        "Rosuvastatina",
        "Cardiovascular",
        ("5 mg", "10 mg", "20 mg"),
        "tabletas",
        True,
        False,
    ),
    (
        "Simvastatina",
        "Cardiovascular",
        ("10 mg", "20 mg", "40 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Verapamilo", "Cardiovascular", ("80 mg", "120 mg"), "tabletas", True, False),
    ("Nifedipino", "Cardiovascular", ("30 mg", "60 mg"), "tabletas", True, False),
    (
        "Bisoprolol",
        "Cardiovascular",
        ("2,5 mg", "5 mg", "10 mg"),
        "tabletas",
        True,
        False,
    ),
    (
        "Propranolol",
        "Cardiovascular",
        ("10 mg", "40 mg", "80 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Warfarina", "Cardiovascular", ("2,5 mg", "5 mg"), "tabletas", True, False),
    ("Digoxina", "Cardiovascular", ("0,25 mg",), "tabletas", True, False),
    (
        "Isosorbide",
        "Cardiovascular",
        ("5 mg", "10 mg", "20 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Omeprazol", "Digestivo", ("10 mg", "20 mg", "40 mg"), "cápsulas", False, False),
    ("Esomeprazol", "Digestivo", ("20 mg", "40 mg"), "cápsulas", True, False),
    ("Ranitidina", "Digestivo", ("150 mg", "300 mg"), "tabletas", False, False),
    ("Loperamida", "Digestivo", ("2 mg",), "tabletas", False, False),
    ("Metoclopramida", "Digestivo", ("10 mg",), "tabletas", True, False),
    ("Domperidona", "Digestivo", ("10 mg",), "tabletas", True, False),
    (
        "Butilbromuro de hioscina",
        "Digestivo",
        ("10 mg", "20 mg"),
        "tabletas",
        False,
        False,
    ),
    ("Lansoprazol", "Digestivo", ("15 mg", "30 mg"), "cápsulas", True, False),
    ("Sucralfato", "Digestivo", ("1 g",), "tabletas", True, False),
    ("Bisacodilo", "Digestivo", ("5 mg",), "tabletas", False, False),
    ("Simeticona", "Digestivo", ("40 mg", "80 mg", "125 mg"), "cápsulas", False, False),
    ("Pantoprazol", "Digestivo", ("20 mg", "40 mg"), "tabletas", True, False),
    ("Ondansetrón", "Digestivo", ("4 mg", "8 mg"), "tabletas", True, False),
    ("Trimebutina", "Digestivo", ("100 mg", "200 mg"), "tabletas", True, False),
    ("Racecadotrilo", "Digestivo", ("100 mg",), "cápsulas", True, False),
    ("Salbutamol", "Respiratorio", ("2 mg", "4 mg"), "tabletas", True, False),
    ("Loratadina", "Respiratorio", ("10 mg",), "tabletas", False, False),
    ("Ambroxol", "Respiratorio", ("30 mg",), "tabletas", False, False),
    ("Bromhexina", "Respiratorio", ("8 mg",), "tabletas", False, False),
    ("Montelukast", "Respiratorio", ("4 mg", "5 mg", "10 mg"), "tabletas", True, False),
    ("Dextrometorfano", "Respiratorio", ("15 mg",), "tabletas", False, False),
    (
        "Acetilcisteína",
        "Respiratorio",
        ("100 mg", "200 mg", "600 mg"),
        "sobres",
        False,
        False,
    ),
    (
        "Teofilina",
        "Respiratorio",
        ("100 mg", "200 mg", "300 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Cetirizina", "Antialérgicos", ("5 mg", "10 mg"), "tabletas", False, False),
    ("Desloratadina", "Antialérgicos", ("5 mg",), "tabletas", False, False),
    ("Clorfeniramina", "Antialérgicos", ("4 mg",), "tabletas", False, False),
    ("Hidroxicina", "Antialérgicos", ("10 mg", "25 mg"), "tabletas", True, False),
    ("Ebastina", "Antialérgicos", ("10 mg", "20 mg"), "tabletas", False, False),
    ("Bilastina", "Antialérgicos", ("20 mg",), "tabletas", False, False),
    ("Fexofenadina", "Antialérgicos", ("120 mg", "180 mg"), "tabletas", False, False),
    (
        "Prednisolona",
        "Antialérgicos",
        ("5 mg", "20 mg", "50 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Betametasona", "Antialérgicos", ("0,5 mg",), "tabletas", True, False),
    (
        "Dexametasona",
        "Antialérgicos",
        ("0,5 mg", "4 mg", "8 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Metformina", "Metabólico", ("500 mg", "850 mg", "1 g"), "tabletas", True, False),
    ("Glibenclamida", "Metabólico", ("5 mg",), "tabletas", True, False),
    (
        "Levotiroxina",
        "Metabólico",
        ("25 mcg", "50 mcg", "100 mcg"),
        "tabletas",
        True,
        False,
    ),
    (
        "Sitagliptina",
        "Metabólico",
        ("25 mg", "50 mg", "100 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Glimepirida", "Metabólico", ("2 mg", "4 mg"), "tabletas", True, False),
    ("Empagliflozina", "Metabólico", ("10 mg", "25 mg"), "tabletas", True, False),
    ("Alopurinol", "Metabólico", ("100 mg", "300 mg"), "tabletas", True, False),
    ("Ácido fólico", "Metabólico", ("1 mg", "5 mg"), "tabletas", False, False),
    (
        "Vitamina D3",
        "Metabólico",
        ("1000 UI", "2000 UI", "50000 UI"),
        "cápsulas",
        False,
        False,
    ),
    (
        "Calcio + vitamina D",
        "Metabólico",
        ("600 mg", "1200 mg"),
        "tabletas",
        False,
        False,
    ),
    ("Sulfato ferroso", "Metabólico", ("300 mg",), "tabletas", False, False),
    ("Complejo B", "Metabólico", ("100 mg",), "tabletas", False, False),
    ("Vitamina C", "Metabólico", ("500 mg", "1 g"), "tabletas", False, False),
    ("Zinc", "Metabólico", ("20 mg", "50 mg"), "tabletas", False, False),
    ("Magnesio", "Metabólico", ("250 mg", "400 mg"), "tabletas", False, False),
    ("Omega 3", "Metabólico", ("1000 mg",), "cápsulas", False, False),
    ("Melatonina", "Metabólico", ("3 mg", "5 mg", "10 mg"), "tabletas", False, False),
    (
        "Pregabalina",
        "Analgésicos",
        ("25 mg", "75 mg", "150 mg"),
        "cápsulas",
        True,
        False,
    ),
    (
        "Gabapentina",
        "Analgésicos",
        ("300 mg", "400 mg", "600 mg"),
        "cápsulas",
        True,
        False,
    ),
    ("Amitriptilina", "Analgésicos", ("10 mg", "25 mg"), "tabletas", True, False),
    ("Sumatriptán", "Analgésicos", ("50 mg", "100 mg"), "tabletas", True, False),
    ("Colchicina", "Analgésicos", ("0,5 mg", "1 mg"), "tabletas", True, False),
    ("Ácido mefenámico", "Analgésicos", ("250 mg", "500 mg"), "cápsulas", True, False),
    ("Tiocolchicósido", "Analgésicos", ("4 mg", "8 mg"), "cápsulas", True, False),
    ("Ciclobenzaprina", "Analgésicos", ("5 mg", "10 mg"), "tabletas", True, False),
    ("Baclofeno", "Analgésicos", ("10 mg", "25 mg"), "tabletas", True, False),
    ("Diclofenaco potásico", "Analgésicos", ("50 mg",), "tabletas", False, False),
    ("Acetaminofén + cafeína", "Analgésicos", ("500/65 mg",), "tabletas", False, False),
    (
        "Tramadol + acetaminofén",
        "Analgésicos",
        ("37,5/325 mg",),
        "tabletas",
        True,
        True,
    ),
    ("Codeína + acetaminofén", "Analgésicos", ("30/500 mg",), "tabletas", True, True),
    ("Morfina", "Analgésicos", ("10 mg", "30 mg"), "tabletas", True, True),
    ("Oxicodona", "Analgésicos", ("5 mg", "10 mg"), "tabletas", True, True),
    ("Lidocaína", "Analgésicos", ("2%", "5%"), "parches", True, False),
    ("Cefuroxima", "Antibióticos", ("250 mg", "500 mg"), "tabletas", True, False),
    ("Cefixima", "Antibióticos", ("200 mg", "400 mg"), "cápsulas", True, False),
    ("Moxifloxacina", "Antibióticos", ("400 mg",), "tabletas", True, False),
    ("Rifampicina", "Antibióticos", ("150 mg", "300 mg"), "cápsulas", True, False),
    ("Isoniazida", "Antibióticos", ("100 mg", "300 mg"), "tabletas", True, False),
    (
        "Fluconazol",
        "Antibióticos",
        ("50 mg", "150 mg", "200 mg"),
        "cápsulas",
        True,
        False,
    ),
    ("Itraconazol", "Antibióticos", ("100 mg",), "cápsulas", True, False),
    ("Ketoconazol", "Antibióticos", ("200 mg",), "tabletas", True, False),
    ("Terbinafina", "Antibióticos", ("250 mg",), "tabletas", True, False),
    (
        "Aciclovir",
        "Antibióticos",
        ("200 mg", "400 mg", "800 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Valaciclovir", "Antibióticos", ("500 mg", "1 g"), "tabletas", True, False),
    ("Albendazol", "Antibióticos", ("200 mg", "400 mg"), "tabletas", False, False),
    ("Ivermectina", "Antibióticos", ("6 mg",), "tabletas", True, False),
    ("Praziquantel", "Antibióticos", ("600 mg",), "tabletas", True, False),
    ("Nistatina", "Antibióticos", ("500000 UI",), "tabletas", True, False),
    ("Irbesartán", "Cardiovascular", ("150 mg", "300 mg"), "tabletas", True, False),
    (
        "Candesartán",
        "Cardiovascular",
        ("8 mg", "16 mg", "32 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Telmisartán", "Cardiovascular", ("40 mg", "80 mg"), "tabletas", True, False),
    (
        "Lisinopril",
        "Cardiovascular",
        ("5 mg", "10 mg", "20 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Captopril", "Cardiovascular", ("25 mg", "50 mg"), "tabletas", True, False),
    (
        "Ramipril",
        "Cardiovascular",
        ("2,5 mg", "5 mg", "10 mg"),
        "cápsulas",
        True,
        False,
    ),
    ("Atenolol", "Cardiovascular", ("50 mg", "100 mg"), "tabletas", True, False),
    (
        "Diltiazem",
        "Cardiovascular",
        ("60 mg", "90 mg", "120 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Ezetimiba", "Cardiovascular", ("10 mg",), "tabletas", True, False),
    ("Fenofibrato", "Cardiovascular", ("145 mg", "200 mg"), "cápsulas", True, False),
    ("Gemfibrozilo", "Cardiovascular", ("600 mg", "900 mg"), "tabletas", True, False),
    (
        "Rivaroxabán",
        "Cardiovascular",
        ("10 mg", "15 mg", "20 mg"),
        "tabletas",
        True,
        False,
    ),
    ("Apixabán", "Cardiovascular", ("2,5 mg", "5 mg"), "tabletas", True, False),
    ("Mebeverina", "Digestivo", ("135 mg", "200 mg"), "cápsulas", True, False),
    ("Dimenhidrinato", "Digestivo", ("50 mg",), "tabletas", False, False),
    ("Saccharomyces boulardii", "Digestivo", ("250 mg",), "cápsulas", False, False),
    (
        "Enzimas pancreáticas",
        "Digestivo",
        ("10000 UI", "25000 UI"),
        "cápsulas",
        True,
        False,
    ),
    ("Rifaximina", "Digestivo", ("200 mg", "550 mg"), "tabletas", True, False),
    ("Mesalazina", "Digestivo", ("500 mg", "800 mg"), "tabletas", True, False),
    ("Picosulfato de sodio", "Digestivo", ("5 mg",), "tabletas", False, False),
    ("Polietilenglicol", "Digestivo", ("17 g",), "sobres", False, False),
    ("Formoterol", "Respiratorio", ("12 mcg",), "cápsulas", True, False),
    ("Carbocisteína", "Respiratorio", ("375 mg",), "cápsulas", False, False),
    ("Guaifenesina", "Respiratorio", ("200 mg", "400 mg"), "tabletas", False, False),
    ("Pseudoefedrina", "Respiratorio", ("30 mg", "60 mg"), "tabletas", False, False),
    ("Levodropropizina", "Respiratorio", ("60 mg",), "tabletas", False, False),
    ("Ketotifeno", "Antialérgicos", ("1 mg",), "tabletas", True, False),
    ("Rupatadina", "Antialérgicos", ("10 mg",), "tabletas", False, False),
    ("Levocetirizina", "Antialérgicos", ("5 mg",), "tabletas", False, False),
    ("Metilprednisolona", "Antialérgicos", ("4 mg", "16 mg"), "tabletas", True, False),
    ("Hidrocortisona", "Antialérgicos", ("10 mg", "20 mg"), "tabletas", True, False),
    ("Deflazacort", "Antialérgicos", ("6 mg", "30 mg"), "tabletas", True, False),
    ("Linagliptina", "Metabólico", ("5 mg",), "tabletas", True, False),
    ("Dapagliflozina", "Metabólico", ("5 mg", "10 mg"), "tabletas", True, False),
    ("Pioglitazona", "Metabólico", ("15 mg", "30 mg"), "tabletas", True, False),
    ("Acarbosa", "Metabólico", ("50 mg", "100 mg"), "tabletas", True, False),
    ("Metimazol", "Metabólico", ("5 mg", "10 mg"), "tabletas", True, False),
    ("Finasterida", "Metabólico", ("1 mg", "5 mg"), "tabletas", True, False),
    ("Tamsulosina", "Metabólico", ("0,4 mg",), "cápsulas", True, False),
    ("Sildenafil", "Metabólico", ("50 mg", "100 mg"), "tabletas", True, False),
    ("Tadalafil", "Metabólico", ("5 mg", "20 mg"), "tabletas", True, False),
    ("Alendronato", "Metabólico", ("70 mg",), "tabletas", True, False),
    ("Calcitriol", "Metabólico", ("0,25 mcg",), "cápsulas", True, False),
    ("Vitamina B12", "Metabólico", ("1000 mcg",), "tabletas", False, False),
    ("Vitamina E", "Metabólico", ("400 UI",), "cápsulas", False, False),
    ("Biotina", "Metabólico", ("5 mg", "10 mg"), "tabletas", False, False),
    ("Colágeno hidrolizado", "Metabólico", ("10 g",), "sobres", False, False),
    ("Glucosamina", "Metabólico", ("500 mg", "1500 mg"), "tabletas", False, False),
    ("Coenzima Q10", "Metabólico", ("100 mg",), "cápsulas", False, False),
    ("Hierro polimaltosado", "Metabólico", ("100 mg",), "tabletas", False, False),
]

#: The liquids, which take a volume where a solid takes a pack count.
VOLUMES = (30, 60, 120, 150, 240, 480)

#: `(nombre, categoría, [concentraciones], forma, receta, cadena de frío)`.
LIQUIDS: list[tuple[str, str, tuple[str, ...], str, bool, bool]] = [
    (
        "Acetaminofén jarabe",
        "Analgésicos",
        ("150 mg/5 ml", "160 mg/5 ml"),
        "jarabe",
        False,
        False,
    ),
    (
        "Ibuprofeno suspensión",
        "Analgésicos",
        ("100 mg/5 ml", "200 mg/5 ml"),
        "suspensión",
        False,
        False,
    ),
    (
        "Amoxicilina suspensión",
        "Antibióticos",
        ("250 mg/5 ml", "500 mg/5 ml"),
        "suspensión",
        True,
        False,
    ),
    (
        "Azitromicina suspensión",
        "Antibióticos",
        ("200 mg/5 ml",),
        "suspensión",
        True,
        False,
    ),
    (
        "Cefalexina suspensión",
        "Antibióticos",
        ("250 mg/5 ml",),
        "suspensión",
        True,
        False,
    ),
    (
        "Ambroxol jarabe",
        "Respiratorio",
        ("15 mg/5 ml", "30 mg/5 ml"),
        "jarabe",
        False,
        False,
    ),
    ("Salbutamol jarabe", "Respiratorio", ("2 mg/5 ml",), "jarabe", True, False),
    ("Dextrometorfano jarabe", "Respiratorio", ("15 mg/5 ml",), "jarabe", False, False),
    ("Loratadina jarabe", "Antialérgicos", ("5 mg/5 ml",), "jarabe", False, False),
    ("Cetirizina gotas", "Antialérgicos", ("10 mg/ml",), "gotas", False, False),
    ("Simeticona gotas", "Digestivo", ("40 mg/ml",), "gotas", False, False),
    ("Metoclopramida gotas", "Digestivo", ("4 mg/ml",), "gotas", True, False),
    (
        "Hidróxido de aluminio suspensión",
        "Digestivo",
        ("320 mg/5 ml",),
        "suspensión",
        False,
        False,
    ),
    ("Lactulosa jarabe", "Digestivo", ("3,3 g/5 ml",), "jarabe", False, False),
    ("Sulfato ferroso gotas", "Metabólico", ("125 mg/ml",), "gotas", False, False),
    ("Vitamina D gotas", "Metabólico", ("400 UI",), "gotas", False, False),
    ("Complejo B jarabe", "Metabólico", ("100 mg",), "jarabe", False, False),
    ("Alcohol antiséptico", "Cuidado personal", ("70%",), "solución", False, False),
    ("Agua oxigenada", "Cuidado personal", ("10 vol",), "solución", False, False),
    ("Solución salina nasal", "Respiratorio", ("0,9%",), "solución", False, False),
    # The cold chain, which S3 reads and nothing in this stage moves.
    ("Insulina glargina", "Metabólico", ("100 UI/ml",), "vial", True, True),
    ("Insulina NPH", "Metabólico", ("100 UI/ml",), "vial", True, True),
    ("Insulina lispro", "Metabólico", ("100 UI/ml",), "vial", True, True),
    (
        "Enoxaparina",
        "Cardiovascular",
        ("40 mg/0,4 ml", "60 mg/0,6 ml"),
        "jeringa",
        True,
        True,
    ),
]

#: `(base, [variantes], categoría, IVA)`. The non-medicine half of what a
#: droguería actually sells, and the reason `vat_class` is per item: a large
#: share of it is not excluded from IVA at all.
GOODS: list[tuple[str, tuple[str, ...], str, str]] = [
    (
        "Jabón de glicerina",
        ("90 g", "125 g", "× 3 unidades", "avena 125 g", "azufre 90 g", "neutro 125 g"),
        "Cuidado personal",
        "rate_19",
    ),
    (
        "Shampoo anticaspa",
        ("200 ml", "400 ml", "700 ml", "control graso 400 ml", "cuero sensible 400 ml"),
        "Cuidado personal",
        "rate_19",
    ),
    (
        "Acondicionador",
        ("200 ml", "400 ml", "700 ml", "reparación 400 ml"),
        "Cuidado personal",
        "rate_19",
    ),
    (
        "Crema humectante corporal",
        ("120 ml", "220 ml", "400 ml", "avena 400 ml", "urea 10% 220 ml"),
        "Cuidado personal",
        "rate_19",
    ),
    (
        "Crema facial hidratante",
        ("30 g", "50 g", "FPS 30 50 g"),
        "Cuidado personal",
        "rate_19",
    ),
    (
        "Desodorante en barra",
        ("50 g", "90 g", "sin aluminio 50 g", "piel sensible 50 g"),
        "Cuidado personal",
        "rate_19",
    ),
    ("Desodorante en aerosol", ("150 ml", "250 ml"), "Cuidado personal", "rate_19"),
    (
        "Crema dental",
        (
            "75 ml",
            "100 ml",
            "150 ml",
            "blanqueadora 100 ml",
            "encías 100 ml",
            "infantil 75 ml",
        ),
        "Cuidado personal",
        "rate_19",
    ),
    (
        "Cepillo dental",
        ("suave", "medio", "duro", "× 2 unidades", "infantil"),
        "Cuidado personal",
        "rate_19",
    ),
    ("Seda dental", ("50 m", "100 m", "encerada 50 m"), "Cuidado personal", "rate_19"),
    (
        "Enjuague bucal",
        ("250 ml", "500 ml", "sin alcohol 500 ml"),
        "Cuidado personal",
        "rate_19",
    ),
    (
        "Bloqueador solar FPS 50",
        ("60 ml", "120 ml", "toque seco 60 ml", "infantil 120 ml"),
        "Cuidado personal",
        "rate_19",
    ),
    ("Bloqueador solar FPS 30", ("60 ml", "120 ml"), "Cuidado personal", "rate_19"),
    (
        "Repelente de insectos",
        ("120 ml", "240 ml", "en crema 60 g"),
        "Cuidado personal",
        "rate_19",
    ),
    (
        "Toallas higiénicas",
        ("× 10", "× 16", "nocturnas × 8", "diarias × 30"),
        "Cuidado personal",
        "rate_19",
    ),
    ("Protectores diarios", ("× 15", "× 30", "× 60"), "Cuidado personal", "rate_19"),
    (
        "Pañal para adulto",
        ("talla M × 8", "talla G × 8", "talla XG × 8", "talla M × 20"),
        "Cuidado personal",
        "rate_19",
    ),
    (
        "Pañal infantil",
        (
            "etapa 1 × 20",
            "etapa 2 × 20",
            "etapa 3 × 20",
            "etapa 4 × 20",
            "etapa 5 × 20",
        ),
        "Cuidado personal",
        "rate_19",
    ),
    (
        "Pañitos húmedos",
        ("× 50", "× 80", "× 100", "sin fragancia × 80"),
        "Cuidado personal",
        "rate_19",
    ),
    ("Crema antipañalitis", ("60 g", "120 g"), "Cuidado personal", "rate_19"),
    ("Talco para pies", ("100 g", "220 g"), "Cuidado personal", "rate_19"),
    ("Aceite de almendras", ("60 ml", "120 ml"), "Cuidado personal", "rate_19"),
    ("Vaselina sólida", ("60 g", "120 g", "220 g"), "Cuidado personal", "rate_19"),
    (
        "Algodón",
        ("25 g", "50 g", "100 g", "en rollo 500 g"),
        "Cuidado personal",
        "rate_19",
    ),
    ("Copitos de algodón", ("× 100", "× 200"), "Cuidado personal", "rate_19"),
    (
        "Máquina de afeitar",
        ("× 2", "× 4", "tres hojas × 2"),
        "Cuidado personal",
        "rate_19",
    ),
    ("Espuma de afeitar", ("200 ml",), "Cuidado personal", "rate_19"),
    (
        "Gel antibacterial",
        ("60 ml", "250 ml", "500 ml", "1 l"),
        "Cuidado personal",
        "rate_19",
    ),
    (
        "Jabón líquido de manos",
        ("250 ml", "500 ml", "repuesto 1 l"),
        "Cuidado personal",
        "rate_19",
    ),
    ("Crema para peinar", ("300 ml", "500 ml"), "Cuidado personal", "rate_19"),
    (
        "Tinte para cabello",
        ("castaño", "negro", "rubio", "caoba"),
        "Cuidado personal",
        "rate_19",
    ),
    ("Lubricante íntimo", ("50 ml", "100 ml"), "Cuidado personal", "rate_19"),
    (
        "Preservativo",
        ("× 3", "× 6", "× 12", "texturizado × 3"),
        "Cuidado personal",
        "rate_19",
    ),
    ("Prueba de embarazo", ("× 1", "× 2"), "Dispositivos médicos", "rate_19"),
    (
        "Suero oral",
        (
            "sabor naranja 500 ml",
            "sabor manzana 500 ml",
            "sin sabor 500 ml",
            "sobre × 2",
        ),
        "Bebidas y sueros",
        "rate_5",
    ),
    (
        "Bebida hidratante",
        ("500 ml", "1 l", "sabor mandarina 500 ml", "sabor uva 500 ml"),
        "Bebidas y sueros",
        "rate_19",
    ),
    (
        "Agua mineral",
        ("300 ml", "600 ml", "1,5 l", "con gas 600 ml"),
        "Bebidas y sueros",
        "rate_19",
    ),
    (
        "Té frío",
        ("400 ml", "600 ml", "limón 600 ml", "durazno 600 ml"),
        "Bebidas y sueros",
        "rate_19",
    ),
    ("Jugo de naranja", ("200 ml", "1 l"), "Bebidas y sueros", "rate_19"),
    ("Bebida energizante", ("250 ml", "473 ml"), "Bebidas y sueros", "rate_19"),
    (
        "Malteada proteica",
        ("vainilla 400 g", "chocolate 400 g", "fresa 400 g"),
        "Bebidas y sueros",
        "rate_19",
    ),
    (
        "Suplemento nutricional en polvo",
        ("400 g", "800 g", "sin azúcar 400 g"),
        "Bebidas y sueros",
        "rate_19",
    ),
    ("Lactato de Ringer", ("500 ml", "1000 ml"), "Bebidas y sueros", "excluded"),
    ("Dextrosa al 5%", ("500 ml", "1000 ml"), "Bebidas y sueros", "excluded"),
    (
        "Tensiómetro digital",
        ("de brazo", "de muñeca", "de brazo con adaptador"),
        "Dispositivos médicos",
        "exempt",
    ),
    (
        "Termómetro digital",
        ("axilar", "infrarrojo", "de punta flexible"),
        "Dispositivos médicos",
        "exempt",
    ),
    ("Glucómetro", ("kit completo", "con estuche"), "Dispositivos médicos", "exempt"),
    (
        "Tiras para glucometría",
        ("× 25", "× 50", "× 100"),
        "Dispositivos médicos",
        "exempt",
    ),
    ("Lancetas", ("× 100", "× 200"), "Dispositivos médicos", "rate_19"),
    (
        "Jeringa desechable",
        ("1 ml × 10", "3 ml × 10", "5 ml × 10", "10 ml × 10"),
        "Dispositivos médicos",
        "rate_19",
    ),
    (
        "Gasa estéril",
        ("7,5 × 7,5 cm × 10", "10 × 10 cm × 10", "en rollo"),
        "Dispositivos médicos",
        "rate_19",
    ),
    ("Venda elástica", ("5 cm", "10 cm", "15 cm"), "Dispositivos médicos", "rate_19"),
    ("Venda de yeso", ("10 cm", "15 cm"), "Dispositivos médicos", "rate_19"),
    (
        "Curas adhesivas",
        ("× 10", "× 50", "× 100", "infantiles × 20"),
        "Dispositivos médicos",
        "rate_19",
    ),
    (
        "Esparadrapo",
        ("2,5 cm", "5 cm", "microporoso 2,5 cm"),
        "Dispositivos médicos",
        "rate_19",
    ),
    (
        "Guantes de examen",
        ("talla S × 100", "talla M × 100", "talla L × 100"),
        "Dispositivos médicos",
        "rate_19",
    ),
    (
        "Tapabocas quirúrgico",
        ("× 10", "× 50", "× 100"),
        "Dispositivos médicos",
        "rate_19",
    ),
    ("Nebulizador", ("de mesa", "portátil"), "Dispositivos médicos", "exempt"),
    ("Inhalocámara", ("adulto", "pediátrica"), "Dispositivos médicos", "exempt"),
    ("Bolsa de agua caliente", ("2 l",), "Dispositivos médicos", "rate_19"),
    (
        "Compresa fría y caliente",
        ("pequeña", "grande"),
        "Dispositivos médicos",
        "rate_19",
    ),
    ("Muletas de aluminio", ("talla M", "talla G"), "Dispositivos médicos", "exempt"),
    (
        "Bastón ortopédico",
        ("regulable", "con trípode"),
        "Dispositivos médicos",
        "exempt",
    ),
    (
        "Collar cervical",
        ("talla S", "talla M", "talla G"),
        "Dispositivos médicos",
        "exempt",
    ),
    (
        "Rodillera elástica",
        ("talla S", "talla M", "talla G"),
        "Dispositivos médicos",
        "rate_19",
    ),
    (
        "Tobillera elástica",
        ("talla S", "talla M", "talla G"),
        "Dispositivos médicos",
        "rate_19",
    ),
    (
        "Media de compresión",
        ("talla M", "talla G", "hasta la rodilla talla M"),
        "Dispositivos médicos",
        "rate_19",
    ),
    ("Pesa digital", ("de baño", "de cocina"), "Dispositivos médicos", "rate_19"),
    ("Oxímetro de pulso", ("de dedo",), "Dispositivos médicos", "exempt"),
    (
        "Suplemento de fibra",
        ("200 g", "400 g", "sobres × 14"),
        "Bebidas y sueros",
        "rate_19",
    ),
    (
        "Leche de fórmula infantil",
        ("etapa 1 400 g", "etapa 2 400 g", "etapa 3 400 g", "sin lactosa 400 g"),
        "Bebidas y sueros",
        "rate_19",
    ),
    ("Avena en polvo", ("300 g", "600 g"), "Bebidas y sueros", "rate_19"),
    (
        "Caramelo para la tos",
        ("miel × 12", "eucalipto × 12", "cereza × 12"),
        "Cuidado personal",
        "rate_19",
    ),
    ("Crema para manos", ("60 ml", "120 ml"), "Cuidado personal", "rate_19"),
    ("Exfoliante facial", ("100 ml",), "Cuidado personal", "rate_19"),
    ("Agua micelar", ("200 ml", "400 ml"), "Cuidado personal", "rate_19"),
    (
        "Protector labial",
        ("FPS 15", "FPS 30", "con color"),
        "Cuidado personal",
        "rate_19",
    ),
    ("Loción antipiojos", ("120 ml", "kit con peine"), "Cuidado personal", "rate_19"),
    ("Sales de baño", ("500 g",), "Cuidado personal", "rate_19"),
    ("Plantilla ortopédica", ("talla M", "talla G"), "Dispositivos médicos", "rate_19"),
    (
        "Faja lumbar",
        ("talla M", "talla G", "talla XG"),
        "Dispositivos médicos",
        "exempt",
    ),
    ("Cabestrillo", ("adulto", "pediátrico"), "Dispositivos médicos", "exempt"),
    ("Recolector de orina", ("estéril × 1", "× 10"), "Dispositivos médicos", "rate_19"),
    ("Prueba de antígeno", ("× 1",), "Dispositivos médicos", "rate_19"),
    ("Aplicador de gotas", ("× 1",), "Dispositivos médicos", "rate_19"),
    (
        "Pastillero semanal",
        ("simple", "cuatro tomas"),
        "Dispositivos médicos",
        "rate_19",
    ),
    ("Bolsa para colostomía", ("× 10", "× 30"), "Dispositivos médicos", "exempt"),
    ("Sonda nasogástrica", ("14 Fr", "16 Fr"), "Dispositivos médicos", "exempt"),
    (
        "Equipo de venoclisis",
        ("macrogoteo", "microgoteo"),
        "Dispositivos médicos",
        "rate_19",
    ),
]


def _forms(form):
    """The unit a base unit is counted in, given the pack's own form."""
    return {"tabletas": "tableta", "cápsulas": "cápsula", "sobres": "sobre"}.get(
        form, "unidad"
    )


def generated_products():
    """Every product the grammar can build, in one fixed order.

    Deterministic and total: the order does not depend on a hash iteration or on
    a set, so the fixture takes the same first N on every machine and the ids
    derived from these names do not move between runs.
    """
    rows = []
    # Goods first, then liquids, then solids -- and the solids loop runs
    # **pack-outermost**. The fixture takes the first N of this stream, so the
    # order decides what a smaller catalog loses: this way it loses the largest
    # pack sizes of every molecule rather than the last eighty molecules
    # outright, and every category, every IVA class and every laboratorio still
    # has rows behind its filter chip.
    for base, variants, category, vat in GOODS:
        for variant in variants:
            rows.append(
                {
                    "name": f"{base} {variant}",
                    "presentation": variant,
                    "category": category,
                    "active_ingredient": "",
                    "strength": "",
                    "unit": "unidad",
                    "pack_unit": "unidad",
                    "pack": 1,
                    "vat_class": vat,
                    "requires_prescription": False,
                    "controlled": False,
                    "cold_chain": False,
                    # INVIMA registers cosmetics and devices too, but not a
                    # bottle of water: the seed leaves those `not_applicable`,
                    # which is what that value is for.
                    "registrable": category != "Bebidas y sueros",
                }
            )
    for name, category, strengths, form, rx, cold in LIQUIDS:
        for strength in strengths:
            for volume in VOLUMES:
                rows.append(
                    {
                        "name": f"{name} {strength} {volume} ml",
                        "presentation": f"frasco {volume} ml",
                        "category": category,
                        "active_ingredient": name.split(" ")[0],
                        "strength": strength,
                        "unit": "frasco",
                        "pack_unit": "frasco",
                        "pack": 1,
                        "vat_class": (
                            "rate_19" if category == "Cuidado personal" else "excluded"
                        ),
                        "requires_prescription": rx,
                        "controlled": False,
                        "cold_chain": cold,
                        "registrable": True,
                    }
                )
    for pack in PACKS:
        for ingredient, category, strengths, form, rx, controlled in SOLIDS:
            for strength in strengths:
                rows.append(
                    {
                        "name": f"{ingredient} {strength} × {pack}",
                        "presentation": f"caja × {pack} {form}",
                        "category": category,
                        "active_ingredient": ingredient,
                        "strength": strength,
                        "unit": "caja",
                        "pack_unit": _forms(form),
                        "pack": pack,
                        "vat_class": "excluded",
                        "requires_prescription": rx,
                        "controlled": controlled,
                        "cold_chain": False,
                        "registrable": True,
                    }
                )
    return rows


def stable_int(*parts):
    """A deterministic integer from a natural key.

    Not `random`: a fixture that draws from a shared generator changes every row
    downstream when somebody inserts one product, and a demo whose ids and
    prices move on an unrelated edit is a demo nobody can screenshot. Hashing
    each row's own key gives the same "randomness" with none of that coupling.
    """
    digest = hashlib.blake2b(
        "·".join(str(part) for part in parts).encode("utf-8"), digest_size=8
    )
    return int.from_bytes(digest.digest(), "big")


def ean13(seed):
    """An EAN-13 with a valid check digit, from the in-store `200`–`299` prefix
    range, so no seeded code can collide with a real manufacturer's GTIN."""
    body = f"{200 + seed % 100}{seed // 100 % 10_000_000_000:010d}"[:12]
    total = sum(
        int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(body)
    )
    return body + str((10 - total % 10) % 10)
