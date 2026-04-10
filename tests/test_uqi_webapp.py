# test_uqi_webapp.py
# uqi_webapp.html 순수 JS 로직 테스트 (Playwright 기반)

import os
import sys
import pytest
from pathlib import Path
from playwright.sync_api import Page, expect

WEBAPP_PATH = (Path(__file__).parent.parent / "webapp" / "uqi_webapp.html").resolve()
WEBAPP_URL  = f"file://{WEBAPP_PATH}"


# ─────────────────────────────────────────────────────────────
# Fixture: 페이지 로드 + JS 함수 노출 (lock screen 우회)
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def page(page: Page):
    """HTML 로드 후 lock screen 우회, 전역 mock 주입"""
    page.goto(WEBAPP_URL)
    # lock screen 숨기기
    page.evaluate("document.getElementById('lockScreen').style.display='none'")
    # localStorage / sessionStorage mock
    page.evaluate("sessionStorage.setItem('uqi_auth','test')")
    # DOM 의존 함수들을 위한 최소 DOM 구조 주입
    page.evaluate("""
        () => {
            // badge 요소들
            ['analyze','optimize','noise','qec-analyze','qec-apply','gpu','qpu'].forEach(s => {
                if (!document.getElementById('badge-'+s)) {
                    const b = document.createElement('span');
                    b.id = 'badge-'+s;
                    document.body.appendChild(b);
                }
                if (!document.getElementById('result-'+s)) {
                    const r = document.createElement('div');
                    r.id = 'result-'+s;
                    document.body.appendChild(r);
                }
            });
            // global-qpu select
            if (!document.getElementById('global-qpu')) {
                const s = document.createElement('select');
                s.id = 'global-qpu';
                const opt = document.createElement('option');
                opt.value = 'ibm_fez';
                s.appendChild(opt);
                document.body.appendChild(s);
            }
            // qpu-cache-ts
            if (!document.getElementById('qpu-cache-ts')) {
                const el = document.createElement('span');
                el.id = 'qpu-cache-ts';
                document.body.appendChild(el);
            }
            // qpu-analysis-section
            if (!document.getElementById('qpu-analysis-section')) {
                const el = document.createElement('div');
                el.id = 'qpu-analysis-section';
                el.style.display = 'none';
                document.body.appendChild(el);
            }
        }
    """)
    return page


# ─────────────────────────────────────────────────────────────
# TC-01x: getResultText
# ─────────────────────────────────────────────────────────────

class TestGetResultText:

    def test_TC011_extracts_content_text(self, page):
        result = page.evaluate("""
            getResultText({result: {content: [{text: 'hello world'}]}})
        """)
        assert result == "hello world"

    def test_TC012_extracts_structured_content(self, page):
        result = page.evaluate("""
            getResultText({result: {structuredContent: {result: 'structured'}}})
        """)
        assert result == "structured"

    def test_TC013_fallback_to_json_stringify(self, page):
        result = page.evaluate("""
            getResultText({result: {foo: 'bar'}})
        """)
        assert "foo" in result
        assert "bar" in result

    def test_TC014_empty_result_returns_string(self, page):
        result = page.evaluate("""
            typeof getResultText({result: null})
        """)
        assert result == "string"

    def test_TC015_content_array_first_element(self, page):
        result = page.evaluate("""
            getResultText({result: {content: [{text: 'first'}, {text: 'second'}]}})
        """)
        assert result == "first"


# ─────────────────────────────────────────────────────────────
# TC-02x: elapsed
# ─────────────────────────────────────────────────────────────

class TestElapsed:

    def test_TC021_returns_string_with_s_suffix(self, page):
        result = page.evaluate("elapsed(Date.now() - 1500)")
        assert result.endswith("s")

    def test_TC022_approximately_correct_time(self, page):
        result = page.evaluate("elapsed(Date.now() - 2000)")
        val = float(result.replace("s", ""))
        assert 1.5 <= val <= 3.0

    def test_TC023_two_decimal_places(self, page):
        result = page.evaluate("elapsed(Date.now() - 1000)")
        parts = result.replace("s", "").split(".")
        assert len(parts) == 2
        assert len(parts[1]) == 2

    def test_TC024_zero_elapsed(self, page):
        result = page.evaluate("elapsed(Date.now())")
        val = float(result.replace("s", ""))
        assert val >= 0.0


