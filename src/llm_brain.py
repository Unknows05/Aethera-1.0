"""
LLM Brain v2 — OpenAI-compatible ReAct agent loop with Meridian-inspired improvements.

Improvements:
1. Rate limiting (1s min between requests)
2. Request deduplication (ONCE_PER_SESSION tools)
3. Fallback model (deepseek/deepseek-chat-v3-0324:free)
4. JSON repair with jsonrepair library
5. Intent-based tool filtering
6. Better error handling with retry logic
7. Performance guards

Supports: OpenRouter (free/paid), OpenAI, Groq, local (LM Studio/Ollama).
"""
import json
import os
import logging
import asyncio
import time
import re
from datetime import datetime
from typing import Dict, List, Optional, Set
import threading
from threading import Lock

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    logger.warning("[LLM] openai package not installed — LLM disabled")

try:
    from jsonrepair import jsonrepair
    HAS_JSONREPAIR = True
except ImportError:
    HAS_JSONREPAIR = False
    logger.debug("[LLM] jsonrepair not installed — using basic JSON parsing")

# ── LLM Model Options ─────────────────────────────────────────
MODELS = {
    "free": {
        "gemini-flash": "google/gemini-2.0-flash-001",
        "deepseek": "deepseek/deepseek-chat-v3-0324:free",
    },
    "paid": {
        "gpt-4o-mini": "openai/gpt-4o-mini",
        "claude-sonnet": "anthropic/claude-sonnet-4-20250514",
        "groq-llama": "groq/llama-4-maverick-17b-128e-instruct",
        "gpt-4o": "openai/gpt-4o",
    },
    "local": {
        "ollama": "ollama/llama3.1",
        "lmstudio": "local-model",
    }
}

# Fallback model kalau primary fail
# Valid free models on OpenRouter (verified 2026-05-11):
# - deepseek/deepseek-chat-v3-0324:free (reliable)
# - google/gemini-2.0-flash-001 (fast)
# - meta-llama/llama-3.1-8b-instruct:free
FALLBACK_MODEL = "deepseek/deepseek-chat-v3-0324:free"

# ── Tool Control ──────────────────────────────────────────────
# Tools yang hanya boleh dipanggil 1x per session (prevent duplicate deploy/close)
ONCE_PER_SESSION: Set[str] = {
    "execute_trade", "close_position", "adjust_position"
}

# Tools yang tidak boleh retry kalau gagal
NO_RETRY_TOOLS: Set[str] = {
    "execute_trade"  # Jangan retry trade kalau sudah attempted
}

# ── Intent-based Tool Filtering ───────────────────────────────
INTENT_PATTERNS = {
    "screen": re.compile(r"\b(screen|scan|find|search|cari|detect)\b", re.IGNORECASE),
    "trade": re.compile(r"\b(buy|sell|trade|entry|long|short|beli|jual|masuk)\b", re.IGNORECASE),
    "manage": re.compile(r"\b(close|exit|manage|keluar|tutup|trailing)\b", re.IGNORECASE),
    "analyze": re.compile(r"\b(analyze|why|kenapa|explain|analisis|mengapa)\b", re.IGNORECASE),
    "performance": re.compile(r"\b(performance|history|stats|report|wr|win rate)\b", re.IGNORECASE),
}

INTENT_TOOLS = {
    "screen": ["get_market_context", "get_coin_history"],
    "trade": ["get_coin_history", "get_recent_lessons", "get_decision_log"],
    "manage": ["get_decision_log", "get_recent_lessons"],
    "analyze": ["get_market_context", "get_coin_history", "get_recent_lessons", "get_decision_log"],
    "performance": ["get_coin_history", "get_decision_log"],
}

