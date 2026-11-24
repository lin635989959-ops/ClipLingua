"""多语言核心配置与分词引擎：统一支持中文 (zh)、西班牙语 (es)、英语 (en)、瑞典语 (sv)。

涵盖四语在以下环节的全部语言学规则：
- Whisper 语音识别模型路由与语言代码
- 智能分词（兼容空格分词语言与无空格中文）与自然拼接
- OCR 字幕轨匹配与变音符折叠
- 多语言词典原形（lemma）归并前缀与 AI 翻译提示词模板
- 多语言在线/离线 TTS 发音配置
- 拼写练习特殊字母与宽松模式规则
"""
from __future__ import annotations

import os
import re

SUPPORTED_LANGS = ("sv", "en", "es", "zh")
DEFAULT_TARGET_LANG = "sv"
DEFAULT_NATIVE_LANG = "zh"

# 句末标点（覆盖拉丁语系与中文全角标点）
SENTENCE_END = (".", "!", "?", "…", "。", "！", "？")
PAUSE_PUNCT = (",", ";", "，", "；", "、")

# Unicode CJK 汉字及中文全角标点判断
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CJK_PUNCT_RE = re.compile(r"[，。！？；：、“”‘’（）【】《》—…]")
# 拉丁/通用单词核心：首尾是字母或数字，中间允许 - ' ’ : .
_LATIN_CORE = re.compile(r"^(?P<pre>[^\w]*)(?P<core>\w(?:[\w\-'’:.]*\w)?)(?P<post>[^\w]*)$", re.UNICODE)
# 中文混合分段：拆出 连续ASCII词/数字 | 单个汉字 | 标点符号
_ZH_PIECE = re.compile(r"[A-Za-z0-9]+(?:['’\-.:][A-Za-z0-9]+)*|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[^\s]")