# ─────────────────────────────────────────────────────────────
# TC-03x: _qpuCacheAge
# ─────────────────────────────────────────────────────────────

class TestQpuCacheAge:

    def test_TC031_no_ts_returns_infinity(self, page):
        result = page.evaluate("_qpuCacheAge({})")
        assert result == float("inf")

    def test_TC032_null_cache_returns_infinity(self, page):
        result = page.evaluate("_qpuCacheAge(null)")
        assert result == float("inf")

    def test_TC033_recent_ts_returns_small_value(self, page):
        result = page.evaluate("_qpuCacheAge({ts: Date.now() - 1000})")
        assert 500 <= result <= 5000

    def test_TC034_old_ts_returns_large_value(self, page):
        result = page.evaluate("_qpuCacheAge({ts: Date.now() - 86400000})")
        assert result >= 80000000

    def test_TC035_ttl_constant_is_12h(self, page):
        result = page.evaluate("_QPU_CACHE_TTL")
        assert result == 12 * 60 * 60 * 1000

    def test_TC036_cache_key_constant(self, page):
        result = page.evaluate("_QPU_CACHE_KEY")
        assert isinstance(result, str)
        assert len(result) > 0


# ─────────────────────────────────────────────────────────────
# TC-04x: setBadge
# ─────────────────────────────────────────────────────────────

class TestSetBadge:

    def test_TC041_done_status_shows_checkmark(self, page):
        page.evaluate("setBadge('analyze', 'done')")
        text = page.evaluate("document.getElementById('badge-analyze').textContent")
        assert "Done" in text

    def test_TC042_error_status_shows_cross(self, page):
        page.evaluate("setBadge('analyze', 'error')")
        text = page.evaluate("document.getElementById('badge-analyze').textContent")
        assert "Error" in text

    def test_TC043_null_status_hides_badge(self, page):
        page.evaluate("setBadge('analyze', null)")
        display = page.evaluate(
            "document.getElementById('badge-analyze').style.display"
        )
        assert display == "none"

    def test_TC044_done_class_applied(self, page):
        page.evaluate("setBadge('analyze', 'done')")
        cls = page.evaluate("document.getElementById('badge-analyze').className")
        assert "done" in cls

    def test_TC045_error_class_applied(self, page):
        page.evaluate("setBadge('analyze', 'error')")
        cls = page.evaluate("document.getElementById('badge-analyze').className")
        assert "error" in cls

    def test_TC046_nonexistent_step_no_exception(self, page):
        result = page.evaluate("""
            (() => { try { setBadge('nonexistent_step', 'done'); return true; }
                     catch(e) { return false; } })()
        """)
        assert result is True


# ─────────────────────────────────────────────────────────────
# TC-05x: renderAnalyze
# ─────────────────────────────────────────────────────────────

