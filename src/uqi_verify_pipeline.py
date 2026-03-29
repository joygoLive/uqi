# uqi_verify_pipeline.py
# PennyLane → QIR → IBM/IQM 파이프라인 검증
# 사용법: python uqi_verify_pipeline.py -f <알고리즘파일.py>

import argparse
import sys

from uqi_extractor import UQIExtractor
from uqi_qir_converter import UQIQIRConverter
from uqi_executor_ibm import UQIExecutorIBM
from uqi_executor_iqm import UQIExecutorIQM


def run_verification(
    algorithm_file: str,
    use_ibm_simulator: bool = True,
    use_iqm_simulator: bool = True,
    use_perceval_simulator: bool = True,
    shots: int = 1024,
    ibm_backend: str = "ibm_fez",
    iqm_backend_url: str = "https://resonance.meetiqm.com/computers/garnet",
):
    from dotenv import load_dotenv
    import os
    load_dotenv()

    ibm_token      = os.getenv("IBM_QUANTUM_TOKEN")
    iqm_token      = os.getenv("IQM_QUANTUM_TOKEN")
    quandela_token = os.getenv("QUANDELA_TOKEN")

    print("=" * 60)
    print("UQI Pipeline Verification")
    print(f"파일: {algorithm_file}")
    print(f"IBM 토큰: {'✓' if ibm_token else '✗ 없음'}")
    print(f"IQM 토큰: {'✓' if iqm_token else '✗ 없음'}")
    print(f"Quandela 토큰: {'✓' if quandela_token else '✗ 없음'}")
    print("=" * 60)

    steps = {
        "detect":    False,
        "extract":   False,
        "qasm":      False,
        "qir":       False,
        "ibm":       False,
        "iqm":       False,
        "cudaq_iqm": False,
        "cudaq_ibm": False,
        "perceval":  False,
    }

    # ── Step 1: Framework 감지 ──────────────
    print("\n[Step 1] Framework 감지")
    extractor = UQIExtractor(algorithm_file)
    try:
        framework = extractor.detect_framework()
        print(f"  → {framework}")
        steps["detect"] = True
    except Exception as e:
        print(f"  ✗ 실패: {e}")
        _print_summary(steps)
        return steps

    if framework not in ('PennyLane', 'Qrisp', 'CUDAQ', 'Qiskit', 'Perceval'):
        print(f"  ✗ 현재 검증 범위 외 framework: {framework}")
        _print_summary(steps)
        return steps

    # ── Step 2: 회로 추출 ───────────────────
    print("\n[Step 2] 회로 추출 (monkey patch 기반)")
    try:
        extractor.extract_circuits()

        if framework == 'CUDAQ':
            if not extractor.cudaq_kernels:
                print("  ✗ 추출된 커널 없음")
                _print_summary(steps)
                return steps
            for name in extractor.cudaq_kernels:
                print(f"  [{name}]")
            steps["extract"] = True
            print("  → PASS")

        elif framework == 'Perceval':
            if not extractor.perceval_circuits:
                print("  ✗ 추출된 회로 없음")
                _print_summary(steps)
                return steps
            for name, (circuit, input_state) in extractor.perceval_circuits.items():
                print(f"  [{name}]")
                m = circuit.m if circuit else 0
                print(f"    모드 수: {m}")
                print(f"    입력 상태: {input_state}")
            steps["extract"] = True
            print("  → PASS")

        else:
            if not extractor.tapes and not extractor.sessions and not extractor.circuits:
                print("  ✗ 추출된 회로 없음")
                _print_summary(steps)
                return steps
            if extractor.tapes:
                for name in extractor.tapes:
                    extractor.print_tape_info(name)
            elif extractor.sessions:
                for name, session in extractor.sessions.items():
                    print(f"  [{name}]")
                    print(f"    큐비트 수: {len(session.qubits)}")
            elif extractor.circuits:
                for name, circuit in extractor.circuits.items():
                    print(f"  [{name}]")
                    print(f"    큐비트 수: {circuit.num_qubits}")
                    print(f"    게이트 수: {len(circuit.data)}")
            steps["extract"] = True
            print("  → PASS")

    except Exception as e:
        print(f"  ✗ 실패: {e}")
        _print_summary(steps)
        return steps

    # ── CUDAQ 전용 경로 ──────────────────────
    if framework == 'CUDAQ':
        print(f"\n[Step 7] CUDAQ IQM 실행 (emulator={use_iqm_simulator})")
        from uqi_executor_cudaq import UQIExecutorCUDAQ
        executor_cudaq = UQIExecutorCUDAQ(extractor, shots=shots)
        executor_cudaq.run_all(
            target="iqm",
            backend_url="https://cocos.resonance.meetiqm.com/garnet",
            use_emulator=use_iqm_simulator,
            token=iqm_token,
            run_ibm=True,
            use_ibm_simulator=use_ibm_simulator,
            ibm_backend_name=ibm_backend,
            ibm_token=ibm_token,
        )
        executor_cudaq.print_summary()
        if any(r["ok"] for r in executor_cudaq.results.values()):
            steps["cudaq_iqm"] = True
        if any(r["ok"] and "_ibm" in n for n, r in executor_cudaq.results.items()):
            steps["cudaq_ibm"] = True
        _print_summary(steps)
        return steps

    # ── Perceval 전용 경로 ───────────────────
    if framework == 'Perceval':
        print(f"\n[Step 7] Perceval Quandela 실행 (simulator={use_perceval_simulator})")
        from uqi_executor_perceval import UQIExecutorPerceval
        executor_perceval = UQIExecutorPerceval(extractor, shots=shots)
        executor_perceval.run_all(
            use_simulator=use_perceval_simulator,
            token=quandela_token,
        )
        executor_perceval.print_summary()
        if any(r["ok"] for r in executor_perceval.results.values()):
            steps["perceval"] = True
        _print_summary(steps)
        return steps

    # ── Step 3 & 4: QIR 변환 ────────────────
    print("\n[Step 3] OpenQASM 변환")
    print("[Step 4] QIR 변환 (OpenQASM → LLVM QIR)")
    converter = UQIQIRConverter(extractor)
    converter.convert_all()
    converter.print_summary()

    if any(converter.qasm_results.values()):
        steps["qasm"] = True
    if any(converter.qir_results.values()):
        steps["qir"] = True

    # ── Step 5: IBM 실행 ────────────────────
    print(f"\n[Step 5] IBM 실행 (simulator={use_ibm_simulator})")
    executor_ibm = UQIExecutorIBM(converter, shots=shots)
    executor_ibm.run_all(
        use_simulator=use_ibm_simulator,
        backend_name=ibm_backend,
        token=ibm_token,
    )
    executor_ibm.print_summary()
    if any(r["ok"] for r in executor_ibm.results.values()):
        steps["ibm"] = True

    # ── Step 6: IQM 실행 ────────────────────
    print(f"\n[Step 6] IQM 실행 (simulator={use_iqm_simulator})")
    executor_iqm = UQIExecutorIQM(converter, shots=shots)
    executor_iqm.run_all(
        use_simulator=use_iqm_simulator,
        backend_url=iqm_backend_url,
        token=iqm_token,
    )
    executor_iqm.print_summary()
    if any(r["ok"] for r in executor_iqm.results.values()):
        steps["iqm"] = True

    _print_summary(steps)
    return steps


