"""KiloVault HLX+ battery monitor.

An offline-first monitoring program for KiloVault HLX / HLX+ LiFePO4 batteries,
which talk Bluetooth Low Energy but whose vendor (and phone app) are gone.

Public API surface is intentionally small; see the submodules:

- ``kilovault.protocol``   — frame assembly / decode / encode (pure)
- ``kilovault.models``     — shared data types
- ``kilovault.transports`` — BLE, ESP32 serial bridge and simulator sources
- ``kilovault.storage``    — SQLite time-series logging
- ``kilovault.estimator``  — derived metrics and bank aggregation
- ``kilovault.alarms``     — alarm evaluation and notifications
- ``kilovault.manager``    — ties a transport to storage/alarms/subscribers
- ``kilovault.server``     — local, dependency-free web dashboard
"""

from .protocol import (  # noqa: F401
    BatterySample,
    FrameAssembler,
    decode_frame,
    decode_payload,
    encode_frame,
    decode_alarms,
    ALARM_BITS,
)

__version__ = "1.1.0"
__all__ = [
    "BatterySample",
    "FrameAssembler",
    "decode_frame",
    "decode_payload",
    "encode_frame",
    "decode_alarms",
    "ALARM_BITS",
    "__version__",
]
