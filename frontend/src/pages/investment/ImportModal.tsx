import { useState } from "react";
import { CheckCircle2, FileSpreadsheet, Upload } from "lucide-react";
import { Button, ErrorBox, Field, Modal } from "../../components/ui";
import { recordActionName, amount, describe, investmentPost, investmentRequest, marketName, messageOf, timestamp, type ImportSheet, type Inspection, type PreviewRow } from "./model";

const mappingFields = [
  ["action", "记录类型", "动作,类型,操作,业务名称,交易类型,买卖方向,type"], ["occurred_at", "发生日期 / 时间", "日期,时间,交易日期,成交日期"],
  ["market", "市场", "市场,交易市场"], ["symbol", "证券 / 资产代码", "代码,证券代码,股票代码,证券代号,code"], ["name", "资产名称", "名称,证券名称,股票名称"],
  ["currency", "原币币种", "币种,货币,报价币种,币种代码"], ["settlement_currency", "结算 / 转入币种", "结算币种,转入币种,目标币种"],
  ["quantity", "数量 / 拆股比例", "数量,成交数量,持仓数量,qty"], ["price", "单价", "单价,价格,成交价格,成本价,cost_price"], ["amount", "原币 / 转出金额", "金额,成交金额,发生金额"],
  ["fee", "原币费用", "费用,手续费,fee_amount"], ["settlement_amount", "实际结算 / 转入金额", "结算金额,人民币金额,人民币结算金额,转入金额,目标金额"],
 ["settlement_fee", "实际结算费用", "结算费用,结算手续费,人民币结算费用"], ["fx_rate", "汇率", "汇率"], ["asset_class", "资产类别", "资产类别"], ["note", "备注", "备注,说明"], ["external_id", "外部流水号", "流水号,成交编号,合同编号,交易编号"],
];
function suggest(sheet: ImportSheet): Record<string, string> {
  const mapping: Record<string, string> = {};
  for (const [key, label, aliases] of mappingFields) {
    const candidates = [key, label, ...aliases.split(",")].map((item) => item.toLowerCase());
    const column = sheet.columns.find((item) => candidates.includes(item.trim().toLowerCase()));
    if (column) mapping[key] = column;
  }
  return mapping;
}
const errorsOf = (row: PreviewRow) => (Array.isArray(row.errors) ? row.errors : row.errors ? [row.errors] : []).map(messageOf);
const selectable = (row: PreviewRow) => !errorsOf(row).length && !row.external_duplicate;
export default function ImportModal({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [sheetName, setSheetName] = useState("");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [mode, setMode] = useState("transactions");
  const [snapshotDate, setSnapshotDate] = useState("");
  const [preview, setPreview] = useState<PreviewRow[] | null>(null);
  const [discrepancies, setDiscrepancies] = useState<Record<string, unknown>[]>([]);
  const [previewWarnings, setPreviewWarnings] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [result, setResult] = useState<unknown>();
  const [fileName, setFileName] = useState("");
  const sheet = inspection?.sheets.find((item) => item.name === sheetName);
  const invalidatePreview = () => { setPreview(null); setSelected(new Set()); setError(undefined); };
  const inspect = async (file: File) => {
    setBusy(true); setError(undefined); setInspection(null); invalidatePreview(); setFileName(file.name);
    try {
      const data = new FormData(); data.append("file", file);
      const response = await investmentRequest<Inspection>("/imports/inspect", { method: "POST", body: data });
      setInspection(response); setSheetName(response.sheets[0]?.name || ""); setMapping(response.sheets[0] ? suggest(response.sheets[0]) : {});
    } catch (cause) { setError(cause); } finally { setBusy(false); }
  };
  const generatePreview = async () => {
    if (!inspection || !sheet) return;
    setBusy(true); setError(undefined);
    try {
      if (mode !== "transactions" && !snapshotDate) throw new Error("持仓快照必须选择快照日期。");
      const data = await investmentPost<{ rows: PreviewRow[]; warnings?: unknown[]; discrepancies?: Record<string, unknown>[] }>("/imports/preview", { id: inspection.id, sheet: sheetName, mapping: Object.fromEntries(mappingFields.map(([key]) => [key, mapping[key] || ""])), mode, ...(mode !== "transactions" ? { snapshot_date: snapshotDate } : {}) });
      setPreview((data.rows || []).map((row) => ({ ...row, record: row.record || {} }))); setDiscrepancies(data.discrepancies || []); setPreviewWarnings((data.warnings || []).map(messageOf));
      setSelected(new Set(mode === "reconcile" ? [] : (data.rows || []).filter((row) => selectable(row) && !row.suspected_duplicate).map((row) => row.index)));
    } catch (cause) { setError(cause); } finally { setBusy(false); }
  };
  const confirm = async () => {
    if (!inspection || !selected.size || mode === "reconcile") return;
    setBusy(true); setError(undefined);
    try {
      const summary = await investmentPost("/imports/confirm", { id: inspection.id, indices: [...selected].sort((a, b) => a - b) });
      setResult(summary ?? { imported: selected.size }); onImported();
    } catch (cause) { setError(cause); } finally { setBusy(false); }
  };
  const summaryLabels: Record<string, string> = { imported: "已导入", imported_count: "已导入", accepted: "已接收", skipped: "已跳过", duplicates: "重复记录", failed: "失败", errors: "错误", total: "总行数", warnings: "提示", records_created: "新增记录", id: "导入编号" };
  return <Modal title="导入投资记录" onClose={onClose} wide><div className="investment-import investment-form">
    <ol className="investment-steps" aria-label="导入步骤">{["选择文件", "映射字段", "核对并确认"].map((label, i) => <li key={label} aria-current={(!inspection ? 0 : !preview ? 1 : 2) === i ? "step" : undefined}><span>{i + 1}</span>{label}</li>)}</ol>
    {result !== undefined ? <div className="investment-import-success"><CheckCircle2 size={38} /><h3>导入已完成</h3><p>已按你选择的行提交，资产和流水已重新加载。</p><dl>{Object.entries(typeof result === "object" && result !== null ? result : { result }).map(([key, value]) => <div key={key}><dt>{summaryLabels[key] || key}</dt><dd>{describe(value)}</dd></div>)}</dl><Button onClick={onClose}>完成</Button></div> : <>
      <label className="investment-upload"><FileSpreadsheet size={28} /><strong>{fileName || "选择账单或持仓文件"}</strong><span>Excel / CSV · 先预览，确认后才会入账</span><input aria-label="上传投资文件" type="file" accept=".xlsx,.csv" disabled={busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) void inspect(file); event.target.value = ""; }} /></label>
      {inspection?.duplicate_file && <div className="investment-callout" role="status"><strong>这个文件曾经导入过。</strong><p>文件内容指纹相同。请核对预览中的重复提示，只有你明确勾选并确认的有效行会提交。</p></div>}
      {inspection && !sheet && <p className="investment-callout">文件中没有可用的工作表，请选择其他文件。</p>}
      {sheet && <>
        <div className="investment-form-grid"><Field label="工作表"><select aria-label="工作表" disabled={busy} value={sheetName} onChange={(event) => { const next = inspection!.sheets.find((item) => item.name === event.target.value)!; setSheetName(next.name); setMapping(suggest(next)); invalidatePreview(); }}>{inspection!.sheets.map((item) => <option key={item.name}>{item.name}</option>)}</select></Field>
          <Field label="导入方式"><select aria-label="导入方式" disabled={busy} value={mode} onChange={(event) => { setMode(event.target.value); invalidatePreview(); }}><option value="transactions">历史交易流水</option><option value="snapshot">持仓快照（作为期初基线）</option><option value="reconcile">持仓核对（不入账）</option></select></Field>
          {mode !== "transactions" && <Field label="快照日期"><input aria-label="快照日期" type="date" required value={snapshotDate} disabled={busy} onChange={(event) => { setSnapshotDate(event.target.value); invalidatePreview(); }} /></Field>}
        </div>
        {mode === "reconcile" && <p className="investment-callout">仅比较该日期账本持仓与文件中的持仓，不会入账或修改记录。</p>}
        {mode === "snapshot" && <p className="investment-callout">快照只建立该日期的持仓基线，无法还原此前盈亏。完整收益需要补充历史流水与现金余额。</p>}
        <details className="investment-sample" open><summary>原始数据样本 · {sheet.name}</summary><div className="investment-table-scroll"><table><thead><tr>{sheet.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{sheet.rows.slice(0, 4).map((row, i) => <tr key={i}>{sheet.columns.map((column, columnIndex) => <td key={column}>{describe(Array.isArray(row) ? row[columnIndex] : ((row as { raw?: Record<string, unknown> })?.raw || row as Record<string, unknown>)?.[column])}</td>)}</tr>)}</tbody></table></div></details>
        <div className="investment-section-head"><h3>字段映射</h3><small>选择对应的原始列；没有的字段留空</small></div>
        <div className="investment-mapping-grid">{mappingFields.map(([key, label]) => <Field key={key} label={label}><select aria-label={`映射${label}`} disabled={busy} value={mapping[key] || ""} onChange={(event) => { setMapping((current) => ({ ...current, [key]: event.target.value })); invalidatePreview(); }}><option value="">不映射</option>{sheet.columns.map((column) => <option key={column}>{column}</option>)}</select></Field>)}</div>
        <Button variant="secondary" busy={busy} onClick={() => void generatePreview()}><Upload size={16} />生成导入预览</Button>
      </>}
      {preview && <section className="investment-preview" aria-label="导入预览"><div className="investment-section-head"><h3>核对导入预览</h3><span>已选 {selected.size} / {preview.length} 行</span></div>
        <p className="investment-note">疑似重复默认不勾选，你可逐行确认。存在错误或已匹配外部流水号的行不可提交。</p>
        {previewWarnings.map((warning, i) => <p className="investment-callout" key={i}>{warning}</p>)}
        <label className="investment-check"><input type="checkbox" checked={preview.some((row) => selectable(row) && !row.suspected_duplicate) && preview.filter((row) => selectable(row) && !row.suspected_duplicate).every((row) => selected.has(row.index))} disabled={busy || mode === "reconcile"} onChange={(event) => setSelected(event.target.checked ? new Set(preview.filter((row) => selectable(row) && !row.suspected_duplicate).map((row) => row.index)) : new Set())} />选择全部有效非重复行</label>
        <div className="investment-table-scroll"><table><thead><tr><th>选择</th><th>日期</th><th>记录</th><th>金额 / 数量</th><th>检查结果</th></tr></thead><tbody>{preview.map((row) => <tr key={row.index}><td><label className="investment-check"><input type="checkbox" aria-label={`导入第 ${row.index} 行`} checked={selected.has(row.index)} disabled={busy || mode === "reconcile" || !selectable(row)} onChange={(event) => setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(row.index); else next.delete(row.index); return next; })} /><span>{row.index}</span></label></td><td>{timestamp(row.record.occurred_at, row.record.date_only)}</td><td>{row.record.action ? recordActionName(row.record.action) : "快照"}<small>{row.record.name || row.record.symbol || row.record.currency || "—"}</small></td><td>{amount(row.record.amount ?? row.record.quantity)} {row.record.amount != null ? row.record.currency : ""}</td><td>{errorsOf(row).map((error, i) => <p key={i} className="investment-error-text">{error}</p>)}{row.external_duplicate ? <span className="investment-badge investment-warning">外部流水号重复</span> : row.suspected_duplicate ? <span className="investment-badge investment-warning">疑似重复 · 请确认</span> : !errorsOf(row).length ? <span className="investment-badge">有效</span> : null}</td></tr>)}</tbody></table></div>
        {!preview.length && <p className="investment-empty">没有可导入的数据，请检查工作表与字段映射。</p>}
        {mode === "reconcile" ? <><h3>持仓核对结果</h3>{discrepancies.length ? <div className="investment-table-scroll"><table><thead><tr><th>市场 / 代码</th><th>文件数量</th><th>账本数量</th></tr></thead><tbody>{discrepancies.map((item, i) => <tr key={i}><td>{marketName(String(item.market))} · {String(item.symbol)}</td><td>{amount(item.imported_quantity, false, 4)}</td><td>{amount(item.ledger_quantity, false, 4)}</td></tr>)}</tbody></table></div> : <p className="investment-note">有效预览行与账本持仓数量一致；错误行未参与核对。</p>}<Button variant="secondary" onClick={onClose}>完成核对</Button></> : <div className="investment-modal-actions"><Button variant="secondary" disabled={busy} onClick={onClose}>取消</Button><Button busy={busy} disabled={!selected.size} onClick={() => void confirm()}>确认导入 {selected.size} 行</Button></div>}
      </section>}
      <ErrorBox error={error} />{busy && <p role="status" className="investment-note">正在处理，请稍候…</p>}
    </>}
  </div></Modal>;
}
