import clsx, { type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/** The class merger, taught this system's closed scales so it can dedupe them. */
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      text: ["10", "11", "12", "14", "16", "20", "28", "36"],
      radius: [
        "mark",
        "check",
        "icon",
        "segment",
        "control",
        "card",
        "panel",
        "pill",
      ],
      shadow: ["plane", "segment", "overlay"],
      tracking: ["eyebrow", "display"],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
