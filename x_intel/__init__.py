"""x_intel — intelligence / decision layer for 0xbot.

Intelligence emits intents only. Execution stays disarmed until XINTEL_ARMED.
experiment_id default: xintel_v0
"""

from x_intel.config import do_not_execute_until_armed as _dna
from x_intel.config import is_armed

__version__ = "0.1.1"
EXPERIMENT_ID = "xintel_v0"


def __getattr__(name: str):
    # Keep DO_NOT_EXECUTE_UNTIL_ARMED as a live binding of the env switch.
    if name == "DO_NOT_EXECUTE_UNTIL_ARMED":
        return _dna()
    raise AttributeError(name)
