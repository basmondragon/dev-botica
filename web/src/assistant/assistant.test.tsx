import { describe, expect, it } from "vitest";
import type { Bundle } from "./bundle";
import { extract, fold } from "./extract";
import {
  IRRELEVANT,
  SATISFIED,
  UNRESOLVED,
  evaluate,
  makesConditional,
  readExtraction,
} from "./filters";
import { emptyBody, localProse, type Card } from "./pipeline";
import { counterLabel, unitsLabel } from "./vocabulary";

/**
 * The half of this stage that has to work with the fibre cut.
 *
 * **The lexicon is data and the algorithm is code**, so these run against the
 * bundle shape the server serves rather than against a second copy of the
 * vocabulary — which is the property that keeps the two extractors from
 * drifting apart.
 */

const BUNDLE = {
  version: "1.test",
  symptoms: {
    diarrhea: { label: "diarrea", forms: ["diarrea", "soltura"] },
    fever: { label: "fiebre", forms: ["fiebre", "calentura"] },
    blood_in_stool: {
      label: "sangre en la deposición",
      forms: ["hay sangre", "con sangre"],
    },
    cough: { label: "tos", forms: ["tos"] },
  },
  populations: {
    adult: { label: "adulto", forms: ["adulto"] },
    child: { label: "niño", forms: ["nino", "niño"] },
    pregnant: { label: "embarazada", forms: ["embarazada"] },
  },
  ingredients: {
    losartan: { label: "losartán", forms: ["losartan"] },
    warfarina: { label: "warfarina", forms: ["warfarina"] },
  },
  treatment_leads: ["toma", "esta tomando"],
  negations: ["sin", "no"],
  number_words: { dos: 2, tres: 3 },
  age_populations: ["adult", "child"],
  symptom_category_map: { diarrhea: ["cat-1"] },
  strings: {
    symptom_primary: { diarrhea: "repone la pérdida de líquidos" },
    symptom_primary_default: "es lo primero que se ofrece",
    templates: {
      symptom_secondary: "alivia el síntoma que describe el cliente",
      bought_together_location:
        "aparece en el {share} de los tickets con {anchor}",
      bought_together_location_low: "se lleva junto con {anchor} en esta sede",
      bought_together_network: "en la red se lleva junto con {anchor}",
      ticket_companion: "se lleva junto con lo que ya está en el ticket",
      substitute_available: "sustituto de una referencia agotada en la sede",
    },
    empty: {
      title: "Ninguna referencia disponible para lo que describe el cliente",
      one: "La referencia que aplica está agotada en esta sede.",
      many: "Las {count} referencias que aplican están agotadas en esta sede.",
      none: "Ninguna referencia del catálogo aplica a lo que describe el cliente.",
    },
    local: {
      primary_first: "Ofrezca {item} primero.",
      primary_conditional: "Lea la condición de la tarjeta antes de ofrecer.",
      primary_none: "No hay una primera opción disponible en esta sede.",
      secondary_pair: "En esta sede {item} se lleva junto con {anchor}.",
      secondary_one: "La sede tiene la referencia disponible.",
      secondary_many: "La sede tiene las {count} referencias disponibles.",
    },
  },
  enabled: true,
  suggestion_card_count: 3,
  retain_transcripts: true,
} as unknown as Bundle;

const HANDOFF =
  "Lleva dos días con diarrea y algo de fiebre. Adulto, toma losartán.";

describe("the lexicon, on the device", () => {
  it("draws the handoff's four chips and holds the duration off the row", () => {
    const facts = extract(HANDOFF, BUNDLE);
    const chips = facts.filter((one) => one.kind !== "duration");
    expect(chips.map((one) => one.label)).toEqual([
      "diarrea",
      "fiebre",
      "adulto",
      "tratamiento activo · losartán",
    ]);
    expect(facts.find((one) => one.kind === "duration")?.value).toBe(2);
  });

  it("puts the temperature on the fever chip", () => {
    const facts = extract(
      "Lleva dos días con diarrea y fiebre de 39. Adulto.",
      BUNDLE,
    );
    const fever = facts.find((one) => one.key === "fever");
    expect(fever?.value).toBe(39);
    expect(fever?.label).toBe("fiebre 39 °C");
  });

  it("tells a denial from a silence, which is the whole of negation handling", () => {
    const facts = extract("Diarrea sin fiebre, adulto", BUNDLE);
    expect(facts.find((one) => one.key === "fever")?.negated).toBe(true);
    expect(facts.find((one) => one.key === "fever")?.label).toBe("sin fiebre");
  });

  it("needs a lead word before it calls a molecule an active treatment", () => {
    expect(
      extract("Necesito losartán de 50", BUNDLE).filter(
        (one) => one.kind === "active_treatment",
      ),
    ).toEqual([]);
    expect(
      extract("Adulto, toma losartán", BUNDLE)
        .filter((one) => one.kind === "active_treatment")
        .map((one) => one.key),
    ).toEqual(["losartan"]);
  });

  it("folds accents on both sides of every match", () => {
    expect(fold("Losartán")).toBe("losartan");
    expect(
      extract("adulto, toma LOSARTAN", BUNDLE).map((one) => one.key),
    ).toContain("losartan");
  });

  it("finds nothing at all without a bundle, and does not throw", () => {
    expect(extract(HANDOFF, null)).toEqual([]);
  });
});

