from .states import WorkflowState, WorkflowEvent
from .transitions import get_next_state, get_allowed_events

class VerificationMachine:
    """
    A class representing a verification machine that manages the state of a workflow.
    """

    def __init__(self, initial_state = WorkflowState.CREATED):
        self.current_state = initial_state

    def can_transition(self, event: WorkflowEvent) -> bool:
        """
        Check if a transition is allowed for the current state and event.

        Args:
            event (WorkflowEvent): The event to check.

        Returns:
            bool: True if the transition is allowed, False otherwise.
        """
        return event in get_allowed_events(self.current_state)

    def get_allowed_events(self) -> list:
        """
        Get the list of allowed events for the current state.

        Returns:
            list: The list of allowed events for the current state.
        """
        return get_allowed_events(self.current_state)

    def transition(self, event: WorkflowEvent):
        """
        Transition to the next state based on the current state and event.

        Args:
            event (WorkflowEvent): The event that triggers the transition.

        Raises:
            ValueError: If the transition is not defined for the current state and event.
        """

        if not self.can_transition(event):
            raise ValueError(f"Transition not allowed from state {self.current_state.value} with event {event.value}.")
        
        next_state = get_next_state(self.current_state, event)

        previous_state = self.current_state
        self.current_state = next_state

        return {
            "from state": previous_state.value,
            "event": event.value,
            "to state": self.current_state.value,
        }

    def is_verified(self) -> bool:
        """
        Check if the current state is VERIFIED.

        Returns:
            bool: True if the current state is VERIFIED, False otherwise.
        """
        return self.current_state == WorkflowState.VERIFIED

    def is_rejected(self) -> bool:
        """
        Check if the current state is REJECTED.

        Returns:
            bool: True if the current state is REJECTED, False otherwise.
        """
        return self.current_state == WorkflowState.REJECTED