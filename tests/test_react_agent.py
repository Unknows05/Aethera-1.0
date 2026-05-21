#!/usr/bin/env python3
"""
Unit tests for ReAct agent system: tool definitions, executor, decision log,
symbol memory, lesson derivation, threshold evolution, skill engine, prompt builder.
"""
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Test 1: Tool Definitions ────────────────────────────────

def test_tool_definitions():
    """Tool definitions should have correct structure and role filtering."""
    print("\n🔧 Testing tool definitions...")
    from src.agents.tools.definitions import TOOLS, get_tools_for_role, SCREENER_TOOLS, MANAGER_TOOLS

    # All tools should have proper structure
    for t in TOOLS:
        assert "type" in t, f"Tool missing type: {t}"
        assert "function" in t, f"Tool missing function: {t}"
        assert "name" in t["function"], f"Tool missing name: {t}"
        assert "description" in t["function"], f"Tool missing description: {t}"
        assert "parameters" in t["function"], f"Tool missing parameters: {t}"

    # Role filtering should work
    screener = get_tools_for_role("SCREENER")
    manager = get_tools_for_role("MANAGER")
    general = get_tools_for_role("GENERAL")

    assert len(screener) > 5, f"Screener should have >5 tools, got {len(screener)}"
    assert len(manager) > 5, f"Manager should have >5 tools, got {len(manager)}"
    assert len(general) >= len(screener), "General should have all tools"

    # Screener should NOT have manager-specific tools
    screener_names = {t["function"]["name"] for t in screener}
    assert "close_position" not in screener_names, "Screener should not have close_position"
    assert "get_open_positions" not in screener_names, "Screener should not have get_open_positions"

    print(f"  ✅ {len(TOOLS)} tools defined, role filtering works")
    return True


# ── Test 2: Decision Log ────────────────────────────────────