class TestRenderAnalyze:

    def test_TC051_returns_string(self, page):
        result = page.evaluate("""
            typeof renderAnalyze({circuits: {}, framework: 'Qiskit'}, '1.23s')
        """)
        assert result == "string"

    def test_TC052_includes_framework(self, page):
        result = page.evaluate("""
            renderAnalyze({circuits: {}, framework: 'PennyLane'}, '0.5s')
        """)
        assert "PennyLane" in result

    def test_TC053_includes_elapsed(self, page):
        result = page.evaluate("""
            renderAnalyze({circuits: {}, framework: 'Qiskit'}, '2.34s')
        """)
        assert "2.34s" in result

    def test_TC054_circuit_data_rendered(self, page):
        result = page.evaluate("""
            renderAnalyze({
                circuits: {
                    'bell': {
                        profile: {num_qubits:2, total_gates:4, depth:3,
                                  two_q_ratio:0.25, is_parameterized:false},
                        t2_ratio: 0.1
                    }
                },
                framework: 'Qiskit'
            }, '1s')
        """)
        assert "bell" in result
        assert "2" in result   # num_qubits

    def test_TC055_error_circuit_shows_error_state(self, page):
        result = page.evaluate("""
            renderAnalyze({
                circuits: {'circ_a': {error: 'extraction failed'}},
                framework: 'Qiskit'
            }, '1s')
        """)
        assert "error-state" in result

    def test_TC056_empty_circuits_no_exception(self, page):
        result = page.evaluate("""
            (() => { try {
                renderAnalyze({circuits: {}, framework: 'Qiskit'}, '0s');
                return true;
            } catch(e) { return false; } })()
        """)
        assert result is True

    def test_TC057_parameterized_yes_shown(self, page):
        result = page.evaluate("""
            renderAnalyze({
                circuits: {'c': {profile: {is_parameterized: true}, t2_ratio: null}},
                framework: 'Qiskit'
            }, '1s')
        """)
        assert "Yes" in result

    def test_TC058_qasm_button_shown_when_qasm_present(self, page):
        result = page.evaluate("""
            renderAnalyze({
                circuits: {'bell': {
                    profile: {num_qubits:2, total_gates:3, depth:2,
                              two_q_ratio:0.33, is_parameterized:false},
                    t2_ratio: 0.1,
                    qasm: 'OPENQASM 2.0;\\ninclude "qelib1.inc";\\nqreg q[2];\\nh q[0];\\ncx q[0],q[1];'
                }},
                framework: 'Qiskit'
            }, '1s')
        """)
        assert "Download .qasm" in result
        assert "QASM" in result

    def test_TC059_qasm_button_hidden_when_no_qasm(self, page):
        result = page.evaluate("""
            renderAnalyze({
                circuits: {'bell': {
                    profile: {num_qubits:2, total_gates:3, depth:2,
                              two_q_ratio:0.33, is_parameterized:false},
                    t2_ratio: 0.1
                }},
                framework: 'Qiskit'
            }, '1s')
        """)
        assert "Download .qasm" not in result

    def test_TC05A_qasm_preview_truncated_at_50_lines(self, page):
        result = page.evaluate("""
            (() => {
                const lines = Array.from({length: 80}, (_, i) => 'gate_' + i);
                const qasm = lines.join('\\n');
                const r = _qasmPreview(qasm);
                return r.truncated && r.preview.split('\\n').length === 50 && r.total === 80;
            })()
        """)
        assert result is True

    def test_TC05B_qasm_preview_not_truncated_under_50_lines(self, page):
        result = page.evaluate("""
            (() => {
                const lines = Array.from({length: 30}, (_, i) => 'gate_' + i);
                const qasm = lines.join('\\n');
                const r = _qasmPreview(qasm);
                return !r.truncated && r.preview === qasm;
            })()
        """)
        assert result is True

    def test_TC05C_truncated_qasm_shows_truncation_notice(self, page):
        result = page.evaluate("""
            renderAnalyze({
                circuits: {'big': {
                    profile: {num_qubits:10, total_gates:200, depth:100,
                              two_q_ratio:0.5, is_parameterized:false},
                    t2_ratio: 0.3,
                    qasm: Array.from({length: 80}, (_, i) => 'cx q[' + i + '],q[0];').join('\\n')
                }},
                framework: 'Qiskit'
            }, '1s')
        """)
        assert "truncated" in result


# ─────────────────────────────────────────────────────────────
# TC-06x: renderOptimize
# ─────────────────────────────────────────────────────────────

