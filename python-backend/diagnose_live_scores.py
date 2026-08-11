"""
Diagnostic endpoint to check why live_score (R-EWMA) is not updating.
Run this from within the FastAPI app context.
"""
from app.db import get_db
from app import crud
from app.models import Trade, EnsembleDecision
from datetime import datetime, timedelta

def diagnose():
    db = next(get_db())
    
    print("=" * 80)
    print("DIAGNOSTIC: Live Score Feedback")
    print("=" * 80)
    
    # 1. Check if score_feedback is enabled
    print("\n1. Score Feedback Configuration:")
    print("-" * 40)
    enabled = crud.get_setting(db, "score_feedback_enabled") or "true"
    alpha = crud.get_setting(db, "score_feedback_alpha") or "0.2"
    bound = crud.get_setting(db, "score_feedback_score_bound") or "3.0"
    print(f"   score_feedback_enabled: {enabled}")
    print(f"   score_feedback_alpha: {alpha}")
    print(f"   score_feedback_score_bound: {bound}")
    
    if str(enabled).lower() not in ("1", "true", "yes", "on"):
        print(f"\n   ❌ PROBLEM: Score feedback is DISABLED! (value='{enabled}')")
    
    # 2. Check recent trades
    print("\n2. Recent Closed Trades (last 10):")
    print("-" * 40)
    from app.db import COLL_TRADES
    trades = list(db[COLL_TRADES].find(
        {"result": {"$in": ["WIN", "LOSS"]}},
        {"_id": 1, "strategy_name": 1, "direction": 1, "result": 1, "closed_at": 1, "pnl": 1, "entry_price": 1, "exit_price": 1, "stop_loss": 1}
    ).sort("closed_at", -1).limit(10))
    
    if not trades:
        print("   ❌ NO CLOSED TRADES FOUND!")
        print("   Possible causes:")
        print("   - Trades are not closing (check MT5 connection)")
        print("   - Webhook/position_stream not updating trade results")
        print("   - Check for OPEN trades that should be closed")
        
        # Check for OPEN trades
        open_count = db[COLL_TRADES].count_documents({"result": None})
        print(f"\n   Found {open_count} OPEN trades")
    else:
        print(f"   ✅ Found {len(trades)} closed trades")
        for t in trades[:5]:
            entry = t.get("entry_price", 0)
            exit_p = t.get("exit_price", 0)
            sl = t.get("stop_loss", 0)
            print(f"   Trade #{t['_id']}: {t.get('result')} {t.get('direction')} "
                  f"by {t.get('strategy_name')} pnl={t.get('pnl', 0):.4f} "
                  f"(entry={entry:.5f} exit={exit_p:.5f} sl={sl:.5f})")
    
    # 3. Check if EnsembleDecisions exist for these trades
    print("\n3. EnsembleDecisions for Recent Trades:")
    print("-" * 40)
    if trades:
        decisions_found = 0
        decisions_missing = 0
        decisions_no_votes = 0
        
        from app.db import COLL_ENSEMBLE_DECISIONS
        
        for t in trades[:5]:
            trade_id = t["_id"]
            decision_doc = db[COLL_ENSEMBLE_DECISIONS].find_one(
                {"trade_id": trade_id},
                {"trade_id": 1, "strategy_votes_json": 1, "resolved_direction": 1}
            )
            
            if decision_doc:
                votes = decision_doc.get("strategy_votes_json", [])
                if votes:
                    decisions_found += 1
                    print(f"   ✅ Trade #{trade_id}: Found decision with {len(votes)} strategy votes")
                    
                    # Show voting strategies
                    for vote in votes[:3]:
                        print(f"      - {vote.get('strategy_name')}: {vote.get('direction')} "
                              f"(conf={vote.get('raw_confidence', 0):.2f} weight={vote.get('weight', 0):.3f})")
                else:
                    decisions_no_votes += 1
                    print(f"   ⚠️  Trade #{trade_id}: Decision found but strategy_votes_json is EMPTY!")
            else:
                decisions_missing += 1
                print(f"   ❌ Trade #{trade_id}: NO EnsembleDecision found!")
        
        print(f"\n   Summary: {decisions_found} good, {decisions_no_votes} no votes, {decisions_missing} missing")
        
        if decisions_missing > 0 or decisions_no_votes > 0:
            print("\n   ⚠️  PROBLEM: Some trades lack proper EnsembleDecisions!")
            print("   Score feedback requires strategy_votes_json to know which strategies voted.")
    
    # 4. Check current strategy live_scores
    print("\n4. Current Strategy Live Scores:")
    print("-" * 40)
    strategies = crud.get_all_strategies(db)
    
    all_zero = all(s.live_score == 0.0 for s in strategies)
    if all_zero:
        print("   ❌ ALL live_scores are 0.00 - no updates have been applied")
    else:
        print("   ✅ Some live_scores have been updated:")
    
    for s in sorted(strategies, key=lambda x: x.live_score, reverse=True):
        status = "✅" if s.live_score != 0.0 else "  "
        active = "ACTIVE" if s.is_active else "inactive"
        print(f"   {status} {s.name:<20} live_score={s.live_score:>7.3f} ({active})")
    
    # 5. Try to manually run score feedback on the most recent closed trade
    print("\n5. Manual Score Feedback Test:")
    print("-" * 40)
    if trades:
        test_trade_doc = trades[0]
        test_trade = Trade.from_doc(test_trade_doc)
        
        print(f"   Testing with trade #{test_trade.id}:")
        print(f"   - Result: {test_trade.result}")
        print(f"   - Direction: {test_trade.direction}")
        print(f"   - Entry: {test_trade.entry_price}, Exit: {test_trade.exit_price}, SL: {test_trade.stop_loss}")
        
        from app.services.score_feedback import apply_score_feedback
        
        try:
            result = apply_score_feedback(db, test_trade)
            if result:
                print(f"\n   ✅ Score feedback applied successfully!")
                print(f"   Updated {len(result)} strategies:")
                for name, data in result.items():
                    print(f"      {name}: R={data['r']:.2f}, contribution={data['contribution']:.2f}, new_score={data['live_score']:.3f}")
            else:
                print(f"\n   ⚠️  Score feedback returned empty dict!")
                print(f"   Checking why...")
                
                # Check each condition
                if str(enabled).lower() not in ("1", "true", "yes", "on"):
                    print(f"   - DISABLED: score_feedback_enabled = '{enabled}'")
                
                if test_trade.result not in ("WIN", "LOSS"):
                    print(f"   - Invalid result: '{test_trade.result}'")
                
                if not test_trade.direction:
                    print(f"   - No direction: '{test_trade.direction}'")
                
                try:
                    trade_id = int(test_trade.id)
                    decision = crud.get_ensemble_decision_by_trade_id(db, trade_id)
                    if not decision:
                        print(f"   - NO EnsembleDecision found for trade #{trade_id}")
                    elif not decision.strategy_votes_json:
                        print(f"   - EnsembleDecision found but strategy_votes_json is empty")
                    else:
                        print(f"   - EnsembleDecision exists with {len(decision.strategy_votes_json)} votes")
                except Exception as e:
                    print(f"   - Error checking decision: {e}")
        
        except Exception as e:
            print(f"\n   ❌ Error running score feedback: {e}")
            import traceback
            traceback.print_exc()
    
    # 6. Trade status distribution
    print("\n6. Trade Status Distribution:")
    print("-" * 40)
    from app.db import COLL_TRADES
    total_trades = db[COLL_TRADES].count_documents({})
    open_trades = db[COLL_TRADES].count_documents({"result": None})
    win_trades = db[COLL_TRADES].count_documents({"result": "WIN"})
    loss_trades = db[COLL_TRADES].count_documents({"result": "LOSS"})
    
    print(f"   Total: {total_trades}, OPEN: {open_trades}, WIN: {win_trades}, LOSS: {loss_trades}")
    
    if win_trades + loss_trades == 0:
        print("\n   ❌ NO CLOSED TRADES! Trades are not closing with WIN/LOSS results")
    
    # 7. Diagnostic summary
    print("\n" + "=" * 80)
    print("DIAGNOSIS COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    diagnose()
