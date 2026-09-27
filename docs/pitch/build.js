// Презентация Transit Pulse для питча (pptxgenjs).
// Сборка: npm i pptxgenjs && node docs/pitch/build.js docs/pitch/img docs/pitch/transit-pulse-pitch.pptx
// Шрифты IBM Plex Sans / Mono (OFL) — в docs/pitch/fonts, установите их перед показом .pptx.
const path = require("path");
const fs = require("fs");
const pptxgen = require("pptxgenjs");

const IMG = path.resolve(process.argv[2]);
const OUT = path.resolve(process.argv[3]);
const SIZES = JSON.parse(fs.readFileSync(path.join(IMG, "sizes.json"), "utf8"));

// ---------- токены (как в dashboard/src/styles/tokens.css)
const C = {
  bg: "0E1116", map: "0B0E13", surface: "151A22", surface2: "1C2330", line: "2A3342",
  text: "E6EAF0", dim: "8B93A1", faint: "5B6472",
  red: "E5484D", amber: "F2B134", green: "2FBF71", blue: "3E9BFF",
};
const SANS = "IBM Plex Sans";
const MONO = "IBM Plex Mono";
const W = 13.333, H = 7.5, M = 0.6;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.title = "Transit Pulse — питч";
pres.author = "Команда Transit Pulse";

// ---------- помощники
const img = (name) => path.join(IMG, name);
const aspect = (name) => SIZES[name].w / SIZES[name].h;

function text(slide, str, o) {
  slide.addText(str, {
    fontFace: o.mono ? MONO : SANS, color: C.text, fontSize: 14, margin: 0, valign: "top",
    isTextBox: true, ...o, mono: undefined,
  });
}

// Глиф риска, как маркеры на карте дашборда: ▲ red, ◆ amber, ● green, ○ blue.
function glyph(slide, kind, x, y, s) {
  const map = {
    red: { shape: pres.shapes.ISOSCELES_TRIANGLE, fill: C.red },
    amber: { shape: pres.shapes.DIAMOND, fill: C.amber },
    green: { shape: pres.shapes.OVAL, fill: C.green },
  };
  if (kind === "blue") {
    slide.addShape(pres.shapes.OVAL, { x, y, w: s, h: s, fill: { type: "none" }, line: { color: C.blue, width: 2 } });
    return;
  }
  const g = map[kind];
  slide.addShape(g.shape, { x, y, w: s, h: s, fill: { color: g.fill }, line: { type: "none" } });
}

function base(n, kicker, kind) {
  const s = pres.addSlide();
  s.background = { color: C.bg };
  if (kicker) {
    glyph(s, kind || "blue", M, 0.5, 0.16);
    text(s, `${String(n).padStart(2, "0")}  ·  ${kicker}`, {
      x: M + 0.3, y: 0.44, w: 8, h: 0.3, fontSize: 11, color: C.dim, mono: true, charSpacing: 2,
    });
    text(s, "TRANSIT PULSE", { x: W - M - 3, y: 0.44, w: 3, h: 0.3, fontSize: 10, color: C.faint, mono: true, align: "right", charSpacing: 3 });
  }
  return s;
}

function title(s, str, o = {}) {
  text(s, str, { x: M, y: 0.9, w: W - 2 * M, h: 1.1, fontSize: 34, bold: true, valign: "top", ...o });
}

function box(s, x, y, w, h, o = {}) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h, rectRadius: o.r ?? 0.08,
    fill: { color: o.fill || C.surface, transparency: o.transparency || 0 },
    line: o.line ? { color: o.line, width: o.lineW || 1, dashType: o.dash || "solid" } : { type: "none" },
    shadow: o.shadow ? { type: "outer", color: "000000", blur: 18, offset: 6, angle: 90, opacity: 0.45 } : undefined,
  });
}

function arrow(s, x1, y1, x2, y2, color, o = {}) {
  s.addShape(pres.shapes.LINE, {
    x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1) || 0.001, h: Math.abs(y2 - y1) || 0.001,
    flipH: x2 < x1, flipV: y2 < y1,
    line: { color, width: o.width || 2, endArrowType: o.noEnd ? undefined : "triangle", beginArrowType: o.both ? "triangle" : undefined, dashType: o.dash || "solid" },
  });
}

