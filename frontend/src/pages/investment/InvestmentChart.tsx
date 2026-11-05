import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, TrendingUp } from "lucide-react";
import { ErrorBox } from "../../components/ui";
import { amount, numeric, periods, investmentRequest, timestamp, tone, type CurvePoint, type Period } from "./model";

export default function InvestmentChart({ metric, title }: { metric: "total_assets" | "cumulative_pnl" | "time_weighted_return"; title: string }) {
  const isRate = metric === "time_weighted_return";
  const isReturn = metric !== "total_assets";
  const [period, setPeriod] = useState<Period>("month");
  const [selected, setSelected] = useState<number | null>(null);
  const gradient = useId().replace(/:/g, "");
  const curve = useQuery({ queryKey: ["investment", "curve", period], queryFn: () => investmentRequest<{ points: CurvePoint[]; history_note?: string; return_note?: string }>(`/curve?period=${period}`), retry: 1, refetchInterval: 60000 });
  const now = Date.now();
  const points = (curve.data?.points || []).filter((point) => Number.isFinite(Date.parse(point.occurred_at)) && Date.parse(point.occurred_at) <= now).sort((a, b) => Date.parse(a.occurred_at) - Date.parse(b.occurred_at));
  const valid = points.map((point, index) => ({ point, index, value: numeric(point[metric]) })).filter((item) => item.value !== null);
  const values = valid.map((item) => item.value!);
  const seriesColor = metric === "total_assets" || !values.at(-1) ? "#7960b8" : values.at(-1)! > 0 ? "#c35761" : "#267c68";
  const low = values.length ? Math.min(...values) : 0;
  const high = values.length ? Math.max(...values) : 1;
  const padding = high === low ? Math.max(Math.abs(high) * .02, isRate ? .01 : 1) : (high - low) * .15;
  const min = low - padding, max = high + padding;
  const dateValues = points.map((point) => Date.parse(point.occurred_at));
  const firstTime = dateValues[0], lastTime = dateValues.at(-1)!;
  const timeScale = dateValues.every(Number.isFinite) && lastTime > firstTime;
  const x = (index: number) => 68 + (timeScale ? (dateValues[index] - firstTime) / (lastTime - firstTime) : index / Math.max(points.length - 1, 1)) * 604;
  const y = (value: number) => 204 - (value - min) / (max - min) * 170;
  const segments: { index: number; value: number }[][] = [];
  points.forEach((point, index) => {
    const value = numeric(point[metric]);
    if (value === null) return;
    const previous = points[index - 1];
    const minuteGap = previous && (point.kind === "minute" || previous.kind === "minute") && Date.parse(point.occurred_at) - Date.parse(previous.occurred_at) > 150000;
    const dailyGap = previous && point.kind === "daily" && Date.parse(point.occurred_at) - Date.parse(previous.occurred_at) > 36 * 3600000;
    if (!index || numeric(previous[metric]) === null || point.break_before || minuteGap || dailyGap) segments.push([]);
    segments.at(-1)!.push({ index, value });
  });
  const currentIndex = selected !== null && points[selected] && numeric(points[selected][metric]) !== null ? selected : valid.at(-1)?.index;
  const current = currentIndex === undefined ? undefined : points[currentIndex];
  const latestWarning = points.at(-1)?.twr_warning;
  const step = (direction: number) => {
    const cursor = valid.findIndex((item) => item.index === currentIndex);
    setSelected(valid[Math.max(0, Math.min(valid.length - 1, cursor + direction))]?.index ?? null);
  };
  return <section className="investment-panel investment-chart" aria-label={title}>
    <div className="investment-section-head"><h2 title={isRate ? curve.data?.return_note : undefined}>{title}{isRate && <small>跟踪以来</small>}</h2><TrendingUp size={19} /></div>
    <div className="investment-periods" role="group" aria-label={`${title}时间范围`}>
      {periods.map(([key, label]) => <button key={key} aria-pressed={period === key} onClick={() => { setPeriod(key); setSelected(null); }}>{label}</button>)}
    </div>
    <ErrorBox error={curve.error} />
    {curve.isPending ? <div className="investment-chart-empty" role="status">正在加载曲线…</div> : !valid.length ? <div className="investment-chart-empty"><TrendingUp size={28} /><p>暂无可用的{title}数据</p><small>{isRate ? "需要完整的期初资产市值及出入金时估值，才能衔接收益率。" : "录入记录并补齐估值后，曲线会显示在这里。"}</small></div> : <>
      <div className="investment-chart-readout" aria-live="polite"><strong className={isReturn ? tone(current?.[metric]) : undefined}>{isRate ? `${amount(current?.[metric], true)}%` : `¥ ${amount(current?.[metric], isReturn)}`}</strong><span>{timestamp(current?.occurred_at)}{isRate && current?.twr_estimated ? " · 估算" : ""}{current?.complete === false ? " · 数据不完整" : ""}{current?.kind === "minute" ? " · 盘中采样" : current?.kind && !["market", "valuation", "daily", "snapshot"].includes(current.kind) ? " · 记录事件" : ""}</span></div>
      {isRate && numeric(points.at(-1)?.[metric]) === null && <p className="investment-note" title={latestWarning || undefined}>最新值缺失 · 显示最近有效值</p>}
      <svg viewBox="0 0 704 246" role="img" aria-label={`${title}交互曲线`} tabIndex={0} onKeyDown={(event) => { if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); step(event.key === "ArrowLeft" ? -1 : 1); } }} onPointerMove={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        const coordinate = (event.clientX - rect.left) / rect.width * 704;
        setSelected(valid.reduce((nearest, item) => Math.abs(x(item.index) - coordinate) < Math.abs(x(nearest) - coordinate) ? item.index : nearest, valid[0].index));
      }}>
        <title>{title}，使用左右方向键查看各时间点</title>
        <defs><linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={seriesColor} stopOpacity=".2" /><stop offset="100%" stopColor="white" stopOpacity="0" /></linearGradient></defs>
        {[0, 1, 2, 3].map((row) => { const value = min + (max - min) * row / 3; return <g key={row}><line x1="68" x2="672" y1={y(value)} y2={y(value)} stroke="#ebe8ee" strokeDasharray="3 5" /><text x="59" y={y(value) + 4} textAnchor="end" fill="#807889" fontSize="11">{isRate ? `${amount(value)}%` : Math.abs(value) >= 10000 ? `${amount(value / 10000, false, 1)}万` : amount(value, false, 0)}</text></g>; })}
        {segments.map((segment, i) => { const line = segment.map((item, index) => `${index ? "L" : "M"}${x(item.index)},${y(item.value)}`).join(" "); return <g key={i}><path d={`${line} L${x(segment.at(-1)!.index)},204 L${x(segment[0].index)},204 Z`} fill={`url(#${gradient})`} /><path d={line} fill="none" stroke={seriesColor} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" /></g>; })}
        {valid.map(({ index, point, value }) => <circle key={index} cx={x(index)} cy={y(value!)} r={valid.length === 1 || point.kind === "transaction" || !point.complete ? 4 : 2} fill={!point.complete || (isRate && point.twr_estimated) ? "#bd842e" : seriesColor} />)}
        {currentIndex !== undefined && current && <g><line x1={x(currentIndex)} x2={x(currentIndex)} y1="26" y2="204" stroke="#aba0be" strokeDasharray="4 4" /><circle cx={x(currentIndex)} cy={y(numeric(current[metric])!)} r="5" fill={seriesColor} stroke="white" strokeWidth="2" /></g>}
        <text x="68" y="234" fill="#807889" fontSize="11">{timestamp(points[0].occurred_at, period !== "today")}</text><text x="672" y="234" textAnchor="end" fill="#807889" fontSize="11">{timestamp(points.at(-1)?.occurred_at, period !== "today")}</text>
      </svg>
      <div className="investment-chart-footer"><span title="缺失区间不连线">{segments.length > 1 || points.some((point) => !point.complete || numeric(point[metric]) === null) ? "数据有缺口" : ""}</span><div><button aria-label={`${title}上一个点`} onClick={() => step(-1)} disabled={currentIndex === valid[0]?.index}><ArrowLeft size={15} /></button><button aria-label={`${title}下一个点`} onClick={() => step(1)} disabled={currentIndex === valid.at(-1)?.index}><ArrowRight size={15} /></button></div></div>
    </>}
    {isRate && !valid.length && latestWarning && <p className="investment-note">{latestWarning}</p>}
  </section>;
}
