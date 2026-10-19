from enum import Enum


class WorkflowState(Enum):
    """The states (Q) of the verification automaton."""
    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    EVIDENCE_SUBMITTED = "EVIDENCE_SUBMITTED"
    VALIDATING = "VALIDATING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class WorkflowEvent(Enum):
    """The input alphabet (Sigma) of the verification automaton."""
    START = "START"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    SUBMIT_EVIDENCE = "SUBMIT_EVIDENCE"
    START_VALIDATION = "START_VALIDATION"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    RESTART = "RESTART"


class Actor(Enum):
    """Who is allowed to fire an event."""
    OWNER = "OWNER"        # the developer who owns the task
    REVIEWER = "REVIEWER"  # a reviewer who does NOT own the task