// =====================================================================
// 1. Титул
{
  const s = pres.addSlide();
  s.background = { color: C.bg };
  s.addImage({ path: img("overview.jpg"), x: 0, y: 0, w: W, h: H });
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: W, h: H, fill: { color: C.bg, transparency: 30 }, line: { type: "none" } });
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 8.2, h: H, fill: { color: C.bg, transparency: 12 }, line: { type: "none" } });
  text(s, "ХАКАТОН МОСКОВСКОГО ТРАНСПОРТА  ·  27.09.2026", { x: M, y: 0.7, w: 8, h: 0.3, fontSize: 11, color: C.dim, mono: true, charSpacing: 2 });
  text(s, "Transit Pulse", { x: M, y: 2.2, w: 7.6, h: 1.3, fontSize: 66, bold: true });
  text(s, "Предупреждаем диспетчера о задержке транспорта за 10–15 минут — до того, как она случилась", {
    x: M, y: 3.55, w: 6.9, h: 1.2, fontSize: 22, color: C.text,
  });
  const leg = [["red", "опоздание"], ["amber", "риск"], ["green", "в графике"], ["blue", "раньше графика"]];
  let lx = M;
  leg.forEach(([k, l]) => {
    glyph(s, k, lx, 5.47, 0.17);
    text(s, l, { x: lx + 0.27, y: 5.4, w: 1.9, h: 0.3, fontSize: 12, color: C.dim });
    lx += 0.27 + l.length * 0.095 + 0.4;
  });
  text(s, "NDTP  →  backend  →  ML  →  дашборд диспетчера", { x: M, y: 6.45, w: 7.5, h: 0.3, fontSize: 12, color: C.faint, mono: true });
  s.addNotes(
    "[~10 с] Добрый день! Мы — Transit Pulse: система, которая предупреждает диспетчера о задержке за 10–15 минут до того, как она случится, и говорит, почему и что делать."
  );
}

// =====================================================================
// 2. Проблема: таймлайн «сейчас» против «Transit Pulse»
{
  const s = base(1, "ПРОБЛЕМА", "red");
  title(s, "Диспетчер узнаёт об опоздании,\nкогда автобус уже опоздал");
  const X0 = 3.1, X1 = 12.6, T0 = -18, T1 = 8;
  const xt = (t) => X0 + ((t - T0) / (T1 - T0)) * (X1 - X0);
  const yA = 3.45, yB = 5.25;

  // вертикаль «плановое прибытие»
  s.addShape(pres.shapes.LINE, { x: xt(0), y: 2.55, w: 0, h: 3.55, line: { color: C.faint, width: 1, dashType: "dash" } });
  text(s, "плановое прибытие", { x: xt(0) - 1.2, y: 2.25, w: 2.4, h: 0.28, fontSize: 11, color: C.dim, align: "center", mono: true });

  // строка A — сегодня
  text(s, "Сегодня", { x: M, y: yA - 0.3, w: 2.3, h: 0.35, fontSize: 18, bold: true, color: C.text });
  text(s, "реакция постфактум", { x: M, y: yA + 0.08, w: 2.3, h: 0.3, fontSize: 12, color: C.dim });
  s.addShape(pres.shapes.LINE, { x: X0, y: yA, w: X1 - X0, h: 0, line: { color: C.line, width: 2 } });
  glyph(s, "red", xt(1.5) - 0.14, yA - 0.14, 0.28);
  text(s, "узнал об опоздании", { x: xt(1.5) - 1.2, y: yA - 0.62, w: 2.4, h: 0.3, fontSize: 12, color: C.red, align: "center", bold: true });
  [3.4, 5.0, 6.6].forEach((t, i) => {
    s.addShape(pres.shapes.OVAL, { x: xt(t) - 0.08, y: yA - 0.08, w: 0.16, h: 0.16, fill: { color: C.red, transparency: 25 + i * 22 }, line: { type: "none" } });
  });
  text(s, "эффект домино на маршруте", { x: xt(2.8), y: yA + 0.2, w: 2.6, h: 0.3, fontSize: 11, color: C.dim });

  // строка B — Transit Pulse
  text(s, "Transit Pulse", { x: M, y: yB - 0.3, w: 2.3, h: 0.35, fontSize: 18, bold: true, color: C.blue });
  text(s, "алерт заранее", { x: M, y: yB + 0.08, w: 2.3, h: 0.3, fontSize: 12, color: C.dim });
  s.addShape(pres.shapes.RECTANGLE, { x: xt(-15), y: yB - 0.32, w: xt(-10) - xt(-15), h: 0.64, fill: { color: C.blue, transparency: 78 }, line: { color: C.blue, width: 1 } });
  text(s, "окно алерта 10–15 мин", { x: xt(-15) - 0.6, y: yB - 0.68, w: xt(-10) - xt(-15) + 1.2, h: 0.3, fontSize: 11, color: C.blue, align: "center", mono: true });
  s.addShape(pres.shapes.LINE, { x: X0, y: yB, w: X1 - X0, h: 0, line: { color: C.line, width: 2 } });
  glyph(s, "blue", xt(-12) - 0.14, yB - 0.14, 0.28);
  arrow(s, xt(-12) + 0.2, yB, xt(0) - 0.08, yB, C.green, { width: 3 });
  text(s, "время на меру: придержать · резервный выпуск · объезд", { x: xt(-9.6), y: yB + 0.22, w: xt(0) - xt(-9.6) + 2.6, h: 0.3, fontSize: 12, color: C.green });

  // шкала
  [-15, -10, -5, 0, 5].forEach((t) => {
    text(s, t === 0 ? "0" : `${t > 0 ? "+" : "−"}${Math.abs(t)} мин`, { x: xt(t) - 0.6, y: 6.2, w: 1.2, h: 0.28, fontSize: 11, color: C.faint, align: "center", mono: true });
  });
  s.addNotes(
    "[~20 с] Сегодня диспетчер узнаёт об опоздании, когда автобус уже опоздал. Реакция постфактум — и задержка переходит на следующие рейсы, эффект домино. А придержать, дать резервный выпуск или пустить объездом нужно за 10–15 минут до события. Этого окна сейчас нет — мы его даём."
  );
}

