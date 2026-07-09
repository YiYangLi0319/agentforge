"""中文友好分词：jieba 搜索模式 + 停用词过滤，供 BM25 索引与查询共用。"""

import re

import jieba

jieba.setLogLevel(60)  # 关闭初始化日志

_STOPWORDS = {
    # 中文高频虚词
    "的", "了", "和", "是", "在", "我", "有", "他", "这", "中", "大", "来", "上", "国", "个",
    "到", "说", "们", "为", "子", "与", "也", "你", "对", "生", "能", "而", "会", "着", "去",
    "之", "过", "如", "什么", "怎么", "哪些", "以及", "或者", "但是", "因为", "所以", "如果",
    "请问", "一个", "进行", "可以", "需要", "我们", "他们", "它们", "这个", "那个", "怎样",
    # 英文高频词
    "the", "a", "an", "is", "are", "was", "were", "be", "of", "to", "in", "on", "for",
    "and", "or", "not", "with", "at", "by", "from", "it", "this", "that", "what", "how",
}

_TOKEN_RE = re.compile(r"^[\w\u4e00-\u9fff]+$")


def tokenize(text: str) -> list[str]:
    """分词并过滤停用词/标点；英文统一小写。"""
    tokens = []
    for tok in jieba.cut_for_search(text):
        tok = tok.strip().lower()
        if len(tok) < 1 or tok in _STOPWORDS or not _TOKEN_RE.match(tok):
            continue
        tokens.append(tok)
    return tokens
