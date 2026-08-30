"""三層分類器(SPIKES.md W4)。名單內容以 W4 裁決為準;此處鎖行為邊界。"""

from __future__ import annotations

from context_render.attributor.structure_tokens import CODE_KEY, classify_part


def test_code_key_constant():
    assert CODE_KEY == "code structure"


# ---- 第 1 層:宣告前綴剝離 ----

def test_bare_declaration_prefix_is_structure_exact():
    for pat in ("def ", "def", "class ", "import "):
        c = classify_part(pat, glob_source=False)
        assert c.structure and c.confidence == "exact", pat


def test_prefix_with_remainder_harvests_keyword():
    c = classify_part("def _short", glob_source=False)
    assert not c.structure and c.keyword == "_short"
    c = classify_part("class FooHandler", glob_source=False)
    assert not c.structure and c.keyword == "FooHandler"


def test_anchored_or_escaped_prefix_still_matches():
    # canonical 正規化同款清洗:錨點與 escape class 先去除再比對
    assert classify_part(r"^def ", glob_source=False).structure
    c = classify_part(r"def\s+_short", glob_source=False)
    assert not c.structure and c.keyword is not None


# ---- 第 2 層:純結構 glob(僅 glob_source=True)----

def test_pure_extension_glob_is_structure_exact():
    for pat in ("*.py", "**/*.ts", "src/*.py"):
        c = classify_part(pat, glob_source=True)
        assert c.structure and c.confidence == "exact", pat


def test_glob_with_stem_harvests_stem():
    c = classify_part("**/*handler*.py", glob_source=True)
    assert not c.structure and c.keyword == "handler"


def test_regex_pattern_skips_glob_layer():
    # grep 的 regex 不走 glob 層:不會因長得像副檔名而變 structure
    assert not classify_part(r".*\.py", glob_source=False).structure


# ---- 第 3 層:stoplist ----

def test_stoplist_hit_is_structure_heuristic():
    for pat in ("len(", "self.", "return"):
        c = classify_part(pat, glob_source=False)
        assert c.structure and c.confidence == "heuristic", pat


def test_glob_stem_cascades_into_stoplist():
    c = classify_part("*main*", glob_source=True)
    assert c.structure and c.confidence == "heuristic"


# ---- W4 dry-run 誤殺回歸(真實語料樣本,W3 #19 慣例:踩過的坑不允許回歸)----

def test_literal_dotfile_name_stays_keyword():
    # find -name ".claude.local.md":字面檔名搜尋是真實資訊需求,不是結構探測
    c = classify_part(".claude.local.md", glob_source=True)
    assert not c.structure and c.keyword is None


def test_prose_after_prefix_word_stays_keyword():
    # grep 'use --md for all':普通英文動詞開頭的句子,不是 Rust use 宣告探測
    c = classify_part("use --md for all", glob_source=False)
    assert not c.structure and c.keyword is None


# ---- 三層皆不中:零行為改變 ----

def test_ordinary_vocabulary_untouched():
    for pat in ("registerHandler", "fromisoformat", "重試策略"):
        c = classify_part(pat, glob_source=False)
        assert not c.structure and c.keyword is None, pat


def test_generic_english_words_stay_keywords():
    # 寧漏勿誤:通用英文詞可能是真實資訊需求,不入 stoplist
    for pat in ("error", "test", "config"):
        assert not classify_part(pat, glob_source=False).structure, pat