// =====================================================================
// 3. Решение: архитектура
{
  const s = base(2, "РЕШЕНИЕ", "blue");
  title(s, "Ранний алерт с причиной и рекомендацией —\nна живом потоке телематики");

  // источник
  const sx = M, sy = 3.35, sw = 2.35, sh = 1.75;
  box(s, sx, sy, sw, sh, { fill: C.surface, line: C.line });
  text(s, "Поток NDTP", { x: sx + 0.22, y: sy + 0.2, w: sw - 0.4, h: 0.4, fontSize: 17, bold: true });
  text(s, "30 бортов · TCP :9201\nreplayer или официальный эмулятор", { x: sx + 0.22, y: sy + 0.68, w: sw - 0.35, h: 0.9, fontSize: 11, color: C.dim, lineSpacingMultiple: 1.1 });

  // контейнер docker compose
  const cx = 3.45, cy = 2.3, cw = W - M - 3.45, ch = 4.55;
  box(s, cx, cy, cw, ch, { fill: C.bg, line: C.faint, dash: "dash", r: 0.12 });
  text(s, "$ docker compose up", { x: cx + 0.25, y: cy + 0.18, w: 4, h: 0.3, fontSize: 12, color: C.dim, mono: true });
  text(s, "3 модуля · healthcheck · готово за 17.5 с", { x: cx + cw - 4.75, y: cy + 0.18, w: 4.5, h: 0.3, fontSize: 12, color: C.dim, mono: true, align: "right" });

  // backend
  const bx = 3.8, by = 2.95, bw = 3.9, bh = 3.55;
  box(s, bx, by, bw, bh, { fill: C.surface2, line: C.blue, lineW: 1.5, shadow: true });
  text(s, "backend", { x: bx + 0.3, y: by + 0.25, w: 2.5, h: 0.45, fontSize: 22, bold: true });
  text(s, ":8000", { x: bx + bw - 1.3, y: by + 0.32, w: 1.0, h: 0.35, fontSize: 12, color: C.dim, mono: true, align: "right" });
  text(s, [
    { text: "приём и разбор NDTP", options: { bullet: { code: "25B8" }, breakLine: true } },
    { text: "привязка к нитке графика", options: { bullet: { code: "25B8" }, breakLine: true } },
    { text: "признаки на момент T", options: { bullet: { code: "25B8" }, breakLine: true } },
    { text: "инциденты, REST + WebSocket", options: { bullet: { code: "25B8" } } },
  ], { x: bx + 0.3, y: by + 0.95, w: bw - 0.5, h: 2.3, fontSize: 14, color: C.text, paraSpaceAfter: 8 });

  // ml
  const mx = 8.75, my = 2.95, mw = 3.95, mh = 1.6;
  box(s, mx, my, mw, mh, { fill: C.surface2, line: C.amber, lineW: 1.5, shadow: true });
  text(s, "ml", { x: mx + 0.3, y: my + 0.2, w: 2, h: 0.45, fontSize: 22, bold: true });
  text(s, ":8001", { x: mx + mw - 1.3, y: my + 0.27, w: 1.0, h: 0.35, fontSize: 12, color: C.dim, mono: true, align: "right" });
  text(s, "CatBoost q10/q50/q90 + GRU (ONNX)\nSHAP-вклады → причина", { x: mx + 0.3, y: my + 0.78, w: mw - 0.45, h: 0.7, fontSize: 12, color: C.dim, lineSpacingMultiple: 1.1 });

  // dashboard
  const dx = 8.75, dy = 4.9, dw = 3.95, dh = 1.6;
  box(s, dx, dy, dw, dh, { fill: C.surface2, line: C.green, lineW: 1.5, shadow: true });
  text(s, "dashboard", { x: dx + 0.3, y: dy + 0.2, w: 2.5, h: 0.45, fontSize: 22, bold: true });
  text(s, ":8080", { x: dx + dw - 1.3, y: dy + 0.27, w: 1.0, h: 0.35, fontSize: 12, color: C.dim, mono: true, align: "right" });
  text(s, "карта · лента инцидентов\nкарточка · what-if", { x: dx + 0.3, y: dy + 0.78, w: dw - 0.45, h: 0.7, fontSize: 12, color: C.dim, lineSpacingMultiple: 1.1 });

  // стрелки
  arrow(s, sx + sw + 0.05, sy + sh / 2, bx - 0.05, sy + sh / 2, C.blue, { width: 2.5 });
  arrow(s, bx + bw + 0.05, my + mh / 2, mx - 0.05, my + mh / 2, C.amber, { width: 2, both: true });
  text(s, "HTTP", { x: bx + bw + 0.1, y: my + mh / 2 - 0.36, w: 0.9, h: 0.28, fontSize: 10, color: C.dim, mono: true, align: "center" });
  arrow(s, bx + bw + 0.05, dy + dh / 2, dx - 0.05, dy + dh / 2, C.green, { width: 2 });
  text(s, "WS", { x: bx + bw + 0.1, y: dy + dh / 2 - 0.36, w: 0.9, h: 0.28, fontSize: 10, color: C.dim, mono: true, align: "center" });
  s.addNotes(
    "[~25 с] Телематика приходит потоком по протоколу NDTP, как от настоящих бортов. Backend накладывает каждую точку на нитку графика и считает признаки, ML-ядро прогнозирует задержку на остановках через 10–15 минут, дашборд показывает алерт с причиной и рекомендацией. Три независимых модуля в Docker, поднимаются одной командой docker compose up."
  );
}

