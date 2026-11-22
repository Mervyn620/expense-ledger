/* Shared, local-only appearance preferences. No ledger data is stored here. */
(() => {
  const KEY = "expense-ledger-appearance-v1";
  const wallpapers = [
    { id: "cloud", label: "云海晨曦", color: "暮玫", file: "pexels-dreamypixel-552784.jpg" },
    { id: "ink", label: "黑沙纹理", color: "墨金", file: "pexels-adrien-olichon-1257089-2387819.jpg" },
    { id: "jade", label: "极光海岸", color: "极光", file: "pexels-evelin-magnus-34540965.jpg" },
    { id: "indigo", label: "蓝紫流光", color: "夜蓝", file: "pexels-steve-29586678.jpg" },
  ];
  const ids = wallpapers.map(item => item.id);
  const defaults = { wallpaper: "cloud", color: "auto", solid: false, reducedMotion: false };
  const legacyIds = { alpine: "cloud", snow: "indigo", lake: "jade", coast: "ink" };
  function normalize(value) {
    const wallpaper = legacyIds[value?.wallpaper] ?? value?.wallpaper;
    const color = legacyIds[value?.color] ?? value?.color;
    return {
      wallpaper: [...ids, "none"].includes(wallpaper) ? wallpaper : defaults.wallpaper,
      color: [...ids, "auto"].includes(color) ? color : defaults.color,
      solid: value?.solid === true,
      reducedMotion: value?.reducedMotion === true,
    };
  }
  function read(initial = false) {
    const snapshot = window.__expenseDesktopNativeAppearance ? window.__expenseDesktopAppearanceSnapshot : null;
    const fallback = snapshot ?? defaults;
    if (initial && snapshot) return normalize(snapshot);
    try {
      const saved = localStorage.getItem(KEY);
      return normalize(saved === null ? fallback : JSON.parse(saved));
    } catch { return normalize(fallback); }
  }
  let current = read(true);
  function syncDesktopMode() {
    if (!window.__expenseDesktopHost) return;
    const root = document.documentElement;
    root.dataset.desktopVisualMode = window.__expenseDesktopVisualMode === "particle" ? "particle" : "static";
    if (window.__expenseDesktopAutomaticTone) {
      window.__expenseDesktopUiTone = "dark"; // every desktop palette is dark glass since 0.17
    }
    root.dataset.desktopTone = window.__expenseDesktopUiTone === "dark" ? "dark" : "light";
  }
  function apply() {
    current = normalize(current);
    const root = document.documentElement;
    if (window.__expenseDesktopHost) {
      root.classList.add("expense-native");
      root.dataset.desktopVisualMode = window.__expenseDesktopVisualMode === "particle" ? "particle" : "static";
      root.dataset.desktopTone = window.__expenseDesktopUiTone === "dark" ? "dark" : "light";
      root.dataset.desktopGlass = window.__expenseDesktopGlassEnabled === false ? "opaque" : "glass";
    }
    root.dataset.wallpaper = current.wallpaper;
    root.dataset.theme = current.color === "auto" ? (current.wallpaper === "none" ? "cloud" : current.wallpaper) : current.color;
    root.dataset.transparency = current.solid ? "solid" : "glass";
    root.dataset.motion = current.reducedMotion ? "reduced" : "full";
    syncDesktopMode();
    try {
      if (window.__expenseDesktopNativeAppearance && typeof window.chrome?.webview?.postMessage === "function") {
        window.chrome.webview.postMessage({ type: "appearance", appearance: { ...current } });
      }
    } catch { /* Appearance remains usable while the native host is unavailable. */ }
  }
  apply();
  // Share normalized snapshots with settings and the native host.
  window.LedgerAppearance = Object.freeze({ normalize, current: () => ({ ...current }), defaults: Object.freeze({ ...defaults }), syncDesktopMode });

  document.addEventListener("DOMContentLoaded", () => {
    const dialog = document.createElement("dialog");
    dialog.id = "appearanceDialog";
    dialog.className = "appearance-dialog";
    dialog.setAttribute("aria-labelledby", "appearanceTitle");
    dialog.innerHTML = `
      <header class="appearance-heading"><div><p>让账本更适合你</p><h2 id="appearanceTitle">外观</h2></div><button type="button" class="appearance-close" aria-label="关闭外观设置">×</button></header>
      <fieldset data-wallpaper-settings><legend>壁纸</legend><div class="wallpaper-grid">${wallpapers.map(item => `<button type="button" class="wallpaper-option" data-wallpaper-choice="${item.id}" aria-pressed="false"><img src="/static/wallpapers/${item.file}" alt="" loading="lazy" decoding="async"><span>${item.label}<small>${item.color}</small></span></button>`).join("")}</div><button class="plain-wallpaper" type="button" data-wallpaper-choice="none" aria-pressed="false">纯色背景</button></fieldset>
      <fieldset><legend>主题色</legend><div class="theme-colors"><button type="button" data-color-choice="auto" aria-pressed="false">跟随壁纸</button>${wallpapers.map(item => `<button type="button" data-color-choice="${item.id}" aria-pressed="false"><i class="theme-dot theme-dot-${item.id}" aria-hidden="true"></i>${item.color}</button>`).join("")}</div></fieldset>
      <div class="appearance-toggles"><label><span>提高卡片不透明度<small>让数字和文字更清晰</small></span><input type="checkbox" id="solidSurfaces"></label><label><span>减少动效<small>关闭上浮和扫光</small></span><input type="checkbox" id="reduceAppearanceMotion"></label></div>
      <footer class="appearance-footer"><button type="button" class="appearance-reset">恢复默认</button><span id="appearanceSaveStatus" role="status">${window.__expenseDesktopNativeAppearance ? "自动保存到此应用" : "自动保存到此浏览器"}</span></footer>`;
    document.body.append(dialog);
    const status = dialog.querySelector("#appearanceSaveStatus");
    function render() {
      dialog.querySelectorAll("[data-wallpaper-choice]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.wallpaperChoice === current.wallpaper)));
      dialog.querySelectorAll("[data-color-choice]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.colorChoice === current.color)));
      dialog.querySelector("#solidSurfaces").checked = current.solid;
      dialog.querySelector("#reduceAppearanceMotion").checked = current.reducedMotion;
    }
    function save() {
      apply();
      render();
      try { localStorage.setItem(KEY, JSON.stringify(current)); status.textContent = "外观已保存"; }
      catch { status.textContent = "已应用；浏览器未允许保存，刷新后将恢复默认"; }
    }
    document.querySelectorAll("[data-open-appearance]").forEach(button => button.addEventListener("click", () => { render(); window.LedgerMotion ? window.LedgerMotion.openDialog(dialog) : dialog.showModal(); }));
    const close = () => window.LedgerMotion ? window.LedgerMotion.closeDialog(dialog) : dialog.close();
    dialog.querySelector(".appearance-close").addEventListener("click", close);
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    dialog.querySelectorAll("[data-wallpaper-choice]").forEach(button => button.addEventListener("click", () => { current.wallpaper = button.dataset.wallpaperChoice; save(); }));
    dialog.querySelectorAll("[data-color-choice]").forEach(button => button.addEventListener("click", () => { current.color = button.dataset.colorChoice; save(); }));
    dialog.querySelector("#solidSurfaces").addEventListener("change", event => { current.solid = event.target.checked; save(); });
    dialog.querySelector("#reduceAppearanceMotion").addEventListener("change", event => { current.reducedMotion = event.target.checked; save(); });
    dialog.querySelector(".appearance-reset").addEventListener("click", () => { current = { ...defaults }; save(); });
    window.addEventListener("storage", event => { if (event.key === KEY || event.key === null) { current = read(); apply(); render(); } });
    render();
  });
})();
