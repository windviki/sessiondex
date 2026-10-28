from sessiondex import tokenize


def test_tokenize_ascii_words():
    assert tokenize.tokenize("fix the ch011 bug") == ["fix", "the", "ch011", "bug"]


def test_tokenize_cjk_bigrams():
    # 4-char run -> 3 bigrams
    assert tokenize.tokenize("启动速度") == ["启动", "动速", "速度"]


def test_tokenize_cjk_single_char():
    assert tokenize.tokenize("好") == ["好"]


def test_tokenize_mixed():
    tokens = tokenize.tokenize("优化ch011模块")
    assert "优化" in tokens
    assert "ch011" in tokens
    assert "模块" in tokens


def test_index_text_roundtrip_query():
    text = "帮我优化 ch011 模块的启动速度"
    indexed = tokenize.index_text(text)
    query = tokenize.fts_query("ch011 启动")
    assert query == '"ch011" "启动"'
    assert "ch011" in indexed
    assert "启动" in indexed and "动速" in indexed and "速度" in indexed


def test_fts_query_cjk_long_run():
    q = tokenize.fts_query("启动速度优化")
    assert q == '"启动 动速 速度 度优 优化"'


def test_fts_query_empty():
    assert tokenize.fts_query("") == ""
    assert tokenize.fts_query("   ") == ""
