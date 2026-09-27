import { useMemo } from "react";
import ReactEChartsCore from "echarts-for-react/esm/core";
import type { LeadMae } from "@contract";
import { chartChrome, cssVar, echarts } from "../../lib/echarts";

type Point = { name: string; value: number | null; n: number };

/** Онлайн-MAE по минуте упреждения 11…15: насколько прогноз хуже на дальнем краю окна. */
export function LeadMaeChart({ buckets }: { buckets: LeadMae[] }) {
  const option = useMemo(() => {
    const ui = chartChrome();
    const bar = cssVar("--text-faint");
    return {
      animation: false,
      grid: { left: 4, right: 4, top: 18, bottom: 20 },
      tooltip: {
        ...ui.tooltip,
        trigger: "axis",
        axisPointer: { type: "shadow", shadowStyle: { color: "rgba(255,255,255,0.04)" } },
        formatter: (p: { name: string; data: Point }[]) => {
          const d = p[0].data;
          if (d.value == null) return `упреждение ${d.name} мин: сверенных прогнозов нет`;
          return `упреждение ${d.name} мин: MAE <b>${Math.round(d.value)} с</b><br/><span style="color:${ui.dim}">сверено прогнозов: ${d.n}</span>`;
        },
      },
      xAxis: {
        type: "category",
        data: buckets.map((b) => String(b.lead_min)),
        axisLine: { lineStyle: { color: ui.line } },
        axisTick: { show: false },
        axisLabel: { color: ui.dim, fontFamily: ui.mono, fontSize: 11 },
      },
      yAxis: { type: "value", show: false, min: 0 },
      series: [
        {
          type: "bar",
          data: buckets.map((b): Point => ({ name: String(b.lead_min), value: b.mae_s, n: b.n })),
          barWidth: "62%",
          itemStyle: { color: bar, borderRadius: [2, 2, 0, 0] },
          label: {
            show: true,
            position: "top",
            color: ui.text,
            fontFamily: ui.mono,
            fontSize: 11,
            formatter: (p: { value: number | null }) => (p.value == null ? "—" : String(Math.round(p.value))),
          },
        },
      ],
    };
  }, [buckets]);

  return <ReactEChartsCore echarts={echarts} option={option} notMerge style={{ height: 96, width: "100%" }} />;
}
