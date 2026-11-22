const modeLabels = {
  month: "月度花销",
  year: "年度花销",
  trend: "总结与趋势",
};
const chatHistory = [];
const maxChatHistoryMessages = 10;
let analysisPeriods = {};
let chatgptPeriods = {};
let chatgptExport = null;
let pendingChatgptAction = null;
let analysisBusy = false;
let deepseekConfigured = false;
let deepseekModel = "正在连接";
let provider = "chatgpt";
try { if (localStorage.getItem("expense-analysis-provider") === "deepseek") provider = "deepseek"; } catch {}

function refreshProvider() {
  const chatgpt = provider === "chatgpt";
  document.querySelectorAll("[data-provider]").forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.provider === provider));
    button.disabled = analysisBusy;
  });
  document.getElementById("providerLabel").textContent = chatgpt ? "CHATGPT" : "DEEPSEEK";
  document.getElementById("providerHint").textContent = chatgpt
    ? "点击下方范围，将文件附到 Chrome 的 Claude 草稿，再由你发送。"
    : "点击下方范围，在客户端内查看 DeepSeek 分析。";
  document.getElementById("analysisResult").hidden = chatgpt;
  document.getElementById("deepseekChat").hidden = chatgpt;
  document.getElementById("chatgptPanel").hidden = !chatgpt;
  const status = document.getElementById("serviceStatus");
  status.textContent = chatgpt ? "Chrome · Ledger User" : deepseekModel;
  status.classList.toggle("ready", chatgpt || deepseekConfigured);
  status.classList.toggle("missing", !chatgpt && !deepseekConfigured);
  const now = new Date();
  document.getElementById("monthPeriod").textContent = chatgpt
    ? `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")} · 本月`
    : analysisPeriods.latest_month || "暂无月份";
  document.getElementById("yearPeriod").textContent = (chatgpt ? chatgptPeriods.latest_year : analysisPeriods.latest_year) || analysisPeriods.latest_year || "最近有记录年份";
  document.getElementById("trendPeriod").textContent = analysisPeriods.range_label || "全部账本";
  setButtonsDisabled(analysisBusy || (!chatgpt && !deepseekConfigured));
}

function setAnalysisBusy(value) {
  analysisBusy = value;
  document.getElementById("chatgptPanel").setAttribute("aria-busy", String(value));
  document.querySelectorAll(".chatgpt-export-buttons button").forEach(button => { button.disabled = value; });
  refreshProvider();
}


function analysisTitle(mode) {
  const month = /^(\d{4})-(\d{2})$/.exec(analysisPeriods.latest_month || "");
  if (mode === "month" && month) return `${month[1]}年${Number(month[2])}月 · 月度花销分析`;
  if (mode === "year" && analysisPeriods.latest_year) return `${analysisPeriods.latest_year}年 · 年度花销分析`;
  return mode === "trend" ? "总结与趋势分析" : `${modeLabels[mode]}分析`;
}

function setResultState(state, message = "") {
  const panel = document.getElementById("analysisResult");
  panel.dataset.state = state;
  panel.setAttribute("aria-busy", String(state === "loading"));
  document.getElementById("resultHeading").hidden = state === "empty";
  document.getElementById("resultEmpty").hidden = state === "ready";
  document.getElementById("emptyTitle").hidden = state !== "empty";
  document.getElementById("analysisPrivacy").hidden = state !== "empty";
  document.getElementById("emptyMessage").textContent = message;
}

function resizeComposer() {
  const input = document.getElementById("chatQuestion");
  input.style.height = "auto";
  input.style.height = `${Math.max(44, Math.min(input.scrollHeight, 176))}px`;
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

function setButtonsDisabled(disabled) {
  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.disabled = disabled;
  });
}

function setChatDisabled(disabled) {
  const question = document.getElementById("chatQuestion");
  const submit = document.getElementById("chatSubmit");
  if (question) question.disabled = disabled;
  if (submit) submit.disabled = disabled;
}

function showError(message) {
  const toast = document.getElementById("analysisToast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showError.timer);
  showError.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 6000);
}

function appendList(container, title, values) {
  if (!values?.length) return;
  const section = document.createElement("section");
  section.className = "result-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  const list = document.createElement("ul");
  values.forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    list.appendChild(item);
  });
  section.append(heading, list);
  container.appendChild(section);
}

