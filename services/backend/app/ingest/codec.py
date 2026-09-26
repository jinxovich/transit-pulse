"""Точка импорта NDTP-кодека для бэкенда (``transit_core.ndtp``, INTERFACES §1).

Имена реэкспортируются как ``X as X`` (без ``__all__``), чтобы Sphinx не дублировал
описания классов кодека.
"""

from __future__ import annotations

from transit_core.ndtp import Cell as Cell
from transit_core.ndtp import Frame as Frame
from transit_core.ndtp import FrameDecoder as FrameDecoder
from transit_core.ndtp import NavCell as NavCell
from transit_core.ndtp import encode_handshake as encode_handshake
from transit_core.ndtp import encode_realtime as encode_realtime
from transit_core.ndtp import encode_result as encode_result
from transit_core.ndtp import frame_fields as frame_fields

NPH_CONN_REQUEST = 100
"""NPH-тип handshake терминала (``NPH_SGC_CONN_REQUEST``)."""
