import "@testing-library/jest-dom/vitest";

// jsdom implements no layout, so it ships no `scrollIntoView`. Every list in
// this product keeps the active option in view (§B.5.4, §B.13.2), so without
// this stub any test that opens one fails on the environment rather than on the
// component. A no-op is the honest stand-in: there is nothing to scroll.
//
// Guarded on `Element` itself, because the token and module checks run in the
// `node` environment where there is no DOM at all.
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
