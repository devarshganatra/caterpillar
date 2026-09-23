"""
Machine State Classifier (hot path)
Deterministic rules with dwell and asymmetric hysteresis to classify machine state.
"""

from contracts.events import TelemetryFrame, MachineState

def classify_state(frame: TelemetryFrame, previous_state: MachineState) -> MachineState:
    pass
