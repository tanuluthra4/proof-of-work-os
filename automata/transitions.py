from .states import WorkflowState, WorkflowEvent

TRANSITIONS = {
    WorkflowState.CREATED: {
        WorkflowEvent.START: WorkflowState.IN_PROGRESS
    },
    WorkflowState.IN_PROGRESS: {
        WorkflowEvent.REQUEST_EVIDENCE: WorkflowState.EVIDENCE_REQUIRED,
    },
    WorkflowState.EVIDENCE_REQUIRED: {
        WorkflowEvent.SUBMIT_EVIDENCE: WorkflowState.EVIDENCE_SUBMITTED
    },
    WorkflowState.EVIDENCE_SUBMITTED: {
        WorkflowEvent.START_VALIDATION: WorkflowState.VALIDATING
    },
    WorkflowState.VALIDATING: {
        WorkflowEvent.APPROVE: WorkflowState.VERIFIED,
        WorkflowEvent.REJECT: WorkflowState.REJECTED
    },
    WorkflowState.VERIFIED: {
        WorkflowEvent.RESTART: WorkflowState.IN_PROGRESS
    },
    WorkflowState.REJECTED: {
        WorkflowEvent.RESTART: WorkflowState.IN_PROGRESS
    }
}

def get_next_state(current_state: WorkflowState, event: WorkflowEvent) -> WorkflowState:
    """
    Get the next state based on the current state and event.

    Args:
        current_state (WorkflowState): The current state of the workflow.
        event (WorkflowEvent): The event that triggers the transition.

    Returns:
        WorkflowState: The next state after applying the event.

    Raises:
        ValueError: If the transition is not defined for the given state and event.
    """
    if current_state in TRANSITIONS and event in TRANSITIONS[current_state]:
        return TRANSITIONS[current_state][event]
    else:
        raise ValueError(f"No transition defined for state {current_state.value} with event {event.value}.")


def get_allowed_events(current_state: WorkflowState) -> list:
    """
    Get the list of allowed events for the current state.

    Args:
        current_state (WorkflowState): The current state of the workflow.

    Returns:
        list: The list of allowed events for the current state.
    """
    return list(TRANSITIONS.get(current_state, {}).keys())