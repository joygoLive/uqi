# uqi_benchmark_viz.py
import json
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
import os
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# 한글 폰트 설정
import subprocess
result = subprocess.run(['fc-list', ':lang=ko'], capture_output=True, text=True)
korean_fonts = [line.split(':')[0].strip() for line in result.stdout.strip().split('\n') if line]

if korean_fonts:
    font_path = korean_fonts[0]
    fm.fontManager.addfont(font_path)
    font_name = fm.FontProperties(fname=font_path).get_name()
    plt.rcParams['font.family'] = font_name
else:
    # 폰트 없으면 영어로 대체
    plt.rcParams['font.family'] = 'DejaVu Sans'

plt.rcParams['axes.unicode_minus'] = False

# ── 데이터 로드 ──────────────────────────────
fez_file      = "benchmark_results_ibm_fez_20260318_210259.json"
marrakesh_file = "benchmark_results_ibm_marrakesh_20260318_202936.json"

with open(fez_file, 'r') as f:
    fez_data = json.load(f)
with open(marrakesh_file, 'r') as f:
    marrakesh_data = json.load(f)

fez_results      = fez_data["results"]
marrakesh_results = marrakesh_data["results"]

# ── 양자 이득 임계 큐비트 (문헌 기반) ────────────
QUANTUM_ADVANTAGE_THRESHOLD = {
    "QAOA(p=1)": {"qubits": 300,  "note": "수백q (Max-Cut 속도향상)"},
    "QAOA(p=3)": {"qubits": 300,  "note": "수백q (Max-Cut 속도향상)"},
    "VQE(Real)": {"qubits": 1000, "note": "수천q (화학 시뮬레이션)"},
    "VQE(ESU2)": {"qubits": 1000, "note": "수천q (화학 시뮬레이션)"},
    "IQAE":      {"qubits": 50,   "note": "수십q (Monte Carlo √N 이득)"},
    "QFT":       {"qubits": 2000, "note": "수천q (Shor 알고리즘 맥락)"},
    "Grover":    {"qubits": 200,  "note": "수백q (실용적 DB 탐색)"},
    "Simon":     {"qubits": None, "note": "실용 문제 없음 (학술 목적)"},
    "GHZ":       {"qubits": 100,  "note": "수십~수백q (양자 통신/센싱)"},
    "QPE":       {"qubits": 1000, "note": "수천q (분자 시뮬레이션)"},
    "QNN":       {"qubits": None, "note": "양자 이득 미입증"},
    "QSVM":      {"qubits": None, "note": "양자 이득 미입증"},
}

# ── 색상 팔레트 ──────────────────────────────
ALGO_COLORS = {
    "QAOA(p=1)": "#E74C3C",
    "QAOA(p=3)": "#C0392B",
    "VQE(Real)": "#3498DB",
    "VQE(ESU2)": "#2980B9",
    "IQAE":      "#2ECC71",
    "QFT":       "#F39C12",
    "Grover":    "#9B59B6",
    "Simon":     "#1ABC9C",
    "GHZ":       "#E67E22",
    "QPE":       "#34495E",
    "QNN":       "#E91E63",
    "QSVM":      "#795548",
}

DOMAIN_COLORS = {
    "최적화":     "#E74C3C",
    "양자화학":   "#3498DB",
    "금융/샘플링": "#2ECC71",
    "기반서브루틴": "#F39C12",
    "탐색/암호":  "#9B59B6",
    "머신러닝":   "#E91E63",
}

def get_valid(results):
    return [r for r in results if r.get("tvd") is not None]

fez_valid      = get_valid(fez_results)
marrakesh_valid = get_valid(marrakesh_results)

# ── Figure 설정 ──────────────────────────────
fig = plt.figure(figsize=(22, 26))
fig.patch.set_facecolor('#F8F9FA')
gs = GridSpec(3, 2, figure=fig, hspace=0.38, wspace=0.32,
              top=0.94, bottom=0.05, left=0.07, right=0.97)

