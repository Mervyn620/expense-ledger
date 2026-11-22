import { createHash } from "node:crypto";

const MONTHS = {
  jan: "01", feb: "02", mar: "03", apr: "04", may: "05", jun: "06",
  jul: "07", aug: "08", sep: "09", oct: "10", nov: "11", dec: "12",
};

export function normalizeScotiaDate(value) {
  const match = String(value || "").match(/([A-Za-z]{3})\.?\s+(\d{1,2})\s+(\d{4})/);
  if (!match) return "";
  const month = MONTHS[match[1].toLowerCase()];
  return month ? `${match[3]}-${month}-${String(Number(match[2])).padStart(2, "0")}` : "";
}

export function scotiaPeriodMatches(firstDateText, value, now = new Date()) {
  const date = normalizeScotiaDate(firstDateText);
  if (!date) return false;
  if (value === "currentStatementPeriod") {
    return date.slice(0, 7) === now.toISOString().slice(0, 7);
  }
  if (/^\d{4}-\d{2}-01$/.test(value)) {
    return date.slice(0, 7) === value.slice(0, 7);
  }
  return true;
}

export function scotiaTransactionKey(row) {
  return createHash("sha256")
    .update([row.date, String(row.description || "").toLowerCase(), Number(row.amount).toFixed(2)].join("|"))
    .digest("hex")
    .slice(0, 20);
}

export function normalizeScotiaTransaction(row) {
  const date = normalizeScotiaDate(row.posted_date_text);
  const amount = Number(row.amount);
  if (!date || !Number.isFinite(amount)) return null;
  const normalized = {
    transaction_id: "",
    date,
    description: String(row.description || "").trim(),
    amount: Math.abs(amount).toFixed(2),
    direction: amount < 0 ? "debit" : "credit",
    currency: "CAD",
    account_id: "scotia_chequing_3333",
    source: "scotia_dom",
  };
  normalized.transaction_id = scotiaTransactionKey({
    ...normalized,
    amount: Number(normalized.amount),
  });
  return normalized;
}
