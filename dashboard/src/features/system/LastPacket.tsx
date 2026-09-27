import { useEffect, useState } from "react";
import type { LastPacket as Packet } from "@contract";
import { formatSimTime } from "../../lib/time";

// Кадр NDTP: [NPL 15 байт][NPH 10 байт][ячейки] — как в transit_core/ndtp/codec.py.
const PARTS = [
  { name: "NPL", hint: "транспортный заголовок", from: 0, to: 15 },
  { name: "NPH", hint: "заголовок сервиса", from: 15, to: 25 },
  { name: "Ячейки", hint: "навигация и датчики", from: 25, to: undefined },
];

const FIELD_LABEL: Record<string, string> = {
  cell: "Ячейка",
  unit_id: "Борт",
  crc_ok: "CRC",
  timestamp: "Время терминала",
  lon: "Долгота",
  lat: "Широта",
  valid: "Координаты валидны",
  speed_kmh: "Скорость, км/ч",
  course: "Курс, °",
  nsat: "Спутников",
  proto: "Версия протокола",
};

function bytesOf(hex: string): string[] {
  return hex.match(/.{1,2}/g) ?? [];
}

function fieldValue(key: string, v: number | boolean | string): string {
  if (typeof v === "boolean") {
    if (key === "crc_ok") return v ? "сходится" : "ошибка";
    return v ? "да" : "нет";
  }
  // Время терминала — unix-секунды времени датасета: показываем как сим-время, в UTC.
  if (key === "timestamp" && typeof v === "number") {
    return formatSimTime(new Date(v * 1000).toISOString().slice(0, 19), true);
  }
  return String(v);
}

function agoLabel(iso: string, now: number): string {
  const sec = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (sec < 60) return `${sec} с назад`;
  if (sec < 3600) return `${Math.floor(sec / 60)} мин назад`;
  return `${Math.floor(sec / 3600)} ч назад`;
}

/** Последний принятый NDTP-кадр: hex по байтам с разбивкой NPL | NPH | ячейки и раскодированные поля. */
export function LastPacket({ packet }: { packet: Packet }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const all = bytesOf(packet.hex);
  const parts = PARTS.map((p) => ({ ...p, chunk: all.slice(p.from, p.to) })).filter((p) => p.chunk.length > 0);

  const received = new Date(packet.received_at);
  const fields = Object.entries(packet.fields);

  return (
    <div className="pkt">
      <div className="pkt-head">
        <span>
          Последний пакет · борт <b className="num">{packet.unit_id}</b>
        </span>
        <span className="dim num small">
          принят {received.toLocaleTimeString("ru-RU")} · {agoLabel(packet.received_at, now)} · {all.length} байт
        </span>
      </div>
      <div className="pkt-grid">
        <div className="pkt-hex num" aria-label="Кадр в шестнадцатеричном виде">
          {parts.map((p) => (
            <div key={p.name} className={`pkt-part pkt-${p.name === "Ячейки" ? "cells" : p.name.toLowerCase()}`}>
              <div className="pkt-part-label">
                <b>{p.name}</b> <span>{p.hint} · {p.chunk.length} байт</span>
              </div>
              <div className="pkt-bytes">
                {p.chunk.map((b, i) => (
                  <span key={i}>{b}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
        <dl className="pkt-fields" aria-label="Раскодированные поля">
          {fields.map(([k, v]) => (
            <div key={k}>
              <dt>{FIELD_LABEL[k] ?? k}</dt>
              <dd className={`num${k === "crc_ok" && v === false ? " tone-red" : ""}`}>{fieldValue(k, v)}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}