def _print_summary(steps: dict):
    print("\n" + "=" * 60)
    print("검증 결과 요약")
    print("=" * 60)
    labels = {
        "detect":    "Framework 감지",
        "extract":   "회로 추출",
        "qasm":      "OpenQASM 변환",
        "qir":       "QIR 변환",
        "ibm":       "IBM 실행",
        "iqm":       "IQM 실행",
        "cudaq_iqm": "CUDAQ IQM 실행",
        "cudaq_ibm": "CUDAQ IBM 실행",
        "perceval":  "Perceval Quandela 실행",
    }
    for key, label in labels.items():
        status = "PASS ✓" if steps[key] else "FAIL ✗"
        print(f"  {label:<28} {status}")
    passed = sum(steps.values())
    print(f"\n  {passed}/{len(steps)} 단계 통과")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UQI Pipeline Verifier")
    parser.add_argument("-f", "--file", required=True,
                        help="검증할 알고리즘 .py 파일 경로")
    parser.add_argument("--ibm-real", action="store_true",
                        help="IBM 실제 QPU 사용 (기본: 시뮬레이터)")
    parser.add_argument("--iqm-real", action="store_true",
                        help="IQM 실제 QPU 사용 (기본: 시뮬레이터)")
    parser.add_argument("--shots", type=int, default=1024,
                        help="샷 수 (기본: 1024)")
    parser.add_argument("--perceval-real", action="store_true",
                        help="Quandela 실제 QPU 사용 (기본: 시뮬레이터)")
    parser.add_argument("--ibm-backend", type=str, default="ibm_fez",
                        help="IBM 백엔드 이름 (기본: ibm_fez)")
    parser.add_argument("--iqm-backend-url", type=str,
                        default="https://resonance.meetiqm.com/computers/garnet",
                        help="IQM 백엔드 URL")
    args = parser.parse_args()

    run_verification(
        algorithm_file=args.file,
        use_ibm_simulator=not args.ibm_real,
        use_iqm_simulator=not args.iqm_real,
        use_perceval_simulator=not args.perceval_real,
        shots=args.shots,
        ibm_backend=args.ibm_backend,
        iqm_backend_url=args.iqm_backend_url,
    )