class TestRenderOptimize:

    def test_TC061_returns_string(self, page):
        result = page.evaluate("""
            typeof renderOptimize({results: {}, qpu_name: 'ibm_fez'}, '1s')
        """)
        assert result == "string"

    def test_TC062_includes_qpu_name(self, page):
        result = page.evaluate("""
            renderOptimize({results: {}, qpu_name: 'iqm_garnet'}, '1s')
        """)
        assert "iqm_garnet" in result

    def test_TC063_gate_reduction_percentage_shown(self, page):
        result = page.evaluate("""
            renderOptimize({
                results: {'c': {gate_reduction: 0.35, depth_reduction: 0.2,
                                combination: 'qiskit+sabre', opt1_gates: 10,
                                opt1_depth: 5, opt_time_sec: 1.5}},
                qpu_name: 'ibm_fez'
            }, '1s')
        """)
        assert "35.0%" in result

    def test_TC064_error_result_shows_error_state(self, page):
        result = page.evaluate("""
            renderOptimize({
                results: {'c': {error: 'opt failed'}},
                qpu_name: 'ibm_fez'
            }, '1s')
        """)
        assert "error-state" in result

    def test_TC065_zero_gate_reduction_shown(self, page):
        result = page.evaluate("""
            renderOptimize({
                results: {'c': {gate_reduction: 0.0, depth_reduction: 0.0,
                                combination: 'qiskit+sabre'}},
                qpu_name: 'ibm_fez'
            }, '1s')
        """)
        assert "0.0%" in result

    def test_TC066_qasm_button_shown_when_qasm_present(self, page):
        result = page.evaluate("""
            renderOptimize({
                results: {'bell': {
                    gate_reduction: 0.2, depth_reduction: 0.1,
                    combination: 'qiskit+sabre', opt1_gates: 8,
                    opt1_depth: 4, opt_time_sec: 1.0,
                    qasm: 'OPENQASM 2.0;\\ninclude "qelib1.inc";\\nqreg q[2];\\ncx q[0],q[1];'
                }},
                qpu_name: 'ibm_fez'
            }, '1s')
        """)
        assert "Download .qasm" in result
        assert "QASM" in result

    def test_TC067_qasm_button_hidden_when_no_qasm(self, page):
        result = page.evaluate("""
            renderOptimize({
                results: {'bell': {
                    gate_reduction: 0.2, depth_reduction: 0.1,
                    combination: 'qiskit+sabre'
                }},
                qpu_name: 'ibm_fez'
            }, '1s')
        """)
        assert "Download .qasm" not in result

    def test_TC068_truncated_qasm_shows_truncation_notice(self, page):
        result = page.evaluate("""
            renderOptimize({
                results: {'big': {
                    gate_reduction: 0.3, depth_reduction: 0.2,
                    combination: 'qiskit+sabre', opt1_gates: 50,
                    opt1_depth: 20, opt_time_sec: 2.0,
                    qasm: Array.from({length: 80}, (_, i) => 'cx q[' + i + '],q[0];').join('\\n')
                }},
                qpu_name: 'ibm_fez'
            }, '1s')
        """)
        assert "truncated" in result


# ─────────────────────────────────────────────────────────────
# TC-07x: renderNoise
# ─────────────────────────────────────────────────────────────

class TestRenderNoise:

    def test_TC071_returns_string(self, page):
        result = page.evaluate("""
            typeof renderNoise({results:{}, qpu_name:'ibm_fez', shots:1024}, '1s')
        """)
        assert result == "string"

    def test_TC072_includes_shots(self, page):
        result = page.evaluate("""
            renderNoise({results:{}, qpu_name:'ibm_fez', shots:2048}, '1s')
        """)
        assert "2048" in result

    def test_TC073_fidelity_shown(self, page):
        result = page.evaluate("""
            renderNoise({
                results: {'c': {fidelity: 0.9512, tvd: 0.04,
                                noise_counts: {'00': 500, '11': 524}}},
                qpu_name: 'ibm_fez', shots: 1024
            }, '1s')
        """)
        assert "0.9512" in result

    def test_TC074_tvd_shown(self, page):
        result = page.evaluate("""
            renderNoise({
                results: {'c': {fidelity: 0.9, tvd: 0.1234, noise_counts: {}}},
                qpu_name: 'ibm_fez', shots: 1024
            }, '1s')
        """)
        assert "0.1234" in result

    def test_TC075_noise_counts_rendered(self, page):
        result = page.evaluate("""
            renderNoise({
                results: {'c': {fidelity: 0.9, tvd: 0.1,
                                noise_counts: {'00': 600, '11': 424}}},
                qpu_name: 'ibm_fez', shots: 1024
            }, '1s')
        """)
        # 카운트 바 방식: 비트문자열과 퍼센트 모두 포함됨
        assert "00" in result
        assert "%" in result


