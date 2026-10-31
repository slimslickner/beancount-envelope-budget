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
    setup_popovers();
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

// How much space to keep between a popover and the viewport edge, and between a
// popover and the cell that opened it, in pixels.
const POPOVER_MARGIN = 8;
const POPOVER_GAP = 4;
// Never shrink a popover below this before letting it scroll instead.
const POPOVER_MIN_WIDTH = 160;
const POPOVER_MIN_HEIGHT = 96;

// The toggle whose popover is currently open, and that popover, so it can be
// repositioned as the page or the report's scroll box moves. Null when closed.
let open_toggle = null;
let open_pop = null;

// Where a floated popover came from, so it can be put back on close. A popover
// is moved to the document body while open to escape the table cell's stacking
// context — a fixed element nested in a z-indexed cell is still painted under
// Fava's sidebar, however high its z-index.
const popover_home = new WeakMap();

// Window-level listeners are bound once; the report re-renders in place, so the
// per-cell listeners are re-added on each load but these must not pile up.
let popovers_bound = false;

/**
 * Size popovers to their content and keep them inside the viewport.
 *
 * The popovers are pure CSS — a hidden checkbox reveals them, and the stylesheet
 * anchors them to the cell. This is a progressive enhancement: on open it
 * measures the popover and the space around the cell, switches the popover to a
 * fixed overlay so the report's scroll box cannot clip it and Fava's sidebar
 * cannot cover it, and sizes it to the wider of its content and the available
 * space. Without JavaScript the popovers still open, anchored as before.
 */
function setup_popovers() {
  for (const toggle of document.querySelectorAll(".envelope-pop-toggle")) {
    toggle.addEventListener("change", () => {
      if (toggle.checked) {
        open_toggle = toggle;
        open_pop = popover_of(toggle);
        const host = toggle.closest(".envelope-pop-host");
        if (open_pop && host instanceof HTMLElement) {
          place_popover(host, open_pop);
        }
      } else if (open_toggle === toggle) {
        close_open_popover();
      }
    });
  }
  if (!popovers_bound) {
    popovers_bound = true;
    window.addEventListener("resize", place_open_popover);
    // Capture phase, so the report's own scroll box is caught as well as the page.
    window.addEventListener("scroll", place_open_popover, true);
  }
}

/**
 * The popover a toggle reveals, or null when the cell has none.
 *
 * @param {Element} toggle
 * @returns {HTMLElement | null}
 */
function popover_of(toggle) {
  const host = toggle.closest(".envelope-pop-host");
  const pop = host && host.querySelector(".envelope-pop, .envelope-detail");
  return pop instanceof HTMLElement ? pop : null;
}

/**
 * Reposition the open popover, if any, after a scroll or resize.
 *
 * A scroll inside the popover itself is ignored, so its own scrolling does not
 * make it re-measure and jitter.
 *
 * @param {Event} [event]
 */
function place_open_popover(event) {
  if (!open_toggle || !open_toggle.checked || !open_pop) {
    return;
  }
  const target = event && event.target;
  if (target instanceof Node && open_pop.contains(target)) {
    return;
  }
  const host = open_toggle.closest(".envelope-pop-host");
  if (host instanceof HTMLElement) {
    place_popover(host, open_pop);
  }
}

/** Close and reset the open popover, if any. */
function close_open_popover() {
  if (open_pop) {
    reset_popover(open_pop);
  }
  open_pop = null;
  open_toggle = null;
}

/**
 * Move a popover to the document body so nothing can clip or cover it.
 *
 * Its original place is remembered so :func:`reset_popover` can restore it.
 *
 * @param {HTMLElement} pop
 */
function float_popover(pop) {
  if (pop.parentNode === document.body) {
    return;
  }
  popover_home.set(pop, { parent: pop.parentNode, next: pop.nextSibling });
  pop.classList.add("envelope-pop-floating");
  // The stylesheet reveals the popover through the cell's checked checkbox; once
  // it is moved out of the cell, show it directly instead.
  pop.style.display = "block";
  document.body.append(pop);
}

