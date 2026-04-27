# QPU 비교 (UQI 통합 vendor 종합)

> **마지막 업데이트**: 2026-04-28
> **출처**: 본 세션 직접 검증 + 공식 pricing 페이지 + UQI 통합 코드 (`uqi_pricing.py`, `uqi_calibration.py`)

기준 회로: **Bell state (H + CX, 100 shots)** — 단순 2-qubit gate 회로 기준.
> 더 깊은 회로는 비용/시간 모두 증가. Pasqal은 회로 길이가 비용에 직접 영향, IonQ/Rigetti는 shot 수만 영향.

---

## 📊 종합 비교표 — 비용 + 실행시간 + Fidelity

| QPU | Qubits | 2Q Fid | 100 shots 비용 | 실행시간 (wall-clock) | 비용/시간 효율 ($/min) | 게이트웨이 | 데이터 출처 |
|-----|--------|--------|---------------|---------------------|-----------------------|----------|----------|
| **IBM Heron 계열** (fez/marrakesh/kingston) | 133+ | ~99.7% | **$0** (Open Plan) | ~30~60초 (Open Plan 큐) | 무료 | 자사 | Live |
| **IQM Garnet** | 20 | (확인 필요) | ~15 credits (지원) | ~30초 | (지원 크레딧) | 자사 | Live |
| **IQM Emerald** | 54 | (확인 필요) | ~22 credits (지원) | ~30초 | (지원 크레딧) | 자사 | Live |
| **IQM Sirius** | 150 | (확인 필요) | ~9 credits (지원) | ~30초 | (지원 크레딧) | 자사 | Live |
| **Rigetti Cepheus-1-108Q** ⭐ | 108 | **99.1%** | **$0.34** | ~5분 (큐 비어있을 때, 가용 윈도우 내) | $0.07/min | AWS Braket | Live |
| **QuEra Aquila** | 256 | analog | $1.30 | 수 분 (요일별 가용 시간 다름) | (analog) | AWS Braket | Live |
| **IonQ Forte-1** | 36 | **97% 실측** (이론 ~99%) | **$8.30** | **357초 (5분 57초)** ✅ 실측 | **$1.39/min** | AWS Braket | Live |
| **Pasqal Fresnel (West US)** | 100 | analog | **$54+** (60s 가정), **$540** (10min) | 수십 초 ~ 수 분 (Degraded 상태) | $54/min ⚠️⚠️ | Azure Quantum | Live |
| **Pasqal Fresnel-CAN1** | 100 | analog | $54+ | **큐 대기 ~46시간** ⚠️ | 동일 | Azure Quantum | Live |
| **Quandela qpu:ascella/belenos** | photonic | (다른 의미) | **$0** (월 200 credits 무료) | 수 초 ~ 수십 초 | 무료 | 자사 | Live |
| **Quantinuum H2-2** | 56 | **99.92%** | (HQC, Nexus 보류) | (별도) | 별도 | Nexus 보류 | 정적 (1년 경과) |
| **Quantinuum H2-1** | 56 | 99.9% | (HQC, Nexus 보류) | (별도) | 별도 | Nexus 보류 | 정적 (1년 경과) |
| **Quantinuum H1-1** | 20 | 99.9% | (HQC, Nexus 보류) | (별도) | 별도 | Nexus 보류 | 정적 (1년 경과) |

### 시뮬레이터 (참고)

| Sim | 100 shots 비용 | 실행시간 | 비고 |
|-----|---------------|---------|------|
| **Braket SV1** | **$0.0125** ✅ 실측 | **~6초** ✅ 실측 | 시간 기반 ($0.075/min) |
| Braket DM1 | ~동일 | ~동일 | density matrix |
| Braket TN1 | ~$0.027 | 비슷 | tensor network |
| Local Simulator | 무료 | <1초 | braket SDK 내장 |
| sim:ascella/belenos (Quandela) | 무료 | 수 초 | photonic sim |

---

## 🥇 순위 (단순 비교)

### 비용 (저렴 → 비싸게)
```
1. 무료:   IBM Open / Quandela / IQM (지원)
2. ~$0.5: Rigetti Cepheus ($0.34) ⭐
3. ~$1:   QuEra Aquila ($1.30, AHS only)
4. ~$10:  IonQ Forte-1 ($8.30)
5. $50+:  Pasqal Fresnel ($54+, 시간 기반)
```

