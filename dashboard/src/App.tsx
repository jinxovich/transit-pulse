import { useEffect } from "react";
import { startStream } from "./api/ws";
import { useStream } from "./store/stream";
import { formatSimTime } from "./lib/time";
import { RiskGlyph } from "./features/map/RiskGlyph";

export default function App() {
  useEffect(() => startStream(), []);
  const simTime = useStream((s) => s.simTime);
  const connected = useStream((s) => s.connected);
  const mode = useStream((s) => s.status?.mode);
  const nVehicles = useStream((s) => Object.keys(s.vehicles).length);
  const nIncidents = useStream((s) => Object.keys(s.incidents).length);

  return (
    <>
      <RiskGlyph risk="red" size={40} />
    <pre>
      {`связь: ${connected ? "есть" : "нет"}
        время: ${simTime ? formatSimTime(simTime, true) : "—"}
        режим: ${mode}
        ТС: ${nVehicles}
        инцидентов: ${nIncidents}`}
    </pre>
    </>
  );
}