title = fig.suptitle(
    "UQI NISQ QPU Benchmarking\nibm_fez vs ibm_marrakesh (Heron r2) | 4096 shots",
    fontsize=16, fontweight='bold', y=0.98, color='#2C3E50'
)

# ═══════════════════════════════════════════════════
# 그래프 1: 예측 fidelity vs 실제 TVD 산점도
# ═══════════════════════════════════════════════════
ax1 = fig.add_subplot(gs[0, 0])
ax1.set_facecolor('#FAFAFA')

for r in fez_valid:
    pred_f = r["predicted_fidelity"]
    tvd    = r["tvd"]
    algo   = r["algo"]
    color  = ALGO_COLORS.get(algo, "#95A5A6")
    ax1.scatter(pred_f, tvd, color=color, s=80, alpha=0.85,
                marker='o', edgecolors='white', linewidth=0.5, zorder=3)

for r in marrakesh_valid:
    pred_f = r["predicted_fidelity"]
    tvd    = r["tvd"]
    algo   = r["algo"]
    color  = ALGO_COLORS.get(algo, "#95A5A6")
    ax1.scatter(pred_f, tvd, color=color, s=80, alpha=0.85,
                marker='^', edgecolors='white', linewidth=0.5, zorder=3)

# 완벽 예측선: TVD = 1 - pred_f
x_line = np.linspace(0.7, 1.0, 100)
ax1.plot(x_line, 1 - x_line, 'k--', linewidth=1.5,
         alpha=0.6, label='완벽 예측선 (TVD = 1 - pred_f)', zorder=2)

# 허용 오차 밴드 ±0.05
ax1.fill_between(x_line, (1-x_line)-0.05, (1-x_line)+0.05,
                 alpha=0.1, color='gray', label='허용 오차 ±0.05')

ax1.set_xlabel("예측 Fidelity", fontsize=11)
ax1.set_ylabel("실제 TVD", fontsize=11)
ax1.set_title("① 예측 Fidelity vs 실제 TVD", fontsize=12, fontweight='bold', color='#2C3E50')
ax1.set_xlim(0.68, 1.02)
ax1.set_ylim(-0.02, 0.85)
ax1.grid(True, alpha=0.3)

# 범례: 알고리즘
algo_patches = [mpatches.Patch(color=c, label=a)
                for a, c in ALGO_COLORS.items()]
legend1 = ax1.legend(handles=algo_patches, loc='upper left',
                     fontsize=7, ncol=2, framealpha=0.8,
                     title="알고리즘", title_fontsize=8)
ax1.add_artist(legend1)

# 마커 범례
fez_marker      = plt.Line2D([0],[0], marker='o', color='w',
                              markerfacecolor='gray', markersize=8, label='ibm_fez')
marrakesh_marker = plt.Line2D([0],[0], marker='^', color='w',
                               markerfacecolor='gray', markersize=8, label='ibm_marrakesh')
ax1.legend(handles=[fez_marker, marrakesh_marker], loc='lower right',
           fontsize=9, framealpha=0.8)

# 주요 이상치 레이블
for r in fez_valid + marrakesh_valid:
    pred_f = r["predicted_fidelity"]
    tvd    = r["tvd"]
    diff   = abs(tvd - (1 - pred_f))
    if diff > 0.15:
        backend = "fez" if r in fez_valid else "mrk"
        ax1.annotate(
            f"{r['algo']}\n{r['n_qubits']}q ({backend})",
            xy=(pred_f, tvd),
            xytext=(pred_f - 0.08, tvd + 0.03),
            fontsize=7, color='#C0392B',
            arrowprops=dict(arrowstyle='->', color='#C0392B', lw=1.0)
        )

# ═══════════════════════════════════════════════════
# 그래프 2: 큐비트 수 vs TVD 추세 (fez + marrakesh)
# ═══════════════════════════════════════════════════
ax2 = fig.add_subplot(gs[0, 1])
ax2.set_facecolor('#FAFAFA')

