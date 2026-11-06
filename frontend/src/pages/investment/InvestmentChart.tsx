import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, TrendingUp } from "lucide-react";
import { ErrorBox } from "../../components/ui";
import { amount, compoundedReturnNote, curveTimestamp, dailyReturnNote, numeric, periods, investmentRequest, timestamp, tone, type CurvePoint, type Period } from "./model";

export default function InvestmentChart({ metric, title }: { metric: "total_assets" | "cumulative_pnl" | "compounded_return"; title: string }) {
  const isRate = metric === "compounded_return";
  const isReturn = metric !== "total_assets";
  const [period, setPeriod] = useState<Period>(isRate ? "today" : "month");
  const isDailyRate = isRate && period === "today";
  const valueMetric = isDailyRate ? "daily_return" : metric;
  const chartTitle = isRate ? isDailyRate ? "交易日收益率走势" : "复利累计收益率走势" : title;
  const rateEstimated = (point: CurvePoint) => isDailyRate ? point.daily_return_estimated : point.twr_estimated;
  const [selected, setSelected] = useState<number | null>(null);
  const gradient = useId().replace(/:/g, "");
  const curve = useQuery({ queryKey: ["investment", "curve", period], queryFn: () => investmentRequest<{ points: CurvePoint[]; trading_date?: string; return_note?: string }>(`/curve?period=${period}`), retry: 1, refetchInterval: 60000 });
  const now = Date.now();
  const chartPoints = curve.data?.points || [];
  const points = chartPoints.filter((point) => Number.isFinite(Date.parse(point.occurred_at)) && Date.parse(point.occurred_at) <= now).sort((a, b) => Date.parse(a.occurred_at) - Date.parse(b.occurred_at));
  const valid = points.map((point, index) => ({ point, index, value: numeric(point[valueMetric]) })).filter((item) => item.value !== null);
  const values = valid.map((item) => item.value!);
  const seriesColor = metric === "total_assets" || !values.at(-1) ? "#7960b8" : values.at(-1)! > 0 ? "#c35761" : "#267c68";
  const low = values.length ? Math.min(...values) : 0;
  const high = values.length ? Math.max(...values) : 1;
  const padding = high === low ? Math.max(Math.abs(high) * .02, isRate ? .01 : 1) : (high - low) * .15;
  const min = low - padding, max = high + padding;
  const positions = new Map(valid.map((item, index) => [item.index, index]));
  const x = (index: number) => 68 + (valid.length === 1 ? .5 : (positions.get(index) || 0) / Math.max(valid.length - 1, 1)) * 604;
  const y = (value: number) => 204 - (value - min) / (max - min) * 170;
  const segments = valid.length ? [valid.map((item) => ({ index: item.index, value: item.value! }))] : [];
  const currentIndex = selected !== null && points[selected] && numeric(points[selected][valueMetric]) !== null ? selected : valid.at(-1)?.index;
  const current = currentIndex === undefined ? undefined : points[currentIndex];
  const latestWarning = isDailyRate ? "上一交易日日终资产或该时点估值、出入金数据缺失，无法计算交易日收益率。" : points.at(-1)?.compounded_warning || "缺少期初收益率、资产或出入金边界估值，无法衔接复利累计收益率。";
  const step = (direction: number) => {
    const cursor = valid.findIndex((item) => item.index === currentIndex);
    setSelected(valid[Math.max(0, Math.min(valid.length - 1, cursor + direction))]?.index ?? null);
  };
  return <section className="investment-panel investment-chart" aria-label={title}>
    <div className="investment-section-head"><h2 title={isRate ? isDailyRate ? dailyReturnNote : `${curve.data?.return_note || ""}${compoundedReturnNote}` : undefined}>{chartTitle}</h2><TrendingUp size={19} /></div>
    <div className="investment-periods" role="group" aria-label={`${title}时间范围`}>
      {periods.map(([key, label]) => <button key={key} aria-pressed={period === key} onClick={() => { setPeriod(key); setSelected(null); }}>{label}</button>)}
    </div>
    {period === "today" && curve.data?.trading_date && <p className="investment-chart-trading-date">{curve.data.trading_date.replaceAll("-", "/")} 交易日</p>}
    <ErrorBox error={curve.error} />
    {curve.isPending ? <div className="investment-chart-empty" role="status">正在加载曲线…</div> : !valid.length ? <div className="investment-chart-empty"><TrendingUp size={28} /><p>暂无可用的{chartTitle}数据</p><small>{isRate ? isDailyRate ? "需要完整的估值、出入金记录和大于零的上一交易日日终资产，才能计算交易日收益率。" : "需要完整的期初收益率、资产市值及出入金时估值，才能衔接复利累计收益率。" : "录入记录并补齐估值后，曲线会显示在这里。"}</small></div> : <>
      <div className="investment-chart-readout" aria-live="polite"><strong className={isReturn ? tone(current?.[valueMetric]) : undefined}>{isRate ? `${amount(current?.[valueMetric], true)}%` : `¥ ${amount(current?.[valueMetric], isReturn)}`}</strong><span title={current?.kind === "daily" ? `估值截止：${timestamp(current.occurred_at)}` : undefined}>{curveTimestamp(current)}{current?.quote_delayed ? " · 港股行情约延迟 15 分钟" : ""}{isRate && current && rateEstimated(current) ? " · 估算" : ""}{current?.complete === false ? " · 数据不完整" : ""}{current?.kind === "minute" ? " · 盘中采样" : current?.kind === "close" ? " · 收盘估值" : current?.kind === "latest_sample" ? " · 最近采样" : current?.kind && !["market", "valuation", "daily", "snapshot", "baseline", "close"].includes(current.kind) ? " · 记录事件" : ""}</span></div>
      {isRate && numeric(points.at(-1)?.[valueMetric]) === null && <p className="investment-note" title={latestWarning || undefined}>最新值缺失 · 显示最近有效值</p>}
      <svg viewBox="0 0 704 246" role="img" aria-label={`${title}交互曲线`} tabIndex={0} onKeyDown={(event) => { if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); step(event.key === "ArrowLeft" ? -1 : 1); } }} onPointerMove={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        const coordinate = (event.clientX - rect.left) / rect.width * 704;
        setSelected(valid.reduce((nearest, item) => Math.abs(x(item.index) - coordinate) < Math.abs(x(nearest) - coordinate) ? item.index : nearest, valid[0].index));
      }}>
        <title>{title}，按有效数据点顺序连接，时间间隔已收起；使用左右方向键查看真实时间与金额</title>
        <defs><linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={seriesColor} stopOpacity=".2" /><stop offset="100%" stopColor="white" stopOpacity="0" /></linearGradient></defs>
        {[0, 1, 2, 3].map((row) => { const value = min + (max - min) * row / 3; return <g key={row}><line x1="68" x2="672" y1={y(value)} y2={y(value)} stroke="#ebe8ee" strokeDasharray="3 5" /><text x="59" y={y(value) + 4} textAnchor="end" fill="#807889" fontSize="11">{isRate ? `${amount(value)}%` : Math.abs(value) >= 10000 ? `${amount(value / 10000, false, 1)}万` : amount(value, false, 0)}</text></g>; })}
        {segments.map((segment, i) => { const line = segment.map((item, index) => `${index ? "L" : "M"}${x(item.index)},${y(item.value)}`).join(" "); return <g key={i}><path d={`${line} L${x(segment.at(-1)!.index)},204 L${x(segment[0].index)},204 Z`} fill={`url(#${gradient})`} /><path d={line} fill="none" stroke={seriesColor} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" /></g>; })}
        {valid.map(({ index, point, value }) => <circle key={index} cx={x(index)} cy={y(value!)} r={valid.length === 1 || point.kind === "transaction" || !point.complete ? 4 : 2} fill={!point.complete || (isRate && rateEstimated(point)) ? "#bd842e" : seriesColor} />)}
        {currentIndex !== undefined && current && <g><line x1={x(currentIndex)} x2={x(currentIndex)} y1="26" y2="204" stroke="#aba0be" strokeDasharray="4 4" /><circle cx={x(currentIndex)} cy={y(numeric(current[valueMetric])!)} r="5" fill={seriesColor} stroke="white" strokeWidth="2" /></g>}
        <text x="68" y="234" fill="#807889" fontSize="11">{curveTimestamp(valid[0]?.point, period !== "today")}</text><text x="672" y="234" textAnchor="end" fill="#807889" fontSize="11">{curveTimestamp(valid.at(-1)?.point, period !== "today")}</text>
      </svg>
      <div className="investment-chart-footer"><span /><div><button aria-label={`${title}上一个点`} onClick={() => step(-1)} disabled={currentIndex === valid[0]?.index}><ArrowLeft size={15} /></button><button aria-label={`${title}下一个点`} onClick={() => step(1)} disabled={currentIndex === valid.at(-1)?.index}><ArrowRight size={15} /></button></div></div>
    </>}
  </section>;
}
