import type { RiskLevel } from "@contract";


export function RiskGlyph({risk, size=14}:{risk: RiskLevel, size?: number}){
    const color = `var(--risk-${risk})`;
    return(
    <svg width={size} height={size} viewBox="-8 -8 16 16">
        <rect x={-6} y={-6} width={12} height={12} fill={color} />
    </svg>
    )
}