# 알고리즘별로 fez/marrakesh 라인 각각
algos = list(ALGO_COLORS.keys())
for algo in algos:
    color = ALGO_COLORS[algo]
    # fez
    fez_pts = sorted([(r["n_qubits"], r["tvd"])
                      for r in fez_valid if r["algo"] == algo],
                     key=lambda x: x[0])
    # marrakesh
    mrk_pts = sorted([(r["n_qubits"], r["tvd"])
                      for r in marrakesh_valid if r["algo"] == algo],
                     key=lambda x: x[0])

    if fez_pts:
        xs, ys = zip(*fez_pts)
        ax2.plot(xs, ys, '-o', color=color, linewidth=1.8,
                 markersize=5, alpha=0.9, label=f"{algo}")
    if mrk_pts:
        xs, ys = zip(*mrk_pts)
        ax2.plot(xs, ys, '--^', color=color, linewidth=1.2,
                 markersize=4, alpha=0.5)

ax2.axhline(y=0.2, color='red', linestyle=':', linewidth=1.5,
            alpha=0.7, label='TVD 허용 한계 (0.2)')
ax2.set_xlabel("큐비트 수", fontsize=11)
ax2.set_ylabel("TVD", fontsize=11)
ax2.set_title("② 큐비트 수 vs TVD 추세\n(실선=fez, 점선=marrakesh)",
              fontsize=12, fontweight='bold', color='#2C3E50')
ax2.legend(fontsize=7, ncol=2, loc='upper left',
           framealpha=0.8, title="알고리즘", title_fontsize=8)
ax2.grid(True, alpha=0.3)
ax2.set_xlim(1, 14)
ax2.set_ylim(-0.02, 0.85)

# ═══════════════════════════════════════════════════
# 그래프 3: 현재 신뢰 범위 vs 양자 이득 임계 갭
# ═══════════════════════════════════════════════════
ax3 = fig.add_subplot(gs[1, :])
ax3.set_facecolor('#FAFAFA')

# fez 기준 fidelity 70% 이상 최대 큐비트
algo_max_fez = {}
for r in fez_valid:
    algo = r["algo"]
    pred_f = r["predicted_fidelity"]
    if pred_f >= 0.70:
        if algo not in algo_max_fez or r["n_qubits"] > algo_max_fez[algo]:
            algo_max_fez[algo] = r["n_qubits"]

# marrakesh 기준
algo_max_mrk = {}
for r in marrakesh_valid:
    algo = r["algo"]
    pred_f = r["predicted_fidelity"]
    if pred_f >= 0.70:
        if algo not in algo_max_mrk or r["n_qubits"] > algo_max_mrk[algo]:
            algo_max_mrk[algo] = r["n_qubits"]

algo_list = list(ALGO_COLORS.keys())
y_pos = np.arange(len(algo_list))
bar_height = 0.35

for i, algo in enumerate(algo_list):
    color = ALGO_COLORS[algo]

    # fez 신뢰 범위
    max_q_fez = algo_max_fez.get(algo, 0)
    ax3.barh(i + bar_height/2, max_q_fez, bar_height,
             color=color, alpha=0.85, label=f"{algo} (fez)" if i == 0 else "")

    # marrakesh 신뢰 범위
    max_q_mrk = algo_max_mrk.get(algo, 0)
    ax3.barh(i - bar_height/2, max_q_mrk, bar_height,
             color=color, alpha=0.45, hatch='//',
             label=f"{algo} (marrakesh)" if i == 0 else "")

    # 수치 레이블
    ax3.text(max_q_fez + 0.3, i + bar_height/2,
             f"{max_q_fez}q", va='center', fontsize=8, color='#2C3E50')
    ax3.text(max_q_mrk + 0.3, i - bar_height/2,
             f"{max_q_mrk}q", va='center', fontsize=8, color='#7F8C8D')

    # 양자 이득 임계 마커
    threshold = QUANTUM_ADVANTAGE_THRESHOLD.get(algo, {})
    threshold_q = threshold.get("qubits")
    if threshold_q:
        ax3.plot(threshold_q, i, 'r*', markersize=14, zorder=5,
                 label='양자 이득 임계' if i == 0 else "")
        ax3.annotate(
            f"→ {threshold_q}q+\n{threshold.get('note','')}",
            xy=(threshold_q, i),
            xytext=(threshold_q + 10, i),
            fontsize=7, color='#C0392B', va='center'
        )
    else:
        ax3.text(14, i, threshold.get("note", ""), va='center',
                 fontsize=7, color='#7F8C8D', style='italic')

