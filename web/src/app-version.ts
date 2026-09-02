declare const __APP_VERSION__: string;

/**
 * §A.13.1 · the sidebar's version stamp reads `Botica 2.4.1`. It measures
 * 2.90:1 and is the one informational value below AA in the system, accepted
 * **only** because the settings dialog states the same version at full
 * contrast (§B.15). Removing either one means the stamp steps to `#6b6b6b`.
 */
export function versionLabel(version: string): string {
  const normalised = version.trim().replace(/^v(?=\d)/i, "");
  return normalised ? `Botica ${normalised}` : "Botica desarrollo";
}

export const APP_VERSION = __APP_VERSION__;
export const APP_VERSION_LABEL = versionLabel(APP_VERSION);
