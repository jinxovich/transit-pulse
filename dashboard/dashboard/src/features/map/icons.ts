import type { Map as MlMap } from "maplibre-gl";
import type { RiskLevel, RiskColors } from "@contract";

const SIZE = 36; // размер иконки в CSS-пикселях
const PR = 2; // рисуем в 2 раза детальнее чётко на Retina

// Те же фигуры, что в RiskGlyph, только крупнее. Центр в (0, 0).
const SHAPES: Record<RiskLevel, string> = {
    red: "M 0 -10 L 9.5 7.5 L -9.5 7.5 Z",
    yellow: "M 0 -9 L 7.5 0 L 0 9 L -7.5 0 Z",
    green: "M -6.5 0 A 6.5 6.5 0 1 0 6.5 0 A 6.5 6.5 0 1 0 -6.5 0 Z",
    early: "M -5.5 0 A 5.5 5.5 0 1 0 5.5 0 A 5.5 5.5 0 1 0 -5.5 0 Z",
    none: "M -4 0 A 4 4 0 1 0 4 0 A 4 4 0 1 0 -4 0 Z",
    };

    // Насколько фигура выступает вверх - туда ставим «клюв» курса.
    const TOP: Record<RiskLevel, number> = { red: 10, yellow: 9, green: 6.5, early: 5.5, none: 4 };

    /** Имя иконки, по которому слой карты её найдёт. */
    export function iconName(risk: RiskLevel, stale: boolean, unknown: boolean, heading: boolean) {
    return `${risk}-${stale ? "stale" : "live"}-${unknown ? "u" : "k"}-${heading ? "h" : "n"}`;
    }

    function draw(risk: RiskLevel, color: string, stale: boolean, unknown: boolean, heading: boolean) {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = SIZE * PR;
    const ctx = canvas.getContext("2d")!;
    ctx.scale(PR, PR);
    ctx.translate(SIZE / 2, SIZE / 2); // (0, 0) - центр холста
    ctx.globalAlpha = stale ? 0.4 : 1; // нет свежих данных 

    // «Клюв» — маленький треугольник над фигурой, показывает направление движения.
    if (heading) {
        const top = TOP[risk];
        ctx.beginPath();
        ctx.moveTo(0, -top - 6);
        ctx.lineTo(3.5, -top - 0.5);
        ctx.lineTo(-3.5, -top - 0.5);
        ctx.closePath();
        ctx.fillStyle = color;
        ctx.fill();
    }

    const shape = new Path2D(SHAPES[risk]);
    // Тёмная обводка, чтобы маркер не терялся на подложке
    ctx.lineWidth = 3;
    ctx.strokeStyle = "#0B0E13";
    ctx.stroke(shape);
    if (risk === "early") {
        ctx.lineWidth = 2.5;
        ctx.strokeStyle = color;
        ctx.stroke(shape);
    } else {
        ctx.fillStyle = color;
        ctx.fill(shape);
    }

    // Пунктирное кольцо: борт не найден в справочнике или нет свежих данных.
    if (unknown || stale) {
        ctx.globalAlpha = 1;
        ctx.setLineDash([2.5, 2]);
        ctx.lineWidth = 1.4;
        ctx.strokeStyle = "#C3C9D3";
        ctx.beginPath();
        ctx.arc(0, 0, TOP[risk] + 3, 0, Math.PI * 2);
        ctx.stroke();
    }

    return ctx.getImageData(0, 0, SIZE * PR, SIZE * PR);
    }

    const RISKS: RiskLevel[] = ["red", "yellow", "green", "early", "none"];
    const FLAGS = [false, true];

    /** Нарисовать и зарегистрировать в карте все варианты иконок. */
    export function registerIcons(map: MlMap, colors: RiskColors) {
    for (const risk of RISKS)
        for (const stale of FLAGS)
        for (const unknown of FLAGS)
            for (const heading of FLAGS) {
            const name = iconName(risk, stale, unknown, heading);
            const image = draw(risk, colors[risk], stale, unknown, heading);
            if (map.hasImage(name)) map.updateImage(name, image);
            else map.addImage(name, image, { pixelRatio: PR });
            }
}