### Fidelity (고 → 저, 게이트 모델 기준)
```
1. Quantinuum H2-2:   99.92% (정적 데이터, 1년 경과 — 신뢰도 ↓)
2. Quantinuum H2-1:   99.9%
3. Quantinuum H1-1:   99.9%
4. IBM Heron:        ~99.7%
5. Rigetti Cepheus:   99.1%
6. IonQ Forte-1:     ~99% (이론), 97% (본 세션 실측)
```

### 가성비 (Fidelity ÷ 비용, 게이트 회로 100 shots 기준)
```
1. IBM Open Plan:        99.7% / $0  → ∞ (무료 한도 내)
2. Rigetti Cepheus:      99.1% / $0.34 → 가성비 최고 ⭐
3. IonQ Forte-1:         97% / $8.30  → 24배 비싸지만 all-to-all
4. Pasqal Fresnel:       analog / $54+ → 매우 비쌈, 분석 신중
```

### 실행 효율 (실 양자 시간 vs wall-clock)
**IonQ Forte-1 (실측)**:
- 이론 양자 작업: 70ms (100 shots × 700μs)
- 실측 wall-clock: 357,000ms
- **양자 vs 전체 비율: 0.02%** (큐/스케줄링이 dominant)

→ 양자 컴퓨팅 현재의 한계. 실 게이트 시간보다 큐 대기/스케줄링이 압도적.

---

## 🕐 가용 시간 / 큐 상태 (본 세션 직접 조회, 2026-04-28 KST)

| QPU | 상태 | 가용 윈도우 (UTC) | 큐 대기 |
|-----|------|----------------|---------|
| **IonQ Forte-1** | ONLINE | 매일 00:00–17:59, 18:30–24:00 (수요일 17:45–23:00 점검) | 0 (실측) |
| **Rigetti Cepheus** | ONLINE | 매일 00–07, 09–19, 21–24 (3개 윈도우) | 0 (실측) |
| **QuEra Aquila** | ONLINE | 요일별 다양 (Mon 01:00–23:59, 다른 날 ~12시간) | (조회) |
| **Pasqal Fresnel (West US)** | **DEGRADED** ⚠️ | (Azure 24/7 모델, 단 점검 가능성) | 0 |
| **Pasqal Fresnel-CAN1** | AVAILABLE | (Azure 24/7) | **~166,748초 ≈ 46시간** ⚠️ |

> 가용성 정보는 UQI의 `_build_qpu_submit_message`가 자동 표시 (Braket/Azure 디바이스만).

---

## 🔬 본 세션 직접 측정 데이터

### IonQ Forte-1 — 첫 실 양자 알고리즘 실행
```
회로:     Bell state (H + CX, 2Q)
Shots:    100
비용:     $8.30 (~ 11,500원)
시간:     357초 wall-clock
이론:     70ms 양자 작업 (오버헤드 5,100배)

결과:
  |00>: 49 (이론 50)  ✓
  |01>:  1 (이론  0)  ← noise
  |10>:  2 (이론  0)  ← noise
  |11>: 48 (이론 50)  ✓

Entanglement Fidelity: 97% (이론 100%)
```

### SV1 Simulator — 동일 회로 비교
```
회로:     Bell state, 100 shots
비용:     $0.0125 (~17원, IonQ의 1/664)
시간:     6초 (IonQ의 1/60)
결과:    |00>:54, |11>:46 (노이즈 0)
Fidelity: 100% (시뮬레이터 ideal)
```

→ **SV1 vs IonQ 비교**: 비용 664배, 시간 60배, fidelity -3pp (실 노이즈 측정 값).

### Azure Quantum end-to-end 검증 (Quantinuum syntax checker)
```
Target:   quantinuum.sim.h2-1sc (syntax checker)
회로:     Bell state, 100 shots
비용:     1 eHQC (무료 한도 내)
시간:     19.4초 wall-clock (인증 + 제출 + 폴링 17s + 결과)
결과:    {'00': 100}  ← placeholder (syntax checker는 실행 안 함, 문법만 검증)
검증 흐름: authn → backend.run() → status poll → result.get_counts() ✅
```

