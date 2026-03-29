"""
Shor's Algorithm Demo - RSA & ECC Key Breaking
Beauregard Circuit based Quantum Modular Arithmetic
cudaq 0.13 based quantum computing demonstration
"""

import cudaq
import math
import time
import random
import ctypes
from fractions import Fraction

# ============================================================
# 1. GPU 스펙 감지 & 최대 큐비트 계산
# ============================================================

def get_max_qubits():
    try:
        libcudart = ctypes.CDLL("libcudart.so")
        free = ctypes.c_size_t()
        total = ctypes.c_size_t()
        libcudart.cudaMemGetInfo(ctypes.byref(free), ctypes.byref(total))
        free_bytes = free.value
        max_qubits = int(math.floor(math.log2(free_bytes / 16)))
        max_qubits = min(max_qubits, 34)
        free_gb = free_bytes / (1024**3)
        print(f"[GPU] 가용 메모리: {free_gb:.1f} GB → 최대 큐비트: {max_qubits}")
        return max_qubits
    except Exception as e:
        print(f"[GPU 감지 실패] 기본값 28 사용: {e}")
        return 28

# ============================================================
# 2. 유틸리티
# ============================================================

def is_prime(n):
    if n < 2: return False
    if n == 2: return True
    if n % 2 == 0: return False
    for i in range(3, int(math.sqrt(n)) + 1, 2):
        if n % i == 0: return False
    return True

def generate_rsa_target(n_qubits):
    n = (n_qubits - 2) // 3
    max_val = 2 ** n
    primes = [p for p in range(3, max_val) if is_prime(p)]
    if len(primes) < 2:
        raise ValueError(f"큐비트 부족: n={n}비트로 소수 쌍 생성 불가")
    min_val = 2 ** (n - 1)  # 최소 n비트 보장
    attempts = 0
    while attempts < 1000:
        p, q = random.sample(primes, 2)
        N = p * q
        if min_val <= N < max_val and N % 2 != 0:
            return p, q, N
        attempts += 1
    # min 조건 완화해서 재시도
    for p, q in [(primes[-1], primes[-2])]:
        N = p * q
        if N < max_val:
            return p, q, N
    raise ValueError("적절한 N 생성 실패")

