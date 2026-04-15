"""
Perceval 3모드 빔스플리터 체인 - 단일 광자 버전
sim:ascella 호환 (support_multi_photon=False)
3개 모드에 BS를 연쇄 배치하여 광자 분배 확인
"""

import perceval as pcvl
import perceval.components as comp

# 3모드 회로: BS(0,1) → BS(1,2)
circuit = pcvl.Circuit(3)
circuit.add(0, comp.BS())  # 모드 0,1 빔스플리터
circuit.add(1, comp.BS())  # 모드 1,2 빔스플리터

# 단일 광자를 모드 0에 입력
input_state = pcvl.BasicState("|1,0,0>")

# RemoteProcessor로 실행
import os
token = os.getenv("QUANDELA_TOKEN")
p = pcvl.RemoteProcessor("sim:ascella", token=token)
p.set_circuit(circuit)
p.with_input(input_state)
p.min_detected_photons_filter(1)

sampler = pcvl.algorithm.Sampler(p, max_shots_per_call=1024)
job_result = sampler.sample_count(1024)
print("3모드 결과:", job_result)