def test_decision_log():
    """Decision log should record and retrieve decisions."""
    print("\n📋 Testing decision log...")
    from src.agents.decision_log import append_decision, get_recent_decisions, get_decision_summary

    orig_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    os.chdir(tmpdir)
    os.makedirs("data", exist_ok=True)

    try:
        # Append decisions
        d1 = append_decision("open", "SCREENER", "BTCUSDT", "Strong bull signal", "BULL LONG", "LONG", "BULL", 75)
        d2 = append_decision("close", "MANAGER", "ETHUSDT", "Take profit hit", "WIN", "LONG", "BULL", 80)
        d3 = append_decision("skip", "SCREENER", "SOLUSDT", "Low confidence", "LOW_CONF", "LONG", "CHOP", 30)

        # Retrieve recent
        recent = get_recent_decisions(limit=2)
        assert len(recent) == 2, f"Should get 2 recent, got {len(recent)}"

        # Get summary
        summary = get_decision_summary(limit=3)
        assert summary is not None, "Summary should not be None"
        assert "SCREENER" in summary, "Summary should contain actor"
        assert "BTCUSDT" in summary, "Summary should contain symbol"

        # Stats
        from src.agents.decision_log import get_actor_stats
        stats = get_actor_stats("SCREENER")
        assert stats["total"] == 2, f"SCREENER should have 2 decisions, got {stats['total']}"

        print(f"  ✅ Decision log: {stats['total']} SCREENER decisions recorded")
        return True
    finally:
        os.chdir(orig_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Test 3: Symbol Memory ───────────────────────────────────

def test_symbol_memory():
    """Symbol memory should track trades and generate stats."""
    print("\n💾 Testing symbol memory...")
    from src.agents.symbol_memory import record_trade, get_symbol_stats, format_for_prompt

    orig_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    os.chdir(tmpdir)
    os.makedirs("data", exist_ok=True)

    try:
        # Record trades
        record_trade("BTCUSDT", "LONG", "BULL", "WIN", 5.2, 75, 12.0, "target", "Strong breakout")
        record_trade("BTCUSDT", "LONG", "BULL", "WIN", 3.1, 70, 8.0, "target", "Momentum")
        record_trade("BTCUSDT", "LONG", "BULL", "LOSS", -2.5, 60, 4.0, "stop_loss", "False breakout")
        record_trade("BTCUSDT", "SHORT", "BEAR", "WIN", 4.0, 80, 6.0, "target", "Bearish divergence")

        # Get stats
        stats = get_symbol_stats("BTCUSDT")
        assert stats["exists"], "BTCUSDT should exist"
        assert stats["total_trades"] == 4, f"Should have 4 trades, got {stats['total_trades']}"
        assert stats["wins"] == 3, f"Should have 3 wins, got {stats['wins']}"
        assert stats["losses"] == 1, f"Should have 1 loss, got {stats['losses']}"
        assert stats["win_rate"] == 75.0, f"WR should be 75%, got {stats['win_rate']}"

        # Format for prompt
        prompt_text = format_for_prompt("BTCUSDT")
        assert prompt_text is not None, "Prompt text should not be None"
        assert "BTCUSDT" in prompt_text, "Should contain symbol"
        assert "75.0%" in prompt_text, "Should contain win rate"

        # Non-existent symbol
        empty = get_symbol_stats("NONEXISTENT")
        assert not empty["exists"], "Non-existent symbol should not exist"

        print(f"  ✅ Symbol memory: {stats['total_trades']} trades, {stats['win_rate']}% WR")
        return True
    finally:
        os.chdir(orig_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Test 4: Lesson Derivation ───────────────────────────────

def test_lesson_derivation():
    """Lesson derivation should generate lessons from trades."""
    print("\n📝 Testing lesson derivation...")
    from src.agents.lesson_deriver import derive_lesson

    # Good trade
    good = derive_lesson({
        "symbol": "BTCUSDT", "signal": "LONG", "regime": "BULL",
        "result": "WIN", "pnl_pct": 8.5, "confidence": 75,
        "held_hours": 12.0, "exit_reason": "target", "reason": "Strong breakout",
    })
    assert good is not None, "Should derive lesson from good trade"
    assert good["outcome"] == "good", f"Outcome should be good, got {good['outcome']}"
    assert "WORKED" in good["rule"], "Rule should contain WORKED"

    # Bad trade
    bad = derive_lesson({
        "symbol": "ETHUSDT", "signal": "SHORT", "regime": "BEAR",
        "result": "LOSS", "pnl_pct": -8.0, "confidence": 80,
        "held_hours": 0.5, "exit_reason": "stop_loss", "reason": "False breakdown",
    })
    assert bad is not None, "Should derive lesson from bad trade"
    assert bad["outcome"] == "bad", f"Outcome should be bad, got {bad['outcome']}"
    assert "CAUTION" in bad["rule"] or "AVOID" in bad["rule"], "Rule should contain warning"

    # Neutral trade
    neutral = derive_lesson({
        "symbol": "SOLUSDT", "signal": "LONG", "regime": "CHOP",
        "result": "WIN", "pnl_pct": 0.5, "confidence": 50,
        "held_hours": 2.0, "exit_reason": "", "reason": "",
    })
    assert neutral is None, "Should NOT derive lesson from neutral trade"

    print(f"  ✅ Lesson derivation: good={good['outcome']}, bad={bad['outcome']}")
    return True


# ── Test 5: Threshold Evolution ─────────────────────────────

def test_threshold_evolution():
    """Threshold evolution should adjust params from performance data."""
    print("\n📊 Testing threshold evolution...")
    from src.agents.threshold_evolution import evolve_thresholds, get_thresholds, get_threshold_summary

    orig_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    os.chdir(tmpdir)
    os.makedirs("data", exist_ok=True)

    try:
        # Not enough data
        small = [
            {"signal": "LONG", "regime": "BULL", "pnl_pct": 5, "confidence": 70},
            {"signal": "LONG", "regime": "BULL", "pnl_pct": -2, "confidence": 60},
        ]
        result = evolve_thresholds(small)
        assert result is None, "Should not evolve with too few trades"

        # Enough data with clear signal
        trades = []
        for i in range(15):
            trades.append({
                "signal": "LONG", "regime": "BULL",
                "pnl_pct": 5 + i, "confidence": 70 + i,
            })
        for i in range(10):
            trades.append({
                "signal": "SHORT", "regime": "BEAR",
                "pnl_pct": -5 - i, "confidence": 40 + i,
            })

        result = evolve_thresholds(trades)
        if result:
            assert "changes" in result, "Result should have changes"
            print(f"  ✅ Threshold evolution: {len(result['changes'])} changes")
        else:
            print("  ✅ Threshold evolution: no changes needed (valid)")

        # Get thresholds
        thresholds = get_thresholds()
        assert "min_confidence" in thresholds, "Should have min_confidence"

        # Get summary
        summary = get_threshold_summary()
        assert summary is not None, "Summary should not be None"
        assert "Min confidence" in summary, "Summary should contain confidence"

        return True
    finally:
        os.chdir(orig_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Test 6: Skill Engine ────────────────────────────────────

def test_skill_engine():
    """Skill engine should create, retrieve, and update skills."""
    print("\n🎯 Testing skill engine...")
    from src.agents.skill_engine import (
        create_skill, get_skills, set_active_skill, get_active_skill,
        update_skill_performance, format_for_prompt, init_default_skills,
    )

    orig_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    os.chdir(tmpdir)
    os.makedirs("data", exist_ok=True)

    try:
        # Init defaults first
        init_default_skills()
        all_skills = get_skills()
        assert len(all_skills) >= 3, f"Should have at least 3 default skills, got {len(all_skills)}"

        # Create additional skill
        skill = create_skill(
            name="Test BULL LONG",
            regime="BULL",
            signal="LONG",
            entry_rules={"min_confidence": 65, "require_regime": "BULL"},
            exit_rules={"take_profit_pct": 8, "stop_loss_pct": -4},
            risk_params={"max_position_size_pct": 5},
            description="Test skill",
        )
        assert skill["name"] == "Test BULL LONG", "Skill name should match"
        assert skill["regime"] == "BULL", "Regime should match"

        # Get skills
        skills = get_skills(regime="BULL", signal="LONG")
        assert len(skills) >= 1, "Should find at least 1 skill"

        # Set active
        assert set_active_skill("Test BULL LONG"), "Should set active skill"
        active = get_active_skill()
        assert active is not None, "Active skill should exist"
        assert active["name"] == "Test BULL LONG", "Active skill name should match"

        # Update performance
        update_skill_performance("Test BULL LONG", "WIN", 5.0)
        update_skill_performance("Test BULL LONG", "WIN", 3.0)
        update_skill_performance("Test BULL LONG", "LOSS", -2.0)

        updated = get_skills(regime="BULL")[0]
        assert updated["trade_count"] == 3, f"Should have 3 trades, got {updated['trade_count']}"
        assert updated["win_rate"] == 66.7, f"WR should be 66.7%, got {updated['win_rate']}"

        # Format for prompt
        prompt = format_for_prompt()
        assert prompt is not None, "Prompt should not be None"
        assert "Test BULL LONG" in prompt, "Should contain skill name"

        all_skills = get_skills()
        assert len(all_skills) >= 4, f"Should have at least 4 skills (3 default + 1 test), got {len(all_skills)}"

        print(f"  ✅ Skill engine: {len(all_skills)} skills, active={active['name']}")
        return True
    finally:
        os.chdir(orig_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Test 7: Prompt Builder ──────────────────────────────────

def test_prompt_builder():
    """Prompt builder should create role-specific prompts."""
    print("\n📝 Testing prompt builder...")
    from src.agents.prompt_builder import build_system_prompt

    # SCREENER prompt
    screener = build_system_prompt(
        role="SCREENER",
        portfolio={"balance": 1000, "equity": 1050, "open_positions": 0},
        lessons="- [good] BULL LONG works well",
    )
    assert "SCREENER" in screener, "Should contain role"
    assert "SCREENER INSTRUCTIONS" in screener, "Should have screener instructions"
    assert "PORTFOLIO" in screener, "Should contain portfolio"
    assert "LESSONS" in screener, "Should contain lessons"

    # MANAGER prompt
    manager = build_system_prompt(
        role="MANAGER",
        portfolio={"balance": 1000, "open_positions": 2},
        positions=[
            {"symbol": "BTCUSDT", "signal": "LONG", "pnl_pct": 3.5, "held_hours": 12, "confidence": 75},
        ],
    )
    assert "MANAGER" in manager, "Should contain role"
    assert "MANAGER INSTRUCTIONS" in manager, "Should have manager instructions"
    assert "BTCUSDT" in manager, "Should contain position"

    # GENERAL prompt
    general = build_system_prompt(role="GENERAL")
    assert "GENERAL" in general, "Should contain role"
    assert "GENERAL INSTRUCTIONS" in general, "Should have general instructions"

    print("  ✅ Prompt builder: SCREENER, MANAGER, GENERAL prompts work")
    return True


# ── Test 8: Tool Executor ───────────────────────────────────

def test_tool_executor():
    """Tool executor should register and dispatch tools."""
    print("\n⚡ Testing tool executor...")
    from src.agents.tools.executor import ToolExecutor

    executor = ToolExecutor(engine=None, vault_search=None, hivemind=None, trader=None)
    names = executor.get_tool_names()

    assert len(names) > 10, f"Should have >10 tools, got {len(names)}"
    assert executor.has_tool("get_scan_results"), "Should have get_scan_results"
    assert executor.has_tool("open_position"), "Should have open_position"
    assert executor.has_tool("close_position"), "Should have close_position"
    assert not executor.has_tool("nonexistent"), "Should not have nonexistent tool"

    # Test error handling for unknown tool
    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        executor.execute("nonexistent", {})
    )
    assert not result["success"], "Unknown tool should fail"
    assert "Unknown tool" in result["error"], "Should have error message"

    print(f"  ✅ Tool executor: {len(names)} tools registered")
    return True


# ── Main ────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("🧪 AETHERA V1.6 — REACT AGENT SYSTEM TESTS")
    print("=" * 60)

    results = {
        "Tool definitions": test_tool_definitions(),
        "Decision log": test_decision_log(),
        "Symbol memory": test_symbol_memory(),
        "Lesson derivation": test_lesson_derivation(),
        "Threshold evolution": test_threshold_evolution(),
        "Skill engine": test_skill_engine(),
        "Prompt builder": test_prompt_builder(),
        "Tool executor": test_tool_executor(),
    }

    print("\n" + "=" * 60)
    print("📊 TEST RESULTS")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {name}")

    print(f"\n🎯 Summary: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠️ {total - passed} test(s) failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
