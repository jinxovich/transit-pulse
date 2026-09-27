import type { RiskLevel } from "@contract";
import { useQuery } from "@tanstack/react-query";
import { RiskGlyph } from "./RiskGlyph";
import { RISK_LABEL, riskHint } from "../../lib/labels";
import { fetchConfig } from "../../api/config";

const LEVELS: RiskLevel[] = ["red", "yellow", "green", "early", "none"];

/** Легенда карты: форма и цвет маркера = риск ТС, линии = проблемные перегоны. */
export function MapLegend() {
    const thresholds = useQuery({ queryKey: ["config"], queryFn: fetchConfig, staleTime: Infinity }).data?.thresholds;
    return (
        <details className="map-legend" open>
            <summary>Обозначения</summary>
            <ul className="legend-list">
                {LEVELS.map((risk) => (
                    <li key={risk} title={riskHint(risk, thresholds)}>
                        <RiskGlyph risk={risk} size={risk === "none" ? 12 : 14} />
                        <span>{RISK_LABEL[risk]}</span>
                    </li>
                ))}
                <li title="Пунктирное кольцо: от ТС давно нет координат или борт не опознан">
                    <svg width={14} height={14} viewBox="-8 -8 16 16">
                        <circle r={6.5} fill="none" stroke="#C3C9D3" strokeWidth={1.4} strokeDasharray="2.5 2" />
                    </svg>
                    <span>нет свежих данных</span>
                </li>
            </ul>
            <ul className="legend-list legend-lines">
                <li title="Перегон, где сейчас едет или куда подъезжает ТС с риском опоздания">
                    <span className="legend-line">
                        <i style={{ background: "var(--risk-red)" }} />
                        <i style={{ background: "var(--risk-yellow)" }} />
                    </span>
                    <span>перегон с риском</span>
                </li>
                <li title="Участок и остановка выбранного инцидента">
                    <span className="legend-line legend-line-selected" />
                    <span>выбранный инцидент</span>
                </li>
            </ul>
        </details>
    );
}