// =====================================================================
// 4. Аудит данных
{
  const s = base(3, "АУДИТ ДАННЫХ", "amber");
  title(s, "Мы нашли утечку ответа —\nи не стали ей пользоваться");
  text(s, "151/151", { x: M, y: 2.3, w: 6.2, h: 1.6, fontSize: 96, bold: true, color: C.red });
  text(s, "целевых остановок validate лежат с фактом прибытия в test/ и train/schedule, а телеметрия validate побайтно совпадает с test", {
    x: M, y: 4.1, w: 5.7, h: 1.0, fontSize: 16, color: C.text, lineSpacingMultiple: 1.1,
  });
  text(s, "С утечкой ошибка была бы почти нулевой. Модель, которая так учится, бесполезна на живом потоке.", {
    x: M, y: 5.3, w: 5.7, h: 0.8, fontSize: 13, color: C.dim, lineSpacingMultiple: 1.1,
  });

  const rows = [
    ["green", "Факт не попадает в признаки", "Загрузчик расписания физически не читает колонку факта — это проверяет тест"],
    ["amber", "Синтетика — клоны реальных ТС", "Сдвинутые копии, корреляция 0.88–0.99 → CV с защитой от клонов, иначе цифры завышены"],
    ["blue", "Маршруты не пересекаются", "5 общих остановок из 847 → сетевые признаки не строим: соседей на перегоне нет"],
  ];
  const rx = 7.0, rw = W - M - rx;
  rows.forEach(([k, h, d], i) => {
    const y = 2.55 + i * 1.3;
    box(s, rx, y, rw, 1.12, { fill: C.surface });
    glyph(s, k, rx + 0.28, y + 0.3, 0.24);
    text(s, h, { x: rx + 0.75, y: y + 0.2, w: rw - 0.95, h: 0.35, fontSize: 16, bold: true });
    text(s, d, { x: rx + 0.75, y: y + 0.57, w: rw - 0.95, h: 0.5, fontSize: 12, color: C.dim });
  });
  s.addNotes(
    "[~35 с] Прежде чем строить модель, мы проверили данные и нашли утечку ответа: факт прибытия по всем 151 целевой остановке validate лежит в расписании test и train. С ней ошибка была бы почти нулевой. Мы её не используем — колонка факта физически не попадает в признаки, это проверяет тест. Ещё две находки: синтетические автобусы — клоны реальных, поэтому кросс-валидацию считаем с защитой от клонов. А маршруты почти не пересекаются — 5 общих остановок из 847, поэтому сетевых признаков мы честно не строим."
  );
}

// =====================================================================
// 5. ML-ядро: таблица CV
{
  const s = base(4, "ML-ЯДРО", "amber");
  title(s, "Ошибка на 24% ниже baseline —\nдаже в честной оценке");

  const hdr = (t) => ({ text: t, options: { bold: true, color: C.dim, fontSize: 12, fill: { color: C.bg } } });
  const cell = (t, o = {}) => ({ text: t, options: { color: C.text, fontSize: 18, fontFace: MONO, align: "center", fill: { color: C.surface }, ...o } });
  const rowLbl = (t, sub, o = {}) => ({
    text: [
      { text: t, options: { fontSize: 14, bold: true, color: o.color || C.text, breakLine: true } },
      { text: sub, options: { fontSize: 11, color: C.dim } },
    ],
    options: { fill: { color: o.fill || C.surface }, align: "left" },
  });
  const rows = [
    [hdr("MAE, с"), hdr("Baseline\nорганизаторов"), hdr("LightGBM v1"), hdr("CatBoost"), hdr("Ансамбль")],
    [rowLbl("Как на лидерборде", "клоны в train"), cell("88.4", { color: C.dim }), cell("62.0"), cell("52.1"), cell("51.9", { bold: true })],
    [rowLbl("Честно, «новый день»", "без клонов", { color: C.green, fill: C.surface2 }), cell("88.4", { color: C.dim, fill: { color: C.surface2 } }), cell("69.4", { fill: { color: C.surface2 } }),
      cell("67.1", { bold: true, color: C.green, fontSize: 24, fill: { color: C.surface2 } }), cell("66.4*", { fill: { color: C.surface2 } })],
  ];
  s.addTable(rows, {
    x: M, y: 2.5, w: 8.1, colW: [2.5, 1.5, 1.4, 1.35, 1.35], rowH: [0.62, 0.95, 0.95],
    fontFace: SANS, valign: "middle", border: { type: "solid", color: C.bg, pt: 2 }, margin: [4, 10, 4, 10],
  });
  text(s, "1 494 реальные точки · 3×5 GroupKFold по (ТС, 30-мин блок) · * ансамбль с LightGBM", {
    x: M, y: 5.2, w: 8.1, h: 0.3, fontSize: 11, color: C.faint, mono: true,
  });
  text(s, "Мы показываем обе строки: верхняя сравнима с лидербордом, нижняя — то, что будет на новом дне.", {
    x: M, y: 5.65, w: 8.1, h: 0.6, fontSize: 13, color: C.dim,
  });

  // пайплайн модели справа
  const px = 9.25, pw = W - M - px;
  const steps = [
    ["28 признаков на момент T", "расписание · GPS · отставание от нитки · ETA", C.line],
    ["CatBoost по квантилям", "q10 / q50 / q90 + GRU по 20 мин трека (ONNX)", C.amber],
    ["Прогноз для диспетчера", "задержка · P(опоздание > 2 мин) · интервал 80%", C.green],
  ];
  steps.forEach(([h, d, col], i) => {
    const y = 2.5 + i * 1.3;
    box(s, px, y, pw, 1.0, { fill: C.surface, line: col, lineW: 1.25 });
    text(s, h, { x: px + 0.22, y: y + 0.14, w: pw - 0.4, h: 0.35, fontSize: 14, bold: true });
    text(s, d, { x: px + 0.22, y: y + 0.5, w: pw - 0.35, h: 0.45, fontSize: 11, color: C.dim });
    if (i < steps.length - 1) arrow(s, px + pw / 2, y + 1.02, px + pw / 2, y + 1.28, C.faint, { width: 1.5 });
  });
  s.addNotes(
    "[~35 с] Ядро — CatBoost по квантилям плюс GRU по треку телеметрии. 28 признаков на момент T; квантили дают прогноз, вероятность опоздания и интервал, откалиброванный до 80 процентов. GRU обучена на GPU и работает через ONNX. Верхняя строка таблицы — как на лидерборде: 52 секунды. Нижняя — честный «новый день» без клонов: 67 секунд против 88 у baseline организаторов. Минус 24 процента даже в честной оценке."
  );
}

