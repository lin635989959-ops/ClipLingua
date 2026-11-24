"""多语言总生词本「词条原形（Lemma）」与词性规范。

支持中文 (zh)、西班牙语 (es)、英语 (en)、瑞典语 (sv)：
- 词性统一归一化为语言无关的标准代码（noun / verb / adj / adv / pron / prep / conj / num / interj / phrase），
  并兼容中、英、西、瑞四语的常见写法与缩写。
- 按目标语言剥离词典原形前多余的冠词或不定式标记（例如瑞典语 att/en/ett、英语 to/a/an/the、西语 el/la/los/las）。
- 总生词本词条唯一 ID 采用 `lang|lemma|pos`，避免不同语种的同形词发生错误合并。
"""
from __future__ import annotations

import re

import languages as langs

# 语言无关的标准词性代码
POS_LIST = ["noun", "verb", "adj", "adv", "pron", "prep", "conj", "num", "interj", "phrase"]

# 覆盖中、英、西、瑞四语的词性别名映射
_POS_ALIASES: dict[str, list[str]] = {
    "noun": [
        "名词", "名", "名詞", "n", "substantiv", "subst", "sustantivo", "sust", "nombre",
    ],
    "verb": [
        "动词", "动", "動詞", "v", "助动词", "情态动词", "verbo", "verbos", "auxiliary",
    ],
    "adj": [
        "形容词", "形", "形容詞", "adjective", "adjektiv", "adjetivo",
        "分词", "过去分词", "现在分词", "participle", "participio", "particip",
    ],
    "adv": [
        "副词", "副", "副詞", "adverb", "adverbio",
    ],
    "pron": [
        "代词", "代", "代名词", "代詞", "pronoun", "pronomen", "pronombre",
        "物主代词", "人称代词", "冠词", "限定词", "article", "determiner", "artículo", "artikel",
    ],
    "prep": [
        "介词", "介", "介詞", "preposition", "preposición",
    ],
    "conj": [
        "连词", "连", "連詞", "conjunction", "konjunktion", "subjunktion", "conjunción",
    ],
    "num": [
        "数词", "数", "數詞", "numeral", "räkneord", "número", "number", "量词", "classifier",
    ],
    "interj": [
        "感叹词", "叹词", "感嘆詞", "interjection", "interjektion", "interjección", "语气词", "particle",
    ],
    "phrase": [
        "短语", "词组", "固定搭配", "fras", "frase", "expresión", "locución", "习语", "惯用语", "idiom", "成语",
    ],
}
_POS_MAP = {a.lower(): p for p, al in _POS_ALIASES.items() for a in [p, *al]}

# 各语言在原形前常被 AI 误带的前缀（按词性精准剥离，避免破坏短语）
_LANG_PREFIX: dict[str, dict[str, re.Pattern[str]]] = {
    "sv": {
        "verb": re.compile(r"^att\s+(?=\S)", re.I),
        "noun": re.compile(r"^(?:en|ett)\s+(?=\S)", re.I),
    },
    "en": {
        "verb": re.compile(r"^to\s+(?=\S)", re.I),
        "noun": re.compile(r"^(?:a|an|the)\s+(?=\S)", re.I),
    },
    "es": {
        "noun": re.compile(r"^(?:el|la|los|las|un|una|unos|unas)\s+(?=\S)", re.I),
    },
    "zh": {},
}

# 括号中的补充说明：bil (en) / hablar（动词）
_PAREN = re.compile(r"\s*[(（\[【][^)）\]】]*[)）\]】]")
# 剥离首尾非文字标点（完整保留 Unicode 字母、变音字母与汉字）
_EDGE = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)


def normalize_pos(pos: str | None) -> str:
    """将 AI 或用户输入的四语词性统一为标准代码；无法识别则原样截断保留。"""
    s = str(pos or "").strip()
    if not s:
        return ""
    low = s.lower().rstrip(".")
    if low in _POS_MAP:
        return _POS_MAP[low]
    # 处理「及物动词」「不可数名词」「transitive verb」等复合写法
    for alias, code in _POS_MAP.items():
        if len(alias) >= 2 and alias in low:
            return code
    return s[:20]


def normalize_lemma(lemma: str | None, pos: str | None = None, lang: str = "sv") -> str:
    """整理原形格式：去掉括号说明、斜杠后的备选形态、语言特定冠词/不定式前缀及首尾标点。"""
    code = langs.normalize_lang(lang)
    s = _PAREN.sub("", str(lemma or "")).strip()
    s = re.split(r"\s*[/,，;；、|]\s*", s)[0]
    s = re.sub(r"\s+", " ", s)
    npos = normalize_pos(pos)
    pre = _LANG_PREFIX.get(code, {}).get(npos)
    if pre:
        s = pre.sub("", s)
    s = _EDGE.sub("", s)
    return s[:80]


def entry_id(lemma: str, pos: str, lang: str = "sv") -> str:
    """总生词本词条唯一标识：语言代码 + 原形（小写）+ 标准词性。"""
    code = langs.normalize_lang(lang)
    npos = normalize_pos(pos)
    nlem = normalize_lemma(lemma, npos, code).lower()
    return f"{code}|{nlem}|{npos}"