LANG_CONFIG: dict[str, dict] = {
    "sv": {
        "code": "sv",
        "native_name": "Svenska",
        "names": {"zh": "瑞典语", "en": "Swedish", "es": "Sueco", "sv": "Svenska"},
        "short": "SV",
        "is_cjk": False,
        "collator": "sv",
        "caption_prefixes": ("sv", "swe"),
        "default_voice": "sv_sofie",
        "offline_voice": "sv_lisa",
        "voices": [
            {"id": "sv_sofie", "engine": "edge", "voice": "sv-SE-SofieNeural", "label": "Sofie (Edge)", "gender": "f"},
            {"id": "sv_mattias", "engine": "edge", "voice": "sv-SE-MattiasNeural", "label": "Mattias (Edge)", "gender": "m"},
            {"id": "sv_lisa", "engine": "piper", "model": "sv_SE-lisa-medium.onnx", "label": "Lisa (Offline)", "gender": "f"},
        ],
        "special_letters": ["å", "ä", "ö"],
        "loose_map": {"å": "a", "ä": "a", "ö": "o", "é": "e", "ü": "u"},
        "has_loose": True,
        "lemma_hint": {
            "zh": "名词写单数不定式（不带 en/ett，如 kulturen → kultur；仅复数词保留复数如 pengar）；动词写不定式（不带 att，如 förklarade → förklara）；形容词写通性单数原级（okylda → okyld，större → stor）；代词写主格（mig → jag）",
            "en": "Nouns: singular indefinite without en/ett (kulturen → kultur); Verbs: infinitive without att (förklarade → förklara); Adjectives: common-gender singular positive (större → stor); Pronouns: nominative (mig → jag)",
            "es": "Sustantivos: singular indefinido sin en/ett (kulturen → kultur); Verbos: infinitivo sin att (förklarade → förklara); Adjetivos: singular común positivo (större → stor); Pronombres: nominativo (mig → jag)",
            "sv": "Substantiv: singular obestämd form utan en/ett (kulturen → kultur); Verb: infinitiv utan att (förklarade → förklara); Adjektiv: utrum singular positiv (större → stor); Pronomen: subjektsform (mig → jag)",
        },
        "note_hint": {
            "zh": "名词注明 en/ett 和复数；动词注明现在时/过去时/完成时",
            "en": "For nouns give en/ett & plural; for verbs give present/past/supine",
            "es": "Para sustantivos indica en/ett y plural; para verbos presente/pasado/supino",
            "sv": "För substantiv ange en/ett och plural; för verb presens/preteritum/supinum",
        },
    },
    "en": {
        "code": "en",
        "native_name": "English",
        "names": {"zh": "英语", "en": "English", "es": "Inglés", "sv": "Engelska"},
        "short": "EN",
        "is_cjk": False,
        "collator": "en",
        "caption_prefixes": ("en", "eng"),
        "default_voice": "en_jenny",
        "offline_voice": "en_lessac",
        "voices": [
            {"id": "en_jenny", "engine": "edge", "voice": "en-US-JennyNeural", "label": "Jenny (Edge)", "gender": "f"},
            {"id": "en_guy", "engine": "edge", "voice": "en-US-GuyNeural", "label": "Guy (Edge)", "gender": "m"},
            {"id": "en_lessac", "engine": "piper", "model": "en_US-lessac-medium.onnx", "label": "Lessac (Offline)", "gender": "f"},
        ],
        "special_letters": [],
        "loose_map": {"é": "e", "è": "e", "ï": "i", "ñ": "n"},
        "has_loose": False,
        "lemma_hint": {
            "zh": "名词写单数（不带 a/an/the，如 stories → story）；动词写原形（不带 to，如 went / going → go）；形容词写原级（better → good）；代词写主格（him → he）",
            "en": "Nouns: singular without a/an/the (stories → story); Verbs: base form without to (went/going → go); Adjectives: positive degree (better → good); Pronouns: nominative (him → he)",
            "es": "Sustantivos: singular sin a/an/the (stories → story); Verbos: forma base sin to (went → go); Adjetivos: grado positivo (better → good); Pronombres: nominativo (him → he)",
            "sv": "Substantiv: singular utan a/an/the (stories → story); Verb: grundform utan to (went → go); Adjektiv: positiv (better → good); Pronomen: grundform (him → he)",
        },
        "note_hint": {
            "zh": "不规则动词写过去式/过去分词；可数/不可数或常见搭配简述",
            "en": "For irregular verbs give past/participle; note key collocations or usage",
            "es": "Para verbos irregulares indica pasado/participio; añade colocaciones clave",
            "sv": "För oregelbundna verb ange preteritum/particip; notera vanliga uttryck",
        },
    },
    "es": {
        "code": "es",
        "native_name": "Español",
        "names": {"zh": "西班牙语", "en": "Spanish", "es": "Español", "sv": "Spanska"},
        "short": "ES",
        "is_cjk": False,
        "collator": "es",
        "caption_prefixes": ("es", "spa"),
        "default_voice": "es_elvira",
        "offline_voice": "es_davefx",
        "voices": [
            {"id": "es_elvira", "engine": "edge", "voice": "es-ES-ElviraNeural", "label": "Elvira (Edge)", "gender": "f"},
            {"id": "es_alvaro", "engine": "edge", "voice": "es-ES-AlvaroNeural", "label": "Álvaro (Edge)", "gender": "m"},
            {"id": "es_davefx", "engine": "piper", "model": "es_ES-davefx-medium.onnx", "label": "Davefx (Offline)", "gender": "m"},
        ],
        "special_letters": ["á", "é", "í", "ó", "ú", "ü", "ñ", "¿", "¡"],
        "loose_map": {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u", "ñ": "n"},
        "has_loose": True,
        "lemma_hint": {
            "zh": "名词写单数（不带 el/la/los/las，如 ciudades → ciudad）；动词写不定式（hablando / hablé → hablar，自复动词如 levantarse 保留 -se）；形容词写阳性单数原级（bonitas → bonito）",
            "en": "Nouns: singular without el/la/los/las (ciudades → ciudad); Verbs: infinitive (hablé → hablar, keep -se for reflexive); Adjectives: masculine singular positive (bonitas → bonito)",
            "es": "Sustantivos: singular sin el/la/los/las (ciudades → ciudad); Verbos: infinitivo (hablé → hablar, conserva -se en reflexivos); Adjetivos: masculino singular positivo (bonitas → bonito)",
            "sv": "Substantiv: singular utan el/la/los/las (ciudades → ciudad); Verb: infinitiv (hablé → hablar); Adjektiv: maskulinum singular positiv (bonitas → bonito)",
        },
        "note_hint": {
            "zh": "名词注明阴阳性（el/la）；不规则动词注明关键变位或固定搭配",
            "en": "Give gender (el/la) for nouns; note irregular stems or key verb usage",
            "es": "Indica género (el/la) en sustantivos; señala irregularidades o usos frecuentes",
            "sv": "Ange genus (el/la) för substantiv; notera oregelbundna böjningar",
        },
    },
    "zh": {
        "code": "zh",
        "native_name": "中文",
        "names": {"zh": "中文", "en": "Chinese", "es": "Chino", "sv": "Kinesiska"},
        "short": "ZH",
        "is_cjk": True,
        "collator": "zh-Hans",
        "caption_prefixes": ("zh", "zho", "cmn", "chi"),
        "default_voice": "zh_xiaoxiao",
        "offline_voice": "zh_huayan",
        "voices": [
            {"id": "zh_xiaoxiao", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "label": "Xiaoxiao (Edge)", "gender": "f"},
            {"id": "zh_yunxi", "engine": "edge", "voice": "zh-CN-YunxiNeural", "label": "Yunxi (Edge)", "gender": "m"},
            {"id": "zh_huayan", "engine": "piper", "model": "zh_CN-huayan-medium.onnx", "label": "Huayan (Offline)", "gender": "f"},
        ],
        "special_letters": [],
        "loose_map": {},
        "has_loose": False,
        "lemma_hint": {
            "zh": "中文无屈折变化，直接写词语本身（如「学习」）；若为成语或固定搭配写完整短语",
            "en": "Chinese has no inflection; write the word/phrase itself in Simplified Chinese (e.g. 学习)",
            "es": "El chino no tiene flexión; escribe la palabra o frase en chino simplificado (ej. 学习)",
            "sv": "Kinesiska saknar böjning; skriv själva ordet eller frasen på förenklad kinesiska (t.ex. 学习)",
        },
        "note_hint": {
            "zh": "注明拼音（带声调，如 xuéxí）、常用量词或简短用法说明",
            "en": "Include Pinyin with tone marks (e.g. xuéxí), measure word if applicable, and brief usage",
            "es": "Incluye pinyin con tonos (ej. xuéxí), clasificador si aplica y una breve nota de uso",
            "sv": "Ange pinyin med tontecken (t.ex. xuéxí), måttsord vid behov och kort användningsnotis",
        },
    },
}

# 旧版本语音短名兼容映射
LEGACY_VOICE_ALIAS = {
    "sofie": "sv_sofie",
    "mattias": "sv_mattias",
    "lisa": "sv_lisa",
}


def normalize_lang(lang: str | None, default: str = DEFAULT_TARGET_LANG) -> str:
    """将语言代码规整为支持的四语之一（zh / es / en / sv）。"""
    if not lang:
        return default
    code = str(lang).strip().lower().replace("_", "-").split("-")[0]
    if code in ("swe",):
        code = "sv"
    elif code in ("eng",):
        code = "en"
    elif code in ("spa",):
        code = "es"
    elif code in ("zho", "cmn", "chi"):
        code = "zh"
    return code if code in LANG_CONFIG else default


def is_cjk_lang(lang: str | None) -> bool:
    return normalize_lang(lang) == "zh"


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def get_lang(lang: str | None) -> dict:
    return LANG_CONFIG[normalize_lang(lang)]


def public_languages_info() -> dict:
    """提供给前端的语言元数据与语音列表。"""
    return {
        "supported": list(SUPPORTED_LANGS),
        "default_target": DEFAULT_TARGET_LANG,
        "default_native": DEFAULT_NATIVE_LANG,
        "languages": {
            code: {
                "code": cfg["code"],
                "native_name": cfg["native_name"],
                "names": cfg["names"],
                "short": cfg["short"],
                "is_cjk": cfg["is_cjk"],
                "collator": cfg["collator"],
                "default_voice": cfg["default_voice"],
                "offline_voice": cfg["offline_voice"],
                "voices": [{k: v for k, v in vc.items() if k != "model"} for vc in cfg["voices"]],
                "special_letters": cfg["special_letters"],
                "loose_map": cfg["loose_map"],
                "has_loose": cfg["has_loose"],
            }
            for code, cfg in LANG_CONFIG.items()
        },
    }


def split_token(raw: str) -> tuple[str, str, str]:
    """把单个片段拆成 (前标点, 核心词, 后标点)。纯标点返回 (raw, '', '')。"""
    s = (raw or "").strip()
    if not s:
        return ("", "", "")
    m = _LATIN_CORE.match(s)
    if not m:
        return (s, "", "")
    return (m["pre"], m["core"], m["post"])


def tokenize_text(text: str, lang: str = "sv") -> list[tuple[str, str, str]]:
    """将一段文本按语言切分为 [(pre, core, post), ...]，纯标点自动并到相邻 token。

    - sv / en / es：按空格分词后剥离首尾标点。
    - zh：支持空格手动分词；无空格连续汉字按单字切分，英文/数字保持完整，标点自然附着。
    """
    text = (text or "").strip()
    if not text:
        return []
    is_zh = normalize_lang(lang) == "zh" or has_cjk(text)
    if not is_zh:
        out: list[list[str]] = []
        for chunk in text.split():
            pre, core, post = split_token(chunk)
            if not core:
                if out:
                    out[-1][2] += pre
                continue
            out.append([pre, core, post])
        return [(a, b, c) for a, b, c in out]

    # 中文/中英混合：若用户显式用空格分词，先按空白分段再逐段处理
    out = []
    pending_pre = ""
    open_punct = set("([{\"‘“（【《¡¿")
    for seg in text.split():
        # 若整段不含标点且用户显式用空格隔开（例如中文手动修正输入 "今天 天气"），保留整词
        if " " in text and not any(not ch.isalnum() for ch in seg):
            out.append([pending_pre, seg, ""])
            pending_pre = ""
            continue
        for piece in _ZH_PIECE.findall(seg):
            if _CJK_RE.match(piece) or re.match(r"^[A-Za-z0-9]", piece):
                out.append([pending_pre, piece, ""])
                pending_pre = ""
            else:
                if piece in open_punct or not out:
                    pending_pre += piece
                else:
                    out[-1][2] += piece
    if pending_pre and out:
        out[-1][2] += pending_pre
    return [(a, b, c) for a, b, c in out]


def _is_cjk_char(ch: str) -> bool:
    return bool(ch) and (bool(_CJK_RE.match(ch)) or bool(_CJK_PUNCT_RE.match(ch)))


def join_tokens(tokens: list[dict], lang: str = "sv") -> str:
    """将 token 列表拼成自然句子：拉丁词之间加空格，中文汉字/全角标点之间不强行加空格。"""
    if not tokens:
        return ""
    parts = [t["pre"] + t["text"] + t["post"] for t in tokens]
    if normalize_lang(lang) != "zh" and not any(has_cjk(p) for p in parts):
        return " ".join(parts)
    res = [parts[0]]
    for prev, cur in zip(parts, parts[1:]):
        if not prev or not cur:
            res.append(cur)
            continue
        # 只要前后任一端是汉字或中文全角标点，就不插入空格
        if _is_cjk_char(prev[-1]) or _is_cjk_char(cur[0]):
            res.append(cur)
        else:
            res.append(" " + cur)
    return "".join(res)


def resolve_whisper_model(lang: str, models_dir) -> tuple[str, str]:
    """根据目标语言决定使用哪个 Whisper 模型。

    返回 (model_path_or_id, display_name)：
    - 瑞典语优先使用本地 KBLab 瑞典语微调模型（strict → standard）
    - 其他语言（或未下载本地瑞典语模型时）优先检查本地多语言模型，其次使用环境变量
      `CLIPLINGUA_WHISPER_MODEL` 或 faster-whisper 自动下载的 `large-v3-turbo` / `medium`。
    """
    from pathlib import Path
    mdir = Path(models_dir)
    code = normalize_lang(lang)

    if code == "sv":
        for name in ("kb-whisper-large-strict", "kb-whisper-large"):
            cand = mdir / name
            if (cand / "model.bin").is_file():
                return str(cand), name

    # 检查环境变量覆盖（允许用户自由指定 small / medium / large-v3-turbo / 本地目录）
    env_model = os.environ.get("CLIPLINGUA_WHISPER_MODEL", "").strip()
    if env_model:
        p = Path(env_model)
        return (str(p), p.name) if p.is_dir() else (env_model, env_model)

    # 检查本地已下载的通用多语言模型目录
    for name in ("whisper-large-v3-turbo", "faster-whisper-large-v3-turbo", "whisper-large-v3", "whisper-medium", "whisper-small"):
        cand = mdir / name
        if (cand / "model.bin").is_file():
            return str(cand), name

    # 默认按需下载通用多语言模型（ctranslate2 / faster-whisper 原生支持）
    default_id = "deepdml/faster-whisper-large-v3-turbo-ct2"
    return default_id, "faster-whisper-large-v3-turbo"
