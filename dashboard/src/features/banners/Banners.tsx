import { useStream } from "../../store/stream";

export function ConnectionBanner() {
  const connected = useStream((s) => s.connected);
  const everConnected = useStream((s) => s.simTime !== null);
  if (connected) return null;

  if (!everConnected) {
    return (
      <div className="banner banner-conn" role="status">
        <span className="spinner" aria-hidden /> Подключаемся к серверу…
      </div>
    );
  }
  return (
    <div className="banner banner-conn" role="alert">
      <span className="spinner" aria-hidden />
      <strong>Нет связи с сервером.</strong>
      <span>Переподключаемся автоматически. На экране последние известные данные.</span>
    </div>
  );
}
export function StatusBanner() {
  const connected = useStream((s) => s.connected);
  const status = useStream((s) => s.status);
  if (!connected || !status || status.mode === "LIVE") return null;

  if (status.mode === "DEGRADED") {
    return (
      <div className="banner banner-degraded" role="alert">
        <strong>Поток телеметрии прерван.</strong>
        <span>{status.reason ?? "Прогноз — по расписанию и последнему известному состоянию."}</span>
        {status.last_packet_age_s != null && (
          <span className="banner-age num">нет пакетов {Math.round(status.last_packet_age_s)} с</span>
        )}
        <span className="banner-note">Система работает, данные восстановятся автоматически.</span>
      </div>
    );
  }
  if (status.mode === "WARMING_UP") {
    return (
      <div className="banner banner-warming" role="status">
        <strong>Прогрев.</strong>
        <span>Копится история телеметрии, инциденты появятся через несколько минут.</span>
      </div>
    );
  }
  if (status.mode === "PAUSED") {
    return (
      <div className="banner banner-paused" role="status">
        <strong>Воспроизведение на паузе.</strong>
      </div>
    );
  }
  return (
    <div className="banner banner-offline" role="alert">
      <strong>Нет данных от источника.</strong>
      <span>{status.reason ?? "Телеметрия не поступает."}</span>
    </div>
  );
}