// =====================================================================
// 6. Горизонт на потоке
{
  const s = base(5, "ГОРИЗОНТ НА ПОТОКЕ", "blue");
  title(s, "Каждый алерт — строго\nза 10–15 минут до события");

  // гистограмма lead_min
  const px = M, py = 2.35, pw = 6.6, ph = 4.3;
  box(s, px, py, pw, ph, { fill: C.surface });
  text(s, "Прогнозы на потоке по упреждению, 06.01.2026", { x: px + 0.3, y: py + 0.2, w: pw - 0.6, h: 0.3, fontSize: 12, color: C.dim });
  const ax0 = px + 0.5, ax1 = px + pw - 0.4, base0 = py + ph - 0.75;
  const xm = (m) => ax0 + (m / 20) * (ax1 - ax0);
  const counts = [5268, 5267, 5269, 5267, 5265];
  const barH = 2.2;
  s.addShape(pres.shapes.RECTANGLE, { x: xm(10), y: base0 - barH - 0.5, w: xm(15) - xm(10), h: barH + 0.5, fill: { color: C.blue, transparency: 88 }, line: { type: "none" } });
  counts.forEach((c, i) => {
    const bx = xm(10 + i) + 0.03, bw = xm(11) - xm(10) - 0.06;
    s.addShape(pres.shapes.RECTANGLE, { x: bx, y: base0 - barH * (c / 5300), w: bw, h: barH * (c / 5300), fill: { color: C.blue }, line: { type: "none" } });
  });
  text(s, "26 336 прогнозов\n(10, 15] мин", { x: xm(10) - 0.5, y: base0 - barH - 0.95, w: xm(15) - xm(10) + 1.0, h: 0.5, fontSize: 12, color: C.blue, align: "center", mono: true, bold: true });
  text(s, "0 прогнозов", { x: xm(1), y: base0 - 0.45, w: xm(9) - xm(1), h: 0.3, fontSize: 12, color: C.faint, align: "center", mono: true });
  text(s, "0", { x: xm(15.5), y: base0 - 0.45, w: xm(20) - xm(15.5), h: 0.3, fontSize: 12, color: C.faint, align: "center", mono: true });
  s.addShape(pres.shapes.LINE, { x: ax0, y: base0, w: ax1 - ax0, h: 0, line: { color: C.faint, width: 1 } });
  [0, 5, 10, 15, 20].forEach((m) => {
    text(s, String(m), { x: xm(m) - 0.3, y: base0 + 0.08, w: 0.6, h: 0.25, fontSize: 11, color: C.dim, align: "center", mono: true });
  });
  text(s, "минут до планового прибытия", { x: ax0, y: base0 + 0.36, w: ax1 - ax0, h: 0.28, fontSize: 11, color: C.dim, align: "center" });

  // плитки справа
  const tx = 7.5, tw = (W - M - tx - 0.25) / 2, th = 2.0;
  const tiles = [
    ["353/353", "точек разметки labels_test получили прогноз цели", C.text],
    ["100%", "алертов с упреждением ≥ 10 мин (241 из 241)", C.text],
    ["0.03 с", "паритет офлайн / поток — признаки те же, что при обучении", C.text],
    ["78 с", "MAE на потоке против 93 у подсказки организаторов и 117 онлайн", C.green],
  ];
  tiles.forEach(([big, lbl, col], i) => {
    const x = tx + (i % 2) * (tw + 0.25), y = 2.35 + Math.floor(i / 2) * (th + 0.3);
    box(s, x, y, tw, th, { fill: C.surface });
    text(s, big, { x: x + 0.25, y: y + 0.2, w: tw - 0.4, h: 0.75, fontSize: 34, bold: true, color: col });
    text(s, lbl, { x: x + 0.25, y: y + 1.0, w: tw - 0.4, h: 0.9, fontSize: 12, color: C.dim });
  });
  s.addNotes(
    "[~35 с] Главное — горизонт. На потоке мы прогнозируем все остановки в окне 10–15 минут, инцидент открывается на первом красном прогнозе, задним числом — никогда. Мы проиграли весь день через настоящий путь NDTP — backend — ML и сверили с реальной разметкой: прогноз есть для всех 353 точек, 100 процентов прогнозов строго в окне, все 241 алерт — с упреждением от 10 минут. Поток и офлайн совпадают до 0.03 секунды, а честная ошибка на потоке — 78 секунд против 93 у подсказки организаторов, которая знает будущее."
  );
}