function renderResult(payload) {
  const { result, meta } = payload;
  setResultState("ready");
  document.getElementById("resultTitle").textContent = result.title;
  document.getElementById("resultEyebrow").textContent = modeLabels[meta.mode];
  const metaParts = [
    meta.period,
    `${meta.orders} 笔`,
    `CAD ${Number(meta.total).toFixed(2)}`,
  ];
  if (meta.as_of_date) metaParts.push(`最近消费 ${meta.as_of_date}`);
  if (meta.latest_month_complete === false) metaParts.push("含未结束月份");
  if (Number.isFinite(Number(meta.item_detail_orders)) && Number(meta.quality_scope_orders)) {
    metaParts.push(`商品明细 ${meta.item_detail_orders}/${meta.quality_scope_orders} 笔`);
  }
  if (payload.cached) metaParts.push("已复用");
  document.getElementById("resultMeta").textContent = metaParts.join(" · ");

  const content = document.getElementById("resultContent");
  content.replaceChildren();
  content.hidden = false;
  const summary = document.createElement("p");
  summary.className = "result-summary";
  summary.textContent = result.summary;
  content.appendChild(summary);
  appendList(content, "重点发现", result.highlights);
  appendList(content, "变化趋势", result.trends);
  appendList(content, "轻量预测", result.forecast);
  appendList(content, "可执行建议", result.suggestions);
  appendList(content, "数据限制", result.limitations);
  appendEvidence(content, payload);
}

function renderChatAnswer(payload) {
  const meta = document.getElementById("chatMeta");
  if (payload.meta) {
    meta.hidden = false;
    const parts = [
      payload.meta.period,
      `${payload.meta.orders} 笔`,
      `CAD ${Number(payload.meta.total).toFixed(2)}`,
    ];
    if (payload.meta.as_of_date) parts.push(`最近消费 ${payload.meta.as_of_date}`);
    if (payload.meta.source === "computed") parts.push("本地计算");
    if (payload.meta.source === "computed_fallback") parts.push("本地摘要 · AI 解读未通过校验");
    if (payload.cached) parts.push("已复用");
    meta.textContent = parts.join(" · ");
  }
}

function appendInlineMarkdown(parent, text) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > cursor) {
      parent.appendChild(document.createTextNode(text.slice(cursor, match.index)));
    }
    const token = match[0];
    if (token.startsWith("`")) {
      const code = document.createElement("code");
      code.textContent = token.slice(1, -1);
      parent.appendChild(code);
    } else {
      const strong = document.createElement("strong");
      strong.textContent = token.slice(2, -2);
      parent.appendChild(strong);
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) {
    parent.appendChild(document.createTextNode(text.slice(cursor)));
  }
}

function isTableSeparator(line) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function splitTableRow(line) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function appendParagraph(container, lines) {
  const text = lines.join(" ").trim();
  if (!text) return;
  const paragraph = document.createElement("p");
  appendInlineMarkdown(paragraph, text);
  container.appendChild(paragraph);
}

