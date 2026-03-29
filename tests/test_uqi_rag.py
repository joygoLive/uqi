# test_uqi_rag.py

import os
import sys
import json
import sqlite3
import tempfile
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from uqi_rag import (
    UQIRAG,
    RECORD_TYPES,
    _make_embedding_text,
    _row_to_record,
    _connect,
    _init_rag_db,
    _SKIP_EMBED_TYPES,
)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _tmp_rag(chroma=False) -> UQIRAG:
    rag_f    = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    cache_f  = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    chroma_d = tempfile.mkdtemp()

    if not chroma:
        with patch("chromadb.PersistentClient", side_effect=Exception("chroma disabled")):
            rag = UQIRAG(rag_file=rag_f, cache_file=cache_f, chroma_dir=chroma_d)
    else:
        rag = UQIRAG(rag_file=rag_f, cache_file=cache_f, chroma_dir=chroma_d)

    return rag, rag_f, cache_f


def _cleanup(rag_f, cache_f):
    try: os.unlink(rag_f)
    except: pass
    try: os.unlink(cache_f)
    except: pass


def _fake_row(id="abc12345", type="optimization", timestamp="2024-01-01T00:00:00",
              tags=None, data=None):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE records (id TEXT, type TEXT, timestamp TEXT, tags TEXT, data TEXT)"
    )
    conn.execute(
        "INSERT INTO records VALUES (?,?,?,?,?)",
        (id, type, timestamp,
         json.dumps(tags or ["ibm_fez"]),
         json.dumps(data or {"qpu_name": "ibm_fez", "ok": True}))
    )
    row = conn.execute("SELECT * FROM records").fetchone()
    conn.close()
    return row


# ─────────────────────────────────────────────────────────────
# TC-01x: _make_embedding_text
# ─────────────────────────────────────────────────────────────

class TestMakeEmbeddingText:

    def test_TC011_starts_with_type(self):
        text = _make_embedding_text("optimization", {})
        assert text.startswith("type:optimization")

    def test_TC012_optimization_includes_qpu(self):
        text = _make_embedding_text("optimization", {"qpu_name": "ibm_fez"})
        assert "qpu:ibm_fez" in text

    def test_TC013_execution_includes_sdk(self):
        text = _make_embedding_text("execution", {"sdk": "qiskit"})
        assert "sdk:qiskit" in text

    def test_TC014_pipeline_issue_includes_stage(self):
        text = _make_embedding_text("pipeline_issue", {"stage": "extraction"})
        assert "stage:extraction" in text

    def test_TC015_transpile_pattern_includes_pattern(self):
        text = _make_embedding_text("transpile_pattern", {"pattern": "gphase_filter"})
        assert "pattern:gphase_filter" in text

    def test_TC016_conversion_pattern_includes_formats(self):
        text = _make_embedding_text("conversion_pattern",
                                    {"from_format": "qasm", "to_format": "qir"})
        assert "from:qasm" in text
        assert "to:qir" in text

    def test_TC017_qec_experiment_includes_code(self):
        text = _make_embedding_text("qec_experiment", {"code": "steane"})
        assert "code:steane" in text

    def test_TC018_gpu_benchmark_includes_speedup(self):
        text = _make_embedding_text("gpu_benchmark", {"speedup": 3.5})
        assert "speedup:3.5" in text

    def test_TC019_security_block_includes_reason(self):
        text = _make_embedding_text("security_block", {"reason": "os.system"})
        assert "reason:os.system" in text

    def test_TC01A_unknown_type_flattens_data(self):
        text = _make_embedding_text("custom_type", {"key1": "val1", "key2": 42})
        assert "key1:val1" in text
        assert "key2:42" in text

    def test_TC01B_empty_data_no_exception(self):
        text = _make_embedding_text("optimization", {})
        assert "type:optimization" in text

    def test_TC01C_parts_joined_by_pipe(self):
        text = _make_embedding_text("optimization", {"qpu_name": "ibm"})
        assert " | " in text


# ─────────────────────────────────────────────────────────────
# TC-02x: _row_to_record
# ─────────────────────────────────────────────────────────────

class TestRowToRecord:

    def test_TC021_required_keys_present(self):
        row = _fake_row()
        record = _row_to_record(row)
        assert {"id", "type", "timestamp", "tags", "data"} <= set(record.keys())

    def test_TC022_tags_parsed_as_list(self):
        row = _fake_row(tags=["ibm_fez", "qiskit"])
        record = _row_to_record(row)
        assert isinstance(record["tags"], list)
        assert "ibm_fez" in record["tags"]

    def test_TC023_data_parsed_as_dict(self):
        row = _fake_row(data={"ok": True, "shots": 1024})
        record = _row_to_record(row)
        assert isinstance(record["data"], dict)
        assert record["data"]["ok"] is True

    def test_TC024_id_preserved(self):
        row = _fake_row(id="test1234")
        record = _row_to_record(row)
        assert record["id"] == "test1234"

    def test_TC025_type_preserved(self):
        row = _fake_row(type="execution")
        record = _row_to_record(row)
        assert record["type"] == "execution"