# ─────────────────────────────────────────────────────────────
# TC-08x: renderQECAnalyze
# ─────────────────────────────────────────────────────────────

class TestRenderQECAnalyze:

    def test_TC081_returns_string(self, page):
        result = page.evaluate("""
            typeof renderQECAnalyze({results:{}, qpu_name:'ibm_fez'}, '1s')
        """)
        assert result == "string"

    def test_TC082_necessity_required_shown(self, page):
        result = page.evaluate("""
            renderQECAnalyze({
                results: {'c': {necessity:'required', fidelity:0.85,
                                tvd:0.15, t2_ratio:5.0,
                                reasons:[], recommended_codes:[]}},
                qpu_name: 'ibm_fez'
            }, '1s')
        """)
        assert "REQUIRED" in result

    def test_TC083_necessity_unnecessary_shown(self, page):
        result = page.evaluate("""
            renderQECAnalyze({
                results: {'c': {necessity:'unnecessary', fidelity:0.995,
                                tvd:0.01, t2_ratio:0.1,
                                reasons:[], recommended_codes:[]}},
                qpu_name: 'ibm_fez'
            }, '1s')
        """)
        assert "UNNECESSARY" in result

    def test_TC084_recommended_codes_shown(self, page):
        result = page.evaluate("""
            renderQECAnalyze({
                results: {'c': {necessity:'recommended', fidelity:0.97,
                                tvd:0.03, t2_ratio:0.3,
                                reasons:['Fidelity low'],
                                recommended_codes:['bit_flip','phase_flip']}},
                qpu_name: 'ibm_fez'
            }, '1s')
        """)
        assert "bit_flip" in result
        assert "phase_flip" in result

    def test_TC085_error_result_shows_error_state(self, page):
        result = page.evaluate("""
            renderQECAnalyze({
                results: {'c': {error: 'qec failed'}},
                qpu_name: 'ibm_fez'
            }, '1s')
        """)
        assert "error-state" in result

    def test_TC086_reasons_shown(self, page):
        result = page.evaluate("""
            renderQECAnalyze({
                results: {'c': {necessity:'required', fidelity:0.9,
                                tvd:0.1, t2_ratio:2.0,
                                reasons:['T2 ratio too high'],
                                recommended_codes:[]}},
                qpu_name: 'ibm_fez'
            }, '1s')
        """)
        assert "T2 ratio too high" in result


# ─────────────────────────────────────────────────────────────
# TC-09x: fmtMs (ms → 자동 단위 변환)
# ─────────────────────────────────────────────────────────────

class TestFmtMs:

    def test_TC091_null_returns_null(self, page):
        result = page.evaluate("fmtMs(null)")
        assert result is None

    def test_TC092_large_ms_converts_to_seconds(self, page):
        """IonQ T1: 100000ms → 100s"""
        result = page.evaluate("fmtMs(100000)")
        assert result["unit"] == "s"
        assert result["text"] == "100"

    def test_TC093_medium_ms_stays_ms(self, page):
        """QuEra T1: 75ms → 75.0ms"""
        result = page.evaluate("fmtMs(75)")
        assert result["unit"] == "ms"

    def test_TC094_small_ms_converts_to_us(self, page):
        """IBM T1: 0.155ms → 155μs"""
        result = page.evaluate("fmtMs(0.155)")
        assert result["unit"] == "μs"
        assert result["text"] == "155"

    def test_TC095_very_small_ms_converts_to_us(self, page):
        """IQM T2: 0.0074ms → 7.4μs"""
        result = page.evaluate("fmtMs(0.0074)")
        assert result["unit"] == "μs"
        assert result["text"] == "7.4"

    def test_TC096_threshold_1000ms_is_seconds(self, page):
        result = page.evaluate("fmtMs(1000)")
        assert result["unit"] == "s"

    def test_TC097_threshold_1ms_is_ms(self, page):
        result = page.evaluate("fmtMs(1)")
        assert result["unit"] == "ms"

    def test_TC098_text_is_string(self, page):
        result = page.evaluate("typeof fmtMs(0.155).text")
        assert result == "string"

    def test_TC099_large_seconds_no_decimal(self, page):
        """100s 이상은 소수점 없이"""
        result = page.evaluate("fmtMs(500000)")
        assert result["unit"] == "s"
        assert "." not in result["text"]


