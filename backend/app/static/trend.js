/* Monthly comparisons share the dashboard month, but keep their own selections. */
const TREND_COLORS = ["var(--trend-series-1, #237458)", "var(--trend-series-2, #3265b2)", "var(--trend-series-3, #a65b16)", "var(--trend-series-4, #8952aa)", "var(--trend-series-5, #ba4761)", "var(--trend-series-6, #257a85)", "var(--trend-series-7, #69752c)", "var(--trend-series-8, #765747)"];
const TREND_DASHES = ["", "7 3", "2 3", "9 3 2 3"];

function trendSwatch(style) {
  return `<svg class="trend-swatch" width="20" height="8" aria-hidden="true"><line x1="0" x2="20" y1="4" y2="4" stroke="${style.color}" stroke-width="3" stroke-dasharray="${style.dash}"/></svg>`;
}

function trendStyle(id, catalog) {
  const index = catalog.map(item => item.id).sort().indexOf(id);
  return { color: TREND_COLORS[Math.max(index, 0) % TREND_COLORS.length], dash: TREND_DASHES[Math.floor(Math.max(index, 0) / 2) % TREND_DASHES.length] };
}

function trendSelection(catalog, previous) {
  const valid = new Set(catalog.map(item => item.id));
  return previous === null ? catalog.slice(0, 2).map(item => item.id) : previous.filter(id => valid.has(id));
}

function trendBounds(values) {
  const minimum = Math.min(0, ...values);
  const maximum = Math.max(0, ...values);
  return { min: minimum < 0 ? -niceAxisMaximum(-minimum) : 0, max: maximum > 0 ? niceAxisMaximum(maximum) : minimum < 0 ? 0 : 1 };
}

function saveTrendPreferences() {
  try {
    localStorage.setItem("expense-ledger-trend-v1", JSON.stringify({ mode: state.trendMode, selected: state.trendSelected }));
  } catch { /* Comparisons remain usable when storage is unavailable. */ }
}

function restoreTrendPreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem("expense-ledger-trend-v1"));
    if (["total", "platform", "payment"].includes(saved?.mode)) state.trendMode = saved.mode;
    for (const mode of ["platform", "payment"]) {
      if (Array.isArray(saved?.selected?.[mode])) state.trendSelected[mode] = saved.selected[mode].filter(id => typeof id === "string");
    }
  } catch { /* Ignore stale or invalid preferences. */ }
}

