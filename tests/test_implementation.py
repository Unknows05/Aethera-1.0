#!/usr/bin/env python3
"""
Validation Test Suite untuk Implementation Phase 1
Tests: LLM Brain v2, Threshold Evolution, Performance Guards, HARD Blocks
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_llm_brain():
    """Test LLM Brain v2 improvements."""
    print("\n🧠 Testing LLM Brain v2...")
    
    try:
        from src.llm_brain import get_llm_brain, LLMBrain
        
        # Test instantiation
        brain = get_llm_brain()
        print(f"  ✅ LLM Brain instantiated")
        print(f"  📊 Ready: {brain.is_ready()}")
        print(f"  🤖 Model: {brain.model}")
        
        # Test new attributes
        assert hasattr(brain, '_request_lock'), "Missing _request_lock"
        assert hasattr(brain, '_session_fired_tools'), "Missing _session_fired_tools"
        assert hasattr(brain, '_min_request_interval'), "Missing rate limiting"
        print(f"  ✅ Rate limiting attributes present")
        
        # Test ONCE_PER_SESSION
        from src.llm_brain import ONCE_PER_SESSION
        print(f"  ✅ ONCE_PER_SESSION tools: {len(ONCE_PER_SESSION)}")
        
        # Test INTENT_PATTERNS
        from src.llm_brain import INTENT_PATTERNS
        print(f"  ✅ Intent patterns: {len(INTENT_PATTERNS)}")
        
        # Test jsonrepair availability
        from src.llm_brain import HAS_JSONREPAIR
        print(f"  {'✅' if HAS_JSONREPAIR else '⚠️'} JSON repair: {HAS_JSONREPAIR}")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False


def test_threshold_evolution():
    """Test Threshold Evolution engine."""
    print("\n🧬 Testing Threshold Evolution...")
    
    try:
        from src.threshold_evolution import ThresholdEvolution, PerformanceRecord
        
        # Test instantiation
        engine = ThresholdEvolution()
        print(f"  ✅ Evolution engine instantiated")
        
        # Test analyze with sample data
        sample_data = [
            PerformanceRecord(
                symbol="BTCUSDT", regime="SIDEWAYS", signal="LONG",
                result="LOSS", pnl_pct=-8.5, confidence=45,
                composite_score=52, entry_price=50000, exit_price=45750,
                held_hours=12, close_reason="stop_loss",
                timestamp="2026-05-10T10:00:00"
            ),
            PerformanceRecord(
                symbol="ETHUSDT", regime="BULL", signal="LONG",
                result="WIN", pnl_pct=15.2, confidence=72,
                composite_score=68, entry_price=3000, exit_price=3456,
                held_hours=24, close_reason="take_profit",
                timestamp="2026-05-10T12:00:00"
            ),
        ]
        
        result = engine.analyze_performance(sample_data)
        print(f"  ✅ Analysis completed")
        print(f"  📊 Changes: {len(result['changes'])}")
        print(f"  📊 Blocks: {len(result['blocks'])}")
        
        # Test combo analysis
        combos = engine._analyze_combos(sample_data)
        print(f"  ✅ Combo analysis: {len(combos)} combos")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_performance_guards():
    """Test Performance Guards."""
    print("\n🛡️ Testing Performance Guards...")
    
    try:
        from src.outcome_feedback import validate_performance_record
        
        # Test valid record
        valid = {
            'pnl_pct': -5.0,
            'held_hours': 12,
            'entry_price': 50000,
            'exit_price': 47500,
            'initial_value_usd': 5000,
            'final_value_usd': 4750,
        }
        is_valid, reason = validate_performance_record(valid)
        assert is_valid, f"Valid record rejected: {reason}"
        print(f"  ✅ Valid record accepted")
        
        # Test unit mix detection
        unit_mix = {
            'initial_value_usd': 100,
            'amount_sol': 2.0,
            'final_value_usd': 1.5,  # Looks like SOL amount
            'pnl_pct': -5,
            'held_hours': 10,
        }
        is_valid, reason = validate_performance_record(unit_mix)
        assert not is_valid, "Unit mix should be rejected"
        assert "Unit mix" in reason
        print(f"  ✅ Unit mix detection working: {reason[:50]}...")
        
        # Test absurd PnL detection
        absurd = {
            'pnl_pct': -95,
            'held_hours': 10,
            'entry_price': 50000,
            'exit_price': 2500,
            'initial_value_usd': 5000,
        }
        is_valid, reason = validate_performance_record(absurd)
        assert not is_valid, "Absurd PnL should be rejected"
        print(f"  ✅ Absurd PnL detection working")
        
        # Test negative held_hours
        negative_time = {
            'pnl_pct': -5,
            'held_hours': -5,
            'entry_price': 50000,
            'exit_price': 47500,
        }
        is_valid, reason = validate_performance_record(negative_time)
        assert not is_valid, "Negative time should be rejected"
        print(f"  ✅ Time sanity check working")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_hard_blocked_combos():
    """Test HARD_BLOCKED_COMBOS."""
    print("\n🚫 Testing HARD_BLOCKED_COMBOS...")
    
    try:
        from src.signals import HARD_BLOCKED_COMBOS
        
        # Test that blocks exist
        assert len(HARD_BLOCKED_COMBOS) > 0, "HARD_BLOCKED_COMBOS is empty!"
        print(f"  ✅ HARD_BLOCKED_COMBOS populated: {len(HARD_BLOCKED_COMBOS)} combos")
        
        # Test specific blocks
        sideways_long = HARD_BLOCKED_COMBOS.get(('SIDEWAYS', 'LONG'))
        assert sideways_long is not None, "SIDEWAYS+LONG should be blocked"
        assert 'wr' in sideways_long, "Missing WR data"
        print(f"  ✅ SIDEWAYS+LONG blocked (WR: {sideways_long['wr']}%)")
        
        highvol_short = HARD_BLOCKED_COMBOS.get(('HIGH_VOL', 'SHORT'))
        assert highvol_short is not None, "HIGH_VOL+SHORT should be blocked"
        print(f"  ✅ HIGH_VOL+SHORT blocked (WR: {highvol_short['wr']}%)")
        
        # Test block reason
        assert 'reason' in sideways_long, "Missing block reason"
        print(f"  ✅ Block reasons present")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False


def test_integration():
    """Test integration points."""
    print("\n🔗 Testing Integration...")
    
    try:
        # Test engine has evolution method
        from src.engine_v2 import ScreeningEngineV2
        assert hasattr(ScreeningEngineV2, 'run_threshold_evolution'), \
            "Engine missing run_threshold_evolution"
        print(f"  ✅ Engine has evolution method")
        
        # Test fallback model constant
        from src.llm_brain import FALLBACK_MODEL
        assert FALLBACK_MODEL is not None
        print(f"  ✅ Fallback model: {FALLBACK_MODEL}")
        
        # Test intent patterns
        from src.llm_brain import INTENT_PATTERNS
        test_goals = [
            ("screen for new opportunities", "screen"),
            ("buy BTC now", "trade"),
            ("close my position", "manage"),
            ("why did you enter", "analyze"),
        ]
        for goal, expected_intent in test_goals:
            matched = False
            for intent, pattern in INTENT_PATTERNS.items():
                if pattern.search(goal):
                    matched = True
                    break
            assert matched, f"Intent not matched for: {goal}"
        print(f"  ✅ Intent patterns working")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("🧪 COIN-SCREENER IMPLEMENTATION VALIDATION")
    print("=" * 60)
    
    results = {
        "LLM Brain v2": test_llm_brain(),
        "Threshold Evolution": test_threshold_evolution(),
        "Performance Guards": test_performance_guards(),
        "HARD_BLOCKED_COMBOS": test_hard_blocked_combos(),
        "Integration": test_integration(),
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
        print("\n🎉 All validations passed! Implementation is ready.")
        return 0
    else:
        print(f"\n⚠️ {total - passed} test(s) failed. Please review.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
