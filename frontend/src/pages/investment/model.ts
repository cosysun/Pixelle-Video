import { ApiError } from "../../api";

export type Decimal = string | number | null;
export type Period = "today" | "week" | "month" | "quarter" | "all";
export const periods: [Period, string][] = [["today", "今日"], ["week", "近一周"], ["month", "近一月"], ["quarter", "近三月"], ["all", "全部"]];
export const actions = { buy: "买入", sell: "卖出", deposit: "入金", withdraw: "出金", dividend: "分红", fee: "费用", exchange: "换汇", split: "拆股" };
export type Action = keyof typeof actions;
export const recordActionName = (action: string) => (Object.assign({} as Record<string, string>, actions, { opening_position: "期初持仓", opening_capital: "期初投入", opening_cash: "期初现金" })[action] || action);
export const isOpening = (record: InvestmentRecord) => record.action.startsWith("opening_");
export const markets: Record<string, string> = { A: "A 股", CN: "A 股", HK: "港股", HK_CONNECT: "港股通", US: "美股", FUND: "场内ETF", CASH: "现金", OTHER: "其他资产" };
export const marketName = (market: string) => markets[market] || market || "—";
export interface Position {
  market: string; symbol: string; name: string; currency: string; quantity: Decimal; cost: Decimal;
  fx_at?: string | null; fx_source?: string | null; fx_stale?: boolean; stale?: boolean; average_cost?: Decimal; price: Decimal; market_value_native: Decimal; market_value: Decimal; pnl: Decimal;
  daily_change: Decimal; weight: Decimal; price_at: string | null; source: string | null; estimated: boolean;
}
export interface Overview {
  total_assets: Decimal; market_value: Decimal; cash: Decimal; cumulative_pnl: Decimal;
  daily_pnl: Decimal; net_invested: Decimal; cumulative_return?: Decimal; updated_at: string | null; complete: boolean;
  estimated?: boolean; warnings: string[]; cash_balances: { currency: string; balance: Decimal; rate: Decimal; cny_value: Decimal; fx_at?: string | null; fx_source?: string | null; fx_stale?: boolean }[];
  migration: Record<string, unknown>; sessions: { market: string; state: string; next_open?: string | null }[]; history_note: string;
}
export interface CurvePoint { occurred_at: string; total_assets: Decimal; cumulative_pnl: Decimal; cumulative_return: Decimal; time_weighted_return: Decimal; twr_estimated?: boolean; twr_warning?: string | null; kind: string; complete: boolean; break_before?: boolean }
export interface InvestmentRecord {
  id: string; action: Action | "opening_position" | "opening_capital" | "opening_cash"; occurred_at: string; date_only: boolean; market: string; symbol: string; name: string;
  currency: string; settlement_currency?: string | null; quantity?: Decimal; price?: Decimal; amount?: Decimal;
  fee?: Decimal; settlement_amount?: Decimal; settlement_fee?: Decimal; fx_rate?: Decimal; note?: string;
  external_id?: string | null; voided?: boolean; status?: string; voided_at?: string | null; estimated?: boolean;
  [key: string]: unknown;
}
export interface ImportSheet { name: string; columns: string[]; rows: unknown[] }
export interface Inspection { id: string; hash: string; duplicate_file: boolean; sheets: ImportSheet[] }
export interface PreviewRow { index: number; record: Partial<InvestmentRecord>; errors: unknown[]; suspected_duplicate: boolean; external_duplicate: boolean }

// Keep backend decimals as strings; conversion is for display and SVG coordinates only.
export function numeric(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
export function amount(value: unknown, signed = false, digits = 2): string {
  const number = numeric(value);
  return number === null ? "—" : `${signed && number > 0 ? "+" : ""}${number.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}
export function tone(value: unknown) { const n = numeric(value); return n === null || n === 0 ? "" : n > 0 ? "investment-positive" : "investment-negative"; }
export function timestamp(value: string | null | undefined, dateOnly = false) {
  if (!value || !Number.isFinite(Date.parse(value))) return "时间未知";
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", ...(dateOnly ? {} : { hour: "2-digit", minute: "2-digit" }), hour12: false });
}
export const isVoided = (record: InvestmentRecord) => !!(record.voided || record.voided_at || record.status === "void" || record.status === "voided" || record.is_void);
export function overviewFrom(raw: Partial<Overview>): Overview {
  return { total_assets: null, market_value: raw.market_value ?? (raw as Partial<Overview> & { holdings_value?: Decimal }).holdings_value ?? null, cash: null, cumulative_pnl: null, daily_pnl: null, net_invested: null, updated_at: null,
    complete: false, history_note: "", ...raw,
    warnings: (raw.warnings || []).map((warning: unknown) => messageOf(warning)),
    cash_balances: raw.cash_balances || [], migration: raw.migration || {}, sessions: raw.sessions || [] };
}
export function messageOf(value: unknown): string {
  if (typeof value === "object" && value) { const item = value as Record<string, unknown>; return String(item.message || item.detail || item.reason || item.code || "需要核对的数据"); }
  return describe(value);
}
export function describe(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
export async function investmentRequest<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const multipart = options.body instanceof FormData;
  const response = await fetch(`/api/investment${path}`, { credentials: "same-origin", ...options,
    headers: { ...(multipart ? {} : { "Content-Type": "application/json" }), ...options.headers } });
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("session-expired"));
    const body = await response.json().catch(() => ({}));
    const detail = Array.isArray(body.detail) ? body.detail.map((item: { msg?: string }) => item.msg || describe(item)).join("；") : body.detail;
    throw new ApiError(response.status, typeof detail === "string" ? detail : "请求未完成，请稍后重试");
  }
  return response.status === 204 ? undefined as T : response.json();
}
export const investmentPost = <T = unknown>(path: string, body: unknown = {}) => investmentRequest<T>(path, { method: "POST", body: JSON.stringify(body) });