// =====================================================================
// 7. Причина, рекомендация и what-if (скриншоты карточки)
{
  const s = base(6, "ПРИЧИНА И РЕКОМЕНДАЦИЯ", "red");
  title(s, "Не просто «опоздает» — почему и что делать");
  const ch = 4.75, cw = ch * aspect("card_top.jpg");
  const cx = M, cy = 2.1;
  box(s, cx - 0.04, cy - 0.04, cw + 0.08, ch + 0.08, { fill: C.line, r: 0.06, shadow: true });
  s.addImage({ path: img("card_top.jpg"), x: cx, y: cy, w: cw, h: ch });

  const wx = W - M - 4.2, ww = 4.2, wh = ww / aspect("whatif.jpg");
  box(s, wx - 0.04, cy - 0.04, ww + 0.08, wh + 0.08, { fill: C.line, r: 0.06, shadow: true });
  s.addImage({ path: img("whatif.jpg"), x: wx, y: cy, w: ww, h: wh });

  // подписи посередине
  const mx = cx + cw + 0.45, mw = wx - mx - 0.45;
  const items = [
    ["red", "Прогноз и упреждение", "опоздание, «± ошибка», вероятность и за сколько минут до события"],
    ["amber", "Причина из SHAP-вкладов", "накопленное опоздание, затор на перегоне, долгая стоянка, мало запаса на конечной, опережение, потеря GPS"],
    ["green", "1–3 рекомендации", "с кнопкой «Принять»; инцидент потом закрывается фактом по GPS"],
  ];
  items.forEach(([k, h, d], i) => {
    const y = cy + 0.1 + i * 1.55;
    glyph(s, k, mx, y + 0.06, 0.2);
    text(s, h, { x: mx + 0.35, y, w: mw - 0.35, h: 0.35, fontSize: 15, bold: true });
    text(s, d, { x: mx + 0.35, y: y + 0.4, w: mw - 0.35, h: 1.0, fontSize: 12, color: C.dim });
  });
  text(s, [
    { text: "What-if: ", options: { bold: true, color: C.blue } },
    { text: "эффект меры до её применения — та же модель пересчитывает прогноз с мерой", options: { color: C.dim } },
  ], { x: wx, y: cy + wh + 0.25, w: ww, h: 0.9, fontSize: 13 });
  s.addNotes(
    "[~20 с] Алерт — не просто «опоздает». SHAP-вклады превращаются в причину из справочника с доказательствами в цифрах, к ней — одна-три рекомендации с кнопкой «Принять». What-if показывает эффект меры до её применения: здесь резервный выпуск переводит инцидент из красного в жёлтый, вероятность опоздания падает с 58 до 37 процентов."
  );
}

