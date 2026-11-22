const state = {
  view: "overview",
  month: "latest",
  overview: null,
  filters: null,
  jobs: [],
  activeJobId: null,
  pollTimer: null,
  orderOffset: 0,
  orderLimit: 50,
  orderTotal: 0,
  cash: null,
  collectorBackends: null,
  collectorRecovery: null,
  drawerReturnFocus: null,
  orderDetailRequest: 0,
  trendZoom: 1,
  trendMode: "total",
  trendSelected: { platform: null, payment: null },
  overviewRequest: 0,
  ordersRequest: 0,
  cashRequest: 0,
};

const TREND_ZOOM_LEVELS = [1, 1.25, 1.5, 2];

const platformLabels = {
  walmart_ca: "Walmart",
  amazon_ca: "Amazon",
  the_ordinary_ca: "The Ordinary",
  tnt_ca: "大统华",
  uber_eats: "Uber Eats",
  fantuan: "饭团",
  scotia: "Scotia",
  scotiabank: "Scotiabank",
  bank: "其他银行",
};

const categoryLabels = {
  kitchenware: "餐厨用品", household: "家居日用", unallocated: "未分配差额",
  personal_care: "个护美容",
  food_delivery: "外卖",
  mixed: "综合购物",
  dining: "餐饮",
  telecom: "通讯",
  groceries: "食品杂货",
  coffee_supplies: "咖啡",
  general_retail: "日常零售",
  uncategorized: "未分类",
  software_subscription: "软件订阅",
  bank_fees: "银行费用",
  grocery_delivery: "杂货配送",
  digital_entertainment: "数字娱乐",
  delivery_membership: "外卖会员",
  retail_membership: "购物会员",
  digital_services: "数字服务",
  transportation: "出行",
};

const paymentChannelLabels = {
  online: "线上",
  in_store: "线下",
  account_debit: "账户扣款",
  unknown: "未识别",
};

const viewTitles = {
  overview: ["个人支出", "总览"],
  orders: ["消费记录", "订单"],
  cash: ["现金备忘", "现金"],
  sources: ["资金来源", "付款"],
  operations: ["账单同步", "更新"],
  maintenance: ["后台工具", "维护"],
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function money(value, currency = "CAD") {
  return new Intl.NumberFormat("en-CA", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
  }).format(Number(value || 0));
}

function shortDate(value) {
  if (!value) return "—";
  const parts = String(value).split("-");
  return parts.length === 3 ? `${parts[1]}-${parts[2]}` : value;
}

function dateTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function monthLabel(value) {
  if (value === "all") return "全部时间";
  const [year, month] = String(value).split("-");
  return `${year}年${Number(month)}月`;
}

function labelPlatform(value) {
  return platformLabels[value] || value;
}

function paymentStatusLabel(value) {
  if (value === "confirmed") return "已确认";
  if (value === "inferred") return "推断";
  return "待确认";
}

function paymentChannelLabel(value) {
  return paymentChannelLabels[value] || paymentChannelLabels.unknown;
}

function labelCategory(value) {
  return categoryLabels[value] || String(value || "未分类").replaceAll("_", " ");
}

function reconciliationReason(row) {
  if (row.match_method === "ambiguous_platform_order_candidates") return "同一候选有多种匹配方式，需核实订单号";
  if (row.match_method === "net_bank_purchase_unmatched") return "已按银行流水入账，发现可能对应的平台订单";
  if (row.match_method === "authoritative_platform_order_required") {
    return `未找到金额完全一致的${labelPlatform(row.detected_platform)}订单，可能是拆分扣款或金额调整`;
  }
  if (row.match_method === "platform_no_candidate") {
    return "没有金额和日期都可靠的订单候选";
  }
  return row.notes || row.match_method || "未找到可靠匹配";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `请求失败 (${response.status})`);
  }
  return payload;
}

function showToast(message, tone = false) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  const normalizedTone = tone === true ? "error" : tone;
  toast.classList.toggle("error", normalizedTone === "error");
  toast.classList.toggle("warning", normalizedTone === "warning");
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 3600);
}

function renderScotiaAction() {
  const title = document.getElementById("scotiaActionTitle");
  const hint = document.getElementById("scotiaActionHint");
  if (!title || !hint) return;
  title.textContent = "登录并对账 Scotia";
  hint.textContent = "完成登录后自动采集并对账，无需再次点击";
}

function emptyRow(columns, message) {
  return `<tr><td colspan="${columns}"><div class="empty-state">${escapeHtml(message)}</div></td></tr>`;
}

function fulfillmentLabel(value) {
  const labels = {
    delivered: "已完成",
    pending: "未送达",
    pending_acceptance: "待商家接单",
    preparing: "备餐中",
    in_transit: "配送中",
    scheduled: "预约订单",
    processing: "处理中",
    refunded: "已退款",
    shipped: "已发货",
    cancelled: "已取消",
    bank_reconciled: "银行记录",
    subscription: "订阅",
    delivery: "配送订单",
  };
  return labels[value] || "—";
}

function fulfillmentTone(value) {
  if (value === "delivered") return "passed";
  if (["pending", "shipped", "pending_acceptance", "preparing", "in_transit", "scheduled"].includes(value)) return "warning";
  if (value === "cancelled") return "failed";
  return "";
}

function orderRow(order, includeCategory = false) {
  const categoryCell = includeCategory
    ? `<td><span class="category-pill">${escapeHtml(labelCategory(order.category))}</span></td>`
    : "";
  const paymentLabel = order.payment_status === "inferred"
    ? `${order.card_label}（推断）`
    : order.card_label;
  return `
    <tr data-order-id="${order.order_pk}" tabindex="0">
      <td>${escapeHtml(shortDate(order.date))}</td>
      <td title="${escapeHtml(order.merchant)}">${escapeHtml(order.merchant || "未命名商户")}</td>
      ${categoryCell}
      <td><span class="source-pill">${escapeHtml(labelPlatform(order.platform))}</span></td>
      <td><span class="fulfillment-pill ${fulfillmentTone(order.fulfillment)}">${escapeHtml(fulfillmentLabel(order.fulfillment))}</span></td>
      <td title="${escapeHtml(paymentLabel)}">${escapeHtml(paymentLabel)}</td>
      <td class="numeric">${escapeHtml(money(order.total, order.currency))}</td>
      <td class="row-action"><img src="/static/icons/chevron-right.svg" alt=""></td>
    </tr>`;
}

function bindOrderRows(container) {
  container.querySelectorAll("tr[data-order-id]").forEach((row) => {
    const open = () => openOrder(Number(row.dataset.orderId));
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
  });
}

function renderMetrics(summary) {
  const container = document.getElementById("metricGrid");
  const change = summary.change_percent;
  const changeClass = change > 0 ? "positive" : change < 0 ? "negative" : "";
  const changeIcon = change > 0 ? "arrow-up-right" : "arrow-down-right";
  const changeNote = change == null
    ? "暂无上月比较"
    : `<img src="/static/icons/${changeIcon}.svg" alt="">${Math.abs(change).toFixed(1)}% 较上月`;
  const cards = [
    ["本月支出", money(summary.total), changeNote, changeClass],
    ["订单", `${summary.order_count} 笔`, `${summary.item_count} 条明细`, ""],
    ["平均每笔", money(summary.average_order), "本期订单平均", ""],
    ["待处理", `${summary.review_count} 笔`, summary.review_count ? "需要确认付款来源" : "付款来源完整", ""],
  ];
  const initial = !container.querySelector('.metric-card');
  if (initial) container.innerHTML = cards.map(([label]) => `
    <article class="metric-card">
      <span class="metric-label">${label}</span>
      <strong class="metric-value"></strong>
      <span class="metric-note"></span>
    </article>
  `).join("");
  container.querySelectorAll('.metric-card').forEach((card, index) => {
    const note = card.querySelector('.metric-note');
    if (note.innerHTML !== cards[index][2]) note.innerHTML = cards[index][2];
    note.className = `metric-note ${cards[index][3]}`;
    if (!window.LedgerMotion) card.querySelector('.metric-value').textContent = cards[index][1];
  });
  window.LedgerMotion?.metrics(container, cards.map(card => card[1]), initial);
}