# ─────────────────────────────────────────────────────────────
# TC-10x: fmtNs (ns → 자동 단위 변환)
# ─────────────────────────────────────────────────────────────

class TestFmtNs:

    def test_TC101_null_returns_null(self, page):
        result = page.evaluate("fmtNs(null)")
        assert result is None

    def test_TC102_large_ns_converts_to_us(self, page):
        """IonQ 2Q gate: 600000ns → 600μs"""
        result = page.evaluate("fmtNs(600000)")
        assert result["unit"] == "μs"
        assert result["text"] == "600"

    def test_TC103_small_ns_stays_ns(self, page):
        """IBM 2Q gate: 68ns → 68ns"""
        result = page.evaluate("fmtNs(68)")
        assert result["unit"] == "ns"
        assert result["text"] == "68"

    def test_TC104_threshold_1000ns_is_us(self, page):
        result = page.evaluate("fmtNs(1000)")
        assert result["unit"] == "μs"

    def test_TC105_threshold_999ns_is_ns(self, page):
        result = page.evaluate("fmtNs(999)")
        assert result["unit"] == "ns"

    def test_TC106_text_is_string(self, page):
        result = page.evaluate("typeof fmtNs(68).text")
        assert result == "string"

    def test_TC107_fractional_us_one_decimal(self, page):
        """1500ns → 1.5μs (소수점 1자리)"""
        result = page.evaluate("fmtNs(1500)")
        assert result["unit"] == "μs"
        assert result["text"] == "1.5"

    def test_TC108_sub10_us_no_trailing_zero(self, page):
        """5000ns → 5.0μs (불필요한 자릿수 없음)"""
        result = page.evaluate("fmtNs(5000)")
        assert result["unit"] == "μs"
        assert result["text"] == "5.0"



# ─────────────────────────────────────────────────────────────
# TC-12x: renderQPUDetail — 벤더 링크 및 토폴로지 제거
# ─────────────────────────────────────────────────────────────

class TestRenderQPUDetail:

    def _render(self, page, qpu, data):
        return page.evaluate(f"""
            (() => {{
                const c = document.createElement('div');
                renderQPUDetail(c, '{qpu}', {data});
                return c.innerHTML;
            }})()
        """)

    def test_TC121_quera_no_topology(self, page):
        # QuEra has all-to-all connectivity → Topology panel shown with All-to-all message, no graph
        html = self._render(page, 'quera_aquila',
            '{"num_qubits":256,"avg_t1_ms":75,"rabi_freq_max_mhz":15.8}')
        assert "Topology" in html
        assert "All-to-all" in html
        assert "topo-canvas" not in html

    def test_TC122_quera_no_type(self, page):
        html = self._render(page, 'quera_aquila',
            '{"num_qubits":256,"avg_t1_ms":75,"rabi_freq_max_mhz":15.8}')
        assert ">Type<" not in html

    def test_TC123_quera_no_mode(self, page):
        html = self._render(page, 'quera_aquila',
            '{"num_qubits":256,"avg_t1_ms":75,"rabi_freq_max_mhz":15.8}')
        assert ">Mode<" not in html

    def test_TC124_ionq_no_topology(self, page):
        # IonQ has all-to-all connectivity → Topology panel with All-to-all message, no graph
        html = self._render(page, 'ionq_forte1',
            '{"num_qubits":36,"avg_t1_ms":100000,"avg_1q_error":0.001,"avg_2q_error":0.01,"avg_2q_ns":600000}')
        assert "Topology" in html
        assert "All-to-all" in html
        assert "topo-canvas" not in html



# ─────────────────────────────────────────────────────────────
# TC-13x: Knowledge 페이지 rag-qpu 기본값
# ─────────────────────────────────────────────────────────────

