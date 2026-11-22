/* Selection is deliberately reset on every open, never persisted. */
(() => {
  const defaults = Object.freeze(["walmart_ca", "fantuan"]);
  function bind({ onConfirm, isBusy = () => false, document = window.document }) {
    const button = document.getElementById("billRefreshButton");
    const dialog = document.getElementById("billRefreshDialog");
    const form = document.getElementById("billRefreshForm");
    const confirm = document.getElementById("billRefreshConfirm");
    const inputs = [...form.querySelectorAll("input[name=platform]")];
    let pending = false;
    const selected = () => inputs.filter(input => input.checked).map(input => input.value);
    const update = () => { confirm.disabled = pending || isBusy() || !selected().length; };
    function close() { if (dialog.open) dialog.close(); }
    function open() {
      if (pending || isBusy() || dialog.open) return;
      for (const input of inputs) input.checked = (Array.isArray(window.__expenseDesktopDefaultBillPlatforms) ? window.__expenseDesktopDefaultBillPlatforms : defaults).includes(input.value);
      update();
      dialog.showModal();
      inputs[0].focus();
    }
    button.addEventListener("click", open);
    form.addEventListener("change", update);
    form.querySelectorAll("[data-close-bill-refresh]").forEach(control => control.addEventListener("click", close));
    dialog.addEventListener("close", () => button.focus());
    dialog.addEventListener("click", event => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (pending || isBusy()) return;
      const platforms = selected();
      if (!platforms.length) { update(); return; }
      pending = true;
      button.disabled = true;
      close();
      try { await onConfirm(platforms); }
      finally { pending = false; button.disabled = isBusy(); update(); }
    });
    return { open, close };
  }
  window.LedgerBillRefresh = Object.freeze({ defaults, bind });
})();
