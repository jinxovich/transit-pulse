import { Fragment, useEffect, useRef } from "react";
import type { StopTimelineItem, Thresholds } from "@contract";
import { formatDelayShort } from "../../lib/format";
import { formatSimTime } from "../../lib/time";

const NO_ADDRESS = "Остановка без адреса";

/** Класс цвета задержки по порогам из /config (только подсветка цифры, риск ТС не пересчитываем). */
function delayTone(sec: number | null, t: Thresholds): string {
  if (sec == null) return "";
  if (sec >= t.red_delay_s) return "tone-red";
  if (sec >= t.yellow_delay_s) return "tone-yellow";
  if (sec <= t.early_delay_s) return "tone-early";
  return "";
}

function Delay({ sec, t }: { sec: number | null; t: Thresholds }) {
  if (sec == null) return <span className="faint">—</span>;
  return <span className={delayTone(sec, t)}>{formatDelayShort(sec)}</span>;
}

/** Остановки ТС ±60 мин: план, факт по GPS, задержка, прогноз. Цель выделена, пройденные приглушены. */
export function StopTimeline({
  items,
  thresholds,
  simTime,
}: {
  items: StopTimelineItem[];
  thresholds: Thresholds;
  simTime: string | null;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const nowRow = useRef<HTMLTableRowElement>(null);
  const firstUpcoming = items.findIndex((i) => i.status === "upcoming");

  // Один раз при открытии: «сейчас» у верха списка, пара пройденных остановок видна над ним.
  const scrolled = useRef(false);
  useEffect(() => {
    if (scrolled.current || !scroller.current || !nowRow.current) return;
    scroller.current.scrollTop = Math.max(0, nowRow.current.offsetTop - 110);
    scrolled.current = true;
  }, [items]);

  if (items.length === 0) {
    return <p className="dim small vd-empty">Нет плановых остановок в окне ±60 мин.</p>;
  }

  return (
    <div className="stops" ref={scroller}>
      <table className="stops-table">
        <thead>
          <tr>
            <th>Остановка</th>
            <th className="r">План</th>
            <th className="r">Факт</th>
            <th className="r">Задержка</th>
            <th className="r">Прогноз</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s, i) => {
            const cls = [s.status === "passed" ? "is-passed" : "", s.is_target ? "is-target" : ""].join(" ");
            return (
              <Fragment key={s.visit_id}>
                {i === firstUpcoming && (
                  <tr className="stops-now" ref={nowRow}>
                    <td colSpan={5}>
                      <span className="num">сейчас {simTime ? formatSimTime(simTime) : ""}</span>
                    </td>
                  </tr>
                )}
                <tr className={cls}>
                  <td className="stops-name">
                    {s.is_target && <span className="stops-target">цель</span>}
                    <span className={s.name === NO_ADDRESS ? "faint" : ""}>
                      {s.name === NO_ADDRESS ? "без адреса" : s.name}
                    </span>
                  </td>
                  <td className="r num">{formatSimTime(s.planned_at)}</td>
                  <td className="r num">{s.actual_at ? formatSimTime(s.actual_at, true) : <span className="faint">—</span>}</td>
                  <td className="r num"><Delay sec={s.actual_delay_s} t={thresholds} /></td>
                  <td className="r num"><Delay sec={s.predicted_delay_s} t={thresholds} /></td>
                </tr>
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
