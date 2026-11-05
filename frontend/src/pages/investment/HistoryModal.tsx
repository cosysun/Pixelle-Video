import { useQuery } from "@tanstack/react-query";
import { Button, ErrorBox, Loading, Modal } from "../../components/ui";
import { recordActionName, describe, investmentRequest, timestamp, type InvestmentRecord } from "./model";

export default function HistoryModal({ record, onClose }: { record: InvestmentRecord; onClose: () => void }) {
  const history = useQuery({ queryKey: ["investment", "revisions", record.id], queryFn: () => investmentRequest<{ items?: Record<string, unknown>[]; revisions?: Record<string, unknown>[] } | Record<string, unknown>[]>(`/records/${encodeURIComponent(record.id)}/revisions`), retry: 1 });
  const data = history.data;
  const revisions = Array.isArray(data) ? data : data?.revisions || data?.items || [];
  const labels: Record<string, string> = { action: "类型", occurred_at: "发生时间", date_only: "仅日期", symbol: "代码", name: "名称", market: "市场", currency: "币种", settlement_currency: "结算币种", quantity: "数量", price: "价格", amount: "金额", fee: "费用", settlement_amount: "实际结算金额", settlement_fee: "实际结算费用", fx_rate: "汇率", note: "备注", external_id: "流水号", voided: "已作废", voided_at: "作废时间", status: "状态", revision: "版本", version: "版本" };
  return <Modal title="记录修订历史" onClose={onClose} wide><div className="investment-history"><p className="investment-note">{recordActionName(record.action)} · {record.name || record.symbol || record.currency} · {timestamp(record.occurred_at, record.date_only)}。修改与作废保留历史版本。</p><ErrorBox error={history.error} />{history.isPending ? <Loading /> : !revisions.length ? <p className="investment-empty">暂无修订历史</p> : <ol>{revisions.map((revision, i) => {
    const details = revision.payload || revision.record || revision.data || revision.snapshot || revision.after || revision;
    return <li key={String(revision.id || i)}><div className="investment-section-head"><strong>版本 {String(revision.revision || revision.version || i + 1)}{revision.operation ? ` · ${{ create: "创建", update: "编辑", void: "作废", delete: "作废" }[String(revision.operation)] || revision.operation}` : ""}</strong><span>{timestamp(String(revision.occurred_at || revision.created_at || revision.changed_at || revision.updated_at || ""))}</span></div><dl>{Object.entries(typeof details === "object" && details ? details : { value: details }).filter(([, value]) => value === null || typeof value !== "object").filter(([key]) => !["id", "created_at", "updated_at"].includes(key)).map(([key, value]) => <div key={key}><dt>{labels[key] || key}</dt><dd>{key === "action" ? recordActionName(String(value)) : describe(value)}</dd></div>)}</dl></li>;
  })}</ol>}<div className="investment-modal-actions"><Button variant="secondary" onClick={onClose}>关闭历史</Button></div></div></Modal>;
}
