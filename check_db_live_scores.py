"""
Quick script to check if live_score is being updated in MongoDB.
Run this to diagnose why scores show as zero.

Usage:
    python check_db_live_scores.py
"""
from pymongo import MongoClient
from datetime import datetime

# Update this if your MongoDB connection string is different
MONGODB_URI = "mongodb://localhost:27017/"
DB_NAME = "trading_bot"

def main():
    try:
        client = MongoClient(MONGODB_URI)
        db = client[DB_NAME]
        
        print("=" * 80)
        print("LIVE SCORE DATABASE CHECK")
        print("=" * 80)
        
        # 1. Check strategies collection
        print("\n1. Strategies (sorted by live_score):")
        print("-" * 40)
        strategies = list(db.strategies.find(
            {},
            {"name": 1, "live_score": 1, "is_active": 1, "updated_at": 1, "live_score_params_hash": 1}
        ).sort("live_score", -1))
        
        all_zero = True
        for s in strategies:
            score = s.get("live_score", 0) or 0
            if score != 0:
                all_zero = False
            
            status = "✓ ACTIVE" if s.get("is_active") else "  inactive"
            updated = s.get("updated_at", "unknown")
            params_hash = s.get("live_score_params_hash", "none")[:8]
            
            print(f"  {s['name']:<20} live_score: {score:>7.3f}  {status}  hash:{params_hash}")
        
        if all_zero:
            print("\n  ⚠️  ALL live_scores are 0 - this is the problem!")
        else:
            print("\n  ✓ Some strategies have non-zero scores - feedback is working!")
        
        # 2. Check trade status
        print("\n2. Trade Status Counts:")
        print("-" * 40)
        total_trades = db.trades.count_documents({})
        open_trades = db.trades.count_documents({"result": None})
        win_trades = db.trades.count_documents({"result": "WIN"})
        loss_trades = db.trades.count_documents({"result": "LOSS"})
        closed_trades = win_trades + loss_trades
        
        print(f"  Total trades: {total_trades}")
        print(f"  OPEN: {open_trades}")
        print(f"  WIN: {win_trades}")
        print(f"  LOSS: {loss_trades}")
        print(f"  CLOSED (WIN+LOSS): {closed_trades}")
        
        if closed_trades == 0:
            print("\n  ❌ NO CLOSED TRADES - scores cannot update until trades close!")
            print("     Check if:")
            print("     - MT5 is actually closing positions")
            print("     - Webhook/position_stream is receiving close events")
            return
        
        # 3. Check recent closed trades and their decisions
        print("\n3. Recent Closed Trades (last 5):")
        print("-" * 40)
        recent_closed = list(db.trades.find(
            {"result": {"$in": ["WIN", "LOSS"]}},
            {
                "_id": 1, "strategy_name": 1, "result": 1, "direction": 1,
                "pnl": 1, "entry_price": 1, "exit_price": 1, "stop_loss": 1, "closed_at": 1
            }
        ).sort("closed_at", -1).limit(5))
        
        decisions_missing = 0
        decisions_no_votes = 0
        
        for t in recent_closed:
            trade_id = t["_id"]
            decision = db.ensemble_decisions.find_one(
                {"trade_id": trade_id},
                {"strategy_votes_json": 1}
            )
            
            if decision:
                votes = decision.get("strategy_votes_json", [])
                if votes:
                    status = f"✓ Decision OK ({len(votes)} votes)"
                else:
                    status = "✗ Decision exists but NO VOTES"
                    decisions_no_votes += 1
            else:
                status = "✗ NO DECISION"
                decisions_missing += 1
            
            entry = t.get("entry_price", 0)
            exit_p = t.get("exit_price", 0)
            sl = t.get("stop_loss", 0)
            pnl = t.get("pnl", 0)
            
            print(f"  Trade #{trade_id}: {t['result']} {t['direction']} "
                  f"pnl={pnl:.2f} | {status}")
            print(f"    entry={entry:.5f} exit={exit_p:.5f} sl={sl:.5f}")
        
        print(f"\n  Summary: {decisions_missing} missing, {decisions_no_votes} no votes")
        
        if decisions_missing > 0:
            print("\n  ❌ PROBLEM: EnsembleDecisions are missing for closed trades!")
            print("     This breaks score feedback - check orchestrator.py line 1444")
        
        if decisions_no_votes > 0:
            print("\n  ❌ PROBLEM: EnsembleDecisions have no strategy_votes_json!")
            print("     Check that voter_breakdown is passed to _log_ensemble_decision()")
        
        # 4. Check settings
        print("\n4. Score Feedback Settings:")
        print("-" * 40)
        enabled = db.app_settings.find_one({"key": "score_feedback_enabled"})
        alpha = db.app_settings.find_one({"key": "score_feedback_alpha"})
        bound = db.app_settings.find_one({"key": "score_feedback_score_bound"})
        
        enabled_val = enabled.get("value", "true") if enabled else "true"
        alpha_val = alpha.get("value", "0.2") if alpha else "0.2"
        bound_val = bound.get("value", "3.0") if bound else "3.0"
        
        print(f"  score_feedback_enabled: {enabled_val}")
        print(f"  score_feedback_alpha: {alpha_val}")
        print(f"  score_feedback_score_bound: {bound_val}")
        
        if str(enabled_val).lower() not in ("1", "true", "yes", "on"):
            print(f"\n  ❌ PROBLEM: Score feedback is DISABLED! (value='{enabled_val}')")
            print("     Enable it via MongoDB or API settings")
        
        # 5. Sample decision inspection
        if recent_closed and decisions_missing == 0:
            print("\n5. Sample EnsembleDecision (most recent):")
            print("-" * 40)
            sample_trade_id = recent_closed[0]["_id"]
            decision = db.ensemble_decisions.find_one(
                {"trade_id": sample_trade_id},
                {"strategy_votes_json": 1, "resolved_direction": 1}
            )
            
            if decision:
                print(f"  Trade #{sample_trade_id} - Direction: {decision.get('resolved_direction')}")
                votes = decision.get("strategy_votes_json", [])
                print(f"  Total strategies: {len(votes)}")
                print(f"  Sample votes:")
                for vote in votes[:3]:
                    print(f"    - {vote.get('strategy_name')}: {vote.get('direction')} "
                          f"conf={vote.get('raw_confidence', 0):.2f} "
                          f"weight={vote.get('weight', 0):.3f}")
        
        # 6. Diagnosis
        print("\n" + "=" * 80)
        print("DIAGNOSIS:")
        print("=" * 80)
        
        issues = []
        
        if closed_trades == 0:
            issues.append("No closed trades - scores can't update")
        
        if all_zero and closed_trades > 0:
            issues.append("Scores are zero despite closed trades existing")
        
        if decisions_missing > 0:
            issues.append(f"{decisions_missing} trades missing EnsembleDecisions")
        
        if decisions_no_votes > 0:
            issues.append(f"{decisions_no_votes} decisions have no strategy votes")
        
        if str(enabled_val).lower() not in ("1", "true", "yes", "on"):
            issues.append("Score feedback is disabled in settings")
        
        if issues:
            print("\n❌ ISSUES FOUND:")
            for i, issue in enumerate(issues, 1):
                print(f"  {i}. {issue}")
            
            print("\nRECOMMENDATIONS:")
            if closed_trades == 0:
                print("  → Wait for trades to close or check MT5 connection")
            if decisions_missing > 0:
                print("  → Check logs for errors in orchestrator.py _log_ensemble_decision()")
            if decisions_no_votes > 0:
                print("  → Verify voter_breakdown is passed to _log_ensemble_decision()")
            if all_zero and closed_trades > 0 and decisions_missing == 0:
                print("  → Check application logs for errors in score_feedback.py")
                print("  → Verify run_trade_close_hooks() is being called")
            if str(enabled_val).lower() not in ("1", "true", "yes", "on"):
                print("  → Enable score feedback in app_settings")
        else:
            print("\n✓ No obvious issues found!")
            if not all_zero:
                print("  Score feedback is working correctly.")
            else:
                print("  If scores are still zero, check:")
                print("  - Application logs for Python exceptions")
                print("  - That trades are actually closing (not just opening)")
        
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        print("\nMake sure:")
        print("  1. MongoDB is running")
        print("  2. Connection string is correct")
        print("  3. Database name is 'trading_bot'")

if __name__ == "__main__":
    main()