function compactMoney(value) {
  const amount = Number(value || 0);
  return new Intl.NumberFormat("en-CA", {
    style: "currency",
    currency: "CAD",
    notation: Math.abs(amount) >= 1000 ? "compact" : "standard",
    maximumFractionDigits: Math.abs(amount) >= 1000 ? 1 : Math.abs(amount) < 100 ? 2 : 0,
  }).format(amount);
}

function axisMoney(value) {
  return new Intl.NumberFormat("en-CA", {
    style: "currency",
    currency: "CAD",
    maximumFractionDigits: 0,
  }).format(Number(value || 0));
}

function niceAxisMaximum(maxValue) {
  const roughStep = Math.max(Number(maxValue || 0), 1) / 4;
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const normalizedStep = roughStep / magnitude;
  const niceFactor = normalizedStep <= 1
    ? 1
    : normalizedStep <= 2
      ? 2
      : normalizedStep <= 2.5
        ? 2.5
        : normalizedStep <= 5
          ? 5
          : 10;
  const step = niceFactor * magnitude;
  return Math.ceil(maxValue / step) * step;
}

function updateTrendZoomControls(hasData = true) {
  const index = TREND_ZOOM_LEVELS.indexOf(state.trendZoom);
  document.getElementById("trendZoomLabel").textContent = `${Math.round(state.trendZoom * 100)}%`;
  document.getElementById("trendZoomOut").disabled = !hasData || index <= 0;
  document.getElementById("trendZoomIn").disabled = !hasData || index >= TREND_ZOOM_LEVELS.length - 1;
  document.getElementById("trendZoomReset").disabled = !hasData || state.trendZoom === 1;
}

function setTrendZoom(nextZoom) {
  if (!state.overview?.trend?.length || nextZoom === state.trendZoom) return;
  const chart = document.getElementById("trendChart");
  const centerRatio = chart.scrollWidth
    ? (chart.scrollLeft + (chart.clientWidth / 2)) / chart.scrollWidth
    : 0.5;
  state.trendZoom = nextZoom;
  renderTrend(state.overview.trend, state.overview.selected_month);
  chart.scrollLeft = Math.max(0, (centerRatio * chart.scrollWidth) - (chart.clientWidth / 2));
}

function moveTrendZoom(direction) {
  const index = TREND_ZOOM_LEVELS.indexOf(state.trendZoom);
  const nextIndex = Math.min(TREND_ZOOM_LEVELS.length - 1, Math.max(0, index + direction));
  setTrendZoom(TREND_ZOOM_LEVELS[nextIndex]);
}

async function toggleTrendFullscreen() {
  const surface = document.querySelector(".trend-surface");
  try {
    if (document.fullscreenElement === surface) {
      await document.exitFullscreen();
    } else {
      await surface.requestFullscreen();
    }
  } catch (_error) {
    showToast("无法切换折线图全屏显示", true);
  }
}

function renderTrendFullscreenState() {
  const surface = document.querySelector(".trend-surface");
  const button = document.getElementById("trendFullscreenButton");
  const fullscreen = document.fullscreenElement === surface;
  const label = fullscreen ? "退出支出趋势图全屏" : "全屏显示支出趋势图";
  button.setAttribute("aria-label", label);
  button.setAttribute("aria-pressed", String(fullscreen));
  button.title = fullscreen ? "退出全屏" : "全屏显示";
  button.querySelector("img").src = fullscreen
    ? "/static/icons/minimize-2.svg"
    : "/static/icons/maximize-2.svg";
  window.requestAnimationFrame(() => {
    if (state.overview?.trend?.length) {
      renderTrend(state.overview.trend, state.overview.selected_month);
    }
  });
}

function renderBars(containerId, rows) {
  const container = document.getElementById(containerId);
  const previous = new Map([...container.querySelectorAll('progress')].map(bar => [bar.dataset.barKey, bar.value]));
  window.LedgerMotion?.stop(container);
  if (!rows.length) {
    container.innerHTML = '<div class="empty-state">暂无数据</div>';
    return;
  }
  const max = Math.max(...rows.map((row) => Number(row.total || 0)), 1);
  const total = rows.reduce((sum, row) => sum + Number(row.total || 0), 0);
  container.innerHTML = rows.map((row) => `
    <div class="bar-row">
      <div class="bar-meta">
        <span>${escapeHtml(row.label)}</span>
        <span class="bar-values">
          <span class="bar-amount">${escapeHtml(money(row.total))}</span>
          <small>${total ? ((Number(row.total || 0) / total) * 100).toFixed(1) : "0.0"}%</small>
        </span>
      </div>
      <progress
        class="bar-progress"
        data-bar-key="${escapeHtml(row.label)}"
        max="${max}"
        value="${Math.max(0, Number(row.total || 0))}"
        aria-label="${escapeHtml(`${row.label}支出占当前最高值的比例`)}"
      ></progress>
    </div>
  `).join("");
  window.LedgerMotion?.bars(container, previous);
}

function renderCards(rows) {
  const container = document.getElementById("cardBreakdown");
  if (!rows.length) {
    container.innerHTML = '<div class="empty-state">暂无付款记录</div>';
    return;
  }
  container.innerHTML = rows.map((row) => `
    <div class="source-row">
      <span class="source-icon"><img src="/static/icons/credit-card.svg" alt=""></span>
      <span>
        <strong class="source-name">${escapeHtml(row.label)}</strong>
        <small class="source-orders">${row.orders} 笔订单</small>
      </span>
      <strong class="source-total">${escapeHtml(money(row.total))}</strong>
    </div>
  `).join("");
}

function statusClass(status) {
  if (["passed", "confirmed", "ready"].includes(status)) return "passed";
  if (["failed", "needs_review"].includes(status)) return "failed";
  if (["running", "queued", "inferred"].includes(status)) return "running";
  if (["partial", "setup_required", "login_required", "blocked", "unavailable"].includes(status)) return "warning";
  return "";
}

function collectorProgressText() {
  const collection = state.collectorBackends?.backends?.vps?.collection;
  if (collection?.status !== "running") return "";
  const labels = {
    walmart_ca: "Walmart",
    amazon_ca: "Amazon",
    uber_eats: "Uber Eats",
    the_ordinary_ca: "The Ordinary",
    tnt_ca: "大统华",
    fantuan: "Fantuan",
  };
  const stage = collection.stage === "collecting"
    ? (labels[collection.platform] || "Collecting")
    : collection.stage === "stopping_browser"
      ? "Stopping browser"
      : collection.stage === "building_bundle"
        ? "Building bundle"
        : "Preparing";
  return `VPS · ${stage} · ${Number(collection.components_completed || 0)}/${Number(collection.components_total || 8)}`;
}

