# test_blocked_patterns.py
# Security test file — contains intentionally blocked patterns for testing
# DO NOT use in production

from qiskit import QuantumCircuit
import numpy as np

def build_circuit():
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure_all()
    return qc

# The following lines contain blocked patterns (for security testing only)
import subprocess  # BLOCKED: subprocess module
result = subprocess.run(['ls', '-la'], capture_output=True)

circuit = build_circuit()
