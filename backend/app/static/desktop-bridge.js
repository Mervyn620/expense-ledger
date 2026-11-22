// Installed only by the native host; old shells and normal browsers keep their sidebar.
(() => {
  window.__expenseDesktopHost = true;
  window.addEventListener("keydown", event => {
    if (event.altKey && !event.ctrlKey && !event.metaKey && event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      if (!event.repeat) window.chrome.webview.postMessage({type: "toggle-fullscreen"});
    }
  }, true);
  if (window.__expenseDesktopWallpaperLayout === true && document.documentElement) document.documentElement.dataset.desktopLayout = 'wallpaper';
  const install = () => {
    document.documentElement.classList.add("expense-native");
    // Apply the host's interface size before first paint; desktop-layout.js keeps it in sync later.
    const zoom = Number(window.__expenseDesktopZoom);
    if (Number.isFinite(zoom) && zoom >= 0.5 && zoom <= 2 && zoom !== 1) document.documentElement.style.zoom = String(zoom);
    if (window.__expenseDesktopWallpaperLayout === true) document.documentElement.dataset.desktopLayout = "wallpaper";
    if (window.__expenseDesktopWallpaperLayout === true && !document.getElementById("expense-desktop-layout-script")) {
      const layoutScript = document.createElement("script");
      layoutScript.id = "expense-desktop-layout-script";
      layoutScript.src = "/static/desktop-layout.js?v=20261119-zoom";
      layoutScript.defer = true; document.head.append(layoutScript);
    }
    if (!document.getElementById("expense-desktop-starfield-script")) {
    const effectsScript = document.createElement("script");
    effectsScript.id = "expense-desktop-starfield-script";
    effectsScript.src = "/static/desktop-starfield.js?v=20261120-dodge";
    effectsScript.defer = true;
    document.head.append(effectsScript);
    }
    if (document.querySelector('[data-panel="operations"]') && !document.getElementById("expense-desktop-cosmos-script")) {
      const cosmosScript = document.createElement("script");
      cosmosScript.id = "expense-desktop-cosmos-script";
      cosmosScript.src = "/static/desktop-cosmos-wallpaper.js?v=20261122-game";
      cosmosScript.defer = true;
      document.head.append(cosmosScript);
    }
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install, {once:true});
  } else {
    install();
  }
})();