function renderCollectorBackends(payload) {
  state.collectorBackends = payload;
  const resolved = payload?.resolved_backend;
  const button = document.getElementById("backendToggleButton");
  const label = document.getElementById("backendToggleLabel");
  if (!button || !label) return;
  const currentLabel = resolved === "vps" ? "VPS" : resolved === "local" ? "本地" : "未就绪";
  const target = resolved === "vps"
    ? "local"
    : resolved === "local"
      ? "vps"
      : payload?.backends?.local?.selectable
        ? "local"
        : payload?.backends?.vps?.selectable
          ? "vps"
          : "";
  const targetLabel = target === "vps" ? "VPS" : "本地";
  const targetBackend = payload?.backends?.[target];
  const progressText = collectorProgressText();
  const storage = payload?.backends?.vps?.storage;
  const storageWarning = ["warning", "critical"].includes(storage?.status);
  const storageText = storageWarning
    ? `VPS 磁盘已使用 ${Number(storage.used_percent || 0).toFixed(1)}%（${storage.status === "critical" ? "严重" : "预警"}）`
    : "";
  label.textContent = currentLabel;
  button.dataset.targetBackend = target;
  button.classList.toggle("unavailable", !resolved);
  button.classList.toggle("storage-warning", storageWarning);
  button.disabled = Boolean(state.activeJobId) || !target || !targetBackend?.selectable;
  button.setAttribute(
    "aria-label",
    resolved
      ? `当前${currentLabel}环境，点击切换到${targetLabel}环境`
      : target
        ? `当前运行环境未就绪，点击切换到${targetLabel}环境`
        : "订单采集运行环境未就绪"
  );
  if (resolved === "local" && !targetBackend?.selectable) {
    button.setAttribute("aria-label", "订单采集当前使用本地运行环境");
    button.title = targetBackend?.message?.includes("已停用")
      ? "当前使用本地运行；VPS 暂停维护"
      : `当前使用本地运行；${targetBackend?.message || "VPS 暂时不可用"}`;
    return;
  }
  button.title = progressText || storageText || (resolved
    ? `订单采集当前使用${currentLabel}环境；点击切换到${targetLabel}`
    : target
      ? `当前选择不可用；点击切换到${targetLabel}环境`
      : (payload?.message || "本地与 VPS 运行环境均未就绪"));
}

async function loadCollectorBackends(refresh = false) {
  const suffix = refresh ? "?refresh=true" : "";
  const payload = await api(`/api/collector-backends${suffix}`);
  renderCollectorBackends(payload);
  return payload;
}

function renderCollectorRecovery(payload) {
  state.collectorRecovery = payload;
  const container = document.getElementById("collectorRecovery");
  if (!container) return;
  const rows = (payload?.platforms || []).filter((row) => row.status !== "passed");
  container.hidden = rows.length === 0;
  if (!rows.length) {
    container.innerHTML = "";
    return;
  }
  const retryable = rows.filter((row) => row.retryable);
  const controls = retryable.length > 1
    ? '<button class="button secondary compact" type="button" data-recovery-retry="">只重试全部失败平台</button>'
    : "";
  container.innerHTML = `
    <div class="collector-recovery-heading">
      <strong>VPS 采集恢复</strong>
      ${controls}
    </div>
    ${rows.map((row) => {
      const cache = row.using_cached_data ? " · 正在使用上次有效缓存" : "";
      const action = row.login_required
        ? `<span class="collector-recovery-actions">
            <button class="button secondary compact" type="button" data-recovery-login="${escapeHtml(row.id)}">打开 VPS 登录</button>
            <button class="button secondary compact" type="button" data-recovery-verify="${escapeHtml(row.id)}">验证并重试</button>
          </span>`
        : row.retryable
          ? `<button class="button secondary compact" type="button" data-recovery-retry="${escapeHtml(row.id)}">只重试此平台</button>`
          : "";
      return `
        <div class="collector-recovery-row">
          <span>
            <strong>${escapeHtml(row.label)}</strong>
            <small>${escapeHtml(row.message || row.status)}${escapeHtml(cache)}</small>
          </span>
          ${action}
        </div>`;
    }).join("")}`;
  container.querySelectorAll("[data-recovery-retry]").forEach((button) => {
    button.addEventListener("click", () => retryFailedCollectors(button.dataset.recoveryRetry || null));
  });
  container.querySelectorAll("[data-recovery-login]").forEach((button) => {
    button.addEventListener("click", () => openVpsLogin(button.dataset.recoveryLogin));
  });
  container.querySelectorAll("[data-recovery-verify]").forEach((button) => {
    button.addEventListener("click", () => verifyVpsLogin(button.dataset.recoveryVerify));
  });
  if (state.activeJobId) {
    container.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  }
}

async function loadCollectorRecovery() {
  const payload = await api("/api/collector-recovery");
  renderCollectorRecovery(payload);
  return payload;
}