# ─────────────────────────────────────────────────────────────
# TC-03x: UQIRAG.__init__
# ─────────────────────────────────────────────────────────────

class TestUQIRAGInit:

    def test_TC031_rag_file_stored(self):
        rag, rag_f, cache_f = _tmp_rag()
        try:
            assert rag.rag_file == rag_f
        finally:
            _cleanup(rag_f, cache_f)

    def test_TC032_cache_file_stored(self):
        rag, rag_f, cache_f = _tmp_rag()
        try:
            assert rag.cache_file == cache_f
        finally:
            _cleanup(rag_f, cache_f)

    def test_TC033_chroma_none_when_import_fails(self):
        rag, rag_f, cache_f = _tmp_rag(chroma=False)
        try:
            assert rag._chroma is None
        finally:
            _cleanup(rag_f, cache_f)

    def test_TC034_rag_db_created(self):
        rag, rag_f, cache_f = _tmp_rag()
        try:
            assert os.path.exists(rag_f)
        finally:
            _cleanup(rag_f, cache_f)

    def test_TC035_cache_db_created(self):
        rag, rag_f, cache_f = _tmp_rag()
        try:
            assert os.path.exists(cache_f)
        finally:
            _cleanup(rag_f, cache_f)

    def test_TC036_records_table_exists(self):
        rag, rag_f, cache_f = _tmp_rag()
        try:
            conn = sqlite3.connect(rag_f)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            conn.close()
            table_names = [t[0] for t in tables]
            assert "records" in table_names
        finally:
            _cleanup(rag_f, cache_f)


# ─────────────────────────────────────────────────────────────
# TC-04x: add
# ─────────────────────────────────────────────────────────────

class TestAdd:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.rag, self.rag_f, self.cache_f = _tmp_rag()
        yield
        _cleanup(self.rag_f, self.cache_f)

    def test_TC041_returns_string_id(self):
        rid = self.rag.add("optimization", {"qpu_name": "ibm_fez"})
        assert isinstance(rid, str)
        assert len(rid) > 0

    def test_TC042_record_stored_in_sqlite(self):
        self.rag.add("optimization", {"qpu_name": "ibm_fez"}, tags=["ibm"])
        results = self.rag.search(record_type="optimization")
        assert len(results) == 1

    def test_TC043_multiple_records_stored(self):
        self.rag.add("optimization", {"qpu_name": "ibm_fez"})
        self.rag.add("optimization", {"qpu_name": "iqm_garnet"})
        results = self.rag.search(record_type="optimization")
        assert len(results) == 2

    def test_TC044_data_preserved(self):
        self.rag.add("execution", {"qpu_name": "ibm_fez", "shots": 1024, "ok": True})
        results = self.rag.search(record_type="execution")
        assert results[0]["data"]["shots"] == 1024

    def test_TC045_tags_preserved(self):
        self.rag.add("optimization", {"qpu_name": "ibm_fez"}, tags=["ibm_fez", "qiskit"])
        results = self.rag.search(record_type="optimization")
        assert "ibm_fez" in results[0]["tags"]

    def test_TC046_chroma_add_skipped_when_none(self):
        # _chroma가 None이면 예외 없이 동작
        assert self.rag._chroma is None
        rid = self.rag.add("optimization", {"test": True})
        assert rid is not None

    def test_TC047_skip_embed_types_not_indexed(self):
        # cache/qpu_availability 타입은 Chroma 인덱싱 스킵
        mock_chroma = MagicMock()
        self.rag._chroma = mock_chroma
        self.rag.add("cache", {"key": "val"})
        mock_chroma.add.assert_not_called()

    def test_TC048_non_skip_type_indexed_when_chroma_active(self):
        mock_chroma = MagicMock()
        self.rag._chroma = mock_chroma
        self.rag.add("optimization", {"qpu_name": "ibm_fez"})
        mock_chroma.add.assert_called_once()


# ─────────────────────────────────────────────────────────────
# TC-05x: get_cache / set_cache
# ─────────────────────────────────────────────────────────────