# ── Tool Definitions ──────────────────────────────────────────
LLM_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_market_context",
            "description": "Get current market macro context: BTC.D, USDT.D, Fear & Greed, 24h change, BTC trend.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_coin_history",
            "description": "Get trading history for a specific coin: trades count, win rate, avg PnL per regime, best/worst regimes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Trading pair symbol e.g. BTCUSDT"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_lessons",
            "description": "Get recent lessons learned from closed trades, filtered by role (SCREENER/MANAGER/GENERAL).",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["SCREENER", "MANAGER", "GENERAL"]},
                    "limit": {"type": "integer", "default": 10}
                },
                "required": ["role"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_decision_log",
            "description": "Get recent agent decisions and their rationale — understand WHY previous trades were made or skipped.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10}
                },
                "required": []
            }
        }
    },
]


class LLMBrain:
    """
    ReAct agent loop untuk trading decisions dengan rate limiting dan deduplication.
    """

    def __init__(self, model: str = None):
        self.model = model or os.getenv("LLM_MODEL") or MODELS["free"]["gemini-flash"]
        self.base_url = os.getenv("LLM_BASE_URL") or "https://openrouter.ai/api/v1"
        self.api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY") or ""
        self.client = None
        self._ready = False
        
        # Rate limiting
        self._request_lock = asyncio.Lock()
        self._sync_lock = Lock()
        self._last_request_time = 0.0
        self._min_request_interval = 1.0  # Minimum 1 second between requests
        
        # Session tracking
        self._session_fired_tools: Set[str] = set()
        
        self._connect()

    def _connect(self):
        if not HAS_OPENAI or not self.api_key:
            logger.warning("[LLM] No API key — LLM disabled. Set OPENROUTER_API_KEY in .env")
            return
        try:
            self.client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=60)
            self._ready = True
            # Mask sensitive info in logs
            masked_url = self.base_url.replace("https://", "").replace("http://", "")
            if "/" in masked_url:
                masked_url = masked_url.split("/")[0] + "/***"
            logger.info(f"[LLM] Ready: {self.model} @ {masked_url}")
        except Exception as e:
            logger.error(f"[LLM] Connection failed: {e}")
            self._ready = False

    def reconnect(self):
        """Reconnect with updated env vars."""
        self.model = os.getenv("LLM_MODEL") or self.model
        self.base_url = os.getenv("LLM_BASE_URL") or self.base_url
        self.api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY") or self.api_key
        self._connect()

    def is_ready(self) -> bool:
        return self._ready and self.client is not None

    def _get_tools_for_role(self, role: str, goal: str = "") -> List[Dict]:
        """Get tools filtered by role and intent (Meridian pattern)."""
        if role in ["SCREENER", "MANAGER"]:
            return LLM_TOOLS  # Full tools untuk dedicated agents
        
        # GENERAL role: filter by intent
        matched_tools = set()
        for intent, pattern in INTENT_PATTERNS.items():
            if pattern.search(goal):
                matched_tools.update(INTENT_TOOLS.get(intent, []))
        
        if not matched_tools:
            # Fallback: semua tools
            return LLM_TOOLS
        
        return [t for t in LLM_TOOLS if t["function"]["name"] in matched_tools]

    def build_system_prompt(self, role: str, context: dict = None) -> str:
        """Build system prompt dengan role-specific instructions + injected memory."""
        ctx = context or {}
        market = ctx.get("market", "")
        lessons = ctx.get("lessons", "")
        coin_mem = ctx.get("coin_memory", "")
        decision_log = ctx.get("decision_log", "")

        role_instructions = {
            "SCREENER": """You are the SCREENER AGENT — responsible for evaluating trading opportunities.
Your job: Given a coin's score, regime, confidence, and market context, decide whether to take the trade.

DECISION RULES:
- Score >70 in BULL or BEAR regime with solid confluence → likely LONG/SHORT
- Score 55-65 in SIDEWAYS → be CAUTIOUS, SIDEWAYS+LONG historically weak (WR 38.8%)
- Score <45 regardless of regime → likely WAIT
- BTC.D >55% means risk-off → favor SHORT, reduce LONG confidence
- BTC.D <48% means risk-on → favor LONG
- Volume must confirm the signal — low volume = skip
- Check coin history: if past 10 trades on this coin were losses → skip

OUTPUT FORMAT (JSON only):
{
  "decision": "LONG" | "SHORT" | "WAIT",
  "confidence_adjustment": 0,
  "rationale": "2-3 sentence explanation",
  "risk_warning": "optional warning"
}""",

            "MANAGER": """You are the POSITION MANAGER — responsible for monitoring open positions.
Your job: Given a position's current PnL, time held, regime changes, decide management action.

DECISION RULES:
- If regime flipped against the trade (BULL→SIDEWAYS for LONG) → consider CLOSE
- If profit reached 1.5R and volume declining → TRAIL SL to breakeven
- If profit reached 3R → consider TAKE PROFIT (partial or full)
- If 12+ hours held and still at breakeven → consider CLOSE (capital stuck)
- Never close just because of small drawdown if trend still valid

OUTPUT FORMAT (JSON only):
{
  "action": "STAY" | "TRAIL_SL" | "CLOSE" | "PARTIAL_TP",
  "new_sl": null,
  "partial_pct": null,
  "rationale": "2-3 sentence explanation"
}""",

            "GENERAL": """You are a trading consultant. Provide helpful analysis based on available data.
Always respond in valid JSON format when making recommendations."""
        }

        prompt = role_instructions.get(role, role_instructions["GENERAL"])
        if market:
            prompt += f"\n\n═══ MARKET CONTEXT ═══\n{market}"
        if lessons:
            prompt += f"\n\n═══ LESSONS LEARNED ═══\n{lessons}"
        if coin_mem:
            prompt += f"\n\n═══ COIN HISTORY ═══\n{coin_mem}"
        if decision_log:
            prompt += f"\n\n═══ RECENT DECISIONS ═══\n{decision_log}"
        return prompt

    def _parse_json_safely(self, text: str, fn_name: str = "unknown") -> Dict:
        """Parse JSON dengan repair capability (Meridian pattern)."""
        if not text or not text.strip():
            return {}
        
        text = text.strip()
        
        # Try direct parsing first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        
        # Try extracting JSON from markdown
        if "```json" in text:
            try:
                json_part = text.split("```json")[1].split("```")[0].strip()
                return json.loads(json_part)
            except:
                pass
        
        if text.startswith("```") and text.endswith("```"):
            try:
                return json.loads(text[3:-3].strip())
            except:
                pass
        
        # Try jsonrepair
        if HAS_JSONREPAIR:
            try:
                repaired = jsonrepair(text)
                logger.debug(f"[LLM] Repaired JSON for {fn_name}")
                return json.loads(repaired)
            except Exception as e:
                logger.debug(f"[LLM] JSON repair failed: {e}")
        
        # Last resort: try last line
        try:
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            if lines:
                return json.loads(lines[-1])
        except:
            pass
        
        logger.warning(f"[LLM] Failed to parse JSON for {fn_name}: {text[:200]}")
        return {}

    async def _make_request_with_retry(self, messages: List[Dict], tools: List[Dict], 
                                       tool_choice: str = "auto") -> Optional[Dict]:
        """Make LLM request dengan retry logic dan fallback model."""
        models_to_try = [self.model, FALLBACK_MODEL]
        
        for model_attempt, current_model in enumerate(models_to_try):
            for attempt in range(3):  # Max 3 retries per model
                try:
                    response = self.client.chat.completions.create(
                        model=current_model,
                        messages=messages,
                        tools=tools if tools else None,
                        tool_choice=tool_choice if tools else None,
                        temperature=0.3,
                        max_tokens=1024,
                        timeout=30,
                    )
                    
                    if response and response.choices:
                        if model_attempt > 0:
                            logger.info(f"[LLM] Fallback model {current_model} succeeded")
                        return response
                    
                except Exception as e:
                    error_str = str(e).lower()
                    
                    # Rate limit handling
                    if "429" in error_str or "rate limit" in error_str:
                        wait_time = (attempt + 1) * 10  # 10s, 20s, 30s
                        logger.warning(f"[LLM] Rate limited, waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    # Server error handling
                    if any(code in error_str for code in ["502", "503", "529"]):
                        wait_time = (attempt + 1) * 5
                        logger.warning(f"[LLM] Server error {e}, retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    # Tool choice error
                    if "tool_choice" in error_str and tool_choice == "required":
                        logger.debug("[LLM] Provider rejected tool_choice=required, switching to auto")
                        tool_choice = "auto"
                        continue
                    
                    # Log error
                    logger.error(f"[LLM] Request failed (model={current_model}, attempt={attempt+1}): {e}")
                    
                    # Try fallback model on last attempt
                    if attempt == 2 and model_attempt == 0:
                        logger.info(f"[LLM] Switching to fallback model {FALLBACK_MODEL}")
                        break
                    
                    # Wait before retry
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
        
        return None

    async def decide(self, goal: str, role: str = "SCREENER",
                     context: dict = None, max_steps: int = 8) -> Dict:
        """
        ReAct agent loop dengan rate limiting, deduplication, dan retry logic.
        """
        if not self.is_ready():
            return {"decision": "WAIT", "rationale": "LLM not available", "confidence_adjustment": 0}

        # Reset session tracking
        self._session_fired_tools.clear()
        
        system_prompt = self.build_system_prompt(role, context)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": goal},
        ]
        
        tool_call_count = 0
        no_tool_retry_count = 0
        saw_tool_call = False

        for step in range(max_steps):
            logger.info(f"[LLM] Step {step + 1}/{max_steps}")
            
            # Rate limiting
            async with self._request_lock:
                now = time.time()
                elapsed = now - self._last_request_time
                if elapsed < self._min_request_interval:
                    await asyncio.sleep(self._min_request_interval - elapsed)
                
                # Determine tool choice
                tools = self._get_tools_for_role(role, goal)
                tool_choice = "auto"
                
                # Force tool call pada step 0 untuk action intents
                if step == 0 and role in ["SCREENER", "MANAGER"]:
                    tool_choice = "required"
                
                # Make request dengan retry
                response = await self._make_request_with_retry(messages, tools, tool_choice)
                self._last_request_time = time.time()
            
            if not response:
                return {"decision": "WAIT", "rationale": "LLM request failed after retries", "confidence_adjustment": 0}

            msg = response.choices[0].message
            
            # Handle empty response
            if not msg.content and not msg.tool_calls:
                logger.debug("[LLM] Empty response, retrying...")
                await asyncio.sleep(1)
                continue
            
            messages.append(msg)

            # Check if final answer (no tool calls)
            if not msg.tool_calls:
                if role in ["SCREENER", "MANAGER"] and not saw_tool_call:
                    # Force tool call untuk action requests
                    no_tool_retry_count += 1
                    if no_tool_retry_count >= 2:
                        return {
                            "decision": "WAIT",
                            "rationale": "LLM did not use required tools after retries",
                            "confidence_adjustment": 0
                        }
                    
                    messages.append({
                        "role": "system",
                        "content": "You must use the available tools to get real data. Do not answer from memory. Call a tool first, then report the result."
                    })
                    await asyncio.sleep(0.5)
                    continue
                
                # Parse final answer
                result = self._parse_json_safely(msg.content or "{}", "final_answer")
                if result:
                    return result
                return {"decision": "WAIT", "rationale": msg.content[:300] if msg.content else "Empty response", "confidence_adjustment": 0}

            saw_tool_call = True
            
            # Limit tool calls per response
            tool_calls = msg.tool_calls[:3]  # Max 3 tools per step
            
            # Execute tool calls
            tool_results = []
            for tc in tool_calls:
                fn_name = tc.function.name
                
                # Deduplication check
                if fn_name in self._session_fired_tools and fn_name in ONCE_PER_SESSION:
                    logger.warning(f"[LLM] Blocked duplicate {fn_name} call")
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({
                            "blocked": True,
                            "reason": f"{fn_name} already executed this session"
                        })
                    })
                    continue
                
                # Parse arguments
                fn_args = self._parse_json_safely(tc.function.arguments, fn_name)
                
                # Execute tool
                try:
                    tool_result = await self._execute_tool(fn_name, fn_args, context)
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(tool_result)
                    })
                    tool_call_count += 1
                    
                    # Track fired tools
                    if fn_name in ONCE_PER_SESSION:
                        self._session_fired_tools.add(fn_name)
                    
                    # NO_RETRY_TOOLS: lock immediately
                    if fn_name in NO_RETRY_TOOLS:
                        self._session_fired_tools.add(fn_name)
                        
                except Exception as e:
                    logger.error(f"[LLM] Tool execution error for {fn_name}: {e}")
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"error": str(e)})
                    })

            messages.extend(tool_results)
            
            # Max tool calls check
            if tool_call_count > 10:
                return {"decision": "WAIT", "rationale": "Max tool calls reached", "confidence_adjustment": 0}
            
            # Delay antar step (prevent spam)
            if step < max_steps - 1:
                await asyncio.sleep(0.5)

        return {"decision": "WAIT", "rationale": "Max steps reached", "confidence_adjustment": 0}

    async def _execute_tool(self, fn_name: str, args: dict, context: dict) -> dict:
        """Execute tool call dan return structured result."""
        if fn_name == "get_market_context":
            return {
                "btc_dom": context.get("btc_dom", "?"),
                "usdt_dom": context.get("usdt_dom", "?"),
                "fear_greed": context.get("fear_greed", "?"),
                "btc_trend": context.get("btc_trend", 0),
                "market_bias": context.get("market_bias", "NEUTRAL")
            }

        if fn_name == "get_coin_history":
            from src.coin_memory import recall_coin
            return recall_coin(args.get("symbol", ""))

        if fn_name == "get_recent_lessons":
            from src.lessons import get_recent_lessons
            lessons = get_recent_lessons(args.get("limit", 10))
            return {"lessons": [{"rule": l.get("lesson", "")[:200]} for l in lessons[-5:]]}

        if fn_name == "get_decision_log":
            from src.decision_log import get_recent_decisions
            decisions = get_recent_decisions(args.get("limit", 10))
            return {"decisions": [{"symbol": d.get("symbol", ""), "decision": d.get("decision", ""),
                     "reasons": d.get("reasons", [])[:3]} for d in decisions[-5:]]}

        return {"error": f"Unknown tool: {fn_name}"}

    def decide_sync(self, goal: str, role: str = "SCREENER", context: dict = None) -> Dict:
        """Synchronous wrapper dengan proper timeout."""
        if not self.is_ready():
            return {"decision": "WAIT", "rationale": "LLM not available", "confidence_adjustment": 0}
        
        with self._sync_lock:
            result = {"data": None}
            
            def _run():
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    result["data"] = loop.run_until_complete(
                        asyncio.wait_for(self.decide(goal, role, context), timeout=45.0)
                    )
                    loop.close()
                except asyncio.TimeoutError:
                    logger.error("[LLM] Sync decide timeout")
                    result["data"] = {"decision": "WAIT", "rationale": "LLM timeout", "confidence_adjustment": 0}
                except Exception as e:
                    logger.error(f"[LLM] Sync decide error: {e}")
                    result["data"] = {"decision": "WAIT", "rationale": f"Error: {e}", "confidence_adjustment": 0}
            
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=50)
            
            if t.is_alive():
                logger.error("[LLM] Thread join timeout")
                return {"decision": "WAIT", "rationale": "LLM thread timeout", "confidence_adjustment": 0}
            
            return result["data"] or {"decision": "WAIT", "rationale": "No result", "confidence_adjustment": 0}


_llm_brain: Optional[LLMBrain] = None


def get_llm_brain(model: str = None) -> LLMBrain:
    global _llm_brain
    if _llm_brain is None:
        _llm_brain = LLMBrain(model)
    return _llm_brain


def reset_llm_brain():
    """Reset singleton (useful for testing)."""
    global _llm_brain
    _llm_brain = None
    logger.info("[LLM] Brain reset")