async function retryFailedCollectors(platform = null) {
  try {
    const job = await api("/api/jobs/retry-collectors", {
      method: "POST",
      body: JSON.stringify({ platform }),
    });
    showToast(job.label);
    watchJob(job.id);
    switchView("operations");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function openVpsLogin(platform) {
  const popup = window.open("", "_blank");
  try {
    const payload = await api("/api/vps-login-session", {
      method: "POST",
      body: JSON.stringify({ platform }),
    });
    if (!popup) throw new Error("浏览器阻止了登录窗口，请允许本站打开窗口。");
    popup.location.href = payload.url;
    showToast(`${labelPlatform(platform)} VPS 登录会话已打开`);
  } catch (error) {
    if (popup) popup.close();
    showToast(error.message, true);
  }
}

async function verifyVpsLogin(platform) {
  try {
    const job = await api("/api/vps-login-session/verify", {
      method: "POST",
      body: JSON.stringify({ platform }),
    });
    showToast(`${labelPlatform(platform)} 登录已验证，正在只重试此平台`);
    watchJob(job.id);
    switchView("operations");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function selectCollectorBackend(mode) {
  try {
    const payload = await api("/api/collector-backends", {
      method: "PATCH",
      body: JSON.stringify({ mode }),
    });
    renderCollectorBackends(payload);
    showToast(payload.message);
  } catch (error) {
    showToast(error.message, true);
    await loadCollectorBackends(true);
  }
}

function renderValidation(payload) {
  const status = payload?.status || "unknown";
  const metrics = payload?.metrics || {};
  const checks = [
    ["订单去重", ["transaction_duplicates", "sqlite_order_duplicates"], "重复订单", 0],
    ["明细去重", ["item_file_duplicates", "sqlite_item_duplicates"], "重复明细", 0],
    ["金额对账", ["transaction_reconciliation_failures"], "金额异常", 0],
    ["关联完整", ["sqlite_orphan_items", "sqlite_orphan_payments"], "孤儿记录", 0],
    ["付款一致", ["payment_assignment_missing", "sqlite_payment_mismatches", "chequing_payment_rows", "missing_payment_channels", "invalid_inferred_payments"], "付款异常", 0],
    ["银行关联", ["bank_orphan_reconciliations", "bank_imports_without_orders", "collector_bank_only_orders"], "关联异常", 0],
    ["恢复快照", ["safety_backup_valid"], "备份有效", 1],
  ];
  const resultComplete = checks
    .flatMap(([, keys]) => keys)
    .every((key) => metrics[key] != null);
  const effectiveStatus = status === "passed" && !resultComplete ? "incomplete" : status;
  const badge = document.getElementById("validationBadge");
  badge.textContent = effectiveStatus === "passed"
    ? `通过 · ${dateTime(payload.completed_at)}`
    : effectiveStatus === "failed"
    ? "失败"
    : effectiveStatus === "incomplete"
    ? "结果不完整"
    : "未运行";
  badge.className = `state-badge ${statusClass(effectiveStatus)}`;

  document.getElementById("validationChecks").innerHTML = checks.map(([label, keys, unit, expected]) => {
    const hasMetrics = keys.every((key) => metrics[key] != null);
    const value = keys.reduce((total, key) => total + Number(metrics[key] || 0), 0);
    const passed = effectiveStatus === "passed" && hasMetrics && value === expected;
    const icon = passed ? "check-circle-2" : effectiveStatus === "failed" ? "circle-alert" : "database";
    const text = !hasMetrics ? "等待校验" : expected === 1 && value === 1 ? unit : `${value} ${unit}`;
    return `
      <div class="check-item">
        <img src="/static/icons/${icon}.svg" alt="">
        <span><strong>${label}</strong><span>${escapeHtml(text)}</span></span>
      </div>`;
  }).join("");
}

function renderRunStatus(runState, weeklyStatus, stabilityStatus) {
  const badge = document.getElementById("runBadge");
  const status = runState?.status || "unknown";
  const warnings = weeklyStatus?.warnings || [];
  // collector_health is a cross-run snapshot and can contain a stale Scotia
  // login state even when the latest task intentionally ran only store
  // collectors. Use the current run records for the "recent run" card.
  const collectorRuns = (weeklyStatus?.collector_runs || [])
    .filter((row) => row.phase === "collect");
  const collectorIssues = collectorRuns
    .filter((row) => row.status && row.status !== "passed");
  const collectorWarnings = collectorIssues.map((row) => {
    const reason = row.message ? `：${row.message}` : "";
    return `${labelPlatform(row.collector_id)} 本次采集${row.status === "failed" ? "失败" : "异常"}${reason}`;
  });
  const stabilityWarning = stabilityStatus?.status === "failed"
    ? ["稳定性测试失败，请查看最新报告"]
    : [];
  const allWarnings = [...new Set([...warnings, ...collectorWarnings, ...stabilityWarning])];
  const completedWithWarnings = status === "partial" || (status === "passed" && allWarnings.length > 0);
  badge.textContent = completedWithWarnings
    ? "部分完成"
    : status === "passed" ? "已更新" : status === "failed" ? "更新失败" : status === "running" ? "更新中" : "尚未更新";
  badge.className = `state-badge ${completedWithWarnings ? "warning" : statusClass(status)}`;
  const stabilityLabel = stabilityStatus?.status === "passed"
    ? `通过 · ${dateTime(stabilityStatus.completed_at)}`
    : stabilityStatus?.status === "failed"
    ? "失败"
    : "未运行";
  const facts = [
    ["最近完成", dateTime(runState?.completed_at || weeklyStatus?.generated_at)],
    ["账本校验", weeklyStatus?.validation === "passed" ? "通过" : weeklyStatus?.validation || "—"],
    ["新增订单", `${weeklyStatus?.new_order_count ?? 0} 笔`],
    ["推断付款", `${weeklyStatus?.inferred_payment_count ?? 0} 笔`],
    ["待确认付款", `${weeklyStatus?.review_payment_count ?? 0} 笔`],
    ["采集器", collectorRuns.length ? (collectorIssues.length ? `${collectorIssues.length} 项需处理` : "正常") : "未运行"],
    ["稳定性", stabilityLabel],
    ["警告", allWarnings.length ? `${allWarnings.length} 项` : "无"],
  ];
  document.getElementById("runFacts").innerHTML = facts.map(([key, value]) => `
    <dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>
  `).join("");
  const warningList = document.getElementById("runWarnings");
  warningList.hidden = allWarnings.length === 0;
  warningList.innerHTML = allWarnings.map((warning) => `
    <div class="warning-row">
      <img src="/static/icons/circle-alert.svg" alt="">
      <span>${escapeHtml(warning)}</span>
    </div>
  `).join("");
  const completedAt = runState?.completed_at || weeklyStatus?.generated_at;
  document.getElementById("updateSummary").textContent = status === "running"
    ? "正在更新账单，请稍候"
    : completedAt ? `上次更新 ${dateTime(completedAt)}` : "选择要更新的账单";
  document.getElementById("updateAttention").hidden = !completedWithWarnings && status !== "failed";
}

function renderOverview(payload) {
  state.overview = payload;
  state.month = payload.selected_month;
  const periodStatus = payload.selected_month === "all" ? "全部已记账月份"
    : `${payload.selected_month} · ${payload.calendar_month_ended ? "日历月已结束" : "日历月尚未结束"}`;
  const currencyTotals = (payload.currency_totals || []).map(row => `${row.currency} ${Number(row.total).toFixed(2)}`);
  document.getElementById("coveragePeriod").textContent = `${periodStatus} · 采集完整性未确认。已记账金额 ${currencyTotals.join(" / ") || "暂无记录"}。`;
  document.getElementById("dataCoverage").innerHTML = (payload.data_status || []).map((row) => `
    <div class="coverage-card"><strong>${escapeHtml(row.label)}${row.on_demand ? " · 按需" : ""}</strong>
      <small>账本记录范围：${escapeHtml(row.ledger_from || "未知")} 至 ${escapeHtml(row.ledger_to || "未知")}</small>
      <small>本次实际见到的日期：${row.observed_from ? `${escapeHtml(row.observed_from)} 至 ${escapeHtml(row.observed_to)}` : "未记录"}</small>
      <small>最近成功检查：${escapeHtml(row.last_success_at ? dateTime(row.last_success_at) : "尚无记录")}</small>
      <small>最近尝试：${escapeHtml(({not_checked:"尚无记录",passed:"通过",failed:"失败",partial:"部分完成",import_failed:"导入失败",import_unknown:"导入未确认",skipped:"已跳过",login_required:"需要登录",blocked:"访问受阻"})[row.status] || row.status)} · ${escapeHtml(({local:"本地",vps:"VPS"})[row.backend] || "—")}</small>
      ${row.requested_from ? `<small>请求检查范围：${escapeHtml(row.requested_from)} 起</small>` : ""}
      <small>最近尝试时间：${escapeHtml(row.last_attempt_at ? dateTime(row.last_attempt_at) : "尚无记录")}</small>
      <small>${row.coverage === "bounded" ? "有限范围检查" : "覆盖范围未知"} · 完整截止日：${escapeHtml(row.complete_through || "未确认")}</small>
      <small>缺少明细 ${Number(row.missing_fields?.no_items || 0)} 单 · 缺价 ${Number(row.missing_fields?.no_price || 0)} 行 · 缺数量 ${Number(row.missing_fields?.no_quantity || 0)} 行 · 待分类 ${Number(row.missing_fields?.no_category || 0)} 行</small>
      ${row.missing_fields?.placeholder_orders ? `<small>有银行占位明细的订单 ${Number(row.missing_fields.placeholder_orders)} 单</small>` : ""}
    </div>`).join("");
  renderMetrics(payload.summary);
  renderTrendControls();
  renderTrend(payload.trend, payload.selected_month);
  renderBars("platformBreakdown", payload.platforms);
  renderBars("paymentBreakdown", payload.cards);
  renderBars("categoryBreakdown", payload.categories);
  window.expenseDesktopLayout?.updateOverview(payload);
  renderCards(payload.cards);

  const recent = document.getElementById("recentOrders");
  recent.innerHTML = payload.recent_orders.length
    ? (window.__expenseDesktopWallpaperLayout === true ? payload.recent_orders.slice(0, 5) : payload.recent_orders).map((order) => orderRow(order)).join("")
    : emptyRow(7, "本期暂无订单");
  bindOrderRows(recent);
  renderValidation(payload.validation_status);
  renderRunStatus(
    payload.run_state,
    payload.weekly_status,
    payload.stability_status,
  );
  renderReconciliationReview(payload.reconciliation_review || []);
  populateMonthSelect(payload.months, payload.selected_month);
}

function renderReconciliationReview(rows) {
  document.getElementById("reconciliationReviewSection").hidden = rows.length === 0;
  const badge = document.getElementById("reconciliationReviewBadge");
  const body = document.getElementById("reconciliationReviewRows");
  badge.textContent = `${rows.length} 笔`;
  badge.classList.toggle("warning", rows.length > 0);
  body.innerHTML = rows.length
    ? rows.map((row, index) => `
        <tr>
          <td>${escapeHtml(row.date || "—")}</td>
          <td><span class="source-pill">${escapeHtml(labelPlatform(row.detected_platform || "未识别"))}</span></td>
          <td title="${escapeHtml(row.description)}">${escapeHtml(row.description || row.merchant || "—")}</td>
          <td title="${escapeHtml(row.notes)}">${escapeHtml(reconciliationReason(row))}</td>
          <td class="numeric">${escapeHtml(money(row.amount, row.currency))}</td>
          <td>${row.candidates?.length ? `<form class="match-choice" data-match-row="${index}">
            <label>${row.match_status === "bank_only_imported" ? "已按银行占位入账" : "扣款暂缓入账"}
              <select data-native-select required aria-label="选择已核实的候选订单"><option value="">选择订单</option>
                ${row.candidates.map((o, i) => `<option value="${i}">${escapeHtml(o.date)} · ${escapeHtml(o.merchant)} · ${escapeHtml(o.order_id)} · ${escapeHtml(money(o.total,o.currency))}</option>`).join("")}
              </select></label><button class="quiet-button" type="submit">确认关联</button></form>` : "暂无可靠候选"}</td>
        </tr>`).join("")
    : emptyRow(6, "当前没有待核对的平台扣款");
  body.querySelectorAll("form[data-match-row]").forEach(form => form.addEventListener("submit", async event => {
    event.preventDefault();
    const bank = rows[Number(form.dataset.matchRow)];
    const candidate = bank.candidates[form.querySelector("select").value];
    if (!candidate) return;
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await api("/api/reconciliation/confirm", {method: "POST", body: JSON.stringify({
        transaction_id: bank.transaction_id, platform: candidate.platform, order_id: candidate.order_id, confirmed: true
      })});
      await loadOverview();
      showToast("已关联银行流水与平台订单");
    } catch (error) { showToast(error.message, true); }
    finally { button.disabled = false; }
  }));
}

function renderCashWithdrawals(payload) {
  state.cash = payload;
  const rows = payload.rows || [];
  const summary = payload.summary || {};
  const count = Number(summary.withdrawal_count || 0);
  const noted = Number(summary.noted_count || 0);
  const badge = document.getElementById("cashWithdrawalBadge");
  badge.textContent = `${count} 笔`;
  badge.classList.toggle("passed", count > 0 && count === noted);
  document.getElementById("cashWithdrawalTotal").textContent =
    `本期取现 ${Object.entries(summary.currency_totals || {}).map(([currency, value]) =>
      money(value, currency)).join(" / ") || money(summary.total)}`;
  document.getElementById("cashWithdrawalNoted").textContent =
    `已备注 ${noted} / ${count} 笔`;

  const body = document.getElementById("cashWithdrawalRows");
  body.closest(".cash-table-wrap").classList.toggle("empty", rows.length === 0);
  body.innerHTML = rows.length
    ? rows.map((row) => `
        <tr>
          <td>${escapeHtml(row.date || "—")}</td>
          <td class="cash-bank-record" title="${escapeHtml(row.description)}">
            <strong>${escapeHtml(row.account_label || "Scotiabank")}</strong>
            <small>${escapeHtml(row.description || "现金取现")}</small>
          </td>
          <td class="numeric">${escapeHtml(money(row.amount, row.currency))}</td>
          <td class="cash-note-cell">
            <form class="cash-note-form" data-cash-id="${row.bank_transaction_pk}">
              <input
                class="cash-note-input"
                type="text"
                maxlength="300"
                value="${escapeHtml(row.note)}"
                placeholder="例如：理发、零用现金"
                aria-label="${escapeHtml(`${row.date} 现金取现用途备注`)}"
              >
              <button class="button secondary cash-note-save" type="submit">保存</button>
            </form>
          </td>
        </tr>`).join("")
    : emptyRow(4, "本期未检测到 ATM 现金取现；完成 Scotia 对账后会自动显示在这里");
  bindCashNoteForms();
}

function bindCashNoteForms() {
  document.querySelectorAll(".cash-note-form").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = form.querySelector(".cash-note-input");
      const button = form.querySelector(".cash-note-save");
      button.disabled = true;
      try {
        await api(`/api/cash-withdrawals/${form.dataset.cashId}`, {
          method: "PATCH",
          body: JSON.stringify({ note: input.value }),
        });
        await loadCashWithdrawals();
        showToast(input.value.trim() ? "现金用途备注已保存" : "现金用途备注已清除");
      } catch (error) {
        button.disabled = false;
        showToast(error.message, true);
      }
    });
  });
}