describe("the three outcomes", () => {
  const trigger = [
    { symptom: "fever", operator: ">", value: 38.5, unit: "celsius" },
    { symptom: "blood_in_stool" },
  ];
  const of = (transcript: string) =>
    readExtraction(extract(transcript, BUNDLE), BUNDLE.age_populations ?? []);

  it("is unresolved when the temperature was never given", () => {
    expect(evaluate(trigger, of(HANDOFF))).toBe(UNRESOLVED);
  });

  it("is satisfied when the measurement clears the threshold", () => {
    expect(evaluate(trigger, of("Diarrea con fiebre de 39, adulto"))).toBe(
      SATISFIED,
    );
  });

  it("is irrelevant when the measurement was given and does not clear it", () => {
    expect(evaluate([trigger[0]!], of("Diarrea con fiebre de 37,5"))).toBe(
      IRRELEVANT,
    );
  });

  it("lets an age decide another age false and leaves a chronic state open", () => {
    const adult = of("Adulto con tos");
    expect(evaluate([{ population: "child" }], adult)).toBe(IRRELEVANT);
    expect(evaluate([{ population: "pregnant" }], adult)).toBe(UNRESOLVED);
  });

  it("lets one named treatment decide the others false", () => {
    expect(
      evaluate([{ interacts_with_ingredient: "warfarina" }], of(HANDOFF)),
    ).toBe(IRRELEVANT);
    expect(
      evaluate([{ interacts_with_ingredient: "warfarina" }], of("Tos, adulto")),
    ).toBe(UNRESOLVED);
  });

  it("only turns an unresolved do_not_suggest_if into a card", () => {
    expect(makesConditional("do_not_suggest_if", "blocking", UNRESOLVED)).toBe(
      true,
    );
    expect(makesConditional("contraindication", "blocking", UNRESOLVED)).toBe(
      false,
    );
    expect(makesConditional("interaction", "advisory", SATISFIED)).toBe(true);
    expect(makesConditional("do_not_suggest_if", "blocking", IRRELEVANT)).toBe(
      false,
    );
  });
});

function card(overrides: Partial<Card>): Card {
  return {
    item: { id: "1", name: "Suero oral" } as Card["item"],
    type: "first_choice",
    reasonCode: "symptom_primary",
    reason: "repone la pérdida de líquidos",
    price: 390000,
    availableQuantity: 14,
    warningId: null,
    ruleConfidence: null,
    rank: 1,
    ...overrides,
  };
}

describe("card B's local register", () => {
  it("names the first card and counts the shelf where there is no rule", () => {
    const { primary, secondary } = localProse([card({})], 3, BUNDLE);
    expect(primary).toBe("Ofrezca Suero oral primero.");
    // **The handoff's own second half**, and it is true on a first morning.
    expect(secondary).toBe("La sede tiene las 3 referencias disponibles.");
  });

  it("speaks about the pair where a rule produced one", () => {
    const pair = card({
      item: { id: "2", name: "Electrolitos" } as Card["item"],
      type: "bought_together",
      reasonCode: "bought_together_location",
      reason: "se lleva junto con Suero oral en esta sede",
    });
    const { secondary } = localProse([card({}), pair], 3, BUNDLE);
    expect(secondary).toBe(
      "En esta sede Electrolitos se lleva junto con Suero oral.",
    );
  });

  it("falls back to reading the condition where there is no first choice", () => {
    const only = card({ type: "conditional", warningId: "w1" });
    expect(localProse([only], 2, BUNDLE).primary).toBe(
      "Lea la condición de la tarjeta antes de ofrecer.",
    );
  });
});

describe("card C's copy", () => {
  it("says what the shelf has, not what the history does", () => {
    expect(emptyBody(3, BUNDLE)).toBe(
      "Las 3 referencias que aplican están agotadas en esta sede.",
    );
    expect(emptyBody(1, BUNDLE)).toBe(
      "La referencia que aplica está agotada en esta sede.",
    );
    expect(emptyBody(0, BUNDLE)).toBe(
      "Ninguna referencia del catálogo aplica a lo que describe el cliente.",
    );
  });

  it("counts cards shown against candidates that survived the filter", () => {
    expect(counterLabel(3, 12)).toBe("3 de 12 referencias");
    expect(counterLabel(1, 1)).toBe("1 de 1 referencia");
  });

  it("puts the units before the reason, because that is the order they are read", () => {
    expect(unitsLabel(14, "Chapinero")).toBe("14 unidades en Chapinero");
    expect(unitsLabel(1, "Usme")).toBe("1 unidad en Usme");
  });
});
