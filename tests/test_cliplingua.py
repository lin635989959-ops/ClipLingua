"""ClipLingua 四语全链路自动化测试套件（使用隔离临时目录，不污染真实数据）。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
TMP_LESSONS = Path(tempfile.mkdtemp(prefix="cliplingua_test_"))
os.environ["CLIPLINGUA_LESSONS_DIR"] = str(TMP_LESSONS)
os.environ["CLIPLINGUA_LLM_CONFIG"] = str(TMP_LESSONS / "llm_config.json")
sys.path.insert(0, str(ROOT / "app"))

import languages as langs  # noqa: E402
import lemma  # noqa: E402
import lesson_store as ls  # noqa: E402
import llm  # noqa: E402
import records  # noqa: E402
import review  # noqa: E402
import server  # noqa: E402
import subtitles  # noqa: E402
import transcriber  # noqa: E402
import transcript_edit  # noqa: E402
import translation  # noqa: E402
import vocab  # noqa: E402


class TestLanguagesAndTokenization(unittest.TestCase):
    """验证四语分词、拼接与模型路由逻辑。"""

    def test_latin_and_spanish_punctuation(self):
        toks = langs.tokenize_text("¿Cuándo empezamos, amigo!", "es")
        self.assertEqual(toks, [("¿", "Cuándo", ""), ("", "empezamos", ","), ("", "amigo", "!")])

    def test_chinese_tokenization_and_natural_join(self):
        toks = langs.tokenize_text("清晨泡茶，提升语感！", "zh")
        cores = [c for _, c, _ in toks]
        self.assertEqual(cores, ["清", "晨", "泡", "茶", "提", "升", "语", "感"])
        joined = langs.join_tokens(
            [{"pre": a, "text": b, "post": c} for a, b, c in toks], "zh"
        )
        self.assertEqual(joined, "清晨泡茶，提升语感！")

    def test_chinese_space_preserved_editing(self):
        toks = langs.tokenize_text("今天 天气", "zh")
        self.assertEqual([c for _, c, _ in toks], ["今天", "天气"])

    def test_whisper_model_routing(self):
        ref_en, _ = langs.resolve_whisper_model("en", ROOT / "models")
        ref_es, _ = langs.resolve_whisper_model("es", ROOT / "models")
        ref_zh, _ = langs.resolve_whisper_model("zh", ROOT / "models")
        # 非瑞典语不应路由到瑞典语微调模型 kb-whisper
        self.assertNotIn("kb-whisper", ref_en)
        self.assertNotIn("kb-whisper", ref_es)
        self.assertNotIn("kb-whisper", ref_zh)


class TestLemmaNormalization(unittest.TestCase):
    """验证四语词典原形与跨语种词条隔离。"""

    def test_pos_aliases_four_langs(self):
        self.assertEqual(lemma.normalize_pos("动词"), "verb")
        self.assertEqual(lemma.normalize_pos("sustantivo"), "noun")
        self.assertEqual(lemma.normalize_pos("adjektiv"), "adj")
        self.assertEqual(lemma.normalize_pos("Preposition"), "prep")

    def test_language_specific_prefix_stripping(self):
        self.assertEqual(lemma.normalize_lemma("att förklara", "verb", "sv"), "förklara")
        self.assertEqual(lemma.normalize_lemma("ett hus", "noun", "sv"), "hus")
        self.assertEqual(lemma.normalize_lemma("to explain", "verb", "en"), "explain")
        self.assertEqual(lemma.normalize_lemma("the story", "noun", "en"), "story")
        self.assertEqual(lemma.normalize_lemma("la ciudad", "noun", "es"), "ciudad")
        self.assertEqual(lemma.normalize_lemma("语感（名词）", "noun", "zh"), "语感")

    def test_cross_language_entry_isolation(self):
        sv_id = lemma.entry_id("en", "pron", "sv")
        es_id = lemma.entry_id("en", "prep", "es")
        self.assertNotEqual(sv_id, es_id)
        self.assertTrue(sv_id.startswith("sv|"))
        self.assertTrue(es_id.startswith("es|"))


class TestTranscriberAndOCR(unittest.TestCase):
    """验证多语分句构建与画面字幕 OCR 比对（含中文与变音符语言）。"""

    def test_build_sentences_zh_and_es(self):
        zh_words = [
            SimpleNamespace(word="你好，", start=0.1, end=0.5, probability=0.95),
            SimpleNamespace(word="世界。", start=0.6, end=1.0, probability=0.92),
            SimpleNamespace(word="开始！", start=1.2, end=1.6, probability=0.90),
        ]
        sents = transcriber.build_sentences(zh_words, "zh")
        self.assertEqual(len(sents), 2)
        self.assertEqual(sents[0]["text"], "你好，世界。")
        self.assertEqual(sents[1]["text"], "开始！")

    def test_ocr_alignment_multilingual(self):
        # 1) 西班牙语重音符折叠与差异建议
        meta_es = ls.create_lesson("demo:es", None, language="es", native_lang="en")
        lid_es = meta_es["id"]
        ls.update_meta(lid_es, status=ls.STATUS_READY, title="ES OCR Test", duration=5.0)
        tr_es = {
            "schema_version": ls.SCHEMA_VERSION, "language": "es",
            "sentences": [{
                "id": 1, "start": 0.5, "end": 3.0, "text": "Cuando empezamos hablar despacio",
                "tokens": [
                    {"id": "1-1", "pre": "", "text": "Cuando", "post": "", "key": "cuando", "start": 0.5, "end": 1.0, "prob": 0.9},
                    {"id": "1-2", "pre": "", "text": "empezamos", "post": "", "key": "empezamos", "start": 1.0, "end": 1.7, "prob": 0.9},
                    {"id": "1-3", "pre": "", "text": "hablar", "post": "", "key": "hablar", "start": 1.7, "end": 2.3, "prob": 0.25},
                    {"id": "1-4", "pre": "", "text": "despacio", "post": "", "key": "despacio", "start": 2.3, "end": 3.0, "prob": 0.9},
                ],
            }],
        }
        ls.save(lid_es, ls.TRANSCRIPT_FILE, tr_es)
        ls.save(lid_es, subtitles.SUBS_FILE, {
            "schema_version": 2, "fps": 3,
            "frames": [
                {"t": 1.0, "lines": [{"text": "Cuándo empezamos a hablar despacio", "score": 0.95, "y0": 400, "y1": 430}]},
                {"t": 1.4, "lines": [{"text": "Cuándo empezamos a hablar despacio", "score": 0.96, "y0": 400, "y1": 430}]},
            ],
        })
        st_es = subtitles.align(lid_es)
        self.assertEqual(st_es["confirmed"], 4)
        self.assertGreaterEqual(st_es["suggestions"], 1)

        # 2) 中文无空格 OCR 画面字幕对齐
        meta_zh = ls.create_lesson("demo:zh", None, language="zh", native_lang="en")
        lid_zh = meta_zh["id"]
        ls.update_meta(lid_zh, status=ls.STATUS_READY, title="ZH OCR Test", duration=5.0)
        tr_zh = {
            "schema_version": ls.SCHEMA_VERSION, "language": "zh",
            "sentences": [{
                "id": 1, "start": 0.5, "end": 3.0, "text": "每天坚持听十分钟",
                "tokens": [
                    {"id": f"1-{i+1}", "pre": "", "text": ch, "post": "", "key": ch, "start": 0.5 + i * 0.3, "end": 0.8 + i * 0.3, "prob": 0.9}
                    for i, ch in enumerate("每天坚持听十分钟")
                ],
            }],
        }
        ls.save(lid_zh, ls.TRANSCRIPT_FILE, tr_zh)
        ls.save(lid_zh, subtitles.SUBS_FILE, {
            "schema_version": 2, "fps": 3,
            "frames": [
                {"t": 1.0, "lines": [{"text": "每天坚持听十分钟", "score": 0.96, "y0": 420, "y1": 450}]},
            ],
        })
        st_zh = subtitles.align(lid_zh)
        self.assertEqual(st_zh["confirmed"], 8)

    def test_vocal_two_stage_probe_skip_and_cache_reuse(self):
        from unittest.mock import patch
        import numpy as np
        import vocals

        # 构造 4 段长音频（> PROBE_CHUNKS=2）
        n = vocals.GEN * 3 + vocals.GEN // 2
        mix = np.ones((2, n), dtype=np.float32)

        # 1) 伴奏能量几乎为 0（返回与原声接近的人声），应仅跑 2 个预检段即跳过剩余片段
        called_clean: list[int] = []

        def fake_clean(_sess, mix_p, start):
            called_clean.append(start)
            return mix_p[:, start + vocals.TRIM:start + vocals.TRIM + vocals.GEN] / vocals.COMPENSATE

        with patch.object(vocals, "_get_session", return_value=object()), \
             patch.object(vocals, "_separate_chunk", side_effect=fake_clean):
            voc, ratio, sampled = vocals.separate_if_music(mix)
        self.assertTrue(sampled)
        self.assertIsNone(voc)
        self.assertLess(ratio, vocals.PROBE_SKIP_RATIO)
        self.assertEqual(len(called_clean), vocals.PROBE_CHUNKS)

        # 2) 存在强 BGM（分离后人声明显弱于混音），应补齐全部段且不重复计算已预检段
        called_bgm: list[int] = []

        def fake_bgm(_sess, mix_p, start):
            called_bgm.append(start)
            return np.zeros((2, vocals.GEN), dtype=np.float32)

        with patch.object(vocals, "_get_session", return_value=object()), \
             patch.object(vocals, "_separate_chunk", side_effect=fake_bgm):
            voc2, ratio2, sampled2 = vocals.separate_if_music(mix)
        self.assertFalse(sampled2)
        self.assertIsNotNone(voc2)
        self.assertEqual(voc2.shape, (2, n))
        self.assertGreaterEqual(ratio2, vocals.MUSIC_RATIO)
        self.assertEqual(len(called_bgm), 4)
        self.assertEqual(len(set(called_bgm)), 4)

    def test_check_lesson_skips_ocr_when_captions_present(self):
        from unittest.mock import patch

        meta = ls.create_lesson("demo:cap", None, language="sv", native_lang="zh")
        lid = meta["id"]
        ls.update_meta(lid, status=ls.STATUS_READY, title="Caption Skip Test", duration=5.0)
        (ls.lesson_dir(lid) / ls.VIDEO_FILE).write_bytes(b"fake-mp4")
        ls.save(lid, ls.TRANSCRIPT_FILE, {
            "schema_version": ls.SCHEMA_VERSION, "language": "sv",
            "sentences": [{
                "id": 1, "start": 0.5, "end": 2.5, "text": "Hej världen",
                "tokens": [
                    {"id": "1-1", "pre": "", "text": "Hej", "post": "", "key": "hej", "start": 0.5, "end": 1.2, "prob": 0.9},
                    {"id": "1-2", "pre": "", "text": "världen", "post": "", "key": "världen", "start": 1.3, "end": 2.5, "prob": 0.9},
                ],
            }],
        })
        ls.save(lid, subtitles.CAPTIONS_FILE, {
            "schema_version": ls.SCHEMA_VERSION, "lang": "sv",
            "cues": [{"start": 0.5, "end": 2.5, "text": "Hej världen"}],
        })

        with patch.object(subtitles, "ocr_video", side_effect=AssertionError("不应触发硬字幕 OCR")):
            st = subtitles.check_lesson(lid, fetch_captions=False)
        self.assertEqual(st["caption_cues"], 1)
        self.assertEqual(st["confirmed"], 2)


class TestEndToEndFlowAndAPI(unittest.TestCase):
    """验证完整学习闭环与 Flask API（选词 -> 翻译 -> 修正 -> 总生词本 -> 记忆曲线）。"""

    def setUp(self):
        self.client = server.app.test_client()

    def test_full_pipeline_and_spaced_review(self):
        meta = ls.create_lesson("demo:sv", None, language="sv", native_lang="zh")
        lid = meta["id"]
        ls.update_meta(lid, status=ls.STATUS_READY, title="API Flow Lesson", duration=6.0)
        ls.save(lid, ls.TRANSCRIPT_FILE, {
            "schema_version": ls.SCHEMA_VERSION, "language": "sv",
            "sentences": [{
                "id": 1, "start": 0.2, "end": 3.5, "text": "Hon förklarade allt lugnt.",
                "tokens": [
                    {"id": "1-1", "pre": "", "text": "Hon", "post": "", "key": "hon", "start": 0.2, "end": 0.6, "prob": 0.98},
                    {"id": "1-2", "pre": "", "text": "förklarade", "post": "", "key": "förklarade", "start": 0.7, "end": 1.6, "prob": 0.95},
                    {"id": "1-3", "pre": "", "text": "allt", "post": "", "key": "allt", "start": 1.7, "end": 2.2, "prob": 0.96},
                    {"id": "1-4", "pre": "", "text": "lugnt", "post": ".", "key": "lugnt", "start": 2.3, "end": 3.5, "prob": 0.94},
                ],
            }],
        })

        # 页面与元数据接口正常返回 200
        for route in ("/", "/vocab", "/review", f"/lesson/{lid}", f"/lesson/{lid}/translate", "/api/languages", "/api/state"):
            with self.client.get(route) as r:
                self.assertEqual(r.status_code, 200, f"Route failed: {route}")

        # 选生词
        r = self.client.post(f"/api/lessons/{lid}/vocab", json={"key": "förklarade", "text": "förklarade", "on": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.get_json()["vocab"]["words"]), 1)

        # 生成英文译文任务与导入（验证任意语种组合）
        r_task = self.client.get(f"/api/lessons/{lid}/translation/task?native_lang=en")
        self.assertEqual(r_task.status_code, 200)
        task = r_task.get_json()
        self.assertEqual(task["source_language"], "sv")
        self.assertEqual(task["target_translation_language"], "en")

        ai_reply = """Here is the JSON:
