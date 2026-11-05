import { useState } from "react";
import { Button, ErrorBox, Field, Modal } from "../../components/ui";
import { investmentPost, type Position } from "./model";

export default function ValuationModal({ position, onClose, onSaved }: { position: Position; onClose: () => void; onSaved: () => void }) {
  const [mode, setMode] = useState("price");
  const [value, setValue] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  return <Modal title="手动更新估值" onClose={onClose}><form className="investment-form" onSubmit={async (event) => {
    event.preventDefault(); setBusy(true); setError(undefined);
    try {
      if (!/^\d+(\.\d+)?$/.test(value) || !/[1-9]/.test(value)) throw new Error("请填写大于零的有效估值。");
      if (!date || !time) throw new Error("请填写估值日期和时间。");
      await investmentPost("/valuation", { market: position.market, symbol: position.symbol, occurred_at: `${date}T${time.length === 5 ? `${time}:00` : time}+08:00`, [mode === "price" ? "price" : "total_amount"]: value });
      onSaved();
    } catch (cause) { setError(cause); } finally { setBusy(false); }
  }}>
    <p className="investment-note">{position.name || position.symbol} · {position.currency}。手动估值用于其他资产，按填写的时间生效。</p>
    <Field label="估值方式"><select aria-label="估值方式" value={mode} onChange={(event) => setMode(event.target.value)}><option value="price">单位价格</option><option value="total">资产总金额</option></select></Field>
    <Field label={mode === "price" ? "单位价格" : "资产总金额"}><input aria-label={mode === "price" ? "单位价格" : "资产总金额"} required inputMode="decimal" pattern="[0-9]+([.][0-9]+)?" value={value} onChange={(event) => setValue(event.target.value)} /></Field>
    <div className="investment-form-grid"><Field label="估值日期"><input aria-label="估值日期" required type="date" value={date} onChange={(event) => setDate(event.target.value)} /></Field><Field label="估值时间（北京时间）"><input aria-label="估值时间（北京时间）" required type="time" step="1" value={time} onChange={(event) => setTime(event.target.value)} /></Field></div>
    <ErrorBox error={error} /><div className="investment-modal-actions"><Button type="button" variant="secondary" disabled={busy} onClick={onClose}>取消</Button><Button busy={busy}>保存估值</Button></div>
  </form></Modal>;
}
