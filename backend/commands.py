"""DSS -> sim command execution (the reverse path of the telemetry stream).

WHY THIS EXISTS
---------------
The forward path is: ``perceived_sim`` broadcasts a snapshot -> ``dss_bridge``
translates it -> the teammate's DSS consumes it and *decides what to do*. This
module is the missing return leg: it takes those decisions and **executes them in
the sim**. ``main.py`` exposes it as ``POST /command``; ``dss_bridge.py``
translates DSS-shaped commands and POSTs them here.

DESIGN
------
* **Separate contract, not echoed telemetry.** A command is its own message:
  ``{command_id, vehicle_id, action, params, issued_by, ts}`` (see ``CommandRequest``).
* **The sim owns whether/how it physically happens.** Every action maps to an
  execution primitive already on ``PerceivedSimulator``; this module only routes.
* **Comms-aware delivery (perceived-only thesis applied to the downlink).** A
  command is a *downlink message*: it can only reach a vehicle the operator is
  currently in contact with. A command to an out-of-contact vehicle (submerged /
  stale fix) is *queued*; the ack is ``pending`` and it executes the instant the
  vehicle is next reachable (its next fix / contact window). There is no bypass —
  if you can't talk to it, no order gets through, ``surface`` included.

BACKTRACK-SAFE-ISH
------------------
This whole module deletes cleanly. To remove the feature: drop this file and the
three references in ``main.py`` (construct ``CommandDispatcher``, call
``dispatcher.step()`` in the loop, the ``/command`` + ``/commands`` routes). The
forward path and the ``/ws`` data contract are untouched.
"""

from __future__ import annotations

import time
from typing import Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Command contract (what POST /command accepts; vehicle_id is a SIM id)
# --------------------------------------------------------------------------- #
class CommandRequest(BaseModel):
    command_id: str
    vehicle_id: str                       # sim id, e.g. "UUV-1" (bridge maps sub_1 -> UUV-1)
    action: str
    params: dict = Field(default_factory=dict)
    issued_by: str = "dss"
    ts: Optional[str] = None


# Every action the sim can execute, with the params it reads. Keep this list and
# README_dss_commands.md in sync — it is the contract the teammate's DSS targets.
ACTIONS = {
    "reroute",          # params: lat, lon, task?      -> assign_task (move + optional retitle)
    "reassign_task",    # params: task, lat?, lon?     -> assign_task
    "set_depth",        # params: depth_m              -> set_depth (UUV/USV, metres below surface)
    "set_altitude",     # params: altitude_m           -> set_depth (UAV, metres above surface)
    "surface",          # params: -                    -> surface
    "submerge",         # params: depth_m?             -> submerge (+ set_depth if given)
    "hold",             # params: -                    -> hold (loiter, speed 0)
    "resume",           # params: -                    -> resume
    "rtb",              # params: -                    -> return_to_ship
    "abort",            # params: -                    -> clear waypoint + hold + awaiting_tasking
    "set_status",       # params: status              -> set_status
}