function populateMonthSelect(months, selected) {
  const select = document.getElementById("monthSelect");
  const values = ["all", ...new Set([...(state.overview?.trend || []).map(row => row.month), ...months, ...(selected !== "all" ? [selected] : [])].sort().reverse())];
  select.innerHTML = values.map((month) => `
    <option value="${escapeHtml(month)}" ${month === selected ? "selected" : ""}>
      ${escapeHtml(monthLabel(month))}
    </option>
  `).join("");
}

function populateFilters(payload) {
  state.filters = payload;
  const options = (values, labeler = (value) => value) => values.map((value) =>
    `<option value="${escapeHtml(value)}">${escapeHtml(labeler(value))}</option>`
  ).join("");
  document.getElementById("platformFilter").insertAdjacentHTML(
    "beforeend",
    options(payload.platforms, labelPlatform),
  );
  document.getElementById("categoryFilter").insertAdjacentHTML(
    "beforeend",
    options(payload.categories, labelCategory),
  );
  document.getElementById("cardFilter").insertAdjacentHTML(
    "beforeend",
    payload.cards.map((card) =>
      `<option value="${escapeHtml(card.card_id)}">${escapeHtml(card.display_name)}</option>`
    ).join(""),
  );
}

async function loadOverview(month = state.month) {
  const request = ++state.overviewRequest;
  let payload;
  try { payload = await api(`/api/overview?month=${encodeURIComponent(month)}`); }
  catch (error) { if (request === state.overviewRequest) throw error; return false; }
  if (request !== state.overviewRequest) return false;
  renderOverview(payload);
  return true;
}

async function selectDashboardMonth(month) {
  state.month = month;
  document.getElementById("monthSelect").value = month;
  window.LedgerSelect?.sync(document.getElementById('monthSelect'));
  document.getElementById("metricGrid").setAttribute("aria-busy", "true");
  try {
    if (!await loadOverview(month)) return;
    if (state.view === "orders") await loadOrders(true);
    if (state.view === "cash") await loadCashWithdrawals();
  } catch (error) {
    if (state.month !== month) return;
    if (state.overview) {
      state.month = state.overview.selected_month;
      document.getElementById("monthSelect").value = state.month;
      window.LedgerSelect?.sync(document.getElementById('monthSelect'));
    }
    showToast(error.message, true);
  } finally {
    if (state.month === month || state.month === state.overview?.selected_month) document.getElementById("metricGrid").removeAttribute("aria-busy");
  }
}

async function loadOrders(reset = false) {
  const request = ++state.ordersRequest;
  const requestedMonth = state.month;
  if (reset) state.orderOffset = 0;
  const params = new URLSearchParams({
    month: state.month || "all",
    platform: document.getElementById("platformFilter").value,
    category: document.getElementById("categoryFilter").value,
    card_id: document.getElementById("cardFilter").value,
    q: document.getElementById("orderSearch").value.trim(),
    limit: String(state.orderLimit),
    offset: String(state.orderOffset),
  });
  const payload = await api(`/api/orders?${params}`);
  if (request !== state.ordersRequest || requestedMonth !== state.month) return;
  if (!payload.rows.length && payload.total_count > 0 && state.orderOffset > 0) {
    state.orderOffset = Math.max(0, payload.total_count - state.orderLimit);
    return loadOrders(false);
  }
  state.orderTotal = payload.total_count;
  const body = document.getElementById("ordersTable");
  body.innerHTML = payload.rows.length
    ? payload.rows.map((order) => orderRow(order, true)).join("")
    : emptyRow(8, "没有符合条件的订单");
  bindOrderRows(body);
  document.getElementById("orderCount").textContent = `${payload.total_count} 笔订单`;
  document.getElementById("orderFilterSummary").textContent = monthLabel(state.month);
  const pageStart = payload.total_count ? state.orderOffset + 1 : 0;
  const pageEnd = Math.min(state.orderOffset + payload.rows.length, payload.total_count);
  document.getElementById("orderPageSummary").textContent =
    `第 ${pageStart}–${pageEnd} / ${payload.total_count} 笔`;
  document.getElementById("ordersPrevious").disabled = state.orderOffset === 0;
  document.getElementById("ordersNext").disabled = pageEnd >= payload.total_count;
}

