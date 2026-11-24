"""可选的一键 AI 翻译模块（支持 DeepSeek / OpenAI / Google Gemini / 自定义 OpenAI 兼容接口）。

设计原则：
- 零额外依赖：仅使用 Python 标准库 urllib.request 调用标准的 /chat/completions 接口。
- 隐私与安全：API Key 仅保存在本机根目录 llm_config.json（已在 .gitignore 中排除），
  对外接口仅返回脱敏后的掩码，绝不回传明文 Key。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from env_setup import ROOT
import lesson_store as ls
import progress as prog
import translation

CONFIG_FILE = Path(os.environ.get("CLIPLINGUA_LLM_CONFIG") or (ROOT / "llm_config.json"))

PRESETS: dict[str, dict] = {
    "deepseek": {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "openai": {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1", "gpt-4o"],
    },
    "gemini": {
        "id": "gemini",
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-flash",
        "models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
    },
    "custom": {
        "id": "custom",
        "name": "Custom (OpenAI-compatible)",
        "base_url": "",
        "model": "",
        "models": [],
    },
}


class LLMError(Exception):
    pass


def _mask_key(key: str) -> str:
    s = (key or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "••••"
    return f"{s[:4]}••••{s[-4:]}"


def load_config() -> dict:
    """读取本地 LLM 配置（如不存在则返回空默认结构）。"""
    cfg = ls.read_json(CONFIG_FILE) or {}
    provider = str(cfg.get("provider") or "deepseek").strip().lower()
    if provider not in PRESETS:
        provider = "custom"
    preset = PRESETS[provider]
    return {
        "provider": provider,
        "base_url": str(cfg.get("base_url") or preset["base_url"]).strip().rstrip("/"),
        "model": str(cfg.get("model") or preset["model"]).strip(),
        "api_key": str(cfg.get("api_key") or "").strip(),
    }


def public_config() -> dict:
    """返回脱敏后的配置状态，供前端展示。"""
    cfg = load_config()
    return {
        "configured": bool(cfg["api_key"] and cfg["base_url"] and cfg["model"]),
        "provider": cfg["provider"],
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "api_key_masked": _mask_key(cfg["api_key"]),
        "presets": list(PRESETS.values()),
    }


def save_config(provider: str, api_key: str | None, model: str | None = None, base_url: str | None = None) -> dict:
    """保存本地配置。api_key 传 None 或留空时保留原有 Key，便于单独改模型。"""
    old = load_config()
    prov = str(provider or "deepseek").strip().lower()
    if prov not in PRESETS:
        raise ValueError("Unsupported provider")
    preset = PRESETS[prov]

    url = str(base_url if base_url is not None else "").strip().rstrip("/") or preset["base_url"]
    mdl = str(model if model is not None else "").strip() or preset["model"]
    key = str(api_key).strip() if api_key is not None and str(api_key).strip() else old["api_key"]

    if not url.startswith(("http://", "https://")):
        raise ValueError("Base URL must start with http:// or https://")
    if not mdl:
        raise ValueError("Model name cannot be empty")
    if not key:
        raise ValueError("API Key cannot be empty")

    ls.write_json(CONFIG_FILE, {"provider": prov, "base_url": url, "model": mdl, "api_key": key})
    return public_config()


def clear_config() -> dict:
    """清除本地保存的 API Key 与配置。"""
    CONFIG_FILE.unlink(missing_ok=True)
    return public_config()


def _chat_endpoint(base_url: str) -> str:
    u = base_url.rstrip("/")
    if u.endswith("/chat/completions"):
        return u
    return f"{u}/chat/completions"


def _models_endpoint(base_url: str) -> str:
    u = base_url.rstrip("/")
    if u.endswith("/chat/completions"):
        u = u[:-len("/chat/completions")]
    if u.endswith("/models"):
        return u
    return f"{u}/models"


def list_models(
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> list[str]:
    """向 OpenAI 兼容服务商的 /models 端点动态拉取可用模型 ID 列表。"""
    cfg = load_config()
    if provider:
        prov = provider.strip().lower()
        preset = PRESETS.get(prov, PRESETS["custom"])
        cfg["base_url"] = (base_url or "").strip().rstrip("/") or preset["base_url"]
        if api_key and api_key.strip():
            cfg["api_key"] = api_key.strip()
    if not (cfg.get("api_key") and cfg.get("base_url")):
        raise LLMError("Please enter your API Key and Base URL first.")

    req = urllib.request.Request(
        _models_endpoint(cfg["base_url"]),
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "User-Agent": "ClipLingua/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise LLMError("API Key rejected (401 Unauthorized).") from None
        raise LLMError(f"Failed to fetch models (HTTP {e.code}).") from None
    except Exception as e:  # noqa: BLE001
        raise LLMError(f"Network error fetching models: {e}") from None

    try:
        data = json.loads(raw)
        items = data.get("data") if isinstance(data, dict) else data
        ids = []
        for it in items or []:
            mid = str(it.get("id") or "").strip() if isinstance(it, dict) else str(it).strip()
            if mid:
                # 兼容 Gemini OpenAI 端点偶尔返回的 models/ 前缀
                ids.append(mid.removeprefix("models/"))
    except Exception as e:  # noqa: BLE001
        raise LLMError(f"Unexpected /models response: {e}") from None

    # 去重保序并优先把常用对话模型排前面
    seen = list(dict.fromkeys(ids))
    if not seen:
        raise LLMError("No models returned by this endpoint.")
    return sorted(seen)


def _post_chat(cfg: dict, messages: list[dict], timeout: int = 120) -> str:
    """向 OpenAI 兼容端点发送 Chat Completions 请求并返回文本内容。"""
    if not (cfg.get("api_key") and cfg.get("base_url") and cfg.get("model")):
        raise LLMError("AI API is not configured yet. Click the gear icon in the top-right corner.")

    endpoint = _chat_endpoint(cfg["base_url"])
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.2,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
            "User-Agent": "ClipLingua/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        if e.code == 401:
            raise LLMError("API Key rejected (401 Unauthorized). Please check your key.") from None
        if e.code == 429:
            raise LLMError("Rate limit or quota exceeded (429). Please check your account balance.") from None
        raise LLMError(f"API HTTP {e.code}: {body or e.reason}") from None
    except urllib.error.URLError as e:
        raise LLMError(f"Network error contacting API: {e.reason}") from None
    except TimeoutError:
        raise LLMError("API request timed out. Try translating missing items in smaller batches.") from None

    try:
        parsed = json.loads(raw)
        content = parsed["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        raise LLMError(f"Unexpected API response format: {e}") from None

    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    text = str(content or "").strip()
    if not text:
        raise LLMError("AI returned an empty response")
    return text


def test_connection(
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> dict:
    """发送极短请求验证当前或表单里的 API 配置是否可用。"""
    cfg = load_config()
    if provider:
        prov = provider.strip().lower()
        preset = PRESETS.get(prov, PRESETS["custom"])
        cfg["provider"] = prov
        cfg["base_url"] = (base_url or "").strip().rstrip("/") or preset["base_url"]
        cfg["model"] = (model or "").strip() or preset["model"]
        if api_key and api_key.strip():
            cfg["api_key"] = api_key.strip()
    reply = _post_chat(
        cfg,
        [{"role": "user", "content": "Reply with only: OK"}],
        timeout=25,
    )
    return {"ok": True, "model": cfg["model"], "reply": reply[:40]}


def translate_lesson(lesson_id: str, only_missing: bool = False, native_lang: str | None = None) -> tuple[dict, dict]:
    """一键调用配置好的 AI API 翻译课程并自动合并导入。"""
    cfg = load_config()
    task = translation.build_task(lesson_id, only_missing=only_missing, native_lang=native_lang)
    if not task["sentences"] and not task["words"]:
        raise ValueError("Nothing left to translate")

    messages = [
        {"role": "system", "content": task["instruction"]},
        {"role": "user", "content": json.dumps(task, ensure_ascii=False)},
    ]
    reply_text = _post_chat(cfg, messages, timeout=180)
    t_data, report = translation.import_text(lesson_id, reply_text, native_lang=native_lang)
    if prog.load(lesson_id)["stage"] == "reading":
        prog.advance(lesson_id, "reading")
    return t_data, report
