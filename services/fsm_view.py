"""View-model for the live state diagram.

Nodes are positioned by a fixed cosmetic layout, but *edges, the current
state, visited states and traversed edges all come from the real
transition table and the workflow's stored history* - nothing here
simulates the engine.
"""
import math

from automata.states import WorkflowEvent, WorkflowState
from automata.transitions import INITIAL_STATE, REQUIRED_ACTOR, all_transitions

VIEW_W, VIEW_H = 940, 320
NODE_W, NODE_H = 128, 44

_LAYOUT = {
    WorkflowState.CREATED: (80, 80),
    WorkflowState.IN_PROGRESS: (270, 80),
    WorkflowState.EVIDENCE_REQUIRED: (460, 80),
    WorkflowState.EVIDENCE_SUBMITTED: (650, 80),
    WorkflowState.VALIDATING: (840, 80),
    WorkflowState.REJECTED: (650, 260),
    WorkflowState.VERIFIED: (840, 260),
}

EVENT_LABELS = {
    WorkflowEvent.START: "Start work",
    WorkflowEvent.REQUEST_EVIDENCE: "Ready to provide proof",
    WorkflowEvent.SUBMIT_EVIDENCE: "Submit evidence for review",
    WorkflowEvent.START_VALIDATION: "Begin review",
    WorkflowEvent.APPROVE: "Approve",
    WorkflowEvent.REJECT: "Reject",
    WorkflowEvent.RESTART: "Restart after rejection",
}

STATE_LABELS = {
    WorkflowState.CREATED: "Created",
    WorkflowState.IN_PROGRESS: "In progress",
    WorkflowState.EVIDENCE_REQUIRED: "Evidence required",
    WorkflowState.EVIDENCE_SUBMITTED: "Evidence submitted",
    WorkflowState.VALIDATING: "Validating",
    WorkflowState.VERIFIED: "Verified",
    WorkflowState.REJECTED: "Rejected",
}

HAPPY_PATH = [
    WorkflowState.CREATED,
    WorkflowState.IN_PROGRESS,
    WorkflowState.EVIDENCE_REQUIRED,
    WorkflowState.EVIDENCE_SUBMITTED,
    WorkflowState.VALIDATING,
    WorkflowState.VERIFIED,
]


def progress_percent(state_value):
    """Progress along the happy path; a rejected task sits at the review step."""
    try:
        state = WorkflowState(state_value)
    except ValueError:
        return 0
    if state == WorkflowState.REJECTED:
        return round(100 * HAPPY_PATH.index(WorkflowState.VALIDATING) / (len(HAPPY_PATH) - 1))
    return round(100 * HAPPY_PATH.index(state) / (len(HAPPY_PATH) - 1))


def _clip(cx, cy, dx, dy):
    """Where a ray from a node centre leaves the node's rectangle."""
    hw, hh = NODE_W / 2, NODE_H / 2
    scale = min(
        hw / abs(dx) if dx else math.inf,
        hh / abs(dy) if dy else math.inf,
    )
    return cx + dx * scale, cy + dy * scale


def _label_position(x1, y1, x2, y2, sx, sy, ex, ey):
    """Place the event label: above horizontal edges, beside vertical ones."""
    if y1 == y2:
        return {"lx": round((x1 + x2) / 2, 1), "ly": round(y1 - NODE_H / 2 - 10, 1),
                "anchor": "middle"}
    if x1 == x2:
        return {"lx": round(x1 + 10, 1), "ly": round((sy + ey) / 2 + 4, 1), "anchor": "start"}
    return {"lx": round((sx + ex) / 2, 1), "ly": round((sy + ey) / 2 - 4, 1), "anchor": "middle"}


def build_fsm_view(current_state, history, available_events):
    """Build everything the template needs to draw the automaton.

    current_state    - workflow's stored state (str)
    history          - rows with from_state / event / to_state (real transitions)
    available_events - events the *viewer* may fire right now (WorkflowEvent list)
    """
    current = WorkflowState(current_state)
    traversed = {(h["from_state"], h["event"], h["to_state"]) for h in history}
    visited = {INITIAL_STATE.value, current.value}
    for h in history:
        visited.add(h["from_state"])
        visited.add(h["to_state"])
    available = set(available_events)

    nodes = []
    for state, (x, y) in _LAYOUT.items():
        nodes.append({
            "name": state.value,
            "label": STATE_LABELS[state],
            "x": x - NODE_W / 2,
            "y": y - NODE_H / 2,
            "cx": x,
            "cy": y,
            "current": state == current,
            "visited": state.value in visited,
            "kind": "good" if state == WorkflowState.VERIFIED
                    else "bad" if state == WorkflowState.REJECTED else "",
        })

    edges = []
    for from_state, event, to_state in all_transitions():
        (x1, y1), (x2, y2) = _LAYOUT[from_state], _LAYOUT[to_state]
        dx, dy = x2 - x1, y2 - y1
        sx, sy = _clip(x1, y1, dx, dy)
        ex, ey = _clip(x2, y2, -dx, -dy)

        key = (from_state.value, event.value, to_state.value)
        if key in traversed:
            status = "traversed"
        elif from_state == current and event in available:
            status = "available"
        elif from_state == current:
            status = "waiting"  # defined here, but fired by someone else
        else:
            status = "idle"

        edges.append({
            "from": from_state.value,
            "event": event.value,
            "to": to_state.value,
            "x1": round(sx, 1), "y1": round(sy, 1),
            "x2": round(ex, 1), "y2": round(ey, 1),
            **_label_position(x1, y1, x2, y2, sx, sy, ex, ey),
            "status": status,
            "actor": REQUIRED_ACTOR[event].value.lower(),
        })

    return {
        "width": VIEW_W,
        "height": VIEW_H,
        "node_w": NODE_W,
        "node_h": NODE_H,
        "nodes": nodes,
        "edges": edges,
    }
