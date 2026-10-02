from enum import Enum

class WorkflowState(Enum):
    """Enum representing the different states of a workflow."""
    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    EVIDENCE_SUBMITTED = "EVIDENCE_SUBMITTED"
    VALIDATING = "VALIDATING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"

class WorkflowEvent(Enum):
    """Enum representing the different events that can occur in a workflow."""
    START = "START"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    SUBMIT_EVIDENCE = "SUBMIT_EVIDENCE"
    START_VALIDATION = "START_VALIDATION"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    RESTART = "RESTART"