# test_i18n.py
# i18n 로케일 파일 일관성 테스트

import json
import os
import pytest

LOCALES_DIR = os.path.join(os.path.dirname(__file__), '..', 'webapp', 'locales')
EN_PATH = os.path.join(LOCALES_DIR, 'en.json')
KO_PATH = os.path.join(LOCALES_DIR, 'ko.json')
FR_PATH = os.path.join(LOCALES_DIR, 'fr.json')


@pytest.fixture(scope="module")
def en():
    with open(EN_PATH, encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture(scope="module")
def ko():
    with open(KO_PATH, encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture(scope="module")
def fr():
    with open(FR_PATH, encoding='utf-8') as f:
        return json.load(f)


class TestLocaleFiles:
    def test_en_file_exists(self):
        assert os.path.isfile(EN_PATH)

    def test_ko_file_exists(self):
        assert os.path.isfile(KO_PATH)

    def test_fr_file_exists(self):
        assert os.path.isfile(FR_PATH)

    def test_en_is_valid_json(self, en):
        assert isinstance(en, dict)

    def test_ko_is_valid_json(self, ko):
        assert isinstance(ko, dict)

    def test_fr_is_valid_json(self, fr):
        assert isinstance(fr, dict)

    def test_en_has_lang_key(self, en):
        assert en.get("lang") == "en"

    def test_ko_has_lang_key(self, ko):
        assert ko.get("lang") == "ko"

    def test_fr_has_lang_key(self, fr):
        assert fr.get("lang") == "fr"


class TestKeyConsistency:
    def test_same_number_of_keys(self, en, ko, fr):
        assert len(en) == len(ko), (
            f"en.json has {len(en)} keys, ko.json has {len(ko)} keys"
        )
        assert len(en) == len(fr), (
            f"en.json has {len(en)} keys, fr.json has {len(fr)} keys"
        )

    def test_en_has_all_ko_keys(self, en, ko):
        missing = set(ko.keys()) - set(en.keys())
        assert not missing, f"Keys in ko.json missing from en.json: {missing}"

    def test_ko_has_all_en_keys(self, en, ko):
        missing = set(en.keys()) - set(ko.keys())
        assert not missing, f"Keys in en.json missing from ko.json: {missing}"

    def test_en_has_all_fr_keys(self, en, fr):
        missing = set(fr.keys()) - set(en.keys())
        assert not missing, f"Keys in fr.json missing from en.json: {missing}"

    def test_fr_has_all_en_keys(self, en, fr):
        missing = set(en.keys()) - set(fr.keys())
        assert not missing, f"Keys in en.json missing from fr.json: {missing}"

    def test_identical_key_sets(self, en, ko, fr):
        assert set(en.keys()) == set(ko.keys())
        assert set(en.keys()) == set(fr.keys())


class TestValueQuality:
    def test_no_empty_en_values(self, en):
        empty = [k for k, v in en.items() if not isinstance(v, str) or not v.strip()]
        assert not empty, f"Empty values in en.json: {empty}"

    def test_no_empty_ko_values(self, ko):
        empty = [k for k, v in ko.items() if not isinstance(v, str) or not v.strip()]
        assert not empty, f"Empty values in ko.json: {empty}"

    def test_no_empty_fr_values(self, fr):
        empty = [k for k, v in fr.items() if not isinstance(v, str) or not v.strip()]
        assert not empty, f"Empty values in fr.json: {empty}"

    def test_all_values_are_strings(self, en, ko, fr):
        non_str_en = [k for k, v in en.items() if not isinstance(v, str)]
        non_str_ko = [k for k, v in ko.items() if not isinstance(v, str)]
        non_str_fr = [k for k, v in fr.items() if not isinstance(v, str)]
        assert not non_str_en, f"Non-string values in en.json: {non_str_en}"
        assert not non_str_ko, f"Non-string values in ko.json: {non_str_ko}"
        assert not non_str_fr, f"Non-string values in fr.json: {non_str_fr}"


class TestPlaceholderConsistency:
    """Ensure parameterized strings use matching placeholders in all locales."""

    def _extract_placeholders(self, value: str) -> set:
        """Extract {placeholder} tokens from a string."""
        import re
        return set(re.findall(r'\{(\w+)\}', value))

    def test_placeholder_keys_match(self, en, ko, fr):
        mismatches = []
        for key in en:
            en_ph = self._extract_placeholders(en[key])
            ko_ph = self._extract_placeholders(ko[key])
            fr_ph = self._extract_placeholders(fr[key])
            if en_ph != ko_ph:
                mismatches.append(f"  key='{key}': en={en_ph}, ko={ko_ph}")
            if en_ph != fr_ph:
                mismatches.append(f"  key='{key}': en={en_ph}, fr={fr_ph}")
        assert not mismatches, "Placeholder mismatch across locales:\n" + "\n".join(mismatches)


class TestKeyNamingConventions:
    """Verify key naming follows the expected dot-notation structure."""

    EXPECTED_NAMESPACES = {
        "nav", "header", "status", "lock", "footer",
        "qpu", "pipeline", "job", "step", "btn", "cache", "alert",
        "knowledge", "rag", "hint",
    }

    def test_all_keys_have_namespace(self, en):
        no_dot = [k for k in en if k != "lang" and "." not in k]
        assert not no_dot, f"Keys without namespace prefix: {no_dot}"

    def test_namespaces_are_known(self, en):
        unknown = {
            k.split(".")[0] for k in en
            if k != "lang" and k.split(".")[0] not in self.EXPECTED_NAMESPACES
        }
        assert not unknown, f"Unknown namespaces in en.json: {unknown}"
