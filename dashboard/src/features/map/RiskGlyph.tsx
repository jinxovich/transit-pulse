import type { RiskLevel } from "@contract";
import type { ReactElement } from "react";

export function RiskGlyph({risk, size=14}:{risk: RiskLevel, size?: number}){
    const color = `var(--risk-${risk})`;
    let shape: ReactElement;
    switch (risk) {
    case "red":
        shape = <path d="M 0 -6 L 6 5 L -6 5 Z" fill={color} />;
        break;
    case "yellow":
        shape = <path d="M 0 -6 L 5 0 L 0 6 L -5 0 Z" fill={color} />;
        break;
    case "green":
        shape = <circle cx={0} cy={0} r={5} fill={color} />;
        break;
    case "early":
        shape = <circle cx={0} cy={0} r={4.5} fill="none" stroke={color} strokeWidth={2} />;
        break;
    default:
        shape = <circle cx={0} cy={0} r={3.5} fill={color} />;
    }
    return(
    <svg width={size} height={size} viewBox="-8 -8 16 16">
        {shape}
    </svg>
    )
}