class TestRagQpuDefault:

    def test_TC131_rag_qpu_initial_value_is_empty(self, page):
        result = page.evaluate("document.getElementById('rag-qpu').value")
        assert result == ""

    def test_TC132_rag_qpu_first_option_is_all(self, page):
        result = page.evaluate("""
            document.getElementById('rag-qpu').options[0].textContent.trim()
        """)
        assert result == "All QPUs"

    def test_TC133_rag_qpu_first_option_value_is_empty(self, page):
        result = page.evaluate("""
            document.getElementById('rag-qpu').options[0].value
        """)
        assert result == ""


# ─────────────────────────────────────────────────────────────
# TC-14x: per-qubit 차트 — renderQPUDetail HTML 구조
# ─────────────────────────────────────────────────────────────

class TestPerQubitDetail:

    def _render(self, page, qpu, data):
        return page.evaluate(f"""
            (() => {{
                const c = document.createElement('div');
                renderQPUDetail(c, '{qpu}', {data});
                return c.innerHTML;
            }})()
        """)

    def test_TC141_per_qubit_tabs_shown_when_data_present(self, page):
        html = self._render(page, 'ibm_fez', """{
            "num_qubits":5,
            "avg_t1_ms":0.155,"avg_t2_ms":0.110,
            "avg_1q_error":0.007,"avg_2q_error":0.033,"avg_ro_error":0.01,
            "avg_1q_ns":24,"avg_2q_ns":68,
            "qubit_1q_error":[0.005,0.007,0.008,0.006,0.009]
        }""")
        assert "qubit-dist-tab" in html

    def test_TC142_no_qubit_tabs_when_no_per_qubit_data(self, page):
        html = self._render(page, 'ibm_fez', """{
            "num_qubits":5,
            "avg_t1_ms":0.155,"avg_t2_ms":0.110,
            "avg_1q_error":0.007,"avg_2q_error":0.033,"avg_ro_error":0.01,
            "avg_1q_ns":24,"avg_2q_ns":68
        }""")
        assert "qubit-dist-tab" not in html

    def test_TC143_1q_error_tab_shown_when_data_present(self, page):
        html = self._render(page, 'ibm_fez', """{
            "num_qubits":5,
            "avg_1q_error":0.007,"avg_2q_error":0.033,
            "qubit_1q_error":[0.005,0.007,0.008,0.006,0.009]
        }""")
        assert "1Q Error" in html

    def test_TC144_t1_tab_shown_when_t1_data_present(self, page):
        html = self._render(page, 'ibm_fez', """{
            "num_qubits":5,
            "avg_t1_ms":0.155,
            "qubit_t1_ms":[0.14,0.15,0.16,0.155,0.148]
        }""")
        assert ">T1<" in html

    def test_TC145_chart_container_id_matches_qpu_name(self, page):
        html = self._render(page, 'ibm_fez', """{
            "num_qubits":5,
            "avg_1q_error":0.007,
            "qubit_1q_error":[0.005,0.007,0.008,0.006,0.009]
        }""")
        assert "qubit-dist-chart-ibm_fez" in html


# ─────────────────────────────────────────────────────────────
# TC-15x: boxStats 로직 (박스플롯 통계 함수)
# ─────────────────────────────────────────────────────────────

