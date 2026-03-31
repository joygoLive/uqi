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
        assert "00:600" in result


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