function appendMarkdownTable(container, headerLine, bodyLines) {
  const wrapper = document.createElement("div");
  wrapper.className = "markdown-table-wrap";
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  splitTableRow(headerLine).forEach((cell) => {
    const th = document.createElement("th");
    appendInlineMarkdown(th, cell);
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  bodyLines.forEach((line) => {
    const row = document.createElement("tr");
    splitTableRow(line).forEach((cell) => {
      const td = document.createElement("td");
      appendInlineMarkdown(td, cell);
      row.appendChild(td);
    });
    tbody.appendChild(row);
  });
  table.appendChild(tbody);
  wrapper.appendChild(table);
  container.appendChild(wrapper);
}

function renderMarkdown(markdown) {
  const fragment = document.createDocumentFragment();
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();
    if (!trimmed) {
      index += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = codeLines.join("\n");
      pre.appendChild(code);
      fragment.appendChild(pre);
      continue;
    }

    if (index + 1 < lines.length && line.includes("|") && isTableSeparator(lines[index + 1])) {
      const bodyLines = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        bodyLines.push(lines[index]);
        index += 1;
      }
      appendMarkdownTable(fragment, line, bodyLines);
      continue;
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
    if (heading) {
      const level = Math.min(heading[1].length + 2, 5);
      const element = document.createElement(`h${level}`);
      appendInlineMarkdown(element, heading[2]);
      fragment.appendChild(element);
      index += 1;
      continue;
    }

    if (trimmed.startsWith(">")) {
      const quote = document.createElement("blockquote");
      const quoteLines = [];
      while (index < lines.length && lines[index].trim().startsWith(">")) {
        quoteLines.push(lines[index].trim().replace(/^>\s?/, ""));
        index += 1;
      }
      appendParagraph(quote, quoteLines);
      fragment.appendChild(quote);
      continue;
    }

    const listMatch = /^([-*+]|\d+\.)\s+(.+)$/.exec(trimmed);
    if (listMatch) {
      const ordered = /^\d+\./.test(listMatch[1]);
      const list = document.createElement(ordered ? "ol" : "ul");
      while (index < lines.length) {
        const itemMatch = /^([-*+]|\d+\.)\s+(.+)$/.exec(lines[index].trim());
        if (!itemMatch || /^\d+\./.test(itemMatch[1]) !== ordered) break;
        const item = document.createElement("li");
        appendInlineMarkdown(item, itemMatch[2]);
        list.appendChild(item);
        index += 1;
      }
      fragment.appendChild(list);
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^(#{1,4})\s+/.test(lines[index].trim()) &&
      !/^([-*+]|\d+\.)\s+/.test(lines[index].trim()) &&
      !lines[index].trim().startsWith(">") &&
      !lines[index].trim().startsWith("```") &&
      !(index + 1 < lines.length && lines[index].includes("|") && isTableSeparator(lines[index + 1]))
    ) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    appendParagraph(fragment, paragraphLines);
  }

  if (!fragment.childNodes.length) {
    const paragraph = document.createElement("p");
    paragraph.textContent = "";
    fragment.appendChild(paragraph);
  }
  return fragment;
}

function appendChatMessage(role, label, text) {
  const answer = document.getElementById("chatAnswer");
  if (!answer.dataset.started) {
    answer.replaceChildren();
    answer.dataset.started = "true";
  }
  answer.hidden = false;
  const item = document.createElement("article");
  item.className = `chat-message ${role}`;
  const heading = document.createElement("strong");
  heading.textContent = label;
  const body = document.createElement("div");
  body.className = "chat-message-body";
  if (role === "assistant") {
    body.appendChild(renderMarkdown(text));
  } else {
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    body.appendChild(paragraph);
  }
  item.append(heading, body);
  answer.appendChild(item);
  answer.scrollTop = answer.scrollHeight;
  return item;
}

function appendEvidence(container, payload) {
  if (!payload.evidence) return;
  const evidence = payload.evidence;
  const details = document.createElement("details");
  details.className = "answer-evidence";
  const summary = document.createElement("summary");
  summary.textContent = `查看账本依据 · ${evidence.rows.length} 笔${evidence.truncated ? "（仅显示部分）" : ""}`;
  details.appendChild(summary);
  const basis = document.createElement("p");
  basis.textContent = evidence.basis || "本次分析可用的订单记录";
  details.appendChild(basis);
  if (payload.query_plan) {
    const plan = document.createElement("p");
    plan.textContent = `查询范围：${payload.query_plan.month} · ${payload.query_plan.platforms.join(" / ") || "全部平台"} · ${payload.query_plan.category || "全部分类"}`;
    details.appendChild(plan);
  }
  const list = document.createElement("ul");
  evidence.rows.forEach((row) => {
    if (!Number.isSafeInteger(row.order_pk) || row.order_pk <= 0) return;
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = `/?order=${row.order_pk}`;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = `${row.date} · ${row.merchant || row.platform} · CAD ${Number(row.total).toFixed(2)}`;
    item.appendChild(link);
    list.appendChild(item);
  });
  details.appendChild(list);
  (payload.data_status || []).forEach((row) => {
    const status = document.createElement("p");
    status.textContent = `${row.label}：最近成功检查 ${row.last_success_at || "尚无记录"}；最近消费 ${row.last_order_date || "无记录"}；${row.coverage === "bounded" ? "有限范围检查" : "覆盖范围未知"}`;
    details.appendChild(status);
  });
  container.appendChild(details);
}

function rememberChatTurn(question, answer) {
  chatHistory.push(
    { role: "user", content: question },
    { role: "assistant", content: answer },
  );
  while (chatHistory.length > maxChatHistoryMessages) {
    chatHistory.shift();
  }
}

async function runAnalysis(mode, button) {
  document.querySelectorAll("[data-mode]").forEach((item) => {
    item.classList.toggle("active", item === button);
    item.setAttribute("aria-pressed", String(item === button));
  });
  if (analysisBusy) return;
  setAnalysisBusy(true);
  const title = document.getElementById("resultTitle");
  document.getElementById("resultContent").hidden = true;
  document.getElementById("resultMeta").textContent = "";
  title.textContent = analysisTitle(mode);
  document.getElementById("resultEyebrow").textContent = "正在分析";
  setResultState("loading", "正在整理商品、商户、金额和付款方式…");
  try {
    renderResult(await api("/api/analysis/run", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }));
  } catch (error) {
    document.getElementById("resultEyebrow").textContent = "分析未完成";
    setResultState("error", error.message);
    showError(error.message);
  } finally {
    setAnalysisBusy(false);
  }
}

async function runChat(event) {
  event.preventDefault();
  const input = document.getElementById("chatQuestion");
  const question = input.value.trim();
  if (!question) {
    showError("请输入要询问的问题。");
    input.focus();
    return;
  }
  setChatDisabled(true);
  appendChatMessage("user", "你", question);
  const pending = appendChatMessage("assistant", "DeepSeek", "正在读取全部账本...");
  document.getElementById("chatMeta").textContent = "处理中";
  document.getElementById("chatMeta").hidden = false;
  try {
    const payload = await api("/api/analysis/chat", {
      method: "POST",
      body: JSON.stringify({
        question,
        history: chatHistory.slice(-maxChatHistoryMessages),
      }),
    });
    pending.remove();
    const message = appendChatMessage("assistant", payload.meta?.source === "computed" ? "账本计算" : "DeepSeek", payload.answer || "");
    appendEvidence(message, payload);
    rememberChatTurn(question, payload.answer || "");
    renderChatAnswer(payload);
    input.value = "";
    resizeComposer();
  } catch (error) {
    document.getElementById("chatMeta").textContent = "发送未完成，可重试";
    pending.querySelector(".chat-message-body").replaceChildren(
      renderMarkdown(error.message),
    );
    showError(error.message);
  } finally {
    setChatDisabled(false);
    input.focus();
  }
}

function invalidateChatgptExport() {
  chatgptExport = null;
  pendingChatgptAction = null;
  document.getElementById("chatgptPrepared").hidden = true;
  document.getElementById("chatgptStatus").textContent = "";
}

async function prepareChatgptExport(mode, button) {
  if (analysisBusy) return;
  const status = document.getElementById("chatgptStatus");
  if (!window.__expenseDesktopHost || !window.chrome?.webview) {
    status.textContent = "请在账本 Windows 客户端中使用此功能。";
    return;
  }
  if (!window.__expenseDesktopChatGptAutoPaste) {
    status.textContent = "请从托盘退出账本，再打开新版客户端以启用自动附加。";
    return;
  }
  document.querySelectorAll("[data-mode]").forEach(item => {
    item.classList.toggle("active", item === button);
    item.setAttribute("aria-pressed", String(item === button));
  });
  invalidateChatgptExport();
  setAnalysisBusy(true);
  status.textContent = "正在整理账本文件…";
  try {
    chatgptExport = await api("/api/analysis/chatgpt/prepare", {
      method: "POST", body: JSON.stringify({mode}),
    });
    const meta = chatgptExport.meta;
    const manual = Object.entries(meta.manual_by_currency || {})
      .map(([currency, value]) => `${currency} 手填 ${value.count} 笔`);
    document.getElementById("chatgptScope").textContent = [
      meta.period, `自动订单 ${meta.orders} 笔`, `CAD ${Number(meta.total_cad).toFixed(2)}`,
      `明细 ${meta.included_orders}/${meta.orders} 笔`, ...manual,
      ...(meta.truncated ? ["明细已截断，汇总仍为全范围"] : []),
    ].join(" · ");
    document.getElementById("chatgptPreview").textContent = chatgptExport.content;
    document.getElementById("chatgptFilePath").textContent = `本地文件：${chatgptExport.file_path}`;
    document.getElementById("chatgptPrompt").textContent = `待复制的提问：${chatgptExport.message}`;
    document.getElementById("chatgptDeleteTemp").hidden = Boolean(chatgptExport.persistent);
    document.getElementById("chatgptPrepared").hidden = false;
    sendChatgptHostMessage("chatgpt-handoff");
  } catch (error) {
    status.textContent = error.message;
    showError(error.message);
    setAnalysisBusy(false);
  }
}

function sendChatgptHostMessage(type) {
  if (!chatgptExport) return;
  const status = document.getElementById("chatgptStatus");
  if (!window.__expenseDesktopHost || !window.chrome?.webview) {
    status.textContent = "请在账本 Windows 客户端中使用此功能。";
    return;
  }
  status.textContent = ({
    "chatgpt-handoff": "正在复用 Chrome 并附加文件，请稍候…",
    "chatgpt-copy-question": "正在复制提问…",
    "chatgpt-delete-temp": "正在删除本机临时文件…",
    "chatgpt-open-export": "正在打开本地文件…",
    "chatgpt-copy-path": "正在复制文件路径…",
  })[type];
  setAnalysisBusy(true);
  pendingChatgptAction = type;
  window.chrome.webview.postMessage({
    type, filePath: chatgptExport.file_path, fileSha256: chatgptExport.file_sha256,
    question: chatgptExport.message,
  });
}

window.expenseChatGptHandoffResult = (message, ok, deleted = false) => {
  document.getElementById("chatgptStatus").textContent = message;
  if (deleted || (ok && pendingChatgptAction === "chatgpt-delete-temp")) {
    chatgptExport = null;
    document.getElementById("chatgptPrepared").hidden = true;
  }
  pendingChatgptAction = null;
  setAnalysisBusy(false);
  if (!ok) showError(message);
};

async function initialize() {
  document.querySelectorAll("[data-provider]").forEach(button => {
    button.addEventListener("click", () => {
      if (analysisBusy) return;
      provider = button.dataset.provider;
      try { localStorage.setItem("expense-analysis-provider", provider); } catch {}
      document.querySelectorAll("[data-mode]").forEach(item => {
        item.classList.toggle("active", false);
        item.setAttribute("aria-pressed", "false");
        item.setAttribute("aria-controls", provider === "chatgpt" ? "chatgptPanel" : "analysisResult");
      });
      refreshProvider();
    });
  });
  refreshProvider();
  document.getElementById("chatgptHandoff").addEventListener("click", () => sendChatgptHostMessage("chatgpt-handoff"));
  document.getElementById("chatgptCopyQuestion").addEventListener("click", () => sendChatgptHostMessage("chatgpt-copy-question"));
  document.getElementById("chatgptDeleteTemp").addEventListener("click", () => {
    if (window.confirm("请先在 Claude 确认附件已经出现。现在删除这份本机临时文件？"))
      sendChatgptHostMessage("chatgpt-delete-temp");
  });
  document.getElementById("chatgptOpenFile").addEventListener("click", () => sendChatgptHostMessage("chatgpt-open-export"));
  document.getElementById("chatgptCopyPath").addEventListener("click", () => sendChatgptHostMessage("chatgpt-copy-path"));
  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => provider === "chatgpt" ? prepareChatgptExport(button.dataset.mode, button) : runAnalysis(button.dataset.mode, button));
  });
  document.getElementById("chatForm").addEventListener("submit", runChat);
  const input = document.getElementById("chatQuestion");
  input.addEventListener("input", resizeComposer);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing && !input.disabled) {
      event.preventDefault();
      document.getElementById("chatForm").requestSubmit();
    }
  });
  window.addEventListener("resize", resizeComposer);
  resizeComposer();
  const statusElement = document.getElementById("serviceStatus");
  try {
    const status = await api("/api/analysis/status");
    const periods = status.periods || {};
    analysisPeriods = periods;
    chatgptPeriods = status.chatgpt_periods || {};
    deepseekConfigured = status.configured;
    deepseekModel = status.configured ? status.model : "未配置";
    document.getElementById("monthPeriod").textContent = periods.latest_month || "暂无月份";
    document.getElementById("yearPeriod").textContent = periods.latest_year || "暂无年份";
    document.getElementById("trendPeriod").textContent = periods.range_label || "全部账本";
    statusElement.textContent = status.configured ? status.model : "未配置";
    statusElement.classList.add(status.configured ? "ready" : "missing");
    setChatDisabled(!status.configured);
    setButtonsDisabled(!status.configured);
    if (!status.configured) {
      setButtonsDisabled(true);
      document.getElementById("chatAnswer").hidden = false;
      document.getElementById("chatAnswer").textContent = "需要先配置 DeepSeek API 密钥。";
      document.getElementById("resultTitle").textContent = "需要 DeepSeek API 密钥";
      setResultState("error", "运行 scripts/configure-deepseek.ps1 设置密钥，然后重新启动账本应用。");
    }
  } catch (error) {
    deepseekModel = "不可用";
    statusElement.textContent = "不可用";
    statusElement.classList.add("missing");
    setButtonsDisabled(true);
    setChatDisabled(true);
    document.getElementById("resultTitle").textContent = "暂时无法连接分析服务";
    setResultState("error", error.message);
    if (provider === "deepseek") showError(error.message);
  } finally {
    refreshProvider();
  }
}

initialize();
