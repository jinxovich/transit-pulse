import { useMemo } from "react";
import ReactEChartsCore from "echarts-for-react/esm/core";
import type { LeadBucket } from "@contract";
import { chartChrome, cssVar, echarts } from "../../lib/echarts";

/** Гистограмма упреждения алертов: сколько алертов создано за 10…15 мин до события. */
export function LeadHistogram({ buckets }: { buckets: LeadBucket[] }) {
  const option = useMemo(() => {
    const ui = chartChrome();
    const accent = cssVar("--risk-early");
    return {
      animation: false,
      grid: { left: 4, right: 4, top: 18, bottom: 20 },
      tooltip: {
        ...ui.tooltip,
        trigger: "axis",
        axisPointer: { type: "shadow", shadowStyle: { color: "rgba(255,255,255,0.04)" } },
        formatter: (p: { name: string; value: number }[]) =>
          `за ${p[0].name} мин до события: <b>${p[0].value}</b>`,
      },
      xAxis: {
        type: "category",
        data: buckets.map((b) => String(b.lead_min)),
        axisLine: { lineStyle: { color: ui.line } },
        axisTick: { show: false },
        axisLabel: { color: ui.dim, fontFamily: ui.mono, fontSize: 11 },
      },
      yAxis: { type: "value", show: false },
      series: [
        {
          type: "bar",
          data: buckets.map((b) => b.count),
          barWidth: "62%",
          itemStyle: { color: accent, borderRadius: [2, 2, 0, 0] },
          label: {
            show: true,
            position: "top",
            color: ui.dim,
            fontFamily: ui.mono,
            fontSize: 11,
            formatter: (p: { value: number }) => (p.value ? String(p.value) : ""),
          },
        },
      ],
    };
  }, [buckets]);

  return <ReactEChartsCore echarts={echarts} option={option} notMerge style={{ height: 110, width: "100%" }} />;
}
