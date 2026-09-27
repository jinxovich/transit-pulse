import { useMemo } from "react";
import ReactEChartsCore from "echarts-for-react/esm/core";
import type { DeviationPoint, ForecastPoint, RiskLevel, Thresholds } from "@contract";
import { chartChrome, cssVar, echarts } from "../../lib/echarts";
import { formatDelay } from "../../lib/format";
import { formatSimTime, fromSimMs, simMs } from "../../lib/time";

interface Props {
  series: DeviationPoint[];
  forecast: ForecastPoint | null;
  forecastRisk: RiskLevel;
  thresholds: Thresholds;
  simTime: string | null;
}

const HOUR_MS = 60 * 60_000;
const Y_STEPS = [60, 120, 300, 600, 900, 1800];
const RECENT_POINTS = 10;

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return NaN;
  return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
}

/** Полупрозрачный вариант hex-цвета из /config. */
function alpha(hex: string, a: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

/** Подпись оси Y: минуты со знаком, без секунд — шаг сетки кратен минуте. */
function axisDelay(sec: number): string {
  if (sec === 0) return "0";
  return `${sec > 0 ? "+" : "−"}${Math.abs(sec) / 60} мин`;
}

function buildOption({ series, forecast, forecastRisk, thresholds, simTime }: Props) {
  const ui = chartChrome();
  const risk = {
    red: cssVar("--risk-red"),
    yellow: cssVar("--risk-yellow"),
    green: cssVar("--risk-green"),
    early: cssVar("--risk-early"),
    none: cssVar("--risk-none"),
  };
  const { red_delay_s: red, yellow_delay_s: yellow, early_delay_s: early } = thresholds;

  const line = series.map((p) => [simMs(p.t), p.dev_s]);
  const nowMs = simTime ? simMs(simTime) : line.at(-1)?.[0] ?? Date.now();
  const fc = forecast ? [simMs(forecast.t), forecast.delay_s, forecast.lo_s, forecast.hi_s] : null;

  const xMin = Math.min(nowMs - HOUR_MS, line[0]?.[0] ?? Infinity);
  const xMax = Math.max(nowMs + 5 * 60_000, (fc?.[0] ?? 0) + 3 * 60_000);

  // Шкала по 5–95 перцентилям: одиночный выброс GPS не сплющивает нитку; последние точки и прогноз видны всегда.
  const devs = line.map((p) => p[1]).sort((a, b) => a - b);
  const recent = line.slice(-RECENT_POINTS).map((p) => p[1]);
  const values = [
    quantile(devs, 0.05),
    quantile(devs, 0.95),
    ...recent,
    ...(fc ? [fc[2], fc[3]] : []),
    red + 30,
    early - 30,
  ].filter((v) => Number.isFinite(v));
  const span = Math.max(...values) - Math.min(...values);
  const step = Y_STEPS.find((s) => span / s <= 6) ?? 3600;
  const yMin = Math.floor(Math.min(...values) / step) * step;
  const yMax = Math.ceil(Math.max(...values) / step) * step;

  const fcColor = risk[forecastRisk === "none" ? "none" : forecastRisk];
  const last = line.at(-1);

  // Значения порогов — в легенде над графиком: подписи на линиях слипаются при крупной шкале.
  const threshold = (y: number, color: string) => ({
    yAxis: y,
    lineStyle: { color: alpha(color, 0.55), type: [3, 4] as number[], width: 1 },
    label: { show: false },
  });

  return {
    animation: false,
    textStyle: { fontFamily: ui.sans },
    grid: { left: 58, right: 16, top: 16, bottom: 28 },
    tooltip: {
      ...ui.tooltip,
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: ui.faint, width: 1 } },
      formatter: (params: { seriesIndex: number; value: number[] }[]) => {
        const p = params.find((x) => x.seriesIndex === 0 || x.seriesIndex === 3);
        if (!p) return "";
        const [t, v] = p.value;
        if (p.seriesIndex === 3 && fc) {
          return `<b>Прогноз на ${formatSimTime(fromSimMs(t))}</b><br/>${formatDelay(v)}` +
            `<br/><span style="color:${ui.dim}">от ${formatDelay(fc[2])} до ${formatDelay(fc[3])}</span>`;
        }
        return `${formatSimTime(fromSimMs(t))}<br/><b>${formatDelay(v)}</b> к графику`;
      },
    },
    xAxis: {
      type: "time",
      min: xMin,
      max: xMax,
      splitNumber: 6,
      axisLine: { lineStyle: { color: ui.line } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: {
        color: ui.dim,
        fontFamily: ui.mono,
        fontSize: 11,
        hideOverlap: true,
        formatter: (v: number) => formatSimTime(fromSimMs(v)),
      },
    },
    yAxis: {
      type: "value",
      min: yMin,
      max: yMax,
      interval: step,
      axisLabel: { color: ui.dim, fontFamily: ui.mono, fontSize: 11, formatter: axisDelay },
      splitLine: { lineStyle: { color: alpha(cssVar("--line"), 0.6) } },
    },
    visualMap: {
      show: false,
      type: "piecewise",
      seriesIndex: 0,
      dimension: 1,
      pieces: [
        { gt: red, color: risk.red },
        { gt: yellow, lte: red, color: risk.yellow },
        { gte: early, lte: yellow, color: risk.green },
        { lt: early, color: risk.early },
      ],
    },
    series: [
      {
        name: "Отклонение",
        type: "line",
        data: line,
        clip: true,
        showSymbol: false,
        lineStyle: { width: 2 },
        z: 3,
        markArea: {
          silent: true,
          data: [
            [{ yAxis: red, itemStyle: { color: alpha(risk.red, 0.07) } }, { yAxis: yMax }],
            [{ yAxis: yellow, itemStyle: { color: alpha(risk.yellow, 0.05) } }, { yAxis: red }],
            [{ yAxis: yMin, itemStyle: { color: alpha(risk.early, 0.05) } }, { yAxis: early }],
          ],
        },
        markLine: {
          silent: true,
          symbol: "none",
          data: [
            threshold(red, risk.red),
            threshold(yellow, risk.yellow),
            threshold(early, risk.early),
            {
              xAxis: nowMs,
              lineStyle: { color: ui.dim, type: "solid", width: 1 },
              label: { formatter: "сейчас", color: ui.dim, position: "insideEndTop", fontSize: 10 },
            },
          ],
        },
      },
      {
        name: "К прогнозу",
        type: "line",
        silent: true,
        data: fc && last ? [last, [fc[0], fc[1]]] : [],
        showSymbol: false,
        lineStyle: { color: alpha(fcColor, 0.6), type: [4, 4], width: 1.5 },
        z: 2,
      },
      {
        name: "Интервал",
        type: "custom",
        silent: true,
        data: fc ? [[fc[0], fc[2], fc[3]]] : [],
        z: 4,
        renderItem: (
          _: unknown,
          api: { value: (i: number) => number; coord: (v: number[]) => number[] },
        ) => {
          const lo = api.coord([api.value(0), api.value(1)]);
          const hi = api.coord([api.value(0), api.value(2)]);
          const cap = 7;
          const style = { stroke: fcColor, lineWidth: 2 };
          return {
            type: "group",
            children: [
              { type: "rect", shape: { x: lo[0] - cap, y: hi[1], width: cap * 2, height: lo[1] - hi[1] }, style: { fill: alpha(fcColor, 0.14) } },
              { type: "line", shape: { x1: lo[0], y1: lo[1], x2: hi[0], y2: hi[1] }, style },
              { type: "line", shape: { x1: lo[0] - cap, y1: lo[1], x2: lo[0] + cap, y2: lo[1] }, style },
              { type: "line", shape: { x1: hi[0] - cap, y1: hi[1], x2: hi[0] + cap, y2: hi[1] }, style },
            ],
          };
        },
      },
      {
        name: "Прогноз",
        type: "scatter",
        data: fc ? [[fc[0], fc[1]]] : [],
        symbolSize: 11,
        z: 5,
        itemStyle: { color: fcColor, borderColor: cssVar("--bg"), borderWidth: 2 },
        label: {
          show: true,
          position: "left",
          distance: 12,
          formatter: (p: { value: number[] }) => formatDelay(p.value[1]),
          color: fcColor,
          fontFamily: ui.mono,
          fontSize: 12,
          fontWeight: 500,
        },
      },
    ],
  };
}

/** «Нитка отклонения»: история за 60 мин, прогноз на цель с интервалом, пороги из /config. */
export function DeviationChart(props: Props) {
  const { series, forecast, forecastRisk, thresholds, simTime } = props;
  const option = useMemo(
    () => buildOption({ series, forecast, forecastRisk, thresholds, simTime }),
    [series, forecast, forecastRisk, thresholds, simTime],
  );
  return (
    <ReactEChartsCore
      echarts={echarts}
      option={option}
      notMerge
      lazyUpdate
      style={{ height: "100%", width: "100%" }}
      opts={{ renderer: "canvas" }}
    />
  );
}
