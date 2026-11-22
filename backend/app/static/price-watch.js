const runButton = document.getElementById("runPriceWatch");
const statusTitle = document.getElementById("watchStatusTitle");
const statusBadge = document.getElementById("watchBadge");
const statusMessage = document.getElementById("watchMessage");
const checkedCount = document.getElementById("checkedCount");
const dealCount = document.getElementById("dealCount");
const backendLabel = document.getElementById("watchBackend");
const dealList = document.getElementById("dealList");
const dealListTitle = document.getElementById("dealListTitle");
const resultTimestamp = document.getElementById("resultTimestamp");
const runButtonLabel = document.getElementById("runPriceWatchLabel");
const activeSource = "local";

const phaseLabels = {
  idle: "已同步最近结果",
  preparing_local: "准备本地专用浏览器",
  collecting: "正在慢速检查",
  receiving: "接收检查结果",
  complete: "检查完成",
  failed: "检查未完成",
};

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

function formatTime(value) {
  if (!value) return "尚未同步";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "尚未同步";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "America/Toronto",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function renderStatus(payload) {
  const status = payload.status || "idle";
  const running = ["queued", "running"].includes(status);
  backendLabel.textContent = "本机专用浏览器";
  statusTitle.textContent = status === "failed"
    ? "检查未完成"
    : phaseLabels[payload.phase] || (
      status === "passed" ? "检查完成" : "已同步最近结果"
    );
  statusBadge.textContent = running
    ? "运行中"
    : status === "passed"
      ? "已完成"
      : status === "failed"
        ? "需要处理"
        : "最近结果";
  statusBadge.className = `watch-badge ${running ? "running" : status}`;
  statusBadge.dataset.status = status;
  statusMessage.textContent = payload.message
    || "本机环境只在你主动运行时检查。";
  checkedCount.textContent = String(payload.checked ?? payload.checks ?? 0);
  dealCount.textContent = String(payload.deals ?? 0);
  runButton.disabled = running;
  runButton.classList.toggle("running", running);
}

function renderDeals(payload) {
  const rows = Array.isArray(payload.deals) ? payload.deals : [];
  dealListTitle.textContent = "本机最近结果";
  resultTimestamp.textContent = formatTime(payload.generated_at);
  dealList.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "empty-deals";
    empty.textContent = "目前没有经过“现价低于原价”验证的折扣商品。";
    dealList.append(empty);
    return;
  }
  rows.forEach((row) => {
    const item = document.createElement("article");
    item.className = "deal-item";
    const content = document.createElement("div");
    const link = document.createElement("a");
    link.href = row.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = row.name || "Walmart 商品";
    const meta = document.createElement("p");
    meta.textContent = `原价 $${row.regular_price.toFixed(2)} · 检查于 ${formatTime(row.checked_at)}`;
    content.append(link, meta);
    const price = document.createElement("div");
    price.className = "deal-price";
    const current = document.createElement("strong");
    current.textContent = `$${row.current_price.toFixed(2)}`;
    const discount = document.createElement("span");
    discount.textContent = `-${row.discount_percent.toFixed(1)}%`;
    price.append(current, discount);
    item.append(content, price);
    dealList.append(item);
  });
}

async function loadSource() {
  const [status, deals] = await Promise.all([
    api("/api/price-watch/status"),
    api("/api/price-watch/deals"),
  ]);
  renderStatus(status);
  renderDeals(deals);
}

async function watchJob(jobId) {
  window.clearInterval(watchJob.timer);
  watchJob.timer = window.setInterval(async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      const latest = await api("/api/price-watch/status");
      renderStatus({
        ...latest,
        status: job.status,
        message: latest.message || job.message,
      });
      if (!["queued", "running"].includes(job.status)) {
        window.clearInterval(watchJob.timer);
        await loadSource();
      }
    } catch (error) {
      window.clearInterval(watchJob.timer);
      renderStatus({
        status: "failed",
        phase: "failed",
        message: error.message,
      });
    }
  }, 1500);
}

async function startCheck() {
  renderStatus({
    status: "running",
    phase: "preparing_local",
    message: "正在通过本机专用浏览器读取一次 Walmart My Items 页面……",
  });
  const job = await api("/api/jobs/price-watch", {
    method: "POST",
    body: "{}",
  });
  watchJob(job.id);
}

runButton.addEventListener("click", () => {
  startCheck().catch((error) => {
    renderStatus({
      status: "failed",
      phase: "failed",
      message: error.message,
    });
  });
});

runButtonLabel.textContent = "使用本机检查折扣";

loadSource().catch((error) => {
  renderStatus({ status: "failed", phase: "failed", message: error.message });
});

window.addEventListener("focus", () => {
  loadSource().catch(() => {});
});