// =====================================================================
// 8. Надёжность и скорость
{
  const s = base(7, "НАДЁЖНОСТЬ И СКОРОСТЬ", "green");
  title(s, "Не падает и не тормозит");
  const stats = [
    ["1.5 мс", "инференс на батч 30 ТС"],
    ["< 0.5 с", "полный путь пакет NDTP → дашборд"],
    ["0 потерь", "300 TCP-соединений, 757 пакетов/с, пик 3 390"],
    ["17.5 с", "холодный старт до готовности всех сервисов"],
  ];
  const sw = (W - 2 * M - 3 * 0.3) / 4;
  stats.forEach(([big, lbl], i) => {
    const x = M + i * (sw + 0.3);
    text(s, big, { x, y: 2.3, w: sw, h: 0.95, fontSize: 42, bold: true, color: i === 2 ? C.green : C.text, fit: "shrink" });
    text(s, lbl, { x, y: 3.3, w: sw - 0.2, h: 0.65, fontSize: 13, color: C.dim });
  });

  text(s, "ЕСЛИ ЧТО-ТО ЛОМАЕТСЯ", { x: M, y: 4.45, w: 6, h: 0.3, fontSize: 11, color: C.faint, mono: true, charSpacing: 2 });
  const cards = [
    ["Обрыв потока", "DEGRADED", C.amber, "через 30 с баннер, прогнозы по последнему состоянию; поток вернулся — LIVE"],
    ["Падение ML", "FALLBACK", C.amber, "circuit breaker → эвристика, карта и лента работают; восстановление ≤ 10 с"],
    ["Рестарт backend", "RECONNECT", C.green, "все 30 бортов переподключаются сами, прогнозы восстанавливаются"],
  ];
  const cw = (W - 2 * M - 2 * 0.3) / 3;
  cards.forEach(([h, chip, col, d], i) => {
    const x = M + i * (cw + 0.3), y = 4.85;
    box(s, x, y, cw, 1.75, { fill: C.surface });
    text(s, h, { x: x + 0.25, y: y + 0.22, w: cw - 1.9, h: 0.35, fontSize: 16, bold: true });
    box(s, x + cw - 1.55, y + 0.22, 1.3, 0.34, { fill: C.bg, line: col, r: 0.17 });
    text(s, chip, { x: x + cw - 1.55, y: y + 0.22, w: 1.3, h: 0.34, fontSize: 10, color: col, mono: true, align: "center", valign: "middle", bold: true });
    text(s, d, { x: x + 0.25, y: y + 0.72, w: cw - 0.5, h: 0.9, fontSize: 12, color: C.dim });
  });
  s.addNotes(
    "[~25 с] Инференс — полторы миллисекунды на батч из 30 машин, путь от пакета NDTP до дашборда — меньше полсекунды. 300 TCP-соединений, 757 пакетов в секунду, ноль потерь. Оборвался поток — режим DEGRADED и работа по последнему состоянию; упал ML — эвристика; перезапустили backend — борта переподключаются сами. Холодный старт — 17 с половиной секунд."
  );
}

// =====================================================================
// 9. Дополнительно: бенто
{
  const s = base(8, "СВЕРХ ОБЯЗАТЕЛЬНОГО", "blue");
  title(s, "Все дополнительные фичи из ТЗ — и не только");
  const gx = M, gy = 2.2, gw = W - 2 * M, gh = 4.5, gap = 0.25;
  // большая плитка
  const bw = 4.3;
  box(s, gx, gy, bw, gh, { fill: C.surface2 });
  text(s, "MAP-MATCHING С УЧЁТОМ NDTP", { x: gx + 0.3, y: gy + 0.3, w: bw - 0.6, h: 0.3, fontSize: 11, color: C.dim, mono: true, charSpacing: 1 });
  text(s, "1.9×", { x: gx + 0.3, y: gy + 0.8, w: bw - 0.6, h: 1.4, fontSize: 88, bold: true, color: C.blue, mono: true });
  text(s, "точнее отклонение от нитки графика: MAE 19.4 с против 36.6 с по последней остановке", { x: gx + 0.3, y: gy + 2.35, w: bw - 0.6, h: 0.9, fontSize: 14, color: C.text });
  text(s, "монотонный прогресс, фильтр valid=0, скачков и «телепортов» · 34 мкс на точку", { x: gx + 0.3, y: gy + 3.4, w: bw - 0.6, h: 0.8, fontSize: 11, color: C.dim });

  const rx = gx + bw + gap, rw = gw - bw - gap;
  const tw = (rw - gap) / 2, th = (gh - 2 * gap) / 3;
  const tiles = [
    ["What-if", "4 упреждающие меры, пересчёт той же моделью, монотонный фильтр"],
    ["ONNX", "GRU без PyTorch: 2 мс на ТС, паритет 6·10⁻⁵ с"],
    ["CatBoost + PyTorch GRU", "ансамбль табличных признаков и трека, обучение на RTX 3080"],
    ["Свой NDTP-кодек", "байт-в-байт с официальным эмулятором, CRC, ресинхронизация"],
    ["200+ автотестов", "анти-утечка, инвариант горизонта, E2E по TCP"],
    ["Наблюдаемость", "Prometheus, журнал прогнозов, последний NDTP-пакет в hex"],
  ];
  tiles.forEach(([h, d], i) => {
    const x = rx + (i % 2) * (tw + gap), y = gy + Math.floor(i / 2) * (th + gap);
    box(s, x, y, tw, th, { fill: C.surface });
    text(s, h, { x: x + 0.25, y: y + 0.18, w: tw - 0.5, h: 0.35, fontSize: 16, bold: true });
    text(s, d, { x: x + 0.25, y: y + 0.58, w: tw - 0.5, h: th - 0.7, fontSize: 12, color: C.dim });
  });
  s.addNotes(
    "[~15 с] Все дополнительные фичи из ТЗ сделаны: map-matching с учётом специфики NDTP — в 1.9 раза точнее, what-if, ONNX, ансамбль CatBoost и PyTorch GRU. Плюс собственный NDTP-кодек, байт-в-байт как официальный эмулятор, и больше двухсот автотестов."
  );
}