class TestCache:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.rag, self.rag_f, self.cache_f = _tmp_rag()
        yield
        _cleanup(self.rag_f, self.cache_f)

    def test_TC051_get_nonexistent_returns_none(self):
        assert self.rag.get_cache("nonexistent_key") is None

    def test_TC052_set_and_get_cache(self):
        self.rag.set_cache("key1", "value1")
        assert self.rag.get_cache("key1") == "value1"

    def test_TC053_set_cache_returns_key(self):
        result = self.rag.set_cache("key2", "value2")
        assert result == "key2"

    def test_TC054_overwrite_cache(self):
        self.rag.set_cache("key1", "old_value")
        self.rag.set_cache("key1", "new_value")
        assert self.rag.get_cache("key1") == "new_value"

    def test_TC055_multiple_keys_independent(self):
        self.rag.set_cache("k1", "v1")
        self.rag.set_cache("k2", "v2")
        assert self.rag.get_cache("k1") == "v1"
        assert self.rag.get_cache("k2") == "v2"


# ─────────────────────────────────────────────────────────────
# TC-06x: search
# ─────────────────────────────────────────────────────────────

class TestSearch:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.rag, self.rag_f, self.cache_f = _tmp_rag()
        self.rag.add("optimization", {"qpu_name": "ibm_fez", "ok": True},
                     tags=["ibm_fez", "qiskit"])
        self.rag.add("optimization", {"qpu_name": "iqm_garnet", "ok": False},
                     tags=["iqm_garnet"])
        self.rag.add("execution",    {"qpu_name": "ibm_fez", "ok": True},
                     tags=["ibm_fez"])
        yield
        _cleanup(self.rag_f, self.cache_f)

    def test_TC061_search_all_returns_all(self):
        results = self.rag.search()
        assert len(results) == 3

    def test_TC062_filter_by_type(self):
        results = self.rag.search(record_type="optimization")
        assert len(results) == 2
        assert all(r["type"] == "optimization" for r in results)

    def test_TC063_filter_by_tag(self):
        results = self.rag.search(tags=["ibm_fez"])
        assert all("ibm_fez" in r["tags"] for r in results)

    def test_TC064_filter_by_data_field(self):
        results = self.rag.search(record_type="optimization",
                                   filters={"qpu_name": "ibm_fez"})
        assert len(results) == 1
        assert results[0]["data"]["qpu_name"] == "ibm_fez"

    def test_TC065_limit_respected(self):
        results = self.rag.search(limit=1)
        assert len(results) == 1

    def test_TC066_no_match_returns_empty(self):
        results = self.rag.search(record_type="nonexistent_type")
        assert results == []

    def test_TC067_result_has_required_keys(self):
        results = self.rag.search(limit=1)
        assert {"id", "type", "timestamp", "tags", "data"} <= set(results[0].keys())


# ─────────────────────────────────────────────────────────────
# TC-07x: search_best_combination
# ─────────────────────────────────────────────────────────────

class TestSearchBestCombination:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.rag, self.rag_f, self.cache_f = _tmp_rag()
        yield
        _cleanup(self.rag_f, self.cache_f)

    def test_TC071_no_records_returns_none(self):
        result = self.rag.search_best_combination(4, 100, "ibm_fez")
        assert result is None

    def test_TC072_no_similar_qubits_returns_none(self):
        self.rag.add("optimization", {
            "qpu_name": "ibm_fez", "num_qubits": 20,
            "gate_reduction": 0.3, "ok": True
        })
        result = self.rag.search_best_combination(4, 100, "ibm_fez")
        assert result is None

    def test_TC073_returns_best_gate_reduction(self):
        self.rag.add("optimization", {
            "qpu_name": "ibm_fez", "num_qubits": 4,
            "gate_reduction": 0.2, "ok": True
        })
        self.rag.add("optimization", {
            "qpu_name": "ibm_fez", "num_qubits": 5,
            "gate_reduction": 0.5, "ok": True
        })
        result = self.rag.search_best_combination(4, 100, "ibm_fez")
        assert result is not None
        assert result["data"]["gate_reduction"] == 0.5

    def test_TC074_only_ok_true_considered(self):
        self.rag.add("optimization", {
            "qpu_name": "ibm_fez", "num_qubits": 4,
            "gate_reduction": 0.9, "ok": False
        })
        result = self.rag.search_best_combination(4, 100, "ibm_fez")
        assert result is None


# ─────────────────────────────────────────────────────────────
# TC-08x: stats
# ─────────────────────────────────────────────────────────────

