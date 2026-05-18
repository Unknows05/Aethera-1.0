"""
Model Selector — live model fetch from OpenRouter & Ollama with interactive picker.
"""
import json
import time
import logging
from typing import Optional

import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

logger = logging.getLogger(__name__)
console = Console()

_CACHE_TTL = 600
_models_cache: dict = {"data": None, "fetched_at": 0}


def _cache_valid() -> bool:
    return _models_cache["data"] is not None and (time.time() - _models_cache["fetched_at"]) < _CACHE_TTL


def fetch_openrouter_models(api_key: str = None) -> list:
    if _cache_valid() and _models_cache["data"] and _models_cache.get("provider") == "openrouter":
        return _models_cache["data"]

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        r = requests.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=15)
        if r.status_code != 200:
            return []

        data = r.json()
        models = []
        for m in data.get("data", []):
            model_id = m.get("id", "")
            name = m.get("name", model_id)
            pricing = m.get("pricing", {})

            prompt_cost = float(pricing.get("prompt", "0") or 0)
            completion_cost = float(pricing.get("completion", "0") or 0)

            context_length = m.get("context_length", 0)
            description = m.get("description", "")[:120]

            supported_params = m.get("supported_parameters", [])
            if isinstance(supported_params, list) and "tools" not in supported_params:
                continue
            if isinstance(supported_params, str) and "tools" not in supported_params:
                continue

            models.append((
                model_id,
                name[:80],
                description,
                prompt_cost,
                completion_cost,
                context_length,
            ))

        models.sort(key=lambda x: (x[3] + x[4] == 0, -(x[3] + x[4])))
        _models_cache["data"] = models
        _models_cache["fetched_at"] = time.time()
        _models_cache["provider"] = "openrouter"
        return models
    except Exception as e:
        logger.debug(f"[ModelSelector] OpenRouter fetch failed: {e}")
        return []


def fetch_ollama_models(base_url: str = "http://localhost:11434") -> list:
    if _cache_valid() and _models_cache.get("provider") == "ollama":
        return _models_cache["data"]

    try:
        r = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        if r.status_code != 200:
            return []

        data = r.json()
        models = [m.get("name", "") for m in data.get("models", [])]
        _models_cache["data"] = models
        _models_cache["fetched_at"] = time.time()
        _models_cache["provider"] = "ollama"
        return models
    except Exception as e:
        logger.debug(f"[ModelSelector] Ollama fetch failed: {e}")
        return []


def select_model_interactive(provider: str, api_key: str = "") -> Optional[str]:
    console.print(f"[bold]Fetching models from {provider}...[/bold]")

    if provider == "ollama":
        url = "http://localhost:11434"
        if "OLLAMA_HOST" in __import__("os").environ:
            url = __import__("os").environ["OLLAMA_HOST"]
        models = fetch_ollama_models(url)
        if not models:
            console.print("[yellow]No Ollama models found. Is Ollama running?[/yellow]")
            return None
        table = Table(title="Ollama Models")
        table.add_column("#", style="dim")
        table.add_column("Model ID", style="cyan")
        for i, m in enumerate(models):
            table.add_row(str(i + 1), m)
        console.print(table)
        choice = __import__("builtins").input("Select model number (or type model ID): ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                return models[idx]
        except ValueError:
            pass
        if choice in models:
            return choice
        return models[0] if models else None

    elif provider in ("openrouter", "openai", "groq"):
        if not api_key:
            api_key = (
                __import__("os").environ.get("OPENROUTER_API_KEY")
                or __import__("os").environ.get("OPENAI_API_KEY")
                or __import__("os").environ.get("GROQ_API_KEY")
                or __import__("os").environ.get("LLM_API_KEY", "")
            )
        models = fetch_openrouter_models(api_key)
        if not models:
            console.print("[yellow]Could not fetch live models. Check internet or API key.[/yellow]")
            defaults = [
                ("google/gemini-2.5-flash-lite:free",     "Gemini Flash Lite", "free"),
                ("openrouter/elephant-alpha",              "Elephant (free)", "free"),
                ("google/gemini-2.5-flash",                "Gemini Flash 2.5", "$0.15/M"),
                ("deepseek/deepseek-chat",                 "DeepSeek V3", "$0.89/M"),
                ("anthropic/claude-sonnet-4.6",            "Claude Sonnet 4", "$3/M"),
                ("openai/gpt-5-mini",                      "GPT-5 Mini", "$0.15/M"),
                ("qwen/qwen3.5-plus",                      "Qwen 3.5 Plus", "$0.40/M"),
                ("nvidia/nemotron-3-super-120b-a12b:free", "Nemotron (free)", "free"),
            ]
            table = Table(title="Default Models")
            table.add_column("#", style="dim")
            table.add_column("Model ID", style="cyan")
            table.add_column("Pricing")
            for i, (mid, name, cost) in enumerate(defaults):
                table.add_row(str(i + 1), f"{mid} — [dim]{name}[/dim]", cost)
            console.print(table)
            choice = __import__("builtins").input("Select number: ").strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(defaults):
                    return defaults[idx][0]
            except ValueError:
                pass
            return None

        table = Table(title=f"Available Models — {provider.title()}")
        table.add_column("#", style="dim", width=3)
        table.add_column("Model ID", style="cyan")
        table.add_column("In/Mtok", style="green", justify="right")
        table.add_column("Out/Mtok", style="green", justify="right")

        for i, (mid, name, desc, pc, cc, ctx) in enumerate(models[:25]):
            cost_in = "free" if pc == 0 else f"${pc * 1_000_000:.2f}"
            cost_out = "free" if cc == 0 else f"${cc * 1_000_000:.2f}"
            tag = ""
            if i == 0:
                tag = " [yellow]← recommended[/yellow]"
            elif pc + cc == 0:
                tag = " [green]free[/green]"
            table.add_row(str(i + 1), f"{mid}{tag}", cost_in, cost_out)

        console.print(table)
        choice = __import__("builtins").input("\nSelect model number (or type model ID): ").strip()

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                return models[idx][0]
        except ValueError:
            pass

        for m in models:
            if m[0] == choice:
                return choice
        console.print("[yellow]Invalid selection. No model chosen.[/yellow]")
        return None

    else:
        console.print(f"[red]Unknown provider: {provider}[/red]")
        return None


def test_connection(provider: str, api_key: str, model: str) -> bool:
    if provider == "openrouter":
        base_url = "https://openrouter.ai/api/v1"
    elif provider == "openai":
        base_url = "https://api.openai.com/v1"
    elif provider == "groq":
        base_url = "https://api.groq.com/openai/v1"
    elif provider == "ollama":
        base_url = __import__("os").environ.get("OLLAMA_HOST", "http://localhost:11434/v1")
    else:
        return False

    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=15)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply: OK"}],
            max_tokens=5,
            temperature=0,
        )
        return bool(response.choices)
    except Exception as e:
        logger.debug(f"[ModelSelector] Connection test failed: {e}")
        return False
