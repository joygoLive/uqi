# uqi_qec.py
# 양자 오류 정정 (QEC) - 분석 + 기본 코드 적용
# UQI (Universal Quantum Infrastructure)
#
# 지원:
#   1. QEC 필요성 분석 (노이즈 시뮬 결과 기반)
#   2. QEC 코드 추천 (회로 특성 + QPU 에러율 기반)
#   3. 실제 인코딩 적용 (Bit-flip, Phase-flip)
#   4. 인코딩 전후 Fidelity 비교

import numpy as np
from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister


# ─────────────────────────────────────────────────────────
# QEC 필요성 분석
# ─────────────────────────────────────────────────────────

def analyze_qec_necessity(
        noise_comparison: dict,
        calibration:      dict = None,
        qc:               QuantumCircuit = None) -> dict:
    """
    노이즈 시뮬 결과 기반 QEC 필요성 분석

    Args:
        noise_comparison: UQINoise.compare() 반환값
                          {tvd, fidelity, ...}
        calibration:      uqi_calibration 데이터
        qc:               입력 회로 (T2 분석용)

    Returns:
        {
          necessity:  'unnecessary' | 'recommended' | 'required',
          fidelity:   float,
          tvd:        float,
          t2_ratio:   float | None,
          reasons:    [str],
          recommended_codes: [str],
        }
    """
    fidelity = noise_comparison.get("fidelity", 1.0)
    tvd      = noise_comparison.get("tvd", 0.0)
    reasons  = []
    codes    = []

    # ── T2 비율 계산 ──
    t2_ratio = None
    if calibration and qc:
        t2_ms = calibration.get("avg_t2_ms")
        q2_ns = calibration.get("avg_2q_ns")
        if t2_ms and q2_ns:
            t2_ns       = t2_ms * 1e6
            est_exec_ns = q2_ns * qc.depth()
            t2_ratio    = est_exec_ns / t2_ns

    # ── 판단 기준 ──
    # Fidelity 기반
    if fidelity >= 0.99:
        necessity = "unnecessary"
        reasons.append(f"Fidelity={fidelity:.4f} ≥ 0.99 → QEC 불필요")
    elif fidelity >= 0.95:
        necessity = "recommended"
        reasons.append(f"Fidelity={fidelity:.4f} 0.95~0.99 → QEC 권장")
        codes.extend(["bit_flip", "phase_flip"])
    else:
        necessity = "required"
        reasons.append(f"Fidelity={fidelity:.4f} < 0.95 → QEC 필수")
        codes.extend(["bit_flip", "phase_flip", "shor", "steane"])

    # TVD 기반 보완
    if tvd > 0.05:
        if necessity == "unnecessary":
            necessity = "recommended"
        reasons.append(f"TVD={tvd:.4f} > 0.05 → 분포 왜곡 심함")
        if "bit_flip" not in codes:
            codes.append("bit_flip")

    # T2 비율 기반 보완
    if t2_ratio is not None:
        if t2_ratio > 10:
            necessity = "required"
            reasons.append(f"T2 비율={t2_ratio:.1f}x → 코히어런스 손실 심각")
            if "steane" not in codes:
                codes.extend(["steane", "surface"])
        elif t2_ratio > 1:
            if necessity == "unnecessary":
                necessity = "recommended"
            reasons.append(f"T2 비율={t2_ratio:.1f}x → 코히어런스 손실 주의")

    # 회로 특성 기반 코드 추천 정제
    if qc:
        ops      = qc.count_ops()
        t_gates  = ops.get('t', 0) + ops.get('tdg', 0)
        total    = sum(ops.values())
        t_ratio  = t_gates / total if total > 0 else 0.0
        n_qubits = qc.num_qubits

        if t_ratio > 0.3:
            if "steane" not in codes and necessity != "unnecessary":
                codes.append("steane")
            reasons.append(f"T-gate 비중={t_ratio:.2f} → Steane 코드 적합")

        if n_qubits > 10:
            codes = [c for c in codes if c not in ["steane", "shor"]]
            reasons.append(f"{n_qubits}q 회로 → Surface code만 현실적")
            if necessity == "required" and "surface" not in codes:
                codes.append("surface")

    return {
        "necessity":         necessity,
        "fidelity":          fidelity,
        "tvd":               tvd,
        "t2_ratio":          t2_ratio,
        "reasons":           reasons,
        "recommended_codes": list(dict.fromkeys(codes)),  # 순서 유지 중복 제거
    }


