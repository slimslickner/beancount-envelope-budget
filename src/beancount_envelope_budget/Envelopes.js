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
  window.location.reload();
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
