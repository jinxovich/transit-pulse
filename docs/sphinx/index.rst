Transit Pulse — документация по коду
====================================

Предиктор задержек городского транспорта: поток NDTP → сопоставление с расписанием →
ML-прогноз задержки на 10–15 минут → алерт на дашборде диспетчера.

* Спецификация REST API: ``/docs`` (Swagger backend) и ``:8001/docs`` (Swagger ML-сервиса).
* Контракт WebSocket и типы фронта: ``contracts/schema``, ``contracts/ts``.

.. toctree::
   :maxdepth: 2

   core
   services
