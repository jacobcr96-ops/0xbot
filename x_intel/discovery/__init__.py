"""Early-mover discovery bus — parallel on-chain + X ingest (disarmed).

Not an X-reactive scanner. Either on-chain or X can discover first and trigger
research fan-out. Live BUY emit still respects XINTEL_ARMED / do_not_execute_until_armed.
"""

from x_intel.discovery.models import DiscoveryEvent, DiscoveryRecord
from x_intel.discovery.bus import DiscoveryBus
from x_intel.discovery.pipeline import ingest_event

__all__ = [
    "DiscoveryEvent",
    "DiscoveryRecord",
    "DiscoveryBus",
    "ingest_event",
]
