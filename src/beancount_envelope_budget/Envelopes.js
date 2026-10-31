// Opt-in editing for the Envelopes report.
//
// The server renders a plain `commit` form for every create action, prefilled
// with sensible fields. Without JavaScript those forms submit those fields
// directly. With it, this module asks the `source` endpoint to render the
// Beancount those fields describe, shows it in a small modal for editing, and
// posts the result to `commit` — so the ledger is still written by the server,
// through Fava's own file API, and the client talks to it through Fava's
// extension API (which builds the endpoint URLs correctly).

/** @type {{ init(): void, onPageLoad(): void, onExtensionPageLoad(ctx: unknown): void }} */
export default {
  init() {},
  onPageLoad() {},
  onExtensionPageLoad(ctx) {
    track_scroll();
    scroll_to_window();
    const api = ctx && ctx.api;
    if (!api) {
      return;
    }
    for (const form of document.querySelectorAll("form[data-create]")) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        create(form, api);
      });
    }
  },
};

// Remember the table's scroll position across reloads.
//
// Every navigation (the month links) and every edit reload swaps the page's
// contents in place, recreating the scroll box at the top. Its position lives
// in sessionStorage, keyed by path so it survives a month change but not a move
// to another report; the hook that Fava calls after each render restores it and
// keeps saving as the box scrolls.
const SCROLL_KEY = "beancount-envelope:scroll";

/** @returns {string} The sessionStorage key for the current report page. */
function store_key() {
  return `${SCROLL_KEY}:${window.location.pathname}`;
}

/** Restore the scroll box, then keep its position saved as it scrolls. */
function track_scroll() {
  const box = document.querySelector(".envelope-table-scroll");
  if (!box) {
    return;
  }
  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(store_key()) || "null");
  } catch {
    saved = null;
  }
  if (saved) {
    box.scrollTop = saved.top;
    box.scrollLeft = saved.left;
  }
  let queued = false;
  box.addEventListener(
    "scroll",
    () => {
      if (queued) {
        return;
      }
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        try {
          sessionStorage.setItem(
            store_key(),
            JSON.stringify({ top: box.scrollTop, left: box.scrollLeft }),
          );
        } catch {
          // Storage can be unavailable (private mode); losing the position is fine.
        }
      });
    },
    { passive: true },
  );
}

/**
 * Bring the visible window into view in the month strip.
 *
 * The strip lists the budget's whole history, so at today's month — or after
 * jumping — the active months can sit off the right edge. Scrolling the strip
 * itself (rather than `scrollIntoView`) keeps them in sight without moving the
 * page.
 */
function scroll_to_window() {
  const strip = document.querySelector(".envelope-months");
  const active = strip && strip.querySelector(".envelope-month-visible");
  if (!(strip instanceof HTMLElement) || !(active instanceof HTMLElement)) {
    return;
  }
  const strip_box = strip.getBoundingClientRect();
  const active_box = active.getBoundingClientRect();
  strip.scrollLeft +=
    active_box.left - strip_box.left - strip.clientWidth / 2 + active_box.width / 2;
}

/**
 * Render a form's fields, edit the Beancount, and save it.
 *
 * @param {Element} form
 * @param {{ post(endpoint: string, body: FormData): Promise<any> }} api
 */
async function create(form, api) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  let preview;
  try {
    preview = await api.post("source", new FormData(form));
  } catch (error) {
    window.alert(`Could not prepare the entry:\n\n${describe(error)}`);
    return;
  }
  if (!preview || preview.error) {
    window.alert(`Could not prepare the entry:\n\n${preview?.error ?? "unknown error"}`);
    return;
  }
  const text = await edit_source(preview.source || "");
  if (text === null) {
    return;
  }
  const body = new FormData();
  body.append("source", text);
  body.append("format", "json");
  let result;
  try {
    result = await api.post("commit", body);
  } catch (error) {
    window.alert(`Could not save:\n\n${describe(error)}`);
    return;
  }
  if (result && result.error) {
    window.alert(`Could not save:\n\n${result.error}`);
    return;
  }
  // We made the change, so refresh now rather than waiting for Fava's watcher to
  // notice it (up to five seconds, then only the yellow reload button when
  // auto-reload is off). The refresh's own request reloads the ledger first.
  refresh();
}

/**
 * Refresh the report the way Fava's yellow reload button does.
 *
 * The button's click handler is Fava's own `router.reload()`, which an
 * extension cannot call directly; clicking the button reaches the same code.
 * It is hidden until a change is detected, but a programmatic click fires its
 * handler regardless. `window.location.reload()` is the fallback if a future
 * Fava renames the button.
 */
function refresh() {
  const button = document.querySelector("button.reload-page");
  if (button instanceof HTMLElement) {
    button.click();
  } else {
    window.location.reload();
  }
}

/**
 * Describe an unknown thrown value.
 *
 * @param {unknown} error
 * @returns {string}
 */
function describe(error) {
  return error && typeof error === "object" && "message" in error
    ? String(error.message)
    : String(error);
}

/**
 * Show a modal editor and resolve with the text, or null when cancelled.
 *
 * @param {string} initial
 * @returns {Promise<string | null>}
 */
function edit_source(initial) {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "envelope-edit-dialog";

    const editor = document.createElement("textarea");
    editor.rows = 6;
    editor.spellcheck = false;
    editor.value = initial;

    const actions = document.createElement("div");
    actions.className = "envelope-edit-actions";
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "Save";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    save.addEventListener("click", () => {
      dialog.close();
      resolve(editor.value);
    });
    cancel.addEventListener("click", () => {
      dialog.close();
      resolve(null);
    });
    actions.append(save, cancel);

    dialog.append(editor, actions);
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
    editor.focus();
    editor.setSelectionRange(editor.value.length, editor.value.length);
  });
}