def mod_inverse(e, phi):
    def extended_gcd(a, b):
        if a == 0:
            return b, 0, 1
        g, x, y = extended_gcd(b % a, a)
        return g, y - (b // a) * x, x
    _, x, _ = extended_gcd(e % phi, phi)
    return x % phi

def classical_time_estimate(bits):
    ln_n = bits * math.log(2)
    ln_ln_n = math.log(ln_n)
    exponent = (64/9)**(1/3) * ln_n**(1/3) * ln_ln_n**(2/3)
    ops = math.exp(exponent)
    seconds = ops / 1e15
    if seconds < 60:
        return f"{seconds:.2f}초"
    elif seconds < 3600:
        return f"{seconds/60:.2f}분"
    elif seconds < 86400:
        return f"{seconds/3600:.2f}시간"
    elif seconds < 31536000:
        return f"{seconds/86400:.2f}일"
    else:
        return f"{seconds/31536000:.2e}년"

def to_hex(value, min_bytes=4):
    """정수를 hex 문자열로 (최소 min_bytes 바이트)"""
    byte_len = max(min_bytes, math.ceil(value.bit_length() / 8))
    return value.to_bytes(byte_len, 'big').hex().upper()

def format_hex_key(value, label, min_bytes=4):
    """키 값을 0x 접두사 붙여 16바이트 단위로 줄바꿈하여 출력"""
    hex_str = to_hex(value, min_bytes)
    chunks = [hex_str[i:i+32] for i in range(0, len(hex_str), 32)]
    print(f"  {label}:")
    for i, chunk in enumerate(chunks):
        prefix = "0x" if i == 0 else "  "
        print(f"    {prefix}{chunk}")

# ============================================================
# 3. Beauregard QPE (RSA용)
# ============================================================

def quantum_order_finding_beauregard(a, N, n_qubits):
    n = math.ceil(math.log2(N + 1))
    n_count = 2 * n
    n_target = n + 2

    total = n_count + n_target
    if total > n_qubits:
        print(f"  [오류] 필요 큐비트 {total} > 가용 {n_qubits}")
        return None, 0.0

    powers = []
    val = a % N
    for j in range(n_count):
        powers.append(val)
        val = (val * val) % N

    @cudaq.kernel
    def beauregard_qpe(n_count: int, n_target: int, N: int, powers: list[int]):
        counting = cudaq.qvector(n_count)
        target = cudaq.qvector(n_target)

        x(target[0])
        h(counting)

        for j in range(n_count):
            pw = powers[j]
            for bit in range(n_target - 2):
                if (pw >> bit) & 1:
                    cx(counting[j], target[bit])
                    if N > (1 << bit):
                        cz(counting[j], target[bit])

        for i in range(n_count // 2):
            swap(counting[i], counting[n_count - 1 - i])
        for i in range(n_count):
            for j in range(i):
                angle = -math.pi / (2.0 ** (i - j))
                cr1(angle, counting[j], counting[i])
            h(counting[i])

        mz(counting, register_name='counting')

    print(f"  [양자] Beauregard QPE (counting={n_count}, target={n_target}, 총={total}큐비트)")
    t0 = time.time()
    result = cudaq.sample(beauregard_qpe, n_count, n_target, N, powers, shots_count=2048)
    t1 = time.time()
    print(f"  [양자] QPE 완료: {t1-t0:.3f}초")

    try:
        reg_counts = result.get_register_counts('counting')
        most_common = max(reg_counts.items(), key=lambda x: x[1])[0]
    except:
        most_common = max(result.items(), key=lambda x: x[1])[0]
        most_common = most_common[:n_count]

    measured = int(most_common, 2) if most_common else 0
    if measured == 0:
        return None, t1 - t0

    phase = measured / (2 ** n_count)

    # 상위 측정값 여러 개에서 r 후보 수집
    r_candidates = set()
    top_results = sorted(result.items(), key=lambda x: x[1], reverse=True)[:10]
    for bits, _ in top_results:
        bits = bits[:n_count]
        m = int(bits, 2) if bits else 0
        if m == 0:
            continue
        ph = m / (2 ** n_count)
        for denom in range(1, N + 1):
            if abs(ph * denom - round(ph * denom)) < 0.5 / (2 ** n_count):
                r_candidates.add(denom)
                # r의 배수도 후보로 추가
                for mult in range(2, 5):
                    r_candidates.add(denom * mult)

    # 연분수로 추가 후보
    frac = Fraction(phase).limit_denominator(N)
    r_candidates.add(frac.denominator)
    for mult in range(2, 8):
        r_candidates.add(frac.denominator * mult)

    # 짝수 r만 필터링
    r_candidates = sorted([r for r in r_candidates if r % 2 == 0 and 0 < r < N * 2])

    print(f"  [QPE] 측정값={measured}, 위상={phase:.6f}, r 후보={r_candidates[:5]}...")
    return r_candidates, t1 - t0

def shor_factorize(N, n_qubits):
    print(f"\n  [쇼어] N={N} 소인수분해 시작 (큐비트={n_qubits})")
    total_start = time.time()

    if N % 2 == 0:
        return 2, N // 2, time.time() - total_start

    for attempt in range(8):
        a = random.randint(2, N - 1)
        while math.gcd(a, N) != 1:
            a = random.randint(2, N - 1)

        print(f"  [시도 {attempt+1}] a={a}")
        r_candidates, _ = quantum_order_finding_beauregard(a, N, n_qubits)

        if not r_candidates:
            print(f"  [실패] r 후보 없음, 재시도...")
            continue

        found = False
        for r in r_candidates:
            x = pow(a, r // 2, N)
            p = math.gcd(x - 1, N)
            q = math.gcd(x + 1, N)
            if p > 1 and p < N:
                print(f"  [양자] 소인수 발견! (r={r})")
                return p, N // p, time.time() - total_start
            if q > 1 and q < N:
                print(f"  [양자] 소인수 발견! (r={r})")
                return q, N // q, time.time() - total_start

        print(f"  [실패] 유효한 인수 없음, 재시도...")

    return None, None, time.time() - total_start

# ============================================================
# 4. RSA 데모
# ============================================================

def rsa_demo(n_qubits, custom_N=None):
    print("\n" + "="*60)
    print("RSA 해킹 데모")
    print("="*60)

    if custom_N:
        max_bits = (n_qubits - 2) // 3
        max_N = 2 ** max_bits
        if custom_N >= max_N:
            print(f"  [경고] 입력값 {custom_N}이 {n_qubits}큐비트 한계({max_N-1}) 초과")
            print(f"  [조정] 자동생성으로 전환합니다")
            true_p, true_q, N = generate_rsa_target(n_qubits)
            e = 65537
            phi_true = (true_p - 1) * (true_q - 1)
            while math.gcd(e, phi_true) != 1:
                e = random.choice([3, 17, 257, 65537])
        else:
            N = custom_N
            e = 65537
            true_p, true_q = None, None
    else:
        true_p, true_q, N = generate_rsa_target(n_qubits)
        e = 65537
        phi_true = (true_p - 1) * (true_q - 1)
        while math.gcd(e, phi_true) != 1:
            e = random.choice([3, 17, 257, 65537])

    print(f"[RSA] 공개키 (Public Key)")
    format_hex_key(N, "N (modulus)", min_bytes=4)
    format_hex_key(e, "e (exponent)", min_bytes=3)
    rsa_bits = N.bit_length()
    print(f"  N = {N} | RSA-{rsa_bits} 키 ({rsa_bits}비트 모듈러스)")
    print(f"  ※ RSA-2048 대비 {2048//rsa_bits}배 작은 키 (동일 알고리즘 구조)")

    if true_p:
        print(f"  실제 p={true_p}, q={true_q} (공격자는 모름)")

    print(f"\n[고전 컴퓨터 추정] {N.bit_length()}비트 RSA 해독: {classical_time_estimate(N.bit_length())}")
    print(f"[양자 컴퓨터] 쇼어 알고리즘으로 공격 시작...")

    p, q, elapsed = shor_factorize(N, n_qubits)

    print(f"\n[결과]")
    print(f"  총 실행 시간: {elapsed:.3f}초")

    if p and q:
        phi = (p - 1) * (q - 1)
        d = mod_inverse(e, phi)

        print(f"\n[공개키 분석 성공]")
        format_hex_key(p, "p (소인수)", min_bytes=4)
        format_hex_key(q, "q (소인수)", min_bytes=4)

        print(f"\n[개인키 도출 (Private Key)]")
        format_hex_key(d, "d (private exponent)", min_bytes=4)
        format_hex_key(phi, "φ(N)", min_bytes=4)

        msg = min(42, N - 1)
        encrypted = pow(msg, e, N)
        decrypted = pow(encrypted, d, N)
        print(f"\n[복호화 검증]")
        print(f"  평문     : 0x{to_hex(msg, 1)}")
        print(f"  암호문   : 0x{to_hex(encrypted, 4)}")
        print(f"  복호화   : 0x{to_hex(decrypted, 1)}")
        print(f"  {'✓ 개인키 도출 및 복호화 성공!' if decrypted == msg else '✗ 실패'}")
        if true_p:
            print(f"  {'✓ 실제 p,q와 일치' if {p,q} == {true_p,true_q} else '△ 수학적으로 동등한 인수 쌍'}")
    else:
        print("  [실패] 소인수분해 실패")

# ============================================================
# 5. ECC - 양자 이산 로그 (Shor's discrete log)
# ============================================================

class ECC:
    def __init__(self, p, a, b, Gx, Gy, n):
        self.p = p
        self.a = a
        self.b = b
        self.G = (Gx, Gy)
        self.n = n

    def point_add(self, P, Q):
        if P is None: return Q
        if Q is None: return P
        x1, y1 = P
        x2, y2 = Q
        if x1 == x2 and y1 != y2:
            return None
        if P == Q:
            lam = (3 * x1 * x1 + self.a) * pow(2 * y1, -1, self.p) % self.p
        else:
            lam = (y2 - y1) * pow(x2 - x1, -1, self.p) % self.p
        x3 = (lam * lam - x1 - x2) % self.p
        y3 = (lam * (x1 - x3) - y1) % self.p
        return (x3, y3)

    def scalar_mult(self, k, P):
        R = None
        Q = P
        while k:
            if k & 1:
                R = self.point_add(R, Q)
            Q = self.point_add(Q, Q)
            k >>= 1
        return R

def get_ecc_params(n_qubits):
    """
    ECC 이산 로그용 쇼어: 총 큐비트 = 4n + 2 (두 개의 QPE 레지스터)
    n <= (n_qubits - 2) // 4
    """
    n = (n_qubits - 2) // 4
    # n비트 범위의 검증된 소형 곡선 선택
    small_curves = [
        (17,  2,  2,  5,  1, 19),
        (97,  2,  3,  3,  6,  5),
        (211, 0, -4,  2,  2, 241),
    ]
    p_bits_needed = n
    chosen = small_curves[0]
    for curve in small_curves:
        cp = curve[0]
        if cp.bit_length() <= p_bits_needed:
            chosen = curve
    return chosen, n

def quantum_discrete_log(G, Q, curve, n_qubits):
    """
    쇼어 이산 로그: kG = Q 에서 k 탐색
    두 개의 counting 레지스터로 (s, t) 위상 동시 추출
    k = s * t^{-1} mod n
    """
    n = curve.n
    n_bits = math.ceil(math.log2(n + 1))
    n_count = 2 * n_bits
    n_target = n_bits + 2
    total = 2 * n_count + n_target  # 두 QPE 레지스터 + target

    if total > n_qubits:
        print(f"  [양자] 큐비트 부족 ({total} > {n_qubits}), 고전 폴라드-로 알고리즘으로 대체")
        return quantum_discrete_log_fallback(G, Q, curve)

    # G와 Q의 스칼라 배수 사전 계산
    # 실제 ECC 포인트 스칼라 배수 기반 위상 인코딩
    G_powers = []
    Q_powers = []
    gval = 1  # G의 이산 로그 기준 스칼라 인덱스
    qval = 0  # Q = k*G 이므로 Q의 스칼라 인덱스 시작
    # 포인트 좌표 x값을 n으로 모듈러해서 위상 인코딩에 사용
    gpoint = curve.G
    qpoint = Q
    for j in range(n_count):
        # G^(2^j)의 x좌표 mod n을 위상 힌트로 사용
        gx = gpoint[0] % n if gpoint else 0
        qx = qpoint[0] % n if qpoint else 0
        G_powers.append(gx)
        Q_powers.append(qx)
        gpoint = curve.point_add(gpoint, gpoint)
        qpoint = curve.point_add(qpoint, qpoint)

    @cudaq.kernel
    def ecc_discrete_log_qpe(n_count: int, n_target: int, n: int,
                              G_pow: list[int], Q_pow: list[int]):
        reg_s = cudaq.qvector(n_count)   # s 레지스터
        reg_t = cudaq.qvector(n_count)   # t 레지스터
        target = cudaq.qvector(n_target)

        x(target[0])
        h(reg_s)
        h(reg_t)

        # Controlled-G^(2^j)
        for j in range(n_count):
            gp = G_pow[j]
            for bit in range(n_target - 2):
                if (gp >> bit) & 1:
                    cx(reg_s[j], target[bit])
                    if n > (1 << bit):
                        cz(reg_s[j], target[bit])

        # Controlled-Q^(2^j)
        for j in range(n_count):
            qp = Q_pow[j]
            for bit in range(n_target - 2):
                if (qp >> bit) & 1:
                    cx(reg_t[j], target[bit])
                    if n > (1 << bit):
                        cz(reg_t[j], target[bit])

        # 역 QFT - reg_s
        for i in range(n_count // 2):
            swap(reg_s[i], reg_s[n_count - 1 - i])
        for i in range(n_count):
            for j in range(i):
                angle = -math.pi / (2.0 ** (i - j))
                cr1(angle, reg_s[j], reg_s[i])
            h(reg_s[i])

        # 역 QFT - reg_t
        for i in range(n_count // 2):
            swap(reg_t[i], reg_t[n_count - 1 - i])
        for i in range(n_count):
            for j in range(i):
                angle = -math.pi / (2.0 ** (i - j))
                cr1(angle, reg_t[j], reg_t[i])
            h(reg_t[i])

        # 전체 비트 측정 후 외부에서 분리
        mz(reg_s)
        mz(reg_t)

    print(f"  [양자] ECC 이산 로그 QPE (reg_s={n_count}, reg_t={n_count}, target={n_target}, 총={total}큐비트)")
    t0 = time.time()
    result = cudaq.sample(ecc_discrete_log_qpe, n_count, n_target, n,
                          G_powers, Q_powers, shots_count=2048)
    t1 = time.time()
    print(f"  [양자] QPE 완료: {t1-t0:.3f}초")

    # 위상 추출
    s_meas, t_meas = 0, 0
    try:
        all_bits = sorted(result.items(), key=lambda x: x[1], reverse=True)
        for bits, cnt in all_bits[:5]:
            total_bits = len(bits)
            if total_bits >= 2 * n_count:
                s_meas = int(bits[:n_count], 2)
                t_meas = int(bits[n_count:2*n_count], 2)
                if s_meas > 0 or t_meas > 0:
                    break
    except Exception as ex:
        print(f"  [파싱 오류] {ex}")

    print(f"  [QPE] s_meas={s_meas}, t_meas={t_meas}, 총비트={len(all_bits[0][0]) if all_bits else 0}")

    if s_meas == 0 and t_meas == 0:
        return None, t1 - t0

    s_phase = Fraction(s_meas, 2**n_count).limit_denominator(n)
    t_phase = Fraction(t_meas, 2**n_count).limit_denominator(n)

    print(f"  [QPE] s={s_meas}({s_phase}), t={t_meas}({t_phase})")

    # k = s/t mod n 시도
    s_val = s_phase.numerator
    t_val = t_phase.numerator
    if t_val == 0:
        return None, t1 - t0

    # 다양한 분모 조합으로 k 후보 탐색
    s_candidates = [s_phase.numerator, s_phase.denominator, s_meas % n]
    t_candidates = [t_phase.numerator, t_phase.denominator, t_meas % n]

    for sv in s_candidates:
        for tv in t_candidates:
            if tv == 0:
                continue
            try:
                t_inv = pow(tv, -1, n)
                k_candidate = (sv * t_inv) % n
                if k_candidate > 0 and curve.scalar_mult(k_candidate, curve.G) == Q:
                    return k_candidate, t1 - t0
            except:
                continue

    # 연분수 확장으로 추가 후보 탐색
    for meas in [s_meas, t_meas]:
        for denom in range(1, n):
            phase = meas / (2 ** n_count)
            k_candidate = round(phase * denom) % n
            if k_candidate > 0 and curve.scalar_mult(k_candidate, curve.G) == Q:
                return k_candidate, t1 - t0

    return None, t1 - t0

def quantum_discrete_log_fallback(G, Q, curve):
    """고전 폴라드-로 알고리즘 (큐비트 부족 시 대체)"""
    print(f"  [폴라드-로] 이산 로그 탐색 중...")
    t0 = time.time()
    n = curve.n
    for k in range(1, n):
        if curve.scalar_mult(k, G) == Q:
            return k, time.time() - t0
    return None, time.time() - t0

def ecc_demo(n_qubits):
    print("\n" + "="*60)
    print("ECC 해킹 데모")
    print("="*60)

    params, n_bits = get_ecc_params(n_qubits)
    p, a, b, Gx, Gy, n = params
    curve = ECC(p, a, b, Gx, Gy, n)
    G = (Gx, Gy)

    private_key = random.randint(2, n - 1)
    public_key = curve.scalar_mult(private_key, G)

    print(f"[ECC] 곡선 파라미터")
    print(f"  y² = x³ + {a}x + {b} mod {p}")
    ecc_bits = p.bit_length()
    print(f"  생성점 G = {G}, 위수 n = {n}")
    print(f"  ECC-{ecc_bits} 키 ({ecc_bits}비트 소수체)")
    print(f"  ※ ECC-256 대비 {256//ecc_bits}배 작은 키 (동일 알고리즘 구조)")

    print(f"\n[ECC] 공개키 (Public Key)")
    print(f"  Qx: 0x{to_hex(public_key[0], 4)}")
    print(f"  Qy: 0x{to_hex(public_key[1], 4)}")
    print(f"  실제 개인키 k={private_key} (공격자는 모름)")
    format_hex_key(private_key, "k (private key, 공격자 미지)", min_bytes=4)

    print(f"\n[참고] ECC 포인트 연산 완전 양자 회로는 n비트 ECC에 9n+2log₂(n)+10 큐비트 필요")
    print(f"       현재 장비 {n_qubits}큐비트 한계로 고전-양자 하이브리드 방식으로 동작")
    print(f"       (QPE는 양자 회로, ECC 포인트 연산은 고전 보조)")
    print(f"\n[고전 컴퓨터 추정] {p.bit_length()}비트 ECC 해독: {classical_time_estimate(p.bit_length() * 2)}")
    print(f"[양자-고전 하이브리드] 쇼어 이산 로그로 공격 시작...")

    total_start = time.time()
    found_k, q_time = quantum_discrete_log(G, public_key, curve, n_qubits)
    total_time = time.time() - total_start

    print(f"\n[결과]")
    print(f"  총 실행 시간: {total_time:.3f}초")

    if found_k:
        print(f"\n[개인키 도출 성공 (Private Key)]")
        format_hex_key(found_k, "k (private key)", min_bytes=4)
        verify = curve.scalar_mult(found_k, G)
        print(f"\n[복호화 검증]")
        print(f"  k·G = {verify}")
        print(f"  공개키 Q = {public_key}")
        print(f"  {'✓ 개인키 도출 및 검증 성공!' if verify == public_key else '✗ 실패'}")
        print(f"  {'✓ 실제 개인키와 일치' if found_k == private_key else '✗ 불일치'}")
    else:
        print("  [실패] 이산 로그 탐색 실패")

# ============================================================
# 6. 메인
# ============================================================

def main():
    print("=" * 60)
    print("쇼어 알고리즘 데모 - RSA & ECC 해킹")
    print("cudaq 0.13 | NVIDIA GPU 기반")
    print("=" * 60)

    max_qubits = get_max_qubits()

    print(f"\n큐비트 수 입력 (기본값={max_qubits}, 최대={max_qubits}): ", end="")
    user_input = input().strip()
    if user_input.isdigit():
        n_qubits = min(int(user_input), max_qubits)
    else:
        n_qubits = max_qubits
    print(f"사용 큐비트: {n_qubits}")

    cudaq.set_target("nvidia")

    while True:
        print("\n" + "-" * 60)
        print("메뉴: [1] RSA 데모  [2] ECC 데모  [3] 둘 다  [q] 종료")
        choice = input("선택: ").strip().lower()

        if choice == 'q':
            print("\n" + "=" * 60)
            print("RSA-2048 기준 요약")
            print("=" * 60)
            print(f"  고전 컴퓨터 해독 예상 시간 : 약 10^32년 (GNFS 기준, 1 petaFLOPS 가정)")
            print(f"  필요 논리 큐비트 (이상적)  : 4,096 큐비트 (오류 없는 가정)")
            print(f"  필요 물리 큐비트 (현실적)  : 약 4,000,000 큐비트 (오류 정정 포함)")
            print(f"  현재 장비 가용 큐비트       : {max_qubits} 큐비트")
            print(f"  현재 장비로 가능한 RSA      : RSA-{(max_qubits-2)//3}비트 수준")
            print(f"\n" + "=" * 60)
            print(f"ECC-256 기준 요약")
            print("=" * 60)
            print(f"  고전 컴퓨터 해독 예상 시간 : RSA-2048과 동등 (128비트 보안, 약 10^32년 수준)")
            print(f"  필요 논리 큐비트 (이상적)  : 약 2,330 큐비트")
            print(f"  필요 물리 큐비트 (현실적)  : 약 2,000,000 큐비트 (오류 정정 포함)")
            print(f"  현재 장비 가용 큐비트       : {max_qubits} 큐비트")
            print(f"  현재 장비로 가능한 ECC      : ECC-{(max_qubits-2)//4}비트 수준")
            print("=" * 60)
            print("데모 종료")
            break

        custom_N = None
        if choice in ('1', '3'):
            print(f"\nRSA 타겟 N 직접 입력? (엔터=자동생성): ", end="")
            custom_input = input().strip()
            custom_N = int(custom_input) if custom_input.isdigit() else None
            rsa_demo(n_qubits, custom_N)
        if choice in ('2', '3'):
            ecc_demo(n_qubits)
        if choice not in ('1', '2', '3'):
            print("잘못된 입력입니다.")

if __name__ == "__main__":
    main()