async function loadCashWithdrawals() {
  const request = ++state.cashRequest;
  const requestedMonth = state.month;
  const payload = await api(
    `/api/cash-withdrawals?month=${encodeURIComponent(state.month || "all")}`,
  );
  if (request === state.cashRequest && requestedMonth === state.month) renderCashWithdrawals(payload);
}

function nativeOrdersEnabled() {
  return Boolean(window.__expenseDesktopNativeOrders && !window.__expenseDesktopOrdersFallback && window.chrome?.webview?.postMessage);
}

async function openOrder(orderPk) {
  if (nativeOrdersEnabled()) {
    window.chrome.webview.postMessage({type: "native-order", orderPk, month: state.month});
    return;
  }
  const request = ++state.orderDetailRequest;
  const drawer = document.getElementById("detailDrawer");
  const backdrop = document.getElementById("drawerBackdrop");
  if (drawer.getAttribute("aria-hidden") !== "false") state.drawerReturnFocus = document.activeElement;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  backdrop.hidden = false;
  document.body.classList.add("drawer-open");
  window.requestAnimationFrame(() => {
    document.getElementById("closeDrawerButton").focus();
  });
  document.getElementById("drawerMerchant").textContent = "加载中";
  document.getElementById("drawerDate").textContent = "";
  document.getElementById("drawerContent").innerHTML = '<div class="empty-state">读取订单明细</div>';
  try {
    const detail = await api(`/api/orders/${orderPk}`);
    if (request !== state.orderDetailRequest) return;
    const order = detail.order;
    const isBankOnly = order.fulfillment === "bank_reconciled"
      || /^(scotia|bank)-/.test(String(order.order_id || ""));
    const detailHeading = isBankOnly ? "银行账单记录" : "商品明细";
    document.getElementById("drawerMerchant").textContent = order.merchant || "未命名商户";
    document.getElementById("drawerDate").textContent = `${order.date} · ${labelPlatform(order.platform)}`;
    const items = detail.items.length
      ? detail.items.map((item) => `
          <div class="item-row">
            <span>
              <strong>${escapeHtml(item.product_name)}</strong>
              <small>${escapeHtml(item.quantity || "数量未知")} · ${escapeHtml(labelCategory(item.category))}${item.category_source === "user_rule" ? " · 已记忆" : ""}</small>
            </span>
            <span class="item-price">${item.price == null ? "价格未知" : escapeHtml(money(item.price,item.currency))}</span>
          </div>`).join("")
      : '<div class="empty-state">此订单没有商品级明细</div>';
    document.getElementById("drawerContent").innerHTML = `
      <div class="detail-total"><span>实际支出</span><strong>${escapeHtml(money(order.total, order.currency))}</strong></div>
      <div class="detail-facts">
        ${Number(order.refund_total) > 0 ? `<div class="detail-fact"><span>订单金额</span><strong>${escapeHtml(money(order.gross_total, order.currency))}</strong></div>
        <div class="detail-fact"><span>已退款</span><strong>${escapeHtml(money(order.refund_total, order.currency))}</strong></div>` : ""}
        <div class="detail-fact"><span>付款来源</span><strong>${escapeHtml(order.card_label)}</strong></div>
        <div class="detail-fact"><span>分类</span><strong>${escapeHtml(labelCategory(order.category))}</strong></div>
        <div class="detail-fact"><span>订单状态</span><strong>${escapeHtml(fulfillmentLabel(order.fulfillment))}</strong></div>
        <div class="detail-fact"><span>订单号</span><strong>${escapeHtml(order.order_id)}</strong></div>
        <div class="detail-fact"><span>付款状态</span><strong>${paymentStatusLabel(order.payment_status)}</strong></div>
        <div class="detail-fact"><span>支付渠道</span><strong>${paymentChannelLabel(order.payment_channel)}</strong></div>
      </div>
      <section class="detail-section"><h3>${detailHeading} · ${detail.items.length}</h3>${items}</section>
      <details class="detail-section refund-details"><summary>商品分类与金额分配</summary>
        <p class="detail-note">全部商品同类、有价且小计与整单组成核平时，税费和退款归入该类。其余差额单列。</p>
        ${(detail.allocations || []).map(a => `<p>${escapeHtml(labelCategory(a.category))} · ${escapeHtml(money(a.cents/100, order.currency))}</p>`).join("")}
        ${detail.items.some(i => i.is_product) ? `<form class="refund-form" id="categoryRuleForm">
          <label>商品<select data-native-select name="item" required><option value="">选择商品</option>${detail.items.filter(i => i.is_product).map(i => `<option value="${Number(i.item_pk)}">${escapeHtml(i.product_name)}</option>`).join("")}</select></label>
          <label>同平台同名商品分类<select data-native-select name="category" required><option value="">选择分类</option>${Object.entries(categoryLabels).filter(([k]) => !["mixed","unallocated"].includes(k)).map(([k,v]) => `<option value="${k}">${escapeHtml(v)}</option>`).join("")}<option value="__reset">恢复来源分类</option></select></label>
          <button class="quiet-button" type="submit">保存商品规则</button></form>` : ""}
      </details>
      <details class="detail-section refund-details"><summary>退款与银行核对</summary>
        <p class="detail-note">凭证状态 · ${({bank_only:"银行流水",platform_only:"平台订单",bank_matched:"平台订单与银行流水已关联"})[order.evidence_status] || "未知"}</p>
        ${(detail.bank_matches || []).map((row) => `<p>${escapeHtml(row.date)} · ${row.match_method === "manual_confirmed" ? "人工确认" : escapeHtml(row.match_method)} · ${escapeHtml(money(row.amount,row.currency))}<br>流水标识 ${escapeHtml(row.match_id)}</p>`).join("") || '<p>暂无银行关联记录</p>'}
        ${(detail.refunds || []).map((row) => `<div class="item-row"><span>${escapeHtml(row.date)} · 到账 ${escapeHtml(money(row.amount, order.currency))}</span><button type="button" class="quiet-button" data-unlink-refund="${Number(row.id)}">撤销关联</button></div>`).join("")}
        ${(detail.refund_candidates || []).length ? `<form id="refundForm" class="refund-form">
          <label>选择已经核实的到账退款<select name="bank_transaction_pk" required><option value="">请选择（不会自动匹配）</option>${detail.refund_candidates.map((row) => `<option value="${Number(row.id)}">${escapeHtml(row.date)} · ${escapeHtml(money(Math.abs(row.amount), order.currency))} · ${escapeHtml(row.description)}</option>`).join("")}</select></label>
          <label><input type="checkbox" required> 我已核对，这笔入账是此订单的退款</label>
          <button type="submit" class="quiet-button">关联到账退款</button>
        </form>` : '<p class="detail-note">暂无可关联的同币种银行入账。仅接受不超过剩余订单金额的整笔入账。</p>'}
      </details>
    `;
    document.getElementById("categoryRuleForm")?.addEventListener("submit", async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const values = new FormData(form);
      const item = detail.items.find(i => i.item_pk === Number(values.get("item")));
      if (!item) return;
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      try {
        const category = values.get("category");
        await api(`/api/orders/${orderPk}/items/${item.item_pk}/category`, {method: "PUT",
          body: JSON.stringify({product_name: item.product_name, category: category === "__reset" ? null : category})});
        await finishOrderMutation(orderPk, request);
        showToast("商品分类规则已保存");
      } catch (error) { showToast(error.message, true); button.disabled = false; }
    });
    document.getElementById("refundForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.currentTarget.querySelector('button[type="submit"]');
      const bankId = Number(new FormData(event.currentTarget).get("bank_transaction_pk"));
      button.disabled = true;
      try {
        await api(`/api/orders/${orderPk}/refunds`, {method: "POST", body: JSON.stringify({bank_transaction_pk: bankId, confirmed: true})});
        await finishOrderMutation(orderPk, request);
      } catch (error) { showToast(error.message, true); button.disabled = false; }
    });
    document.querySelectorAll("[data-unlink-refund]").forEach((button) => button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await api(`/api/orders/${orderPk}/refunds/${Number(button.dataset.unlinkRefund)}`, {method: "DELETE"});
        await finishOrderMutation(orderPk, request);
      } catch (error) { showToast(error.message, true); button.disabled = false; }
    }));
  } catch (error) {
    if (request !== state.orderDetailRequest) return;
    document.getElementById("drawerContent").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function finishOrderMutation(orderPk, request) {
  // A completed write should refresh data without reopening a drawer the user left.
  if (window.__expenseDesktopNativeOrders) {
    window.chrome?.webview?.postMessage({type: "order-updated", orderPk});
  }
  const stillOpen = request === state.orderDetailRequest
    && document.getElementById("detailDrawer").getAttribute("aria-hidden") === "false";
  await Promise.all([stillOpen ? openOrder(orderPk) : Promise.resolve(), loadOverview()]);
}

function renderOrderEvent(event) {
  const names = {baseline: "升级时基线", recorded: "首次记录", changed: "订单变化", removed: "订单移除", refund_linked: "关联到账退款", refund_unlinked: "撤销退款关联"};
  const labels = {date: "日期", fulfillment: "状态", total: "原金额", item_total: "商品金额", tax: "税", category: "分类", amount: "退款金额", currency: "币种"};
  const before = event.before || {};
  const after = event.after || {};
  const changes = Object.keys(labels).filter((key) => before[key] !== after[key]).map((key) => `${labels[key]}：${before[key] ?? "—"} → ${after[key] ?? "—"}`);
  return `<div class="order-event"><strong>${escapeHtml(names[event.event_type] || event.event_type)}</strong><small>${escapeHtml(dateTime(event.observed_at))}</small><p>${escapeHtml(changes.join("；"))}</p></div>`;
}

async function loadChanges() {
  const payload = await api("/api/order-changes");
  document.getElementById("recentChanges").innerHTML = payload.rows.map((row) => `<div><p>${escapeHtml(labelPlatform(row.platform))} · ${escapeHtml(row.order_id)}</p>${renderOrderEvent(row)}</div>`).join("") || '<p>升级后暂无账目变化</p>';
}

function closeDrawer() {
  state.orderDetailRequest++;
  const drawer = document.getElementById("detailDrawer");
  if (drawer.getAttribute("aria-hidden") === "true") return;
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  document.getElementById("drawerBackdrop").hidden = true;
  document.body.classList.remove("drawer-open");
  if (state.drawerReturnFocus?.isConnected) {
    state.drawerReturnFocus.focus();
  }
  state.drawerReturnFocus = null;
  if (window.__expenseDesktopNativeOrders && window.__expenseDesktopOrdersFallback) {
    window.chrome?.webview?.postMessage({type: "order-fallback-closed"});
  }
}

function trapDrawerFocus(event) {
  const drawer = document.getElementById("detailDrawer");
  if (event.key !== "Tab" || drawer.getAttribute("aria-hidden") === "true") return;
  const focusable = [...drawer.querySelectorAll(
    'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  )].filter((element) => !element.hidden);
  if (!focusable.length) {
    event.preventDefault();
    drawer.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function switchView(view) {
  if (!Object.hasOwn(viewTitles, view)) return;
  if (view === "orders" && nativeOrdersEnabled()) {
    window.chrome.webview.postMessage({type: "native-orders", month: state.month});
    return;
  }
  if (["cash", "sources"].includes(view) && window.__expenseDesktopNativeFinance && window.chrome?.webview?.postMessage) {
    window.chrome.webview.postMessage({type: "native-finance", view, month: state.month});
    return;
  }
  if (window.__expenseDesktopHost) window.chrome?.webview?.postMessage({type:"view", view});
  const previousView = state.view;
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  document.querySelectorAll(".view-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === view);
  });
  if (previousView !== view) window.LedgerMotion?.view(document.querySelector('.view-panel.active'));
  const [eyebrow, title] = viewTitles[view];
  document.getElementById("pageEyebrow").textContent = eyebrow;
  document.getElementById("pageTitle").textContent = title;
  document.getElementById("monthControl").hidden = ["operations", "maintenance"].includes(view);
  if (view === "orders") {
    loadOrders().catch((error) => showToast(error.message, true));
  }
  if (view === "cash") {
    loadCashWithdrawals().catch((error) => showToast(error.message, true));
  }
  if (view === "operations") {
    Promise.all([loadJobs(), loadCollectorBackends()])
      .catch((error) => showToast(error.message, true));
  }
  if (view === "maintenance") {
    Promise.all([loadJobs(), loadCollectorBackends(), loadCollectorRecovery(), loadChanges()])
      .catch((error) => showToast(error.message, true));
  }
  const reduceMotion = window.LedgerMotion?.reduced() ?? window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
}

function setTaskButtons(disabled) {
  document.querySelectorAll(
    "#validateButton, #validatePanelButton, #backendToggleButton, #billRefreshButton, [data-refresh-mode], [data-collector-platform], [data-scotia-action], [data-recovery-retry], [data-recovery-login], [data-recovery-verify]"
  ).forEach((button) => {
    button.disabled = disabled;
  });
  if (state.collectorBackends) renderCollectorBackends(state.collectorBackends);
}

async function startValidation() {
  try {
    const job = await api("/api/jobs/validate", { method: "POST", body: "{}" });
    showToast("正在校验账本");
    watchJob(job.id);
    switchView("maintenance");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function startRefresh(mode = "stores", platforms = []) {
  try {
    const job = await api("/api/jobs/refresh", {
      method: "POST",
      body: JSON.stringify({ mode, platforms }),
    });
    showToast(job.label);
    watchJob(job.id);
    switchView(mode === "local" ? "maintenance" : "operations");
  } catch (error) {
    showToast(error.message, true);
  }
}

function watchJob(jobId) {
  state.activeJobId = jobId;
  setTaskButtons(true);
  window.clearInterval(state.pollTimer);
  state.pollTimer = window.setInterval(async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      if (
        job.status === "running"
        && ["refresh:stores", "refresh:all"].includes(job.kind)
        && state.view === "maintenance" && state.collectorBackends?.resolved_backend === "vps"
      ) {
        await loadCollectorBackends();
      }
      await loadJobs();
      if (!["queued", "running"].includes(job.status)) {
        window.clearInterval(state.pollTimer);
        state.pollTimer = null;
        state.activeJobId = null;
        setTaskButtons(false);
        const tone = job.status === "partial" ? "warning" : job.status === "passed" ? false : "error";
        showToast(
          state.view === "maintenance" && job.message ? job.message : (job.status === "passed" ? "已完成" : job.status === "partial" ? "部分完成，请到维护页查看" : "未能完成，请到维护页查看"),
          tone,
        );
        await loadOverview(state.month);
        if (state.view === "maintenance") await loadCollectorRecovery();
        if (state.view === "orders") await loadOrders(true);
        if (state.view === "cash") await loadCashWithdrawals();
      }
    } catch (error) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
      setTaskButtons(false);
      showToast(error.message, true);
    }
  }, 1000);
}

function renderJobs(payload) {
  state.jobs = payload.jobs || [];
  state.activeJobId = payload.active_job_id;
  setTaskButtons(Boolean(state.activeJobId));
  if (state.collectorBackends) renderCollectorBackends(state.collectorBackends);
  const list = document.getElementById("jobList");
  if (payload.history_warning && state.view === "maintenance") showToast(payload.history_warning, true);
  const active = state.jobs.find(job => job.id === state.activeJobId);
  if (active) {
    document.getElementById("runBadge").textContent = active.status === "queued" ? "等待中" : "更新中";
    document.getElementById("runBadge").className = "state-badge running";
    document.getElementById("updateSummary").textContent = "正在处理，请稍候";
    document.getElementById("updateAttention").hidden = true;
  } else {
    const latest = state.jobs.find(job => job.kind?.startsWith("refresh:") && job.kind !== "refresh:local");
    const runAt = Date.parse(state.overview?.run_state?.completed_at || "") || 0;
    if (latest && (Date.parse(latest.completed_at || "") || 0) >= runAt) {
      const failed = latest.status === "failed";
      const partial = latest.status === "partial";
      document.getElementById("runBadge").textContent = failed ? "更新失败" : partial ? "部分完成" : latest.kind === "refresh:scotia_login" ? "登录已打开" : "已更新";
      document.getElementById("runBadge").className = `state-badge ${statusClass(latest.status)}`;
      document.getElementById("updateSummary").textContent = `最近操作 ${dateTime(latest.completed_at)}`;
      document.getElementById("updateAttention").hidden = !failed && !partial;
    }
  }
  if (!state.jobs.length) {
    list.innerHTML = '<div class="empty-state">暂无应用内任务</div>';
    return;
  }
  list.innerHTML = state.jobs.map((job) => {
    const icon = ["queued", "running"].includes(job.status)
      ? "loader-circle"
      : job.status === "passed" ? "check-circle-2" : "circle-alert";
    const progress = job.status === "running" ? collectorProgressText() : "";
    return `
      <div class="job-row">
        <img class="${job.status === "running" ? "spin" : ""}" src="/static/icons/${icon}.svg" alt="">
        <span>
          <strong>${escapeHtml(job.label)}</strong>
          <small>${escapeHtml(dateTime(job.completed_at || job.started_at))} · ${escapeHtml(progress || job.message || job.status)}</small>
        </span>
        <span class="state-badge ${statusClass(job.status)}">${escapeHtml(job.status)}</span>
      </div>`;
  }).join("");
}

async function loadJobs() {
  const payload = await api("/api/jobs");
  renderJobs(payload);
  if (payload.active_job_id && !state.pollTimer) {
    watchJob(payload.active_job_id);
  }
}

function bindEvents() {
  const previewForm = document.getElementById("wechatPreviewForm");
  if (previewForm) {
    const output = document.getElementById("wechatPreviewResult");
    let revision = 0;
    previewForm.addEventListener("input", () => { revision++; output.textContent = ""; });
    previewForm.addEventListener("submit", async event => {
      event.preventDefault();
      const current = ++revision;
      output.textContent = "正在核对…";
      try {
        const payload = await api("/api/wechat-bills/preview", {method: "POST",
          body: JSON.stringify(Object.fromEntries(new FormData(previewForm)))});
        if (current !== revision) return;
        const {bill} = payload;
        const lines = [bill.merchant + " · " + bill.paid_at, "微信实付 " + money(bill.paid.amount, bill.paid.currency)];
        if (bill.listed) lines.push("商户标价 " + money(bill.listed.amount, bill.listed.currency));
        lines.push("微信交易单号尾号 " + bill.transaction_id.slice(-4));
        if (bill.merchant_order_id) lines.push("商户单号尾号 " + bill.merchant_order_id.slice(-4));
        output.textContent = [...lines, payload.reason, "仅供核对，未写入账本。"].join("\n");
      } catch (error) {
        if (current === revision) output.textContent = error.message;
      }
    });
  }
  bindTrendEvents();
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  document.querySelectorAll("[data-go-view]").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.goView));
  });
  document.getElementById("monthSelect").addEventListener("change", event => selectDashboardMonth(event.target.value));

  document.getElementById("trendZoomOut").addEventListener("click", () => moveTrendZoom(-1));
  document.getElementById("trendZoomReset").addEventListener("click", () => setTrendZoom(1));
  document.getElementById("trendZoomIn").addEventListener("click", () => moveTrendZoom(1));
  document.getElementById("trendFullscreenButton").addEventListener("click", toggleTrendFullscreen);
  document.addEventListener("fullscreenchange", renderTrendFullscreenState);

  let searchTimer;
  document.getElementById("orderSearch").addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => loadOrders(true).catch((error) => showToast(error.message, true)), 250);
  });
  ["platformFilter", "categoryFilter", "cardFilter"].forEach((id) => {
    document.getElementById(id).addEventListener("change", () => {
      loadOrders(true).catch((error) => showToast(error.message, true));
    });
  });
  document.getElementById("ordersPrevious").addEventListener("click", () => {
    state.orderOffset = Math.max(0, state.orderOffset - state.orderLimit);
    loadOrders().catch((error) => showToast(error.message, true));
  });
  document.getElementById("ordersNext").addEventListener("click", () => {
    if (state.orderOffset + state.orderLimit >= state.orderTotal) return;
    state.orderOffset += state.orderLimit;
    loadOrders().catch((error) => showToast(error.message, true));
  });

  window.LedgerBillRefresh.bind({ onConfirm: platforms => startRefresh("stores", platforms), isBusy: () => Boolean(state.activeJobId) });
  document.getElementById("validateButton").addEventListener("click", startValidation);
  document.getElementById("validatePanelButton").addEventListener("click", startValidation);
  document.querySelectorAll("[data-refresh-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      startRefresh(button.dataset.refreshMode);
    });
  });
  document.querySelectorAll("[data-collector-platform]").forEach((button) => {
    button.addEventListener("click", () => {
      startRefresh("stores", [button.dataset.collectorPlatform]);
    });
  });
  document.getElementById("backendToggleButton").addEventListener("click", (event) => {
    const target = event.currentTarget.dataset.targetBackend;
    if (target) selectCollectorBackend(target);
  });
  document.getElementById("scotiaActionButton").addEventListener("click", () => {
    startRefresh("scotia");
  });

  document.getElementById("closeDrawerButton").addEventListener("click", closeDrawer);
  document.getElementById("drawerBackdrop").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
    trapDrawerFocus(event);
  });
}