function renderTrendControls() {
  const mode = state.trendMode;
  const catalog = state.overview?.trend_series?.[mode] || [];
  const chooser = document.getElementById("trendChooser");
  chooser.hidden = mode === "total";
  document.getElementById("trendTitle").textContent = { total: "支出变化", platform: "平台支出变化", payment: "支付方式支出变化" }[mode];
  document.querySelectorAll("[data-trend-mode]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.trendMode === mode)));
  if (mode === "total") return;
  state.trendSelected[mode] = trendSelection(catalog, state.trendSelected[mode]);
  const selected = state.trendSelected[mode];
  const noun = mode === "platform" ? "平台" : "支付方式";
  document.getElementById("trendSelectionLabel").textContent = `选择${noun} · ${selected.length}`;
  const options = catalog.map(item => {
    const style = trendStyle(item.id, catalog);
    return `<label class="trend-choice" data-choice-label="${escapeHtml(item.label.toLowerCase())}">
      <input type="checkbox" value="${escapeHtml(item.id)}" ${selected.includes(item.id) ? "checked" : ""}>
      ${trendSwatch(style)}
      <span>${escapeHtml(item.label)}</span></label>`;
  }).join("");
  document.getElementById("trendChoices").innerHTML = options || '<p class="muted">暂无可选项目</p>';
  document.getElementById("trendChoiceSearch").value = "";
  updateTrendSelectionSummary();
}

function updateTrendSelectionSummary() {
  const mode = state.trendMode;
  const count = state.trendSelected[mode]?.length || 0;
  document.getElementById("trendSelectionLabel").textContent = `选择${mode === "platform" ? "平台" : "支付方式"} · ${count}`;
  document.getElementById("trendSelectionHint").hidden = count <= 6;
}

function selectedTrendSeries(rows) {
  if (state.trendMode === "total") return [{ id: "total", label: "总支出", values: rows.map(row => Number(row.total || 0)), color: TREND_COLORS[0], dash: "" }];
  const catalog = state.overview?.trend_series?.[state.trendMode] || [];
  return catalog.filter(item => state.trendSelected[state.trendMode]?.includes(item.id)).map(item => ({ ...item, ...trendStyle(item.id, catalog) }));
}

// Visibility changes and parent layout changes do not emit window.resize.
// Coalesce size notifications and ignore our own redraws at the same size.
function observeTrendLayout(chart, redraw) {
  let frame = null;
  let lastWidth = 0;
  let lastHeight = 0;
  const observer = new ResizeObserver(() => {
    if (frame !== null) return;
    frame = window.requestAnimationFrame(() => {
      frame = null;
      const width = chart.clientWidth;
      const height = chart.clientHeight;
      if (width === lastWidth && height === lastHeight) return;
      lastWidth = width;
      lastHeight = height;
      if (width > 0 && height > 0 && chart.isConnected) redraw();
    });
  });
  observer.observe(chart);
  return () => {
    observer.disconnect();
    if (frame !== null) window.cancelAnimationFrame(frame);
    frame = null;
  };
}

function renderTrend(rows, selectedMonth) {
  const chart = document.getElementById("trendChart");
  // Wait for a visible container instead of committing the minimum-width SVG.
  // ResizeObserver renders the latest month/series when this view becomes visible.
  if (chart.clientWidth <= 0 || chart.clientHeight <= 0) return;
  const previous = window.LedgerMotion?.captureTrend(chart);
  const scrollLeft = chart.scrollLeft;
  const focusedMonth = chart.contains(document.activeElement) ? document.activeElement.dataset?.trendMonth : null;
  const series = selectedTrendSeries(rows);
  const dataBody = document.getElementById("trendDataBody");
  document.getElementById("trendTotal").textContent = `${rows.length} 个月`;
  const tooltip = document.getElementById("trendTooltip");
  const tooltipMonth = tooltip.hidden ? null : tooltip.dataset.month;
  tooltip.hidden = true;
  document.getElementById("trendData").hidden = !rows.length || !series.length;
  const legend = document.getElementById("trendLegend");
  legend.innerHTML = state.trendMode === "total" ? "" : series.map((item, index) => `<button type="button" data-series-focus="${index}" aria-pressed="false">${trendSwatch(item)}${escapeHtml(item.label)}</button>`).join("");
  if (!rows.length || !series.length) {
    chart.innerHTML = `<div class="empty-state">${!rows.length ? "暂无月度数据" : state.trendMode === "platform" ? "请选择平台" : "请选择支付方式"}</div>`;
    dataBody.innerHTML = "";
    updateTrendZoomControls(false);
    return;
  }
  const width = Math.round(Math.max(chart.clientWidth - 8, (rows.length - 1) * 48 + 108, 280) * state.trendZoom);
  const height = Math.max(240, Math.round(chart.clientHeight || 280));
  const pad = { top: 20, right: 42, bottom: 36, left: 66 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const bounds = trendBounds(series.flatMap(item => item.values));
  const x = index => rows.length === 1 ? pad.left + plotWidth / 2 : pad.left + plotWidth * index / (rows.length - 1);
  const y = value => pad.top + plotHeight * (bounds.max - value) / (bounds.max - bounds.min);
  const grid = Array.from({ length: 5 }, (_, index) => {
    const value = bounds.min + (bounds.max - bounds.min) * index / 4;
    return `<line class="trend-grid-line" x1="${pad.left}" x2="${width - pad.right}" y1="${y(value)}" y2="${y(value)}"/><text class="trend-y-label" x="${pad.left - 8}" y="${y(value) + 4}" text-anchor="end">${escapeHtml(axisMoney(value))}</text>`;
  }).join("");
  const selectedIndex = rows.findIndex(row => row.month === selectedMonth);
  const guide = selectedIndex < 0 ? "" : `<line class="trend-selected-guide" x1="${x(selectedIndex)}" x2="${x(selectedIndex)}" y1="${pad.top}" y2="${height - pad.bottom}"/>`;
  const lines = series.map((item, seriesIndex) => {
    const points = item.values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
    const area = state.trendMode === "total" && rows.length > 1 ? `<path class="trend-area" fill="url(#trend-fill)" d="M ${x(0)} ${y(0)} L ${points.replaceAll(" ", " L ")} L ${x(rows.length - 1)} ${y(0)} Z"/>` : "";
    return `<g data-trend-series="${seriesIndex}" data-series-key="${escapeHtml(`${state.trendMode}:${item.id}`)}" data-baseline="${y(0)}" clip-path="url(#trend-reveal)">${area}<polyline fill="none" stroke="${item.color}" stroke-width="2.5" stroke-dasharray="${item.dash}" points="${points}"/>${item.values.map((value, index) => `<circle class="${index === selectedIndex ? 'trend-selected-point' : ''}" cx="${x(index)}" cy="${y(value)}" r="${index === selectedIndex ? 5.5 : 4}" fill="${index === selectedIndex ? item.color : 'var(--surface)'}" stroke="${index === selectedIndex ? 'var(--surface)' : item.color}" stroke-width="2"/>`).join("")}</g>`;
  }).join("");
  const hitWidth = rows.length > 1 ? plotWidth / (rows.length - 1) : plotWidth;
  const months = rows.map((row, index) => {
    const label = `${monthLabel(row.month)}，${series.map(item => `${item.label} ${money(item.values[index])}`).join("，")}，点击查看该月`;
    const left = Math.max(pad.left - 28, x(index) - hitWidth / 2);
    const right = Math.min(width - 8, x(index) + hitWidth / 2);
    return `<g class="trend-month ${row.month === selectedMonth ? "selected" : ""}" data-trend-month="${row.month}" data-month-index="${index}" tabindex="0" role="button" aria-pressed="${row.month === selectedMonth}" aria-label="${escapeHtml(label)}"><rect class="trend-hit" x="${left}" y="${pad.top - 8}" width="${right - left}" height="${height - pad.top}" rx="5"/><text class="trend-x-label" x="${x(index)}" y="${height - 14}" text-anchor="middle">${escapeHtml(row.month.replace("-", "/"))}</text></g>`;
  }).join("");
  const defs = `<defs><clipPath id="trend-reveal"><rect class="trend-reveal-rect" x="${pad.left - 6}" y="0" width="${plotWidth + 12}" height="${height}"/></clipPath><linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="${series[0].color}" stop-opacity=".2"/><stop offset="1" stop-color="${series[0].color}" stop-opacity=".015"/></linearGradient></defs>`;
  chart.innerHTML = `<svg class="trend-svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="group" aria-label="月度支出图，选择月份查看账目">${defs}${grid}${guide}${lines}<line class="trend-hover-guide" opacity="0" y1="${pad.top}" y2="${height - pad.bottom}"/>${months}</svg>`;
  if (previous) window.LedgerMotion.trend(chart, previous, rows.map(row => row.month).join(','));
  chart.scrollLeft = scrollLeft;
  chart.querySelectorAll("[data-trend-month]").forEach(point => {
    const show = event => showTrendTooltip(rows, series, Number(point.dataset.monthIndex), event);
    point.addEventListener("pointerenter", show);
    point.addEventListener("pointermove", show);
    point.addEventListener("focus", show);
    point.addEventListener("click", () => { show(); selectDashboardMonth(point.dataset.trendMonth); });
    point.addEventListener("keydown", event => {
      if (["Enter", " "].includes(event.key)) { event.preventDefault(); show(); selectDashboardMonth(point.dataset.trendMonth); }
      if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
        event.preventDefault();
        const next = Number(point.dataset.monthIndex) + (event.key === "ArrowLeft" ? -1 : 1);
        chart.querySelector(`[data-month-index="${next}"]`)?.focus();
      }
      if (event.key === "Escape") hideTrendTooltip();
    });
  });
  if (focusedMonth) chart.querySelector(`[data-trend-month="${focusedMonth}"]`)?.focus({ preventScroll: true });
  else if (tooltipMonth && rows.some(row => row.month === tooltipMonth)) showTrendTooltip(rows, series, rows.findIndex(row => row.month === tooltipMonth));
  legend.querySelectorAll("[data-series-focus]").forEach(button => button.addEventListener("click", () => {
    const active = button.getAttribute("aria-pressed") !== "true";
    legend.querySelectorAll("button").forEach(item => item.setAttribute("aria-pressed", String(active && item === button)));
    chart.querySelectorAll("[data-trend-series]").forEach(line => line.classList.toggle("trend-series-muted", active && line.dataset.trendSeries !== button.dataset.seriesFocus));
  }));
  document.getElementById("trendDataHead").innerHTML = `<tr><th scope="col">月份</th>${series.map(item => `<th scope="col" class="numeric">${escapeHtml(item.label)}</th>`).join("")}</tr>`;
  dataBody.innerHTML = rows.map((row, index) => `<tr><th scope="row"><button type="button" class="trend-month-link" data-table-month="${row.month}">${escapeHtml(monthLabel(row.month))}</button></th>${series.map(item => `<td class="numeric">${escapeHtml(money(item.values[index]))}</td>`).join("")}</tr>`).join("");
  dataBody.querySelectorAll("[data-table-month]").forEach(button => button.addEventListener("click", () => selectDashboardMonth(button.dataset.tableMonth)));
  updateTrendZoomControls(true);
}

function hideTrendTooltip() {
  document.getElementById('trendTooltip').hidden = true;
  document.querySelector('.trend-hover-guide')?.setAttribute('opacity', '0');
}

function showTrendTooltip(rows, series, index, event) {
  const tooltip = document.getElementById("trendTooltip");
  const refresh = tooltip.hidden || tooltip.dataset.month !== rows[index].month || !event;
  tooltip.hidden = false;
  tooltip.dataset.month = rows[index].month;
  if (refresh) tooltip.innerHTML = `<strong>${escapeHtml(monthLabel(rows[index].month))}</strong>${series.map(item => `<span>${trendSwatch(item)}${escapeHtml(item.label)} <b>${escapeHtml(money(item.values[index]))}</b></span>`).join("")}`;
  const chart = document.getElementById('trendChart');
  const label = chart.querySelector(`[data-month-index="${index}"] .trend-x-label`);
  const guide = chart.querySelector('.trend-hover-guide');
  const x = label.getAttribute('x');
  guide.setAttribute('x1', x); guide.setAttribute('x2', x); guide.setAttribute('opacity', '1');
  const wrap = tooltip.parentElement.getBoundingClientRect();
  const anchor = label.getBoundingClientRect();
  const left = (event?.clientX ?? (anchor.left + anchor.width / 2)) - wrap.left + 16;
  const top = event?.clientY != null ? event.clientY - wrap.top - tooltip.offsetHeight - 14 : 8;
  tooltip.style.left = `${Math.max(8, Math.min(left, wrap.width - tooltip.offsetWidth - 8))}px`;
  tooltip.style.top = `${Math.max(8, Math.min(top, wrap.height - tooltip.offsetHeight - 8))}px`;
  tooltip.style.right = 'auto';
}

function bindTrendEvents() {
  restoreTrendPreferences();
  const chart = document.getElementById("trendChart");
  observeTrendLayout(chart, () => {
    if (state.overview) renderTrend(state.overview.trend, state.overview.selected_month);
  });
  chart.addEventListener("pointerleave", () => {
    if (!chart.contains(document.activeElement)) hideTrendTooltip();
  });
  chart.addEventListener("focusout", event => {
    if (!chart.contains(event.relatedTarget)) hideTrendTooltip();
  });
  const redraw = () => { saveTrendPreferences(); renderTrend(state.overview.trend, state.overview.selected_month); };
  document.querySelectorAll("[data-trend-mode]").forEach(button => button.addEventListener("click", () => {
    if (!state.overview) return;
    state.trendMode = button.dataset.trendMode;
    renderTrendControls();
    redraw();
  }));
  document.getElementById("trendChoices").addEventListener("change", event => {
    if (!event.target.matches('input[type="checkbox"]')) return;
    state.trendSelected[state.trendMode] = Array.from(document.querySelectorAll("#trendChoices input:checked"), input => input.value);
    updateTrendSelectionSummary();
    redraw();
  });
  document.querySelectorAll("[data-trend-select]").forEach(button => button.addEventListener("click", () => {
    const all = button.dataset.trendSelect === "all";
    state.trendSelected[state.trendMode] = all ? (state.overview.trend_series[state.trendMode] || []).map(item => item.id) : [];
    renderTrendControls();
    redraw();
  }));
  document.getElementById("trendChoiceSearch").addEventListener("input", event => {
    const query = event.target.value.trim().toLowerCase();
    document.querySelectorAll("[data-choice-label]").forEach(label => { label.hidden = !label.dataset.choiceLabel.includes(query); });
  });
}
