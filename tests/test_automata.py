from automata.states import WorkflowEvent
from automata.verification_machine import VerificationMachine


machine = VerificationMachine()

print("Initial state:", machine.current_state.value)

try:
    machine.transition(WorkflowEvent.APPROVE)
except ValueError as error:
    print("Blocked:", error)