// Accounts imported from OFX/CSV statements need no Scotia login, so its button is hidden
// when the first-run setup chose "other bank".
function applyBankMode(setup) {
  const button = document.getElementById("scotiaActionButton");
  if (button) button.hidden = setup?.bank_mode === "statements";
}

async function initialize() {
  if (window.__expenseDesktopHost) {
    window.__expenseDesktopOrdersFallback = new URLSearchParams(window.location.search).get("nativeFallback") === "1";
    window.expenseDesktopNavigate = (view, options = {}) => {
      window.__expenseDesktopOrdersFallback = Boolean(options.ordersFallback);
      if (!options.ordersFallback) closeDrawer();
      if (window.expenseDesktopReady) switchView(view);
      else window.expenseDesktopPendingView = view;
    };
  }

  bindEvents();
  renderScotiaAction();
  try {
    const health = await api("/api/health");
    const online = health.status === "ok";
    document.getElementById("databaseDot").classList.add(online ? "online" : "offline");
    document.getElementById("databaseStatus").textContent = online ? "账本已连接" : "账本不可用";
    const [overview, filters, jobsPayload] = await Promise.all([
      api("/api/overview?month=latest"),
      api("/api/filters"),
      api("/api/jobs"),
    ]);
    renderOverview(overview);
    populateFilters(filters);
    renderJobs(jobsPayload);
    if (jobsPayload.active_job_id) watchJob(jobsPayload.active_job_id);
    api("/api/setup").then(applyBankMode).catch(() => {});
    window.expenseDesktopReady = true;
    if (window.expenseDesktopPendingView) switchView(window.expenseDesktopPendingView);
    const linkedOrder = Number(new URLSearchParams(window.location.search).get("order"));
    if (Number.isSafeInteger(linkedOrder) && linkedOrder > 0) await openOrder(linkedOrder);
  } catch (error) {
    document.getElementById("databaseDot").classList.add("offline");
    document.getElementById("databaseStatus").textContent = "账本不可用";
    showToast(error.message, true);
  }
}

initialize();