ax3.set_yticks(y_pos)
ax3.set_yticklabels(algo_list, fontsize=10)
ax3.set_xlabel("큐비트 수", fontsize=11)
ax3.set_title(
    "③ 현재 신뢰 가능 큐비트 범위 vs 양자 이득 임계 큐비트\n"
    "(■ fez, ▨ marrakesh | ★ 양자 이득 임계점)",
    fontsize=12, fontweight='bold', color='#2C3E50'
)
ax3.set_xlim(0, 400)
ax3.axvline(x=12, color='gray', linestyle='--', alpha=0.5,
            linewidth=1, label='현재 최대 실험 큐비트 (12q)')
ax3.grid(True, alpha=0.3, axis='x')

fez_patch      = mpatches.Patch(color='gray', alpha=0.85, label='ibm_fez')
mrk_patch      = mpatches.Patch(color='gray', alpha=0.45,
                                 hatch='//', label='ibm_marrakesh')
star_marker    = plt.Line2D([0],[0], marker='*', color='w',
                             markerfacecolor='red', markersize=12,
                             label='양자 이득 임계점')
vline_marker   = plt.Line2D([0],[0], color='gray', linestyle='--',
                             linewidth=1, label='현재 최대 실험 (12q)')
ax3.legend(handles=[fez_patch, mrk_patch, star_marker, vline_marker],
           loc='lower right', fontsize=9, framealpha=0.8)

# ═══════════════════════════════════════════════════
# 그래프 4: 알고리즘별 노이즈 내성 랭킹
# ═══════════════════════════════════════════════════
ax4 = fig.add_subplot(gs[2, 0])
ax4.set_facecolor('#FAFAFA')

# 알고리즘별 평균 diff 계산 (fez + marrakesh)
algo_diff_fez, algo_diff_mrk = {}, {}
for r in fez_valid:
    algo = r["algo"]
    diff = abs(r["tvd"] - (1 - r["predicted_fidelity"]))
    algo_diff_fez.setdefault(algo, []).append(diff)
for r in marrakesh_valid:
    algo = r["algo"]
    diff = abs(r["tvd"] - (1 - r["predicted_fidelity"]))
    algo_diff_mrk.setdefault(algo, []).append(diff)

algo_mean_fez = {a: np.mean(v) for a, v in algo_diff_fez.items()}
algo_mean_mrk = {a: np.mean(v) for a, v in algo_diff_mrk.items()}

# fez 기준 정렬
sorted_algos = sorted(algo_mean_fez.keys(), key=lambda a: algo_mean_fez[a])
y_pos4 = np.arange(len(sorted_algos))

fez_means = [algo_mean_fez.get(a, 0) for a in sorted_algos]
mrk_means = [algo_mean_mrk.get(a, 0) for a in sorted_algos]
colors4   = [ALGO_COLORS.get(a, "#95A5A6") for a in sorted_algos]

bars_fez = ax4.barh(y_pos4 + bar_height/2, fez_means, bar_height,
                     color=colors4, alpha=0.85)
bars_mrk = ax4.barh(y_pos4 - bar_height/2, mrk_means, bar_height,
                     color=colors4, alpha=0.45, hatch='//')

# 판정 색상 배경
for i, algo in enumerate(sorted_algos):
    mean_diff = algo_mean_fez.get(algo, 0)
    if mean_diff < 0.05:
        bg_color = '#D5F5E3'
        label = 'O'
    elif mean_diff < 0.15:
        bg_color = '#FEF9E7'
        label = '~'
    else:
        bg_color = '#FDEDEC'
        label = 'X'
    ax4.axhspan(i - 0.5, i + 0.5, alpha=0.15, color=bg_color, zorder=0)
    ax4.text(max(fez_means) * 1.05, i, label,
             va='center', fontsize=10, fontweight='bold')