// =====================================================================
// 10. Что дальше
{
  const s = base(9, "ЧТО ДАЛЬШЕ", "green");
  title(s, "Готово к реальному парку —\nдальше больше данных");
  const steps = [
    ["Данные за месяцы", "дообучение без остановки сервиса: ML stateless"],
    ["GTFS-RT", "стандартный фид для внешних систем"],
    ["Светофорные фазы", "причина «затор» точнее на перекрёстках"],
    ["Сетевые признаки", "соседи на перегоне — когда маршруты пересекаются"],
    ["Пассажиропоток", "дверные датчики: ячейки IRMA и «Корона» NDTP уже поддержаны"],
  ];
  const TW = 2.3, y0 = 3.4, x0 = M + TW / 2, x1 = W - M - TW / 2;
  const step = (x1 - x0) / (steps.length - 1);
  s.addShape(pres.shapes.LINE, { x: x0, y: y0, w: x1 - x0, h: 0, line: { color: C.line, width: 2 } });
  steps.forEach(([h, d], i) => {
    const x = x0 + i * step;
    s.addShape(pres.shapes.OVAL, { x: x - 0.15, y: y0 - 0.15, w: 0.3, h: 0.3, fill: { color: i === 0 ? C.green : C.bg }, line: { color: i === 0 ? C.green : C.dim, width: 2 } });
    const tw = TW;
    const tx = x - tw / 2;
    const al = "center";
    text(s, h, { x: tx, y: y0 + 0.45, w: tw, h: 0.4, fontSize: 15, bold: true, align: al, color: i === 0 ? C.green : C.text });
    text(s, d, { x: tx, y: y0 + 0.9, w: tw, h: 1.1, fontSize: 12, color: C.dim, align: al });
  });
  box(s, M, 5.65, W - 2 * M, 0.85, { fill: C.surface });
  text(s, [
    { text: "Масштабирование уже заложено: ", options: { bold: true, color: C.text } },
    { text: "ML-сервис stateless и растёт репликами, приём и прогнозы разделены очередью, SHAP считается только для рискованных ТС.", options: { color: C.dim } },
  ], { x: M + 0.3, y: 5.65, w: W - 2 * M - 0.6, h: 0.85, fontSize: 13, valign: "middle" });
  s.addNotes(
    "[~15 с] Дальше — данные за месяцы и дообучение без остановки сервиса, выдача GTFS-RT, светофорные фазы, сетевые признаки и пассажиропоток по дверным датчикам: ячейки IRMA и «Корона» NDTP уже поддерживаются. А теперь — живое демо."
  );
}

// =====================================================================
// 11. Демо
{
  const s = pres.addSlide();
  s.background = { color: C.bg };
  s.addImage({ path: img("card_full.jpg"), x: 0, y: 0, w: W, h: H, transparency: 0 });
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: W, h: H, fill: { color: C.bg, transparency: 18 }, line: { type: "none" } });
  s.addShape(pres.shapes.OVAL, { x: M, y: 0.62, w: 0.16, h: 0.16, fill: { color: C.green }, line: { type: "none" } });
  text(s, "ПОТОК ИДЁТ  ·  localhost:8080", { x: M + 0.3, y: 0.55, w: 6, h: 0.3, fontSize: 12, color: C.green, mono: true, charSpacing: 2 });
  text(s, "Демо", { x: M, y: 1.6, w: 7, h: 1.6, fontSize: 96, bold: true });
  text(s, "Живой поток NDTP, день 06.01.2026 на скорости ×30", { x: M, y: 3.2, w: 7, h: 0.5, fontSize: 18, color: C.dim });
  const plan = [
    ["0:00", "пульт: карта и лента"],
    ["0:40", "красный инцидент: за 11 мин до события"],
    ["1:20", "what-if и «Принять»"],
    ["1:55", "обрыв потока → DEGRADED → LIVE"],
    ["2:35", "NDTP-пакет в hex, Swagger"],
  ];
  plan.forEach(([t, d], i) => {
    const y = 4.15 + i * 0.5;
    text(s, t, { x: M, y, w: 0.9, h: 0.35, fontSize: 14, color: C.blue, mono: true, bold: true });
    text(s, d, { x: M + 1.0, y, w: 6, h: 0.35, fontSize: 14, color: C.text });
  });
  s.addNotes(
    "Переходим к демо (3 минуты). 0:00 — общий вид пульта: сверху сим-время и режим потока, данные идут по NDTP; цвет и форма маркера — уровень риска. 0:20 — лента отсортирована по тому, сколько минут осталось до события. 0:40 — открыть красный инцидент: прогноз ± ошибка, вероятность, алерт создан за 11 минут до события; причина и доказательства; участок подсвечен на карте. 1:20 — What-if «Резервный выпуск»: эффект до применения меры. 1:40 — «Принять». 1:55 — docker compose stop replayer: через 30 с баннер «Поток прерван», карта жива; docker compose start replayer — «Поток идёт». 2:35 — Swagger :8000/docs или /api/v1/ingest/stats: последний NDTP-пакет в hex, ноль ошибок CRC. 2:50 — «Всё поднимается одной командой, инструкция в README». Резерв: запись экрана."
  );
}

pres.writeFile({ fileName: OUT }).then((f) => console.log("written", f));
