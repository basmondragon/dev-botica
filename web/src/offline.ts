/**
 * The PWA application shell (architecture §9).
 *
 * It precaches the built HTML, JS and CSS and the two self-hosted Geist faces,
 * so a reload with no connection paints the sidebar, the header and the route
 * chrome rather than the browser's offline page.
 *
 * **It never caches an API response.** Not `/api/me`, not `/api/locations`, not
 * a settings read, not one byte under `/api/`. The local store S2 builds is the
 * offline data layer, and two caches are two truths -- the specific failure
 * being a stock level served from an HTTP cache to a screen a cashier is about
 * to sell from. This is a rule about the whole product, enforced in the only
 * place it can be enforced, and it is set here because by S2 it is too late to
 * find out that it was not.
 *
 * **A new shell version activates on the next full load, never mid-session.**
 * Swapping the running application under an unsent sale is precisely what S2's
 * queue and S4's ticket cannot tolerate. A pending update is a line in the
 * settings dialog, not a banner and never a forced reload.
 */
let updateWaiting = false;

/**
 * The worker is served from the origin root, not from `/static/`, because a
 * worker's scope is its own directory: one under `/static/` would control the
 * assets and not the routes, and an offline reload of `/inventory` would reach
 * the browser's own offline page.
 */
export async function registerShellWorker() {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator))
    return;
  if (import.meta.env.DEV) return;
  try {
    const registration = await navigator.serviceWorker.register("/sw.js", {
      scope: "/",
    });
    registration.addEventListener("updatefound", () => {
      const installing = registration.installing;
      if (!installing) return;
      installing.addEventListener("statechange", () => {
        if (
          installing.state === "installed" &&
          navigator.serviceWorker.controller
        ) {
          updateWaiting = true;
        }
      });
    });
  } catch {
    // A browser that refuses to register one still runs the application; it
    // simply has no shell to paint from when the connection is gone.
  }
}

/** A pending update is a line in the settings dialog, not a banner. */
export function shellUpdatePending() {
  return updateWaiting;
}