ax4.axvline(x=0.05, color='green', linestyle='--', linewidth=1.5,
            alpha=0.7, label='일치 기준 (0.05)')
ax4.axvline(x=0.15, color='orange', linestyle='--', linewidth=1.5,
            alpha=0.7, label='보통 기준 (0.15)')

ax4.set_yticks(y_pos4)
ax4.set_yticklabels(sorted_algos, fontsize=10)
ax4.set_xlabel("평균 |TVD - 예측오차| (낮을수록 예측 정확)", fontsize=10)
ax4.set_title("④ 알고리즘별 예측 모델 정확도 랭킹\n(■ fez, ▨ marrakesh)",
              fontsize=12, fontweight='bold', color='#2C3E50')
ax4.grid(True, alpha=0.3, axis='x')
ax4.legend(fontsize=9, loc='lower right', framealpha=0.8)

fez_patch2 = mpatches.Patch(color='gray', alpha=0.85, label='ibm_fez')
mrk_patch2 = mpatches.Patch(color='gray', alpha=0.45,
                              hatch='//', label='ibm_marrakesh')
ax4.add_artist(ax4.legend(handles=[fez_patch2, mrk_patch2],
                           loc='upper right', fontsize=9, framealpha=0.8))

# ═══════════════════════════════════════════════════
# 그래프 5: fez vs marrakesh TVD 직접 비교
# ═══════════════════════════════════════════════════
ax5 = fig.add_subplot(gs[2, 1])
ax5.set_facecolor('#FAFAFA')

# 동일 실험 매칭
fez_dict = {(r["algo"], r["n_qubits"]): r["tvd"] for r in fez_valid}
mrk_dict = {(r["algo"], r["n_qubits"]): r["tvd"] for r in marrakesh_valid}
common_keys = set(fez_dict.keys()) & set(mrk_dict.keys())

xs_comp, ys_comp, colors_comp, labels_comp = [], [], [], []
for key in common_keys:
    algo, n = key
    xs_comp.append(fez_dict[key])
    ys_comp.append(mrk_dict[key])
    colors_comp.append(ALGO_COLORS.get(algo, "#95A5A6"))
    labels_comp.append(f"{algo} {n}q")

ax5.scatter(xs_comp, ys_comp, c=colors_comp, s=80,
            alpha=0.85, edgecolors='white', linewidth=0.5, zorder=3)

# y=x 선 (두 백엔드가 동일하면)
max_val = max(max(xs_comp), max(ys_comp)) * 1.05
ax5.plot([0, max_val], [0, max_val], 'k--', linewidth=1.5,
         alpha=0.5, label='동일 성능선 (fez = marrakesh)', zorder=2)

# 이상치 레이블
for i, (x, y, label) in enumerate(zip(xs_comp, ys_comp, labels_comp)):
    if abs(x - y) > 0.1:
        ax5.annotate(label, xy=(x, y),
                     xytext=(x + 0.02, y - 0.03),
                     fontsize=7, color='#C0392B')

ax5.set_xlabel("ibm_fez TVD", fontsize=11)
ax5.set_ylabel("ibm_marrakesh TVD", fontsize=11)
ax5.set_title("⑤ ibm_fez vs ibm_marrakesh TVD 직접 비교\n(대각선 위 = marrakesh가 더 나쁨)",
              fontsize=12, fontweight='bold', color='#2C3E50')
ax5.grid(True, alpha=0.3)
ax5.set_xlim(-0.02, max_val)
ax5.set_ylim(-0.02, max_val)

# 알고리즘 범례
algo_patches5 = [mpatches.Patch(color=c, label=a)
                 for a, c in ALGO_COLORS.items()]
ax5.legend(handles=algo_patches5, loc='upper left',
           fontsize=7, ncol=2, framealpha=0.8,
           title="알고리즘", title_fontsize=8)

# ── 저장 ────────────────────────────────────
output_path = "uqi_benchmark_visualization.png"
plt.savefig(output_path, dpi=150, bbox_inches='tight',
            facecolor=fig.get_facecolor())
print(f"저장 완료: {output_path}")
plt.close()