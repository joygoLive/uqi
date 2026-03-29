"""
Perceval HOM (Hong-Ou-Mandel) 실험 - 단일 광자 버전
sim:ascella / sim:belenos 호환 (support_multi_photon=False)
단일 광자 |1,0> 입력으로 빔스플리터 통과 후 출력 분포 확인
"""

import perceval as pcvl
import perceval.components as comp

# 2모드 빔스플리터 회로 구성
circuit = pcvl.Circuit(2)
circuit.add(0, comp.BS())  # 50:50 빔스플리터

# 단일 광자 입력 (sim 호환)
input_state = pcvl.BasicState("|1,0>")

# RemoteProcessor로 실행 (UQIExtractor 호환)
import os
token = os.getenv("QUANDELA_TOKEN")
p = pcvl.RemoteProcessor("sim:ascella", token=token)
p.set_circuit(circuit)
p.with_input(input_state)
p.min_detected_photons_filter(1)

sampler = pcvl.algorithm.Sampler(p, max_shots_per_call=1024)
job_result = sampler.sample_count(1024)
print("결과:", job_result)