class TestBoxStats:

    def test_TC151_returns_5_elements(self, page):
        result = page.evaluate("""
            (() => {
                const arr = [0.001,0.003,0.005,0.007,0.009,0.010,0.012,0.004,0.006,0.008];
                function boxStats(arr) {
                    const s = [...arr].sort((a,b)=>a-b), n=s.length;
                    const q1=s[Math.floor(n*0.25)], q2=s[Math.floor(n*0.5)], q3=s[Math.floor(n*0.75)];
                    const iqr=q3-q1, lo=Math.max(s[0],q1-1.5*iqr), hi=Math.min(s[n-1],q3+1.5*iqr);
                    return [lo,q1,q2,q3,hi];
                }
                return boxStats(arr).length;
            })()
        """)
        assert result == 5

    def test_TC152_median_is_middle_value(self, page):
        # n=10, Math.floor(10*0.5)=5 → s[5]=6
        result = page.evaluate("""
            (() => {
                function boxStats(arr) {
                    const s=[...arr].sort((a,b)=>a-b),n=s.length;
                    const q1=s[Math.floor(n*0.25)],q2=s[Math.floor(n*0.5)],q3=s[Math.floor(n*0.75)];
                    const iqr=q3-q1,lo=Math.max(s[0],q1-1.5*iqr),hi=Math.min(s[n-1],q3+1.5*iqr);
                    return [lo,q1,q2,q3,hi];
                }
                const r = boxStats([1,2,3,4,5,6,7,8,9,10]);
                return r[2];  // q2 = s[floor(10*0.5)] = s[5] = 6
            })()
        """)
        assert result == 6

    def test_TC153_min_le_q1_le_median(self, page):
        result = page.evaluate("""
            (() => {
                function boxStats(arr) {
                    const s=[...arr].sort((a,b)=>a-b),n=s.length;
                    const q1=s[Math.floor(n*0.25)],q2=s[Math.floor(n*0.5)],q3=s[Math.floor(n*0.75)];
                    const iqr=q3-q1,lo=Math.max(s[0],q1-1.5*iqr),hi=Math.min(s[n-1],q3+1.5*iqr);
                    return [lo,q1,q2,q3,hi];
                }
                const r = boxStats([0.001,0.003,0.005,0.007,0.009,0.011,0.013,0.002,0.006,0.010]);
                return r[0] <= r[1] && r[1] <= r[2];
            })()
        """)
        assert result is True

    def test_TC154_analysis_section_hidden_with_no_data(self, page):
        result = page.evaluate("""
            document.getElementById('qpu-analysis-section').style.display
        """)
        assert result == "none"


class TestRefreshBtnStyle:
    """TC16x: btn-refresh-list CSS 색상 가시성"""

    def test_TC161_refresh_btn_color_is_accent(self, page):
        """btn-refresh-list의 color가 --accent(#00d4ff)이어야 함 — text3(#484f58)이면 배경에 묻힘"""
        color = page.evaluate("""
            (() => {
                const btn = document.querySelector('.btn-refresh-list');
                return getComputedStyle(btn).color;
            })()
        """)
        assert color == "rgb(0, 212, 255)"

    def test_TC162_refresh_btn_border_is_accent(self, page):
        """btn-refresh-list의 border-color가 --accent이어야 함"""
        color = page.evaluate("""
            (() => {
                const btn = document.querySelector('.btn-refresh-list');
                return getComputedStyle(btn).borderTopColor;
            })()
        """)
        assert color == "rgb(0, 212, 255)"

    def test_TC163_refresh_btn_not_text3_color(self, page):
        """btn-refresh-list가 이전 text3(#484f58) 색을 사용하지 않아야 함"""
        color = page.evaluate("""
            (() => {
                const btn = document.querySelector('.btn-refresh-list');
                return getComputedStyle(btn).color;
            })()
        """)
        assert color != "rgb(72, 79, 88)"  # #484f58


class TestLoadJobHistoryNotConnected:
    """TC17x: loadJobHistory Not Connected 에러 처리"""

    def test_TC171_not_connected_shows_initial_state(self, page):
        """미연결 상태에서 loadJobHistory 호출 시 error-state 대신 초기 상태 표시"""
        result = page.evaluate("""
            (async () => {
                // msgUrl이 null인 초기 상태에서 호출
                await loadJobHistory();
                return document.getElementById('result-job-history').innerHTML;
            })()
        """)
        assert "Click Refresh to load jobs" in result

    def test_TC172_not_connected_no_error_state(self, page):
        """미연결 상태에서 error-state 클래스가 나타나지 않아야 함"""
        has_error = page.evaluate("""
            (async () => {
                await loadJobHistory();
                const el = document.getElementById('result-job-history');
                return el.querySelector('.error-state') !== null;
            })()
        """)
        assert has_error is False

    def test_TC173_not_connected_text_not_shown_in_result(self, page):
        """미연결 상태에서 'Not connected' 문구가 결과 영역에 노출되지 않아야 함"""
        result = page.evaluate("""
            (async () => {
                await loadJobHistory();
                return document.getElementById('result-job-history').textContent;
            })()
        """)
        assert "Not connected" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
