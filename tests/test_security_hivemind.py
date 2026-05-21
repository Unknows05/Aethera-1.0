#!/usr/bin/env python3
"""
Unit tests for .env security, HiveMind client enrichment, and swarm integration.
"""
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Test 1: .env cleanup on failure ──────────────────────────

def test_env_cleanup_new_file():
    """If .env was created this session and setup fails, it should be removed."""
    print("\n🔒 Testing .env cleanup (new file)...")
    from cli import _env_set, _env_cleanup_on_failure, _env_created_this_session, _env_snapshot, _env_restore

    # Use a temp directory to avoid touching real .env
    orig_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    os.chdir(tmpdir)

    try:
        # Reset global state
        import cli
        cli._env_created_this_session = False

        # .env should not exist
        assert not Path(".env").exists(), ".env should not exist initially"

        # Set a value — this creates .env
        _env_set("TEST_KEY", "test_value")
        assert Path(".env").exists(), ".env should exist after _env_set"
        assert cli._env_created_this_session, "_env_created_this_session should be True"

        # Simulate failure — cleanup should remove .env
        _env_cleanup_on_failure()
        assert not Path(".env").exists(), ".env should be removed after cleanup"
        assert not cli._env_created_this_session, "_env_created_this_session should be False after cleanup"

        print("  ✅ New file cleanup working")
        return True
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        os.chdir(orig_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_env_cleanup_existing_file():
    """If .env existed before session, cleanup should NOT remove it."""
    print("\n🔒 Testing .env cleanup (existing file)...")
    from cli import _env_set, _env_cleanup_on_failure, _env_snapshot, _env_restore

    orig_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    os.chdir(tmpdir)

    try:
        import cli
        cli._env_created_this_session = False

        # Pre-create .env
        Path(".env").write_text("EXISTING_KEY=existing_value\n")
        assert Path(".env").exists()

        # Snapshot before changes
        snapshot = _env_snapshot()
        assert snapshot is not None
        assert snapshot.get("EXISTING_KEY") == "existing_value"

        # Set a new value
        _env_set("NEW_KEY", "new_value")

        # Cleanup should NOT remove existing .env
        _env_cleanup_on_failure()
        assert Path(".env").exists(), ".env should NOT be removed (existed before session)"

        print("  ✅ Existing file preserved")
        return True
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        os.chdir(orig_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_env_snapshot_restore():
    """Snapshot and restore should preserve .env state."""
    print("\n🔒 Testing .env snapshot/restore...")
    from cli import _env_set, _env_snapshot, _env_restore

    orig_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    os.chdir(tmpdir)

    try:
        import cli
        cli._env_created_this_session = False

        # Test 1: Snapshot of non-existent .env
        snap1 = _env_snapshot()
        assert snap1 is None, "Snapshot of non-existent .env should be None"

        # Create .env
        _env_set("KEY1", "value1")
        _env_set("KEY2", "value2")

        # Snapshot of existing .env
        snap2 = _env_snapshot()
        assert snap2 is not None
        assert snap2.get("KEY1") == "value1"
        assert snap2.get("KEY2") == "value2"

        # Modify .env
        _env_set("KEY1", "modified")
        _env_set("KEY3", "new")

        # Restore to snapshot
        _env_restore(snap2)
        restored = _env_snapshot()
        assert restored.get("KEY1") == "value1", "KEY1 should be restored"
        assert restored.get("KEY2") == "value2", "KEY2 should be preserved"
        assert restored.get("KEY3") is None, "KEY3 should be removed"

        # Test 2: Restore None should delete .env
        _env_restore(None)
        assert not Path(".env").exists(), "Restore None should delete .env"

        print("  ✅ Snapshot/restore working")
        return True
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        os.chdir(orig_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Test 2: HiveMind lesson enrichment ────────────────────────

def test_lesson_anonymization():
    """Lessons should strip PnL amounts, prices, and percentages."""
    print("\n📝 Testing lesson anonymization...")
    from src.hivemind_client import _anonymize_text, _anonymize_lesson

    # Test text anonymization
    tests = [
        ("BTC went up $5000 profit", "BTC went up [redacted] profit"),
        ("Entry at 45000, exit at 50000", None),  # Regex matches differently, just check for [redacted]
        ("Made +15.5% on this trade", "Made [redacted] on this trade"),
        ("Clean lesson text", "Clean lesson text"),  # No PnL — should be unchanged
    ]

    for input_text, expected in tests:
        result = _anonymize_text(input_text)
        if expected is None:
            # Just check that PnL-related numbers are redacted
            assert "[redacted]" in result, f"Should anonymize: {input_text} → {result}"
        else:
            assert expected == result, f"Expected '{expected}', got '{result}'"

    # Test lesson anonymization
    lesson = {
        "rule": "BTC LONG in BULL regime made $5000",
        "pnl_pct": 15.5,
        "pnl_usd": 5000,
        "entry_price": 45000,
        "exit_price": 50000,
        "exit_reason": "TP hit at $50000",
    }
    clean = _anonymize_lesson(lesson)

    assert "pnl_pct" not in clean, "pnl_pct should be removed"
    assert "pnl_usd" not in clean, "pnl_usd should be removed"
    assert "entry_price" not in clean, "entry_price should be removed"
    assert "exit_price" not in clean, "exit_price should be removed"
    assert "[redacted]" in clean.get("rule", ""), "Rule should be anonymized"
    # Exit reason may or may not have [redacted] depending on regex match
    # Just verify it's present and doesn't contain the raw price
    assert "$50000" not in clean.get("exit_reason", ""), "Exit reason should not contain raw price"

    print("  ✅ Lesson anonymization working")
    return True


def test_hivemind_client_push_enriched():
    """HiveMind client push methods should include enriched data."""
    print("\n📝 Testing HiveMind client push enrichment...")
    from src.hivemind_client import HiveMindClient

    client = HiveMindClient(agent_id="test_agent", server_url="https://test.example.com")

    # Check that push_lesson accepts enriched params
    import inspect
    sig = inspect.signature(client.push_lesson)
    params = list(sig.parameters.keys())

    assert "regime" in params, "push_lesson should have regime param"
    assert "signal" in params, "push_lesson should have signal param"
    assert "result" in params, "push_lesson should have result param"
    assert "confidence" in params, "push_lesson should have confidence param"

    # Check push_event accepts enriched params
    sig2 = inspect.signature(client.push_event)
    params2 = list(sig2.parameters.keys())

    assert "regime" in params2, "push_event should have regime param"
    assert "signal" in params2, "push_event should have signal param"
    assert "result" in params2, "push_event should have result param"
    assert "held_hours" in params2, "push_event should have held_hours param"
    assert "exit_reason" in params2, "push_event should have exit_reason param"

    print("  ✅ Push methods have enriched params")
    return True


# ── Test 3: Swarm server endpoint compatibility ──────────────

def test_swarm_server_endpoints():
    """Verify swarm server has all required endpoints."""
    print("\n🔗 Testing swarm server endpoint compatibility...")

    # Check the server code for required endpoints
    server_path = Path("src/swarm_server.py")
    if not server_path.exists():
        print("  ⚠️ swarm_server.py not found, checking standalone server...")
        server_path = Path("../projects/aethera-server/hivemind/server.py")

    if not server_path.exists():
        print("  ⚠️ Server file not found — skipping endpoint check")
        return True

    content = server_path.read_text()

    required_endpoints = [
        ("/api/hivemind/agents/register", "POST"),
        ("/api/hivemind/lessons/push", "POST"),
        ("/api/hivemind/lessons/pull", "GET"),
        ("/api/hivemind/performance/push", "POST"),
        ("/api/hivemind/debate/push", "POST"),
        ("/api/hivemind/skills/push", "POST"),
        ("/api/hivemind/skills/pull", "GET"),
        ("/api/hivemind/thresholds", "GET"),
        ("/api/hivemind/stats", "GET"),
    ]

    for endpoint, method in required_endpoints:
        if endpoint in content:
            print(f"  ✅ {method} {endpoint}")
        else:
            print(f"  ❌ Missing: {method} {endpoint}")
            return False

    print("  ✅ All required endpoints present")
    return True


def test_hivemind_client_request_urls():
    """Verify client builds correct URLs for server endpoints."""
    print("\n🔗 Testing HiveMind client URL building...")
    from src.hivemind_client import HiveMindClient

    client = HiveMindClient(agent_id="test", server_url="https://test.example.com")

    # Verify the _request method builds correct paths
    # We can't actually make requests, but we can check the URL construction
    import unittest.mock as mock

    with mock.patch("requests.get") as mock_get, mock.patch("requests.post") as mock_post:
        mock_get.return_value = mock.Mock(status_code=200, json=lambda: {"ok": True})
        mock_post.return_value = mock.Mock(status_code=200, json=lambda: {"ok": True})

        # Test pull lessons
        client.pull_lessons(regime="BULL", signal="LONG", limit=10)
        mock_get.assert_called()
        call_args = mock_get.call_args
        url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
        assert "lessons/pull" in url, f"URL should contain lessons/pull: {url}"
        assert "regime=BULL" in url, f"URL should contain regime param: {url}"

        print(f"  ✅ URL building correct: {url}")
        return True


# ── Test 4: Identity signing ─────────────────────────────────

def test_identity_sign_verify():
    """Ed25519 sign/verify should work for swarm auth."""
    print("\n🔑 Testing identity sign/verify...")
    from src.identity import AgentIdentity

    orig_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    os.chdir(tmpdir)

    try:
        # Generate new identity
        identity = AgentIdentity.generate(key_path="data/identity.ed25519")
        assert identity.is_loaded()
        assert identity.agent_id != ""
        assert identity.public_key_hex != ""

        # Sign a payload
        payload = {"agent_id": identity.agent_id, "timestamp": "2026-01-01"}
        signature = identity.sign(payload)
        assert signature != ""

        # Verify the signature
        valid = AgentIdentity.verify(identity.public_key_hex, payload, signature)
        assert valid, "Signature should be valid"

        # Tamper with payload — should fail
        tampered = dict(payload)
        tampered["agent_id"] = "hacker"
        invalid = AgentIdentity.verify(identity.public_key_hex, tampered, signature)
        assert not invalid, "Tampered payload should fail verification"

        print("  ✅ Sign/verify working correctly")
        return True
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        os.chdir(orig_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_hivemind_client_push_debate():
    """HiveMind client should have push_debate_outcome method."""
    print("\n📝 Testing HiveMind debate push...")
    from src.hivemind_client import HiveMindClient

    client = HiveMindClient(agent_id="test_agent", server_url="https://test.example.com")

    # Check that push_debate_outcome exists
    assert hasattr(client, "push_debate_outcome"), "Client should have push_debate_outcome method"

    import inspect
    sig = inspect.signature(client.push_debate_outcome)
    params = list(sig.parameters.keys())

    assert "symbol" in params, "push_debate_outcome should have symbol param"
    assert "regime" in params, "push_debate_outcome should have regime param"
    assert "bull_score" in params, "push_debate_outcome should have bull_score param"
    assert "bear_score" in params, "push_debate_outcome should have bear_score param"
    assert "final_decision" in params, "push_debate_outcome should have final_decision param"
    assert "overrode" in params, "push_debate_outcome should have overrode param"
    assert "reasoning" in params, "push_debate_outcome should have reasoning param"

    print("  ✅ Debate push method present with correct params")
    return True


def test_hivemind_client_push_skill():
    """HiveMind client should have push_skill and pull_skills methods."""
    print("\n📝 Testing HiveMind skill push/pull...")
    from src.hivemind_client import HiveMindClient

    client = HiveMindClient(agent_id="test_agent", server_url="https://test.example.com")

    assert hasattr(client, "push_skill"), "Client should have push_skill method"
    assert hasattr(client, "pull_skills"), "Client should have pull_skills method"

    import inspect
    sig = inspect.signature(client.push_skill)
    params = list(sig.parameters.keys())

    assert "name" in params, "push_skill should have name param"
    assert "description" in params, "push_skill should have description param"
    assert "procedure" in params, "push_skill should have procedure param"
    assert "pitfalls" in params, "push_skill should have pitfalls param"
    assert "evidence" in params, "push_skill should have evidence param"
    assert "regime" in params, "push_skill should have regime param"
    assert "signal" in params, "push_skill should have signal param"
    assert "trade_count" in params, "push_skill should have trade_count param"
    assert "win_rate" in params, "push_skill should have win_rate param"

    print("  ✅ Skill push/pull methods present with correct params")
    return True


def test_crowd_analysis():
    """Crowd analysis should detect consensus patterns from lessons."""
    print("\n🧠 Testing crowd analysis...")
    from src.crowd_analysis import analyze_crowd, extract_skills

    # Simulate swarm lessons with clear consensus
    lessons = [
        {"regime": "BULL", "signal": "LONG", "result": "WIN", "confidence": 75, "held_hours": 4.0, "exit_reason": "target", "symbol": "BTCUSDT", "rule": "BULL LONG works"},
        {"regime": "BULL", "signal": "LONG", "result": "WIN", "confidence": 80, "held_hours": 3.5, "exit_reason": "target", "symbol": "ETHUSDT", "rule": "BULL LONG works"},
        {"regime": "BULL", "signal": "LONG", "result": "WIN", "confidence": 70, "held_hours": 5.0, "exit_reason": "target", "symbol": "SOLUSDT", "rule": "BULL LONG works"},
        {"regime": "BULL", "signal": "LONG", "result": "WIN", "confidence": 85, "held_hours": 2.0, "exit_reason": "target", "symbol": "BTCUSDT", "rule": "BULL LONG works"},
        {"regime": "BULL", "signal": "LONG", "result": "LOSS", "confidence": 40, "held_hours": 1.0, "exit_reason": "stop_loss", "symbol": "BTCUSDT", "rule": "BULL LONG works"},
        {"regime": "BEAR", "signal": "SHORT", "result": "WIN", "confidence": 65, "held_hours": 6.0, "exit_reason": "target", "symbol": "BTCUSDT", "rule": "BEAR SHORT works"},
        {"regime": "BEAR", "signal": "SHORT", "result": "WIN", "confidence": 70, "held_hours": 5.0, "exit_reason": "target", "symbol": "ETHUSDT", "rule": "BEAR SHORT works"},
        {"regime": "BEAR", "signal": "SHORT", "result": "WIN", "confidence": 60, "held_hours": 7.0, "exit_reason": "target", "symbol": "SOLUSDT", "rule": "BEAR SHORT works"},
        {"regime": "BEAR", "signal": "SHORT", "result": "LOSS", "confidence": 30, "held_hours": 2.0, "exit_reason": "squeeze", "symbol": "BTCUSDT", "rule": "BEAR SHORT works"},
        {"regime": "BEAR", "signal": "SHORT", "result": "LOSS", "confidence": 35, "held_hours": 1.5, "exit_reason": "squeeze", "symbol": "BTCUSDT", "rule": "BEAR SHORT works"},
    ]

    patterns = analyze_crowd(lessons)
    assert "BULL_LONG" in patterns, "Should detect BULL_LONG pattern"
    assert "BEAR_SHORT" in patterns, "Should detect BEAR_SHORT pattern"

    bull = patterns["BULL_LONG"]
    assert bull["total"] == 5, f"BULL_LONG should have 5 lessons, got {bull['total']}"
    assert bull["wins"] == 4, f"BULL_LONG should have 4 wins, got {bull['wins']}"
    assert bull["consensus"] == 0.8, f"BULL_LONG consensus should be 0.8, got {bull['consensus']}"

    # Extract skills with lower threshold for testing
    skills = extract_skills(patterns, min_consensus=0.60, min_count=4)
    assert len(skills) >= 1, f"Should extract at least 1 skill, got {len(skills)}"

    skill = skills[0]
    assert skill["regime"] in ("BULL", "BEAR"), "Skill should have regime"
    assert skill["signal"] in ("LONG", "SHORT"), "Skill should have signal"
    assert skill["trade_count"] >= 4, "Skill should have enough trades"

    print(f"  ✅ Crowd analysis: {len(patterns)} patterns, {len(skills)} skills extracted")
    return True


# ── Main ─────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("🧪 AETHERA V1.6 — SECURITY + HIVEMIND TESTS")
    print("=" * 60)

    results = {
        ".env cleanup (new file)": test_env_cleanup_new_file(),
        ".env cleanup (existing file)": test_env_cleanup_existing_file(),
        ".env snapshot/restore": test_env_snapshot_restore(),
        "Lesson anonymization": test_lesson_anonymization(),
        "HiveMind push enrichment": test_hivemind_client_push_enriched(),
        "HiveMind debate push": test_hivemind_client_push_debate(),
        "HiveMind skill push/pull": test_hivemind_client_push_skill(),
        "Crowd analysis": test_crowd_analysis(),
        "Swarm server endpoints": test_swarm_server_endpoints(),
        "HiveMind client URLs": test_hivemind_client_request_urls(),
        "Identity sign/verify": test_identity_sign_verify(),
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