```json
{
  "sentences": [{"id": 1, "text": "She explained everything calmly."}],
  "words": [{"key": "förklarade", "text": "explained", "lemma": "att förklara", "pos": "verb", "note": "förklarar / förklarade", "in_sentence": "explained",}],
  "suspects": []
}
```"""
        r_imp = self.client.post(f"/api/lessons/{lid}/translation", json={"text": ai_reply, "native_lang": "en"})
        self.assertEqual(r_imp.status_code, 200)
        self.assertEqual(r_imp.get_json()["report"]["words"], 1)

        # 总生词本自动归一化原形为 förklara 且带语言前缀 sv|förklara|verb
        r_vb = self.client.get("/api/vocabbook?lang=sv")
        words = r_vb.get_json()["words"]
        entry = next((w for w in words if w["id"] == "sv|förklara|verb"), None)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["lemma"], "förklara")
        self.assertEqual(entry["pos"], "verb")

        # 记忆曲线复习调度：答对升 1 级，答错降 2 级
        today = date(2026, 11, 23)
        res1 = review.answer("sv|förklara|verb", "listen", "known", on=today)
        self.assertEqual(res1["level"], 1)
        self.assertEqual(res1["due"], "2026-11-24")
        res2 = review.answer("sv|förklara|verb", "listen", "unknown", on=today)
        self.assertEqual(res2["level"], 0)
        self.assertEqual(res2["due"], "2026-11-24")

        # 修正原文后自动同步生词位置
        transcript_edit.replace_tokens(lid, 1, 3, 3, "allting")
        tr_after = ls.load(lid, ls.TRANSCRIPT_FILE)
        self.assertIn("allting", tr_after["sentences"][0]["text"])

        # 录音上传与删除
        r_rec = self.client.post(
            f"/api/lessons/{lid}/recordings?sentence_id=1",
            data=b"RIFFdemoaudio",
            content_type="audio/wav",
        )
        self.assertEqual(r_rec.status_code, 200)
        fname = r_rec.get_json()["recording"]["file"]
        r_del = self.client.delete(f"/api/lessons/{lid}/recordings/{fname}")
        self.assertEqual(r_del.status_code, 200)
        self.assertEqual(r_del.get_json()["recordings"], [])

    def test_optional_llm_config_and_auto_translate(self):
        from unittest.mock import patch

        # 保存与脱敏展示
        r_save = self.client.post("/api/llm/config", json={
            "provider": "deepseek",
            "api_key": "sk-test-secret-9876",
            "model": "deepseek-chat",
        })
        self.assertEqual(r_save.status_code, 200)
        cfg = r_save.get_json()
        self.assertTrue(cfg["configured"])
        self.assertEqual(cfg["api_key_masked"], "sk-t••••9876")
        self.assertNotIn("sk-test-secret-9876", str(cfg))
        ds_preset = next(p for p in cfg["presets"] if p["id"] == "deepseek")
        self.assertIn("deepseek-chat", ds_preset["models"])

        # 动态拉取模型列表接口
        with patch.object(llm, "list_models", return_value=["deepseek-chat", "deepseek-reasoner"]):
            r_models = self.client.post("/api/llm/models", json={"provider": "deepseek"})
        self.assertEqual(r_models.status_code, 200)
        self.assertEqual(r_models.get_json()["models"], ["deepseek-chat", "deepseek-reasoner"])

        # 创建测试课程并通过 mock LLM 一键翻译
        meta = ls.create_lesson("demo:auto", None, language="es", native_lang="zh")
        lid = meta["id"]
        ls.update_meta(lid, status=ls.STATUS_READY, title="Auto ES Lesson", duration=4.0)
        ls.save(lid, ls.TRANSCRIPT_FILE, {
            "schema_version": ls.SCHEMA_VERSION, "language": "es",
            "sentences": [{
                "id": 1, "start": 0.1, "end": 2.0, "text": "Hablamos despacio.",
                "tokens": [
                    {"id": "1-1", "pre": "", "text": "Hablamos", "post": "", "key": "hablamos", "start": 0.1, "end": 1.0, "prob": 0.95},
                    {"id": "1-2", "pre": "", "text": "despacio", "post": ".", "key": "despacio", "start": 1.1, "end": 2.0, "prob": 0.96},
                ],
            }],
        })
        vocab.set_word(lid, "hablamos", "Hablamos", True)

        fake_reply = '{"sentences":[{"id":1,"text":"我们慢慢说。"}],"words":[{"key":"hablamos","text":"我们说","lemma":"hablar","pos":"verb","note":"-ar 规则动词","in_sentence":"我们说"}],"suspects":[]}'
        with patch.object(llm, "_post_chat", return_value=fake_reply):
            r_auto = self.client.post(f"/api/lessons/{lid}/translation/auto", json={"native_lang": "zh"})
        self.assertEqual(r_auto.status_code, 200)
        body = r_auto.get_json()
        self.assertEqual(body["report"]["sentences"], 1)
        self.assertEqual(body["report"]["words"], 1)
        self.assertEqual(body["translation"]["words"][0]["lemma"], "hablar")

        # 清除配置
        r_clr = self.client.delete("/api/llm/config")
        self.assertEqual(r_clr.status_code, 200)
        self.assertFalse(r_clr.get_json()["configured"])


if __name__ == "__main__":
    unittest.main()
