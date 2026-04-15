"""
Perceval Mach-Zehnder 간섭계 - 단일 광자 버전
sim:ascella 호환 (support_multi_photon=False)
위상 시프터(π/4)를 포함한 MZI로 출력 분포 비대칭 확인
"""

import perceval as pcvl
import perceval.components as comp
import numpy as np

# 2모드 Mach-Zehnder 간섭계
circuit = pcvl.Circuit(2)
circuit.add(0, comp.BS())           # 첫 번째 빔스플리터
circuit.add(0, comp.PS(np.pi / 4))  # 위상 시프터 (π/4)
circuit.add(0, comp.BS())           # 두 번째 빔스플리터

# 단일 광자 입력
input_state = pcvl.BasicState("|1,0>")

# RemoteProcessor로 실행
import os
token = os.getenv("QUANDELA_TOKEN")
p = pcvl.RemoteProcessor("sim:ascella", token=token)
p.set_circuit(circuit)
p.with_input(input_state)
p.min_detected_photons_filter(1)

sampler = pcvl.algorithm.Sampler(p, max_shots_per_call=1024)
job_result = sampler.sample_count(1024)
print("MZI 결과:", job_result)