# ─────────────────────────────────────────────────────────
# QEC 코드 정보
# ─────────────────────────────────────────────────────────

QEC_CODES = {
    "bit_flip": {
        "name":        "Bit-flip Code",
        "qubits":      3,
        "description": "X 에러 보호 (3큐비트)",
        "overhead":    3.0,
        "implemented": True,
    },
    "phase_flip": {
        "name":        "Phase-flip Code",
        "qubits":      3,
        "description": "Z 에러 보호 (3큐비트)",
        "overhead":    3.0,
        "implemented": True,
    },
    "shor": {
        "name":        "Shor Code",
        "qubits":      9,
        "description": "X+Z 에러 보호 (9큐비트)",
        "overhead":    9.0,
        "implemented": False,  # 향후 구현
    },
    "steane": {
        "name":        "Steane Code",
        "qubits":      7,
        "description": "T-gate 최적화 (7큐비트)",
        "overhead":    7.0,
        "implemented": False,  # 향후 구현
    },
    "surface": {
        "name":        "Surface Code",
        "qubits":      "d²+(d-1)²",
        "description": "대규모 범용 (분석만)",
        "overhead":    "d²",
        "implemented": False,
    },
}


# ─────────────────────────────────────────────────────────
# Bit-flip 코드 인코딩
# ─────────────────────────────────────────────────────────

def encode_bit_flip(qc: QuantumCircuit) -> QuantumCircuit:
    """
    Bit-flip 코드 인코딩
    각 논리 큐비트를 3개의 물리 큐비트로 인코딩
    |0⟩ → |000⟩, |1⟩ → |111⟩

    Args:
        qc: 원본 회로 (측정 없음)

    Returns:
        인코딩된 회로
    """
    n = qc.num_qubits
    n_enc = n * 3  # 인코딩 후 큐비트 수

    # 인코딩 레지스터
    qr_enc  = QuantumRegister(n_enc, 'q')
    cr_enc  = ClassicalRegister(n, 'c')
    qc_enc  = QuantumCircuit(qr_enc, cr_enc)

    # ── 인코딩: |ψ⟩ → |ψψψ⟩ ──
    for i in range(n):
        # 각 논리 큐비트 i → 물리 큐비트 3i, 3i+1, 3i+2
        qc_enc.cx(qr_enc[3*i], qr_enc[3*i+1])
        qc_enc.cx(qr_enc[3*i], qr_enc[3*i+2])

    # ── 원본 회로 게이트 적용 (논리 큐비트 → 물리 큐비트 0번) ──
    for inst in qc.data:
        op_name  = inst.operation.name
        if op_name in ['barrier', 'measure']:
            continue
        # 각 게이트를 물리 큐비트 0번(대표)에 적용
        new_qargs = []
        for q in inst.qubits:
            idx = qc.find_bit(q).index
            new_qargs.append(qr_enc[3*idx])
        qc_enc.append(inst.operation, new_qargs)

    # ── 오류 정정 신드롬 측정 ──
    for i in range(n):
        anc = QuantumRegister(2, f'anc{i}')
        cr  = ClassicalRegister(2, f's{i}')
        qc_enc.add_register(anc)
        qc_enc.add_register(cr)

        # 신드롬 측정
        qc_enc.cx(qr_enc[3*i],   anc[0])
        qc_enc.cx(qr_enc[3*i+1], anc[0])
        qc_enc.cx(qr_enc[3*i+1], anc[1])
        qc_enc.cx(qr_enc[3*i+2], anc[1])
        qc_enc.measure(anc[0], cr[0])
        qc_enc.measure(anc[1], cr[1])

        # 오류 정정 (if_else 없이 단순 다수결)
        # 실제 정정은 고전 후처리로 수행

    # ── 디코딩 측정 ──
    for i in range(n):
        qc_enc.measure(qr_enc[3*i], cr_enc[i])

    return qc_enc


