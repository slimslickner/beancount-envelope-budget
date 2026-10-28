// Opt-in editing for the Envelopes report.
//
// The server renders a plain `commit` form for every create action, prefilled
// with the Beancount directive it would write (the `data-source` attribute).
// Without JavaScript those forms submit their fields as-is. With it, this
// module opens a small modal showing that directive, lets it be edited as
// Beancount text, and posts the result to the same endpoint — so the ledger is
// still written by the server, through Fava's own file API.

/** @type {{ init(): void, onPageLoad(): void, onExtensionPageLoad(): void }} */
export default {
  init() {},
  onPageLoad() {},
  onExtensionPageLoad() {
    for (const form of document.querySelectorAll("form[data-source]")) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        open_editor(form);
      });
    }
  },
};

/**
 * Open the editor for a form's prefilled directive and, on save, post it.
 *
 * @param {Element} form
 */
async function open_editor(form) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const text = await edit_source(form.dataset.source || "");
  if (text === null) {
    return;
  }
  const body = new FormData();
  body.append("source", text);
  const response = await fetch(form.action, { method: "POST", body });
  if (response.ok) {
    window.location.reload();
  } else {
    const message = await response.text();
    window.alert(`Could not save:\n\n${message}`);
  }
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