/**
 * Place one popover relative to its cell and the viewport.
 *
 * It is switched to `position: fixed` (via the floating class, so the stylesheet
 * anchors are cleared) and then given an explicit left/right, top/bottom, width
 * and max-height, all within an 8px viewport margin. It grows from the side of
 * the cell with the most room, hanging below the cell when there is space and
 * above it when there is not. The width is the popover's measured content width,
 * capped by the room on the chosen side, so a ledger line is never wrapped —
 * only scrolled if it still cannot fit.
 *
 * @param {HTMLElement} host
 * @param {HTMLElement} pop
 */
function place_popover(host, pop) {
  const host_box = host.getBoundingClientRect();
  const viewport_width = document.documentElement.clientWidth || window.innerWidth;
  const viewport_height = document.documentElement.clientHeight || window.innerHeight;

  // Move it out of the cell and fix it to the viewport, then measure the
  // natural width: unclamped, at the origin, with the popover's own wrapping
  // rules (ledger lines stay on one line).
  float_popover(pop);
  pop.style.visibility = "hidden";
  pop.style.boxSizing = "border-box";
  pop.style.width = "max-content";
  pop.style.maxWidth = "none";
  pop.style.maxHeight = "none";
  pop.style.left = "0px";
  pop.style.top = "0px";
  pop.style.right = "auto";
  pop.style.bottom = "auto";
  const natural_width = pop.getBoundingClientRect().width;

  // The stylesheet's `min-width` keeps a comfortable floor; drop it here so the
  // measured width can shrink to fit a narrow side rather than overflow.
  pop.style.minWidth = "0px";

  const left_space = host_box.right - POPOVER_MARGIN;
  const right_space = viewport_width - host_box.left - POPOVER_MARGIN;
  // Grow left, matching the stylesheet default, unless the left has less room.
  const grow_left = left_space >= right_space || natural_width > right_space;
  const available = Math.max(
    grow_left ? left_space : right_space,
    POPOVER_MIN_WIDTH,
  );
  pop.style.width = `${Math.min(natural_width, available)}px`;
  pop.style.maxWidth = `${available}px`;
  if (grow_left) {
    pop.style.right = `${viewport_width - host_box.right}px`;
    pop.style.left = "auto";
  } else {
    pop.style.left = `${host_box.left}px`;
    pop.style.right = "auto";
  }

  // Hang below the cell, or above it when there is not enough room below.
  const below = host_box.bottom + POPOVER_GAP;
  let height = viewport_height - below - POPOVER_MARGIN;
  if (height < POPOVER_MIN_HEIGHT) {
    pop.style.top = "auto";
    pop.style.bottom = `${viewport_height - host_box.top + POPOVER_GAP}px`;
    height = host_box.top - POPOVER_GAP - POPOVER_MARGIN;
  } else {
    pop.style.top = `${below}px`;
    pop.style.bottom = "auto";
  }
  pop.style.maxHeight = `${Math.max(height, POPOVER_MIN_HEIGHT)}px`;
  pop.style.visibility = "";
}

/**
 * Undo :func:`place_popover`: clear its inline styles and return it to the cell.
 *
 * @param {HTMLElement | null} pop
 */
function reset_popover(pop) {
  if (!pop) {
    return;
  }
  for (const property of [
    "top",
    "right",
    "bottom",
    "left",
    "width",
    "min-width",
    "max-width",
    "max-height",
    "visibility",
    "box-sizing",
  ]) {
    pop.style.removeProperty(property);
  }
  pop.classList.remove("envelope-pop-floating");
  pop.style.removeProperty("display");
  const home = popover_home.get(pop);
  popover_home.delete(pop);
  if (home && home.parent) {
    if (home.next && home.next.parentNode === home.parent) {
      home.parent.insertBefore(pop, home.next);
    } else {
      home.parent.append(pop);
    }
  }
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