class TestStats:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.rag, self.rag_f, self.cache_f = _tmp_rag()
        yield
        _cleanup(self.rag_f, self.cache_f)

    def test_TC081_required_keys_present(self):
        s = self.rag.stats()
        assert {"total", "by_type", "rag_file", "cache_file",
                "chroma_dir", "chroma_index", "last_updated"} <= set(s.keys())

    def test_TC082_empty_db_total_zero(self):
        s = self.rag.stats()
        assert s["total"] == 0

    def test_TC083_total_increments_on_add(self):
        self.rag.add("optimization", {"qpu_name": "ibm_fez"})
        s = self.rag.stats()
        assert s["total"] >= 1

    def test_TC084_by_type_counts_correct(self):
        self.rag.add("optimization", {"qpu_name": "ibm_fez"})
        self.rag.add("optimization", {"qpu_name": "iqm_garnet"})
        self.rag.add("execution",    {"qpu_name": "ibm_fez"})
        s = self.rag.stats()
        assert s["by_type"].get("optimization", 0) == 2
        assert s["by_type"].get("execution", 0) == 1

    def test_TC085_rag_file_path_correct(self):
        s = self.rag.stats()
        assert s["rag_file"] == self.rag_f

    def test_TC086_last_updated_set_after_add(self):
        self.rag.add("optimization", {"qpu_name": "ibm_fez"})
        s = self.rag.stats()
        assert s["last_updated"] is not None


# ─────────────────────────────────────────────────────────────
# TC-09x: 타입별 헬퍼
# ─────────────────────────────────────────────────────────────

class TestTypeHelpers:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.rag, self.rag_f, self.cache_f = _tmp_rag()
        yield
        _cleanup(self.rag_f, self.cache_f)

    def test_TC091_add_optimization_returns_id(self):
        rid = self.rag.add_optimization({"qpu_name": "ibm_fez", "combination": "qiskit+sabre"})
        assert isinstance(rid, str)

    def test_TC092_add_optimization_type_correct(self):
        self.rag.add_optimization({"qpu_name": "ibm_fez"})
        results = self.rag.search(record_type="optimization")
        assert len(results) == 1

    def test_TC093_add_execution_type_correct(self):
        self.rag.add_execution("circ_a", "ibm_fez", "AerSimulator",
                                1024, {"00": 512}, True)
        results = self.rag.search(record_type="execution")
        assert len(results) == 1

    def test_TC094_add_pipeline_issue_type_correct(self):
        self.rag.add_pipeline_issue("extraction", "qiskit",
                                     "QASM 파싱 오류", "gphase 필터링")
        results = self.rag.search(record_type="pipeline_issue")
        assert len(results) == 1

    def test_TC095_add_calibration_type_correct(self):
        self.rag.add_calibration("ibm_fez", {"num_qubits": 156})
        results = self.rag.search(record_type="calibration")
        assert len(results) == 1

    def test_TC096_add_security_block_type_correct(self):
        self.rag.add_security_block("/tmp/algo.py", "os.system 사용", "os_system")
        results = self.rag.search(record_type="security_block")
        assert len(results) == 1

    def test_TC097_add_qpu_availability_type_correct(self):
        self.rag.add_qpu_availability(["ibm_fez"], {"ibm_fez": {"available": True}})
        results = self.rag.search(record_type="qpu_availability")
        assert len(results) == 1

    def test_TC098_add_qec_experiment_type_correct(self):
        self.rag.add_qec_experiment(
            "ghz_3", "ibm_fez", "steane",
            {"fidelity": 0.8, "tvd": 0.2},
            {"fidelity": 0.9, "tvd": 0.1},
            improvement=0.1,
            overhead={"qubit_overhead": 3, "gate_overhead": 10, "orig_qubits": 3}
        )
        results = self.rag.search(record_type="qec_experiment")
        assert len(results) == 1

    def test_TC099_add_gpu_benchmark_type_correct(self):
        self.rag.add_gpu_benchmark(
            "ghz_5", "pennylane", True, True,
            cpu_time_sec=1.0, cpu_status="completed",
            gpu_time_sec=0.3, gpu_status="completed",
            speedup=3.3, verdict="GPU 권장"
        )
        results = self.rag.search(record_type="gpu_benchmark")
        assert len(results) == 1


# ─────────────────────────────────────────────────────────────
# TC-10x: RECORD_TYPES 상수
# ─────────────────────────────────────────────────────────────

class TestRecordTypes:

    def test_TC101_all_required_types_present(self):
        required = {
            "optimization", "execution", "calibration", "transpile_pattern",
            "pipeline_issue", "conversion_pattern", "qpu_comparison",
            "algorithm_mapping", "cost", "qec_experiment", "qpu_availability",
            "gpu_benchmark", "security_block",
        }
        assert required <= set(RECORD_TYPES.keys())

    def test_TC102_all_values_are_strings(self):
        for k, v in RECORD_TYPES.items():
            assert isinstance(v, str)

    def test_TC103_skip_embed_types_are_known_types(self):
        # cache는 RECORD_TYPES에 없지만 시스템 내부 타입으로 정의된 skip 대상
        known = set(RECORD_TYPES.keys()) | {"cache"}
        for t in _SKIP_EMBED_TYPES:
            assert t in known


if __name__ == "__main__":
    pytest.main([__file__, "-v",
                 "--cov=uqi_rag", "--cov-report=term-missing"])