class CommandDispatcher:
    """Validates, routes and (when needed) queues DSS commands for the sim."""

    HISTORY_LIMIT = 50

    def __init__(self, sim):
        self.sim = sim
        self.queue: dict[str, list[CommandRequest]] = {}   # vid -> pending FIFO
        self.history: list[dict] = []                      # recent acks (debug)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def submit(self, cmd: CommandRequest) -> dict:
        """Execute now or queue, returning an ack dict.

        ack.status is one of: executed | pending | rejected.
        """
        p = self.sim.perceived.get(cmd.vehicle_id)
        if p is None:
            return self._record(cmd, "rejected", f"unknown vehicle '{cmd.vehicle_id}'")
        if cmd.action not in ACTIONS:
            return self._record(cmd, "rejected", f"unknown action '{cmd.action}'")
        ok, why = self._validate_params(cmd)
        if not ok:
            return self._record(cmd, "rejected", why)

        # A command is a downlink message: it only lands if we are in contact.
        if self._reachable(p):
            self._execute(cmd)
            return self._record(cmd, "executed", "applied immediately")

        # Out of contact -> queue and tell the DSS roughly when it will land.
        self.queue.setdefault(cmd.vehicle_id, []).append(cmd)
        eta = p.get("expected_next_contact_sec")
        return self._record(
            cmd, "pending",
            "vehicle out of contact; queued until next contact window",
            expected_next_contact_sec=eta,
        )

    def step(self):
        """Drain queued commands for any vehicle that has regained contact.

        Called once per sim tick from ``main.py`` (after ``sim.tick()``). This is
        where a queued ``surface``/``reroute``/etc. for a submerged UUV finally
        fires — the moment a fresh fix slips through and it becomes reachable.
        """
        if not self.queue:
            return
        for vid, pending in list(self.queue.items()):
            p = self.sim.perceived.get(vid)
            if p is None or not self._reachable(p):
                continue
            for cmd in pending:
                self._execute(cmd)
                self._record(cmd, "executed", "delivered at next contact window")
            del self.queue[vid]

    def status(self) -> dict:
        """Debug view for ``GET /commands``."""
        return {
            "history": self.history[-self.HISTORY_LIMIT:],
            "queued": {vid: len(q) for vid, q in self.queue.items() if q},
        }

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _reachable(p: dict) -> bool:
        """Can the operator push an order to this vehicle right now?

        Reachable == we currently have contact == a recent fix is arriving (not
        stale). Nothing more: ``stale`` already reflects the comms physics — a
        submerged UUV's acoustic link drops ~98% of fixes, so it is stale almost
        always, but the rare fix that slips through is a genuine two-way contact
        window, and a queued ``surface``/``reroute`` fires on exactly that tick.

        We deliberately do NOT also gate on ``submerged``: doing so double-counts
        the same physics and makes a commanded-down UUV permanently unreachable
        (it could never receive the ``surface`` order to come back). Contact is
        contact — if a fix got through, a command can ride the same window.
        """
        return not p.get("stale")

    def _validate_params(self, cmd: CommandRequest) -> tuple[bool, str]:
        prm = cmd.params or {}
        a = cmd.action
        if a == "reroute":
            if "lat" not in prm or "lon" not in prm:
                return False, "reroute requires params.lat and params.lon"
        elif a == "reassign_task":
            if not prm.get("task"):
                return False, "reassign_task requires params.task"
        elif a == "set_depth":
            if not isinstance(prm.get("depth_m"), (int, float)):
                return False, "set_depth requires numeric params.depth_m"
        elif a == "set_altitude":
            if not isinstance(prm.get("altitude_m"), (int, float)):
                return False, "set_altitude requires numeric params.altitude_m"
        elif a == "set_status":
            if not prm.get("status"):
                return False, "set_status requires params.status"
        return True, ""

    def _execute(self, cmd: CommandRequest):
        """Route an action onto the sim's execution primitives."""
        sim, p, prm = self.sim, self.sim.perceived[cmd.vehicle_id], (cmd.params or {})
        a = cmd.action

        if a == "reroute":
            sim.assign_task(cmd.vehicle_id, prm.get("task") or p["current_task"],
                            prm["lat"], prm["lon"])
        elif a == "reassign_task":
            sim.assign_task(cmd.vehicle_id, prm["task"], prm.get("lat"), prm.get("lon"))
        elif a == "set_depth":
            sim.set_depth(cmd.vehicle_id, -abs(float(prm["depth_m"])))
        elif a == "set_altitude":
            sim.set_depth(cmd.vehicle_id, abs(float(prm["altitude_m"])))
        elif a == "surface":
            sim.surface(cmd.vehicle_id)
        elif a == "submerge":
            sim.submerge(cmd.vehicle_id)
            if isinstance(prm.get("depth_m"), (int, float)):
                sim.set_depth(cmd.vehicle_id, -abs(float(prm["depth_m"])))
        elif a == "hold":
            sim.hold(cmd.vehicle_id)
        elif a == "resume":
            sim.resume(cmd.vehicle_id)
        elif a == "rtb":
            sim.return_to_ship(cmd.vehicle_id)
        elif a == "abort":
            p["waypoint"] = None
            sim.hold(cmd.vehicle_id)
            p["current_task"] = "Aborted — awaiting tasking"
            sim.push_alert({
                "type": "awaiting_tasking",
                "vehicle": cmd.vehicle_id,
                "message": f"{cmd.vehicle_id} aborted current task — awaiting DSS tasking",
            })
        elif a == "set_status":
            sim.set_status(cmd.vehicle_id, prm["status"])

    def _record(self, cmd: CommandRequest, status: str, reason: str, **extra) -> dict:
        ack = {
            "command_id": cmd.command_id,
            "vehicle_id": cmd.vehicle_id,
            "action": cmd.action,
            "status": status,
            "reason": reason,
            "ts": time.time(),
            **extra,
        }
        self.history.append(ack)
        self.history = self.history[-self.HISTORY_LIMIT:]
        return ack
