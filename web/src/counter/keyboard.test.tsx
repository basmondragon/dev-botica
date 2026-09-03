import { useRef, useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { useCapture, useTillKeys } from "./capture";

/**
 * §B.13.3 · the keyboard on a till surface.
 *
 * **The scanner owns it.** A scan is a burst of characters followed by `Enter`,
 * so any surface where `j` means something is a surface where scanning a
 * product code navigates — and a till whose focus is somewhere else is a till
 * where the next scan goes into the void, which is discovered at a counter with
 * a queue.
 */

function Surface({
  suspended = false,
  onCobrar = () => undefined,
  onSync = () => undefined,
}: {
  suspended?: boolean;
  onCobrar?: () => void;
  onSync?: () => void;
}) {
  const field = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState("");
  useCapture(field, suspended);
  useTillKeys({
    suspended,
    cobrar: onCobrar,
    focusCapture: () => field.current?.focus(),
    syncPanel: onSync,
    clear: () => setValue(""),
  });
  return (
    <div>
      <button type="button">Cobrar</button>
      <div role="row" tabIndex={0}>
        Línea 1
      </div>
      <input
        ref={field}
        aria-label="Escanee o busque un producto"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
    </div>
  );
}

const CAPTURE = "Escanee o busque un producto";

describe("a scan lands wherever focus is", () => {
  it("redirects from the page body, a ticket row and the Cobrar button", async () => {
    // Acceptance 6 · three scans, nothing clicked into a field first.
    const user = userEvent.setup();
    render(<Surface />);
    const field = screen.getByLabelText(CAPTURE);

    document.body.focus();
    await user.keyboard("770200");
    expect(field).toHaveValue("770200");
    await user.clear(field);

    screen.getByRole("row").focus();
    await user.keyboard("123456");
    expect(field).toHaveValue("123456");
    await user.clear(field);

    screen.getByRole("button", { name: "Cobrar" }).focus();
    await user.keyboard("999888");
    expect(field).toHaveValue("999888");
  });

  it("does nothing at all while a dialog is up", async () => {
    // Acceptance 8 · a barcode arriving during payment is a cashier scanning
    // the next customer's item, and adding it to a ticket that is being paid
    // for is the worst outcome available.
    const user = userEvent.setup();
    render(<Surface suspended />);
    document.body.focus();
    await user.keyboard("7702001234567");
    expect(screen.getByLabelText(CAPTURE)).toHaveValue("");
  });
});

describe("the function keys, and no single letter", () => {
  it("binds F2, F4 and F8 and leaves every letter to the field", async () => {
    // Acceptance 27 · letters only type into the capture field.
    const user = userEvent.setup();
    const cobrar = vi.fn();
    const sync = vi.fn();
    render(<Surface onCobrar={cobrar} onSync={sync} />);
    const field = screen.getByLabelText(CAPTURE);

    document.body.focus();
    await user.keyboard("jkgnx");
    expect(field).toHaveValue("jkgnx");
    expect(cobrar).not.toHaveBeenCalled();

    await user.keyboard("{F2}");
    expect(cobrar).toHaveBeenCalledTimes(1);
    await user.keyboard("{F8}");
    expect(sync).toHaveBeenCalledTimes(1);
  });

  it("clears the field on Esc and leaves the sale standing", async () => {
    const user = userEvent.setup();
    const cobrar = vi.fn();
    render(<Surface onCobrar={cobrar} />);
    const field = screen.getByLabelText(CAPTURE);
    document.body.focus();
    await user.keyboard("amoxi");
    expect(field).toHaveValue("amoxi");

    await user.keyboard("{Escape}");
    expect(field).toHaveValue("");
    // **`Esc` never closes or cancels the sale**, so nothing else fired.
    expect(cobrar).not.toHaveBeenCalled();
  });

  it("returns focus to the capture field on F4", async () => {
    const user = userEvent.setup();
    render(<Surface />);
    screen.getByRole("button", { name: "Cobrar" }).focus();
    await user.keyboard("{F4}");
    expect(screen.getByLabelText(CAPTURE)).toHaveFocus();
  });
});
