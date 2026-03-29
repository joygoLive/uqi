"""
McLachlan TDVP for Heat PDE - Hybrid Execution (IQM Emerald)

State Device: Local Simulator (Qiskit Aer)
Hadamard Device: IQM Emerald (Real Hardware)

Comparison: PennyLane original vs Qiskit hybrid

Author: Sean
Date: 2026-01-26
"""

import os
import numpy as np
from dotenv import load_dotenv
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
from iqm.qiskit_iqm import IQMProvider
import matplotlib.pyplot as plt
from datetime import datetime

import sys
from logger_utils import TeeOutput

# 로깅 시작
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
tee = TeeOutput(f"mclachlan_pde_hybrid_iqm_{timestamp}.txt")
original_stdout = sys.stdout
sys.stdout = tee

# ============================ 1. 환경 설정 ============================
load_dotenv()
IQM_TOKEN = os.getenv("IQM_QUANTUM_TOKEN")
IQM_URL = "https://resonance.meetiqm.com/"

if not IQM_TOKEN:
    raise ValueError("IQM_QUANTUM_TOKEN not found in .env file")

print("="*80)
print("McLachlan TDVP - Hybrid Execution (IQM Emerald)")
print("="*80)

# ============================ 2. 물리 파라미터 ============================
D = 0.02           # 확산 계수
Lx = 1.0           # 공간 구간 길이
K = 4              # 푸리에 모드 수
n = 2              # qubits
L = 2              # ansatz depth (하드웨어 실행 고려)
T = 2.0            # 총 시뮬레이션 시간
dt = 1.0           # 시간 간격
reg = 1e-8         # 정규화

k_vals = np.arange(1, K + 1)
kappa = 2 * np.pi * k_vals / Lx
lmbda = -D * (kappa ** 2)
A_matrix = np.diag(lmbda.astype(float))

print(f"\n📋 Configuration:")
print(f"   D={D}, Lx={Lx}, K={K}, n={n}, L={L}")
print(f"   T={T}, dt={dt} → {int(T/dt)+1} timesteps")
print(f"   Eigenvalues λ: {lmbda}")

# ============================ 3. Pauli Decomposition ============================
# 2-qubit 대각 행렬 A를 Pauli 기저로 분해
# A = c0*II + c1*IZ + c2*ZI + c3*ZZ

pauli_coeffs = [
    (np.mean(lmbda), 'I', 'I'),
    ((lmbda[0] - lmbda[1] + lmbda[2] - lmbda[3]) / 4, 'I', 'Z'),
    ((lmbda[0] + lmbda[1] - lmbda[2] - lmbda[3]) / 4, 'Z', 'I'),
    ((lmbda[0] - lmbda[1] - lmbda[2] + lmbda[3]) / 4, 'Z', 'Z')
]

print(f"\n🔬 Pauli decomposition: {len(pauli_coeffs)} terms")
for i, (c, p1, p2) in enumerate(pauli_coeffs):
    print(f"   {i}: {c:.6f} * {p1}{p2}")

# ============================ 4. PennyLane 원본 시뮬레이션 (비교 기준) ============================
print("\n" + "="*80)
print("SECTION 1: PennyLane Original Simulation (T=2.0, dt=1.0)")
print("="*80)

import pennylane as qml
from scipy.linalg import expm

# Pauli decomposition for PennyLane
PAULI = {
    'I': np.array([[1,0],[0,1]], dtype=complex),
    'X': np.array([[0,1],[1,0]], dtype=complex),
    'Y': np.array([[0,-1j],[1j,0]], dtype=complex),
    'Z': np.array([[1,0],[0,-1]], dtype=complex),
}

def kronN(mats):
    out = np.array([[1]], dtype=complex)
    for M in mats:
        out = np.kron(out, M)
    return out

def decompose_to_pauli_2q(M, tol=1e-10):
    labels = ['I','X','Y','Z']
    terms = []
    for a in labels:
        for b in labels:
            P = kronN([PAULI[a], PAULI[b]])
            coeff = np.trace(P.conj().T @ M) / 4.0
            if abs(coeff) > tol:
                terms.append((coeff, [(a,0),(b,1)]))
    return terms

