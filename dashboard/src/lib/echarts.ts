// ECharts по частям: только то, что рисуем, — бандл не тащит весь пакет.
import * as echarts from "echarts/core";
import { BarChart, CustomChart, LineChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  LineChart,
  ScatterChart,
  BarChart,
  CustomChart,
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

export { echarts };

/** Значение CSS-токена: canvas ECharts не понимает var(--…), цвета риска приходят из /config. */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Общий вид подсказки и подписей под тёмный пульт. */
export function chartChrome() {
  return {
    text: cssVar("--text"),
    dim: cssVar("--text-dim"),
    faint: cssVar("--text-faint"),
    line: cssVar("--line"),
    surface2: cssVar("--surface-2"),
    mono: cssVar("--font-mono"),
    sans: cssVar("--font-sans"),
    tooltip: {
      backgroundColor: cssVar("--surface-2"),
      borderColor: cssVar("--line"),
      borderWidth: 1,
      padding: [8, 10],
      textStyle: { color: cssVar("--text"), fontFamily: cssVar("--font-sans"), fontSize: 12 },
      extraCssText: "box-shadow: 0 8px 24px rgba(0,0,0,.45); border-radius: 8px;",
    },
  };
}
