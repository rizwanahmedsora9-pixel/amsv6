"""Tasker — fully isolated delivery tracking board (auto-loaded module).

Owner rules (2026-11-22):
* totally separate from sales / delivery / billing / stock / cash code;
* only reads client + material names for the combo lookups;
* two deadlines per task (note + delivery), admin-only create & time reset;
* any signed-in user stamps the workflow buttons;
* missed deadline => red + looping alarm until admin resets the time.

Routes (all under /tasker):
    /tasker                      the board page
    /tasker/api/board            total list + counters (poll)
    /tasker/api/alarm_state      cheap global-alarm poll
    /tasker/api/clients/search   read-only client combo
    /tasker/api/materials/search read-only material/brand combo
    /tasker/api/tasks            POST create (admin)
    /tasker/api/tasks/<id>       GET detail (items + timeline + resets)
    /tasker/api/tasks/<id>/action        POST stage buttons / cancel / reopen
    /tasker/api/tasks/<id>/reset_deadline POST new manual time (admin)
"""
from ._common import MODULE_CONFIG, tasker_bp  # noqa: F401  (module loader reads these)

from . import views  # noqa: F401,E402  (routes attach to tasker_bp)
from . import api    # noqa: F401,E402