def encode_phase_flip(qc: QuantumCircuit) -> QuantumCircuit:
    """
    Phase-flip 코드 인코딩
    각 논리 큐비트를 3개의 물리 큐비트로 인코딩
    |+⟩ → |+++⟩, |-⟩ → |---⟩

    Args:
        qc: 원본 회로 (측정 없음)

    Returns:
        인코딩된 회로
    """
    n     = qc.num_qubits
    n_enc = n * 3

    qr_enc = QuantumRegister(n_enc, 'q')
    cr_enc = ClassicalRegister(n, 'c')
    qc_enc = QuantumCircuit(qr_enc, cr_enc)

    # ── 인코딩: Hadamard 기저로 변환 후 복사 ──
    for i in range(n):
        qc_enc.cx(qr_enc[3*i], qr_enc[3*i+1])
        qc_enc.cx(qr_enc[3*i], qr_enc[3*i+2])
        qc_enc.h(qr_enc[3*i])
        qc_enc.h(qr_enc[3*i+1])
        qc_enc.h(qr_enc[3*i+2])

    # ── 원본 회로 게이트 적용 ──
    for inst in qc.data:
        op_name = inst.operation.name
        if op_name in ['barrier', 'measure']:
            continue
        new_qargs = []
        for q in inst.qubits:
            idx = qc.find_bit(q).index
            new_qargs.append(qr_enc[3*idx])
        qc_enc.append(inst.operation, new_qargs)

    # ── 디코딩: H 역변환 후 측정 ──
    for i in range(n):
        qc_enc.h(qr_enc[3*i])
        qc_enc.h(qr_enc[3*i+1])
        qc_enc.h(qr_enc[3*i+2])
        qc_enc.cx(qr_enc[3*i], qr_enc[3*i+1])
        qc_enc.cx(qr_enc[3*i], qr_enc[3*i+2])
        qc_enc.measure(qr_enc[3*i], cr_enc[i])

    return qc_enc


# ─────────────────────────────────────────────────────────
# 오버헤드 측정
# ─────────────────────────────────────────────────────────

def measure_overhead(qc_orig: QuantumCircuit,
                     qc_enc:  QuantumCircuit) -> dict:
    """
    QEC 인코딩 오버헤드 측정

    Returns:
        {qubit_overhead, gate_overhead, depth_overhead}
    """
    orig_q = qc_orig.num_qubits
    enc_q  = qc_enc.num_qubits
    orig_g = sum(qc_orig.count_ops().values())
    enc_g  = sum(qc_enc.count_ops().values())

    return {
        "orig_qubits":    orig_q,
        "enc_qubits":     enc_q,
        "qubit_overhead": round(enc_q / orig_q, 1),
        "orig_gates":     orig_g,
        "enc_gates":      enc_g,
        "gate_overhead":  round(enc_g / orig_g, 1),
        "orig_depth":     qc_orig.depth(),
        "enc_depth":      qc_enc.depth(),
        "depth_overhead": round(qc_enc.depth() / qc_orig.depth(), 1),
    }


# ─────────────────────────────────────────────────────────
# 메인 인터페이스
# ─────────────────────────────────────────────────────────

