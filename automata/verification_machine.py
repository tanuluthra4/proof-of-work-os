from .states import WorkflowEvent, WorkflowState
from .transitions import (
    INITIAL_STATE,
    InvalidTransitionError,
    get_allowed_events,
    get_next_state,
)


class VerificationMachine:
    """An in-memory run of the verification automaton.

    It only knows the transition table. Persistence, permissions and
    evidence rules live in services/automata_engine.py.
    """

    def __init__(self, initial_state: WorkflowState = INITIAL_STATE):
        self.current_state = initial_state

    def can_transition(self, event: WorkflowEvent) -> bool:
        return event in get_allowed_events(self.current_state)

    def get_allowed_events(self) -> list:
        return get_allowed_events(self.current_state)

    def transition(self, event: WorkflowEvent) -> dict:
        """Apply `event`; raises InvalidTransitionError (a ValueError) if undefined."""
        if not self.can_transition(event):
            raise InvalidTransitionError(
                f"Invalid transition: {self.current_state.value} + {event.value}"
            )

        previous_state = self.current_state
        self.current_state = get_next_state(previous_state, event)

        return {
            "from_state": previous_state.value,
            "event": event.value,
            "to_state": self.current_state.value,
        }

    def is_verified(self) -> bool:
        return self.current_state == WorkflowState.VERIFIED

    def is_rejected(self) -> bool:
        return self.current_state == WorkflowState.REJECTED