H_terms = decompose_to_pauli_2q(A_matrix)

# PennyLane devices
dev_state_pl = qml.device("default.qubit", wires=n)
dev_hadamard_pl = qml.device("default.qubit", wires=1+n)
AUX = 0
DATA = list(range(1, n+1))

# Ansatz helpers
def ring_entangle_h():
    for q in range(n):
        qml.CNOT(wires=[DATA[q], DATA[(q+1)%n]])

def apply_layer_h(angles):
    for w in range(n):
        qml.RY(angles[w], wires=DATA[w])
    ring_entangle_h()

def apply_ansatz_h(betas):
    for l in range(betas.shape[0]):
        apply_layer_h(betas[l])

def apply_ansatz_with_Y_on_h(betas, target_l, target_w):
    for l in range(betas.shape[0]):
        for w in range(n):
            qml.RY(betas[l, w], wires=DATA[w])
            if (l == target_l) and (w == target_w):
                qml.Y(wires=DATA[w])
        ring_entangle_h()

@qml.qnode(dev_state_pl, interface="autograd")
def state_from_betas_pl(betas):
    qml.BasisState(np.array([0]*n), wires=range(n))
    for l in range(betas.shape[0]):
        for w in range(n):
            qml.RY(betas[l, w], wires=w)
        for q in range(n):
            qml.CNOT(wires=[q, (q+1)%n])
    return qml.state()

@qml.qnode(dev_state_pl, interface="autograd")
def expval_of_word(betas, word):
    qml.BasisState(np.array([0]*n), wires=range(n))
    for l in range(betas.shape[0]):
        for w in range(n):
            qml.RY(betas[l, w], wires=w)
        for q in range(n):
            qml.CNOT(wires=[q, (q+1)%n])
    
    ops = []
    for (label, wi) in word:
        if label == 'I':
            continue
        ops.append(getattr(qml, f"Pauli{label}")(wires=wi))
    if not ops:
        return qml.expval(qml.Identity(0))
    return qml.expval(qml.prod(*ops))

def energy_value(betas):
    val = 0.0 + 0.0j
    for coeff, word in H_terms:
        val += coeff * expval_of_word(betas, word)
    return float(np.real(val))

# Controlled helpers
def ctrl1(fn, control):
    def wrapped(*args, **kwargs):
        qml.ctrl(fn, control=control)(*args, **kwargs)
    return wrapped

def ctrl0(fn, control):
    def wrapped(*args, **kwargs):
        qml.PauliX(wires=control)
        qml.ctrl(fn, control=control)(*args, **kwargs)
        qml.PauliX(wires=control)
    return wrapped

def apply_pauli_word_on_data(word):
    for (label, wi) in word:
        if label == 'I':
            continue
        getattr(qml, f"Pauli{label}")(wires=DATA[wi])

# Hadamard tests
@qml.qnode(dev_hadamard_pl, interface="autograd")
def measure_Aik_raw(betas, i_l, i_w, k_l, k_w):
    qml.H(wires=AUX)
    ctrl0(apply_ansatz_with_Y_on_h, control=AUX)(betas, k_l, k_w)
    ctrl1(apply_ansatz_with_Y_on_h, control=AUX)(betas, i_l, i_w)
    qml.H(wires=AUX)
    return qml.expval(qml.PauliZ(AUX))

def Aik_hadamard(betas, i_l, i_w, k_l, k_w):
    raw = measure_Aik_raw(betas, i_l, i_w, k_l, k_w)
    return float(raw) / 4.0

@qml.qnode(dev_hadamard_pl, interface="autograd")
def measure_Ck_term_raw(betas, k_l, k_w, word):
    qml.H(wires=AUX)
    ctrl0(apply_ansatz_with_Y_on_h, control=AUX)(betas, k_l, k_w)
    def branch1(betas_inner, word_inner):
        apply_ansatz_h(betas_inner)
        apply_pauli_word_on_data(word_inner)
    ctrl1(branch1, control=AUX)(betas, word)
    qml.H(wires=AUX)
    return qml.expval(qml.PauliZ(AUX))