class UQIQEC:
    """
    UQI 양자 오류 정정 관리자

    1. QEC 필요성 분석
    2. QEC 코드 추천
    3. Bit-flip / Phase-flip 인코딩 적용
    4. 인코딩 전후 Fidelity 비교 (노이즈 시뮬)
    """

    def __init__(self, calibration: dict = None):
        self.calibration = calibration or {}

    def analyze(self,
                noise_comparison: dict,
                qc: QuantumCircuit = None) -> dict:
        """QEC 필요성 분석 + 코드 추천"""
        result = analyze_qec_necessity(
            noise_comparison, self.calibration, qc)

        print(f"\n  [QEC] 분석 결과: {result['necessity'].upper()}")
        print(f"    Fidelity={result['fidelity']:.4f} "
              f"TVD={result['tvd']:.4f}")
        if result['t2_ratio']:
            print(f"    T2 비율={result['t2_ratio']:.2f}x")
        for r in result['reasons']:
            print(f"    • {r}")
        if result['recommended_codes']:
            print(f"    추천 코드: {result['recommended_codes']}")

        return result

    def encode(self,
               qc:       QuantumCircuit,
               code:     str) -> QuantumCircuit:
        """
        QEC 코드 인코딩 적용

        Args:
            qc:   원본 회로 (측정 제거 상태)
            code: 'bit_flip' | 'phase_flip'

        Returns:
            인코딩된 회로
        """
        info = QEC_CODES.get(code, {})
        if not info.get("implemented"):
            raise NotImplementedError(
                f"{code} 코드는 미구현 (향후 지원 예정)")

        print(f"\n  [QEC] 인코딩: {info['name']} "
              f"(큐비트 {qc.num_qubits}q → "
              f"{qc.num_qubits * info['qubits']}q)")

        qc_no_meas = qc.remove_final_measurements(inplace=False)

        if code == "bit_flip":
            return encode_bit_flip(qc_no_meas)
        elif code == "phase_flip":
            return encode_phase_flip(qc_no_meas)
        else:
            raise ValueError(f"미지원 코드: {code}")

    def compare_fidelity(self,
                         qc:       QuantumCircuit,
                         code:     str,
                         qpu_name: str,
                         shots:    int = 1024,
                         noise=None) -> dict:
        from uqi_noise import UQINoise

        # noise 캐시 사용, 없으면 새로 생성
        if noise is None:
            noise = UQINoise(qpu_name, self.calibration)

        # ── 인코딩 전 ──
        print(f"\n  [QEC] 인코딩 전 노이즈 시뮬...")
        ideal_counts  = noise.simulate_ideal(qc, shots)
        noisy_counts  = noise.simulate(qc, sdk="qiskit", shots=shots)
        cmp_before    = noise.compare(
            ideal_counts, noisy_counts,
            label_a="ideal", label_b="before_qec")

        # ── 인코딩 ──
        qc_enc    = self.encode(qc, code)
        overhead  = measure_overhead(qc, qc_enc)

        print(f"  [QEC] 오버헤드: "
              f"큐비트 {overhead['orig_qubits']}→{overhead['enc_qubits']} "
              f"({overhead['qubit_overhead']}x) "
              f"게이트 {overhead['orig_gates']}→{overhead['enc_gates']} "
              f"({overhead['gate_overhead']}x)")

        # ── 인코딩 후 노이즈 시뮬 (같은 noise 인스턴스 재사용) ──
        print(f"  [QEC] 인코딩 후 노이즈 시뮬...")
        ideal_enc    = noise.simulate_ideal(qc_enc, shots)
        noisy_enc    = noise.simulate(qc_enc, sdk="qiskit", shots=shots)

        # 논리 비트만 추출 (하위 n비트)
        n = qc.num_qubits
        def extract_logical(counts, n_bits):
            result = {}
            for k, v in counts.items():
                key = k.replace(" ", "")
                logical = key[-n_bits:]  # 하위 n비트 = 논리 큐비트
                result[logical] = result.get(logical, 0) + v
            return result

        ideal_enc_logical = extract_logical(ideal_enc, n)
        noisy_enc_logical = extract_logical(noisy_enc, n)

        cmp_after    = noise.compare(
            ideal_enc_logical, noisy_enc_logical,
            label_a="ideal_enc", label_b="after_qec")

        improvement = cmp_after["fidelity"] - cmp_before["fidelity"]
        print(f"\n  [QEC] Fidelity 변화: "
              f"{cmp_before['fidelity']:.4f} → {cmp_after['fidelity']:.4f} "
              f"({'↑' if improvement > 0 else '↓'}{abs(improvement):.4f})")

        return {
            "before":      {"counts": noisy_counts, **cmp_before},
            "after":       {"counts": noisy_enc,    **cmp_after},
            "improvement": round(improvement, 4),
            "overhead":    overhead,
            "code":        code,
            "qpu_name":    qpu_name,
        }

    def recommend(self,
                  qc:               QuantumCircuit,
                  noise_comparison: dict) -> str:
        """
        최적 QEC 코드 추천

        Returns:
            추천 코드명 또는 'none'
        """
        analysis = self.analyze(noise_comparison, qc)

        if analysis["necessity"] == "unnecessary":
            return "none"

        codes = analysis["recommended_codes"]
        # 구현된 코드 중 첫번째 선택
        for code in codes:
            if QEC_CODES.get(code, {}).get("implemented"):
                return code

        return codes[0] if codes else "none"