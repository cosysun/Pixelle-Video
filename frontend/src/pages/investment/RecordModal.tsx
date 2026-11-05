import { useState } from "react";
import { Button, ErrorBox, Field, Modal } from "../../components/ui";
import { actions, investmentPost, investmentRequest, marketName, type Action, type InvestmentRecord } from "./model";

type Draft = Record<string, string>;
const decimalFields = ["quantity", "price", "amount", "fee", "settlement_amount", "settlement_fee", "fx_rate"];
function localDateTime(value: string) {
  if (!value || !Number.isFinite(Date.parse(value))) return { date: "", time: "" };
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(new Date(value));
  const get = (key: string) => parts.find((part) => part.type === key)?.value || "";
  return { date: `${get("year")}-${get("month")}-${get("day")}`, time: `${get("hour")}:${get("minute")}:${get("second")}` };
}
export default function RecordModal({ record, onClose, onSaved }: { record?: InvestmentRecord; onClose: () => void; onSaved: () => void }) {
  const [draft, setDraft] = useState<Draft>(() => {
    const initial: Draft = { action: "buy", market: "A", currency: "CNY", settlement_currency: "CNY", symbol: "", name: "", quantity: "", price: "", amount: "", fee: "", settlement_amount: "", settlement_fee: "", fx_rate: "", note: "", external_id: "", date: "", time: "" };
    if (record) {
      Object.keys(initial).forEach((key) => { if (record[key] != null) initial[key] = String(record[key]); });
      Object.assign(initial, localDateTime(record.occurred_at));
    }
    return initial;
  });
  const [exactTime, setExactTime] = useState(record ? !record.date_only : false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const action = draft.action as Action;
  const security = ["buy", "sell", "dividend", "split"].includes(action);
  const trade = action === "buy" || action === "sell";
  const connect = (draft.market === "HK_CONNECT" || draft.market === "HK" && draft.settlement_currency === "CNY") && (trade || action === "dividend");
  const exchange = action === "exchange";
  const change = (key: string, value: string) => setDraft((current) => ({ ...current, [key]: value }));
  const decimal = (key: string, label: string, required = false, hint?: string) => <Field key={key} label={label} hint={hint}><input aria-label={label} type="text" inputMode="decimal" pattern="[0-9]+([.][0-9]+)?" required={required} value={draft[key]} onChange={(event) => change(key, event.target.value)} placeholder={required ? "请输入" : "可留空"} /></Field>;
  const submit = async () => {
    setBusy(true); setError(undefined);
    try {
      if (!draft.date || (exactTime && !draft.time)) throw new Error("请选择发生日期；启用精确时间时请填写时间。");
      const occurredAt = `${draft.date}T${exactTime ? (draft.time.length === 5 ? `${draft.time}:00` : draft.time) : "00:00:00"}+08:00`;
      if (!Number.isFinite(Date.parse(occurredAt))) throw new Error("发生日期或时间无效。");
      const fields = new Set(["amount", "fee"]);
      if (trade) { fields.add("quantity"); fields.add("price"); }
      if (action === "split") { fields.clear(); fields.add("quantity"); }
      if (exchange || connect) { fields.add("settlement_amount"); fields.add("settlement_fee"); fields.add("fx_rate"); }
      for (const key of fields) {
        if (draft[key] && !/^\d+(\.\d+)?$/.test(draft[key])) throw new Error("金额、数量和价格请输入非负数字。");
      }
      const required = trade ? ["quantity", "price"] : action === "split" ? ["quantity"] : exchange ? ["amount", "settlement_amount"] : ["amount"];
      if (required.some((key) => !draft[key] || !/[1-9]/.test(draft[key]))) throw new Error("必填金额、价格或数量必须大于 0。");
      if (exchange && draft.currency === draft.settlement_currency) throw new Error("换汇的转出与转入币种必须不同。");
      const body: Record<string, unknown> = { action, occurred_at: occurredAt, date_only: !exactTime, market: security ? draft.market === "HK_CONNECT" ? "HK" : draft.market : "CASH", symbol: security ? draft.symbol.trim() : "", name: security ? draft.name.trim() : "", currency: draft.currency, settlement_currency: exchange || security ? draft.settlement_currency : null, note: draft.note.trim(), external_id: draft.external_id.trim() || null };
      decimalFields.forEach((key) => { body[key] = fields.has(key) && draft[key] ? draft[key] : ["quantity", "price", "amount", "fee"].includes(key) ? "0" : null; });
      if (record) await investmentRequest(`/records/${encodeURIComponent(record.id)}`, { method: "PATCH", body: JSON.stringify(body) });
      else await investmentPost("/records", body);
      onSaved();
    } catch (cause) { setError(cause); } finally { setBusy(false); }
  };
  return <Modal title={record ? "编辑投资记录" : "新增投资记录"} onClose={onClose} wide>
    <form className="investment-form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <p className="investment-note">金额按原币填写；发生日期使用北京时间。只记日期会明确标为“仅日期”，不会生成虚构的成交时间。</p>
      <div className="investment-form-grid">
        <Field label="记录类型"><select aria-label="记录类型" value={action} onChange={(event) => { const next = event.target.value; setDraft((current) => ({ ...current, action: next, ...(next === "exchange" && current.currency === current.settlement_currency ? { settlement_currency: current.currency === "CNY" ? "USD" : "CNY" } : {}) })); }}>{Object.entries(actions).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
        <Field label="发生日期"><input aria-label="发生日期" type="date" required value={draft.date} onChange={(event) => change("date", event.target.value)} /></Field>
        <label className="investment-check investment-span"><input type="checkbox" checked={exactTime} onChange={(event) => setExactTime(event.target.checked)} />填写精确时间（可选，北京时间）</label>
        {exactTime && <Field label="发生时间"><input aria-label="发生时间" type="time" step="1" required value={draft.time} onChange={(event) => change("time", event.target.value)} /></Field>}
        {security && <>
          <Field label="市场"><select aria-label="市场" value={draft.market} onChange={(event) => { const market = event.target.value; setDraft((current) => ({ ...current, market, currency: market === "HK" || market === "HK_CONNECT" ? "HKD" : market === "US" ? "USD" : "CNY", settlement_currency: market === "HK" ? "HKD" : market === "US" ? "USD" : "CNY" })); }}>{["A", "HK_CONNECT", "HK", "US", "FUND", "OTHER"].map((market) => <option key={market} value={market}>{market === "HK" ? "港股（港币结算）" : marketName(market)}</option>)}</select></Field>
          <Field label="证券 / 资产代码"><input aria-label="证券 / 资产代码" required value={draft.symbol} onChange={(event) => change("symbol", event.target.value)} placeholder="如 600000、00700 或自定义代码" /></Field>
          <Field label="资产名称"><input aria-label="资产名称" value={draft.name} onChange={(event) => change("name", event.target.value)} placeholder="可选" /></Field>
        </>}
        <Field label={exchange ? "转出币种" : "原币币种"}><select aria-label={exchange ? "转出币种" : "原币币种"} value={draft.currency} onChange={(event) => change("currency", event.target.value)}>{Array.from(new Set(["CNY", "HKD", "USD", draft.currency])).map((currency) => <option key={currency}>{currency}</option>)}</select></Field>
        {(trade || action === "split") && decimal("quantity", action === "split" ? "拆股比例" : "数量", true, action === "split" ? "新股数 / 原股数，例如 2 表示 1 拆 2" : undefined)}
        {trade && decimal("price", "成交单价", true, draft.currency)}
        {action !== "split" && decimal("amount", exchange ? "转出金额" : trade ? "成交金额" : "金额", !trade, trade ? "可留空，由数量 × 单价计算" : draft.currency)}
        {action !== "split" && decimal("fee", exchange ? "转出手续费" : "原币费用", false, draft.currency)}
        {(connect || exchange) && <>
          <div className="investment-callout investment-span">{connect ? "港股通以港币报价、人民币结算。请填写交割单上的实际人民币金额与费用；留空时仅能按汇率估算，后续可编辑补齐。" : "换汇会从转出币种扣款，向转入币种入账。请分别填写两侧实际金额。"}</div>
          <Field label={exchange ? "转入币种" : "结算币种"}><select aria-label={exchange ? "转入币种" : "结算币种"} value={draft.settlement_currency} onChange={(event) => change("settlement_currency", event.target.value)} disabled={connect}>{(connect ? ["CNY"] : ["CNY", "HKD", "USD"]).map((currency) => <option key={currency}>{currency}</option>)}</select></Field>
          {decimal("settlement_amount", exchange ? "转入金额" : "实际结算金额（CNY）", exchange)}
          {decimal("settlement_fee", exchange ? "转入手续费" : "实际结算费用（CNY）")}
          {decimal("fx_rate", "实际汇率", false, "1 原币 = 多少结算币；可选")}
        </>}
        <div className="investment-span"><Field label="外部流水号"><input aria-label="外部流水号" value={draft.external_id} onChange={(event) => change("external_id", event.target.value)} placeholder="可选，用于识别重复导入" /></Field></div>
        <div className="investment-span"><Field label="备注"><textarea aria-label="备注" rows={2} value={draft.note} onChange={(event) => change("note", event.target.value)} placeholder="记录这次操作的背景" /></Field></div>
      </div>
      <ErrorBox error={error} /><div className="investment-modal-actions"><Button variant="secondary" type="button" disabled={busy} onClick={onClose}>取消</Button><Button busy={busy} type="submit">{record ? "保存修改" : "保存记录"}</Button></div>
    </form>
  </Modal>;
}