→ **Azure SDK end-to-end 정상 동작 확인** (Pasqal Fresnel은 Pulser 비호환이라
   gate 회로로는 직접 검증 불가, 대안으로 Quantinuum syntax checker 활용).

### 비용 함정 발견
- IonQ default 1024 shots = **$82.22** (~ 11만원)
- UQI에서 IonQ 선택 시 자동으로 100 shots로 다운조정 (mcp_server.py 가드)

---

## 🎯 용도별 추천

| 목적 | 추천 QPU | 이유 |
|------|---------|------|
| **무료 학습/실험** | IBM Open + Quandela + IQM (지원) | 비용 0 또는 무한 한도 |
| **저렴한 실 디바이스 검증** | **Rigetti Cepheus** ⭐ | $0.34/100shots, 99.1% fid, 108Q |
| **All-to-all 연결성** | IonQ Forte-1 | 이온트랩 native, 비싸지만 topology 우위 |
| **대규모 회로 (100Q+)** | Rigetti Cepheus / QuEra | 108Q / 256Q (analog) |
| **최고 fidelity (정밀 회로)** | Quantinuum H2-2 (99.92%) | 단 Nexus 계약 + 데이터 정적 |
| **MIS / QAOA / 양자 다체** | QuEra Aquila / Pasqal Fresnel | analog Rydberg blockade |
| **광자 양자 컴퓨팅** | Quandela qpu:ascella/belenos | 무료, photonic |

---

## ⚠️ 주의사항 (본 세션 발견 함정)

| 함정 | 영향 | 회피 |
|------|------|------|
| IonQ default 1024 shots = $82 | 큰 비용 발생 | UQI에서 자동 100으로 다운조정 ✅ |
| Pasqal 시간 기반 과금 | $540+ (10분 회로) | runtime 신중 결정, UQI에서 자동 경고 |
| Pasqal Fresnel-CAN1 큐 ~46시간 | 결과 매우 늦음 | UQI 가용성 사전 표시 ✅ |
| QuEra/Pasqal gate 회로 비호환 | 일반 알고리즘 못 돌림 | UQI에서 SUBMIT_BLOCK 자동 차단 ✅ |
| Quantinuum 데이터 1년 경과 | 분석 신뢰도 ↓ | UQI 메시지에 자동 경고 ✅ |
| Rigetti Ankaa-3 RETIRED | 사용 불가 | UQI 카탈로그 제거 ✅ |
| IonQ Aria-1 RETIRED | 사용 불가 | UQI 카탈로그 제거 ✅ |

---

## 🔄 데이터 출처 분류 (UQI `data_source` 필드)

| 출처 | 표시 | 적용 vendor |
|------|------|-----------|
| **자사 클라우드 직접** | (표시 안 함) | IBM, IQM, Quandela |
| **AWS Braket 게이트웨이** | "AWS Braket 게이트웨이 경유" | IonQ, Rigetti, QuEra |
| **Azure Quantum 게이트웨이** | "Azure Quantum 게이트웨이 경유" | Pasqal |
| **정적 번들 (pytket-quantinuum)** | "정적 번들 (pytket-quantinuum OFFLINE)" + 신뢰도 경고 | Quantinuum (Nexus 통합 전까지) |

---

## 📌 본 세션에서 통합 완료된 vendor

```
✅ AWS Braket    : IonQ Forte-1, Rigetti Cepheus-1-108Q, QuEra Aquila
✅ Azure Quantum : Pasqal Fresnel (West US + CAN1)
✅ 자사 클라우드  : IBM Open Plan, IQM Resonance, Quandela Hub
⏸  보류         : Quantinuum (Nexus 유료계약 불가)
⏸  보류         : Analog QPU 워크플로우 (QuEra+Pasqal Pulser 알고리즘 통합)
```

---

## 🔗 참고

- AWS Braket 가격: https://aws.amazon.com/braket/pricing/
- Azure Quantum 가격: https://learn.microsoft.com/en-us/azure/quantum/pricing
- UQI 가격 모델 코드: `src/uqi_pricing.py`
- UQI 메시지 빌더: `src/mcp_server.py:_build_qpu_submit_message`
- 가용성 체크: `src/uqi_executor_braket.py:check_device_availability`, `src/uqi_executor_azure.py:check_device_availability_azure`
