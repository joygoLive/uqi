"""
Perceval 위상 스윕 실험 - 단일 광자 버전
sim:ascella 호환 (support_multi_photon=False)
BS + PS(θ) + BS 구조에서 θ를 0, π/2, π로 변화시켜 출력 분포 비교
"""

import perceval as pcvl
import perceval.components as comp
import numpy as np
import os

token = os.getenv("QUANDELA_TOKEN")
input_state = pcvl.BasicState("|1,0>")

for label, theta in [("0", 0), ("pi/2", np.pi / 2), ("pi", np.pi)]:
    circuit = pcvl.Circuit(2)
    circuit.add(0, comp.BS())
    circuit.add(0, comp.PS(theta))
    circuit.add(0, comp.BS())

    p = pcvl.RemoteProcessor("sim:ascella", token=token)
    p.set_circuit(circuit)
    p.with_input(input_state)
    p.min_detected_photons_filter(1)

    sampler = pcvl.algorithm.Sampler(p, max_shots_per_call=1024)
    job_result = sampler.sample_count(1024)
    print(f"theta={label}: {job_result}")