def Ck_hadamard(betas, k_l, k_w):
    total = 0.0 + 0.0j
    for coeff, word in H_terms:
        raw = measure_Ck_term_raw(betas, k_l, k_w, word)
        total += coeff * (float(raw) / 2.0)
    return float(np.real(total))

def assemble_A_C_hadamard(betas):
    M = L * n
    A_mat = np.zeros((M, M))
    C_vec = np.zeros(M)
    def lw(j): return (j // n, j % n)
    for k in range(M):
        lk, wk = lw(k)
        C_vec[k] = Ck_hadamard(betas, lk, wk)
        for i in range(M):
            li, wi = lw(i)
            A_mat[k, i] = Aik_hadamard(betas, li, wi, lk, wk)
    return A_mat, C_vec

def step_pl(betas, alpha, dt_step):
    A_mat, C_vec = assemble_A_C_hadamard(betas)
    beta_flat = betas.reshape(-1)
    beta_dot = np.linalg.solve(A_mat + reg*np.eye(A_mat.shape[0]), C_vec)
    betas_new = (beta_flat + dt_step * beta_dot).reshape(L, n)
    E = energy_value(betas)
    alpha_new = alpha + dt_step * E * alpha
    return betas_new, alpha_new

# Run PennyLane simulation
print("   🔬 Running PennyLane TDVP...")
betas_pl = np.zeros((L, n))
alpha_pl = 1.0
ts = np.arange(0, T + 1e-12, dt)

traj_pl = []
for t in ts:
    psi_norm = state_from_betas_pl(betas_pl)
    psi_hat = alpha_pl * psi_norm
    traj_pl.append(np.real(psi_hat[:K]))
    betas_pl, alpha_pl = step_pl(betas_pl, alpha_pl, dt)
    print(f"      t={t:.1f}: coeffs={np.real(psi_hat[:K])}")

traj_pl = np.stack(traj_pl, axis=0)

# Exact solution
u0_coef = np.zeros(K); u0_coef[0] = 1.0
exact_coef = np.stack([(np.exp(lmbda * t) * u0_coef).real for t in ts], axis=0)

coef_rms_pl = np.sqrt(np.mean((traj_pl - exact_coef)**2))
print(f"\n   ✅ PennyLane RMS (coeff space): {coef_rms_pl:.6e}")

# ============================ 5. IQM Emerald 연결 ============================
print("\n" + "="*80)
print("SECTION 2: IQM Emerald Connection")
print("="*80)

print("   📡 Connecting to IQM Emerald...")
try:
    provider = IQMProvider(IQM_URL, token=IQM_TOKEN, quantum_computer="emerald")
    backend = provider.get_backend()
    print(f"   ✅ Connected: {backend.name}")
except Exception as e:
    print(f"   ❌ Connection failed: {e}")
    exit(1)

# ============================ 6. Qiskit 회로 빌더 (수정됨) ============================
print("\n" + "="*80)
print("SECTION 3: Qiskit Circuit Builder (Corrected)")
print("="*80)

# State simulator (로컬)
sim_state = AerSimulator(method='statevector')

def build_state_circuit(betas):
    """State 생성 회로 (시뮬레이터 전용)"""
    qc = QuantumCircuit(n)
    for l in range(L):
        for w in range(n):
            qc.ry(betas[l, w], w)
        # Ring entanglement: CNOT(q, (q+1)%n)
        for q in range(n):
            qc.cx(q, (q+1) % n)
    return qc

def apply_controlled_ansatz(qc, control, betas, insert_y_at=None):
    """Controlled-ansatz 적용 (수정됨)"""
    for l in range(L):
        for w in range(n):
            # Controlled-RY
            qc.cry(betas[l, w], control, w + 1)
            
            # Y gate 삽입 (Hadamard test용)
            if insert_y_at is not None and insert_y_at == (l, w):
                qc.cy(control, w + 1)
        
        # Ring entanglement: controlled-CNOT
        for q in range(n):
            # qc.ccx는 Toffoli가 아니라 여기선 사용 불가
            # 대신: CNOT(q+1, (q+1)%n+1)을 control에 의존하도록
            # 정확한 구현: controlled-CNOT(q+1, ((q+1)%n)+1, control=control)
            # Qiskit에서는 이를 직접 표현 어려움 → 분해 필요
            
            # 간단한 근사: control이 |1⟩일 때만 CNOT 실행
            # 실제로는 Toffoli decomposition 필요하지만 여기선 단순화
            target1 = q + 1
            target2 = ((q + 1) % n) + 1
            
            # Controlled-CNOT = Toffoli(control, target1, target2)
            # IQM은 native CZ만 지원하므로 decomposition 필요
            # 하지만 transpiler가 자동 처리하므로 일단 작성
            qc.ccx(control, target1, target2)

def apply_pauli_word_qiskit(qc, control, p1, p2):
    """Pauli word 적용 (수정됨)"""
    # data qubits: 1, 2 (control=0)
    if p1 == 'Z':
        qc.cz(control, 1)
    elif p1 == 'X':
        qc.cx(control, 1)
    elif p1 == 'Y':
        qc.cy(control, 1)
    # p1 == 'I': do nothing
    
    if p2 == 'Z':
        qc.cz(control, 2)
    elif p2 == 'X':
        qc.cx(control, 2)
    elif p2 == 'Y':
        qc.cy(control, 2)

def build_batch_circuits(betas):
    """배치 회로 생성 (32개)"""
    circs = []
    M_size = L * n
    
    # A matrix 회로 (16개)
    for k in range(M_size):
        lk, wk = k // n, k % n
        for i in range(M_size):
            li, wi = i // n, i % n
            
            qc = QuantumCircuit(n + 1, 1)  # control + data + measurement
            qc.h(0)
            
            # ctrl0: X - controlled-ansatz - X
            qc.x(0)
            apply_controlled_ansatz(qc, 0, betas, insert_y_at=(lk, wk))
            qc.x(0)
            
            # ctrl1: controlled-ansatz
            apply_controlled_ansatz(qc, 0, betas, insert_y_at=(li, wi))
            
            qc.h(0)
            qc.measure(0, 0)
            circs.append(qc)
    
    # C vector 회로 (16개)
    for k in range(M_size):
        lk, wk = k // n, k % n
        for coeff, p1, p2 in pauli_coeffs:
            qc = QuantumCircuit(n + 1, 1)
            qc.h(0)
            
            # ctrl0: X - controlled-ansatz with Y - X
            qc.x(0)
            apply_controlled_ansatz(qc, 0, betas, insert_y_at=(lk, wk))
            qc.x(0)
            
            # ctrl1: controlled-ansatz + Pauli word
            apply_controlled_ansatz(qc, 0, betas, insert_y_at=None)
            apply_pauli_word_qiskit(qc, 0, p1, p2)
            
            qc.h(0)
            qc.measure(0, 0)
            circs.append(qc)
    
    return circs

print(f"   🔧 Circuit builder ready")
print(f"   - State circuit: {n} qubits (simulator)")
print(f"   - Hadamard circuits: {n+1} qubits × 32 (IQM)")

# ============================ 7. 하이브리드 TDVP 루프 ============================
print("\n" + "="*80)
print("SECTION 4: Hybrid TDVP Loop (State=Sim, Hadamard=IQM)")
print("="*80)

betas_qiskit = np.zeros((L, n))
alpha_qiskit = 1.0
traj_qiskit = []

print(f"\n🚀 Starting hybrid execution (T={T}, dt={dt})")

for idx, t in enumerate(ts):
    print(f"\n--- Timestep {idx}: t={t:.1f} ---")
    
    # 1. State 계산 (로컬 시뮬레이터)
    qc_state = build_state_circuit(betas_qiskit)
    qc_state.save_statevector()  # ← 이 줄 추가
    result_state = sim_state.run(qc_state).result()
    statevector = result_state.get_statevector()
    psi_qiskit = alpha_qiskit * np.array(statevector)
    traj_qiskit.append(np.real(psi_qiskit[:K]))
    
    print(f"   📊 State (sim): coeffs={np.real(psi_qiskit[:K])}")
    
    # 2. Hadamard test 배치 회로 생성
    print(f"   🔧 Building 32 Hadamard circuits...")
    circs = build_batch_circuits(betas_qiskit)
    
    # 3. Transpile
    print(f"   ⚙️  Transpiling for IQM Emerald...")
    transpiled = transpile(circs, backend=backend, optimization_level=3)
    
    # 4. IQM 제출
    print(f"   🚀 Submitting to IQM (32 circuits)...")
    job = backend.run(transpiled, shots=1000)
    print(f"   ⏳ Job ID: {job.job_id()} (waiting...)")
    
    # 5. 결과 수신
    result = job.result()
    print(f"   ✅ Results received")
    
    # 6. Expectation values 추출
    expvals = []
    for i in range(len(circs)):
        counts = result.get_counts(i)
        count_0 = counts.get('0', 0)
        count_1 = counts.get('1', 0)
        expval = (count_0 - count_1) / 1000.0
        expvals.append(expval)
    
    # 7. A matrix 조립
    M_size = L * n
    A_mat = np.array(expvals[:M_size**2]).reshape(M_size, M_size) / 4.0
    
    # 8. C vector 조립
    ck_raw = expvals[M_size**2:]
    C_vec = np.zeros(M_size)
    for k in range(M_size):
        for idx_pauli, (coeff, p1, p2) in enumerate(pauli_coeffs):
            C_vec[k] += coeff * (ck_raw[k * 4 + idx_pauli] / 2.0)
    
    print(f"   📐 A matrix: shape={A_mat.shape}, norm={np.linalg.norm(A_mat):.4f}")
    print(f"   📐 C vector: shape={C_vec.shape}, norm={np.linalg.norm(C_vec):.4f}")
    
    # 9. TDVP step
    beta_flat = betas_qiskit.reshape(-1)
    beta_dot = np.linalg.solve(A_mat + reg * np.eye(M_size), C_vec)
    betas_qiskit = (beta_flat + dt * beta_dot).reshape(L, n)
    
    E = np.mean(lmbda)  # 단순화: 평균 eigenvalue 사용
    alpha_qiskit = alpha_qiskit + dt * E * alpha_qiskit
    
    print(f"   ✅ Parameters updated: β_norm={np.linalg.norm(betas_qiskit):.4f}, α={alpha_qiskit:.4f}")

traj_qiskit = np.stack(traj_qiskit, axis=0)

print(f"\n✅ Hybrid execution complete!")

# ============================ 8. 결과 비교 ============================
print("\n" + "="*80)
print("SECTION 5: Results Comparison")
print("="*80)

print("\n📊 Timestep-by-timestep comparison:")
print(f"{'Time':<8} {'PennyLane':<40} {'Qiskit+IQM':<40} {'RMS Error':<12}")
print("-" * 100)

for idx, t in enumerate(ts):
    pl_coef = traj_pl[idx]
    qk_coef = traj_qiskit[idx]
    rms_err = np.sqrt(np.mean((pl_coef - qk_coef)**2))
    
    print(f"{t:<8.1f} {str(pl_coef):<40} {str(qk_coef):<40} {rms_err:<12.6e}")

# 최종 비교
print("\n" + "="*80)
print("Final Comparison (t=2.0)")
print("="*80)

final_pl = traj_pl[-1]
final_qiskit = traj_qiskit[-1]
final_exact = exact_coef[-1]

print(f"\n📊 Coefficient comparison:")
print(f"   Exact:        {final_exact}")
print(f"   PennyLane:    {final_pl}")
print(f"   Qiskit+IQM:   {final_qiskit}")

rms_pl_exact = np.sqrt(np.mean((final_pl - final_exact)**2))
rms_qiskit_exact = np.sqrt(np.mean((final_qiskit - final_exact)**2))
rms_pl_qiskit = np.sqrt(np.mean((final_pl - final_qiskit)**2))

print(f"\n📈 RMS Errors:")
print(f"   PennyLane vs Exact:      {rms_pl_exact:.6e}")
print(f"   Qiskit+IQM vs Exact:     {rms_qiskit_exact:.6e}")
print(f"   PennyLane vs Qiskit+IQM: {rms_pl_qiskit:.6e}")

# ============================ 9. 시각화 ============================
print("\n" + "="*80)
print("SECTION 6: Visualization")
print("="*80)

# Physical space reconstruction
Nx = 128
xs = np.linspace(0, Lx, Nx, endpoint=False)

def synth_from_coef(a_vec, xgrid):
    out = np.zeros_like(xgrid, dtype=float)
    for k in range(1, K+1):
        out += a_vec[k-1] * np.sin(2*np.pi*k*xgrid/Lx)
    return out

fig, axes = plt.subplots(2, 2, figsize=(14, 10))

# Plot 1: Coefficient evolution
ax = axes[0, 0]
for k in range(K):
    ax.plot(ts, exact_coef[:, k], 'k--', linewidth=2, label=f'Exact a_{k+1}' if k==0 else '')
    ax.plot(ts, traj_pl[:, k], 'o-', label=f'PL a_{k+1}')
    ax.plot(ts, traj_qiskit[:, k], 's-', label=f'QK a_{k+1}')
ax.set_xlabel('Time t')
ax.set_ylabel('Coefficient')
ax.set_title('Fourier Coefficients Evolution')
ax.legend()
ax.grid(True)

# Plot 2: Physical space at t=2.0
ax = axes[0, 1]
u_exact_final = synth_from_coef(final_exact, xs)
u_pl_final = synth_from_coef(final_pl, xs)
u_qk_final = synth_from_coef(final_qiskit, xs)

ax.plot(xs, u_exact_final, 'k--', linewidth=2, label='Exact')
ax.plot(xs, u_pl_final, '-', label='PennyLane')
ax.plot(xs, u_qk_final, '--', label='Qiskit+IQM')
ax.set_xlabel('x')
ax.set_ylabel('u(x, t=2)')
ax.set_title('Physical Space Solution (t=2.0)')
ax.legend()
ax.grid(True)

# Plot 3: RMS error evolution
ax = axes[1, 0]
rms_pl_vs_exact = [np.sqrt(np.mean((traj_pl[i] - exact_coef[i])**2)) for i in range(len(ts))]
rms_qk_vs_exact = [np.sqrt(np.mean((traj_qiskit[i] - exact_coef[i])**2)) for i in range(len(ts))]

ax.semilogy(ts, rms_pl_vs_exact, 'o-', label='PennyLane vs Exact')
ax.semilogy(ts, rms_qk_vs_exact, 's-', label='Qiskit+IQM vs Exact')
ax.set_xlabel('Time t')
ax.set_ylabel('RMS Error')
ax.set_title('Error Evolution')
ax.legend()
ax.grid(True)

# Plot 4: Snapshots at multiple times
ax = axes[1, 1]
for idx, t in enumerate(ts):
    u_exact = synth_from_coef(exact_coef[idx], xs)
    u_qk = synth_from_coef(traj_qiskit[idx], xs)
    
    line, = ax.plot(xs, u_qk, '-', label=f'QK t={t:.1f}')
    ax.plot(xs, u_exact, '--', color=line.get_color(), alpha=0.5)

ax.set_xlabel('x')
ax.set_ylabel('u(x,t)')
ax.set_title('Solution Snapshots (Solid: QK, Dash: Exact)')
ax.legend()
ax.grid(True)

plt.tight_layout()

# Save figure
os.makedirs('logs', exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
filepath = f"logs/mclachlan_pde_hybrid_iqm_{timestamp}.png"
plt.savefig(filepath, dpi=150)
print(f"\n💾 Figure saved: {filepath}")

plt.show()

print("\n" + "="*80)
print("🏁 Hybrid Execution Complete!")
print("="*80)
print(f"\n📋 Summary:")
print(f"   Total circuits submitted: {len(circs) * len(ts)}")
print(f"   Final RMS (PL vs QK+IQM): {rms_pl_qiskit:.6e}")
print(f"   Final RMS (QK+IQM vs Exact): {rms_qiskit_exact:.6e}")
print("\n⚠️  Note: Hardware noise may cause deviations from theory.")
print("="*80)

# 로깅 종료
sys.stdout = original_stdout
tee.close()
print(f"\n✅ Log saved: {tee.get_filepath()}")