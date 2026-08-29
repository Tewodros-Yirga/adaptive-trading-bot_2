"""
Diagnostic script to check why live_score (R-EWMA) is not updating.
"""
import sys
from pymongo import MongoClient
from datetime import datetime, timedelta

# Connect to MongoDB
client = MongoClient("mongodb://localhost:27017/")
db = client["trading_bot"]

print("=" * 80)
print("DIAGNOSTIC: Live Score Feedback")
print("=" * 80)

# 1. Check if score_feedback is enabled
print("\n1. Score Feedback Configuration:")
print("-" * 40)
setting = db.app_settings.find_one({"key": "score_feedback_enabled"})
enabled = setting.get("value", "true") if setting else "true"
print(f"   score_feedback_enabled: {enabled}")

alpha_setting = db.app_settings.find_one({"key": "score_feedback_alpha"})
alpha = alpha_setting.get("value", "0.2") if alpha_setting else "0.2"
print(f"   score_feedback_alpha: {alpha}")

bound_setting = db.app_settings.find_one({"key": "score_feedback_score_bound"})
bound = bound_setting.get("value", "3.0") if bound_setting else "3.0"
print(f"   score_feedback_score_bound: {bound}")

# 2. Check recent trades
print("\n2. Recent Closed Trades (last 10):")
print("-" * 40)
trades = list(db.trades.find(
    {"result": {"$in": ["WIN", "LOSS"]}},
    {"_id": 1, "strategy_name": 1, "direction": 1, "result": 1, "closed_at": 1, "pnl": 1}
).sort("closed_at", -1).limit(10))

if not trades:
    print("   ❌ NO CLOSED TRADES FOUND!")
    print("   This is the problem - trades are not closing with WIN/LOSS results.")
else:
    print(f"   ✅ Found {len(trades)} closed trades")
    for t in trades[:5]:
        print(f"   Trade #{t['_id']}: {t.get('result')} {t.get('direction')} "
              f"by {t.get('strategy_name')} pnl={t.get('pnl', 0):.2f}")

# 3. Check if EnsembleDecisions exist for these trades
print("\n3. EnsembleDecisions for Recent Trades:")
print("-" * 40)
if trades:
    decisions_found = 0
    decisions_missing = 0
    
    for t in trades[:5]:
        trade_id = t["_id"]
        decision = db.ensemble_decisions.find_one(
            {"trade_id": trade_id},
            {"trade_id": 1, "strategy_votes_json": 1, "resolved_direction": 1}
        )
        
        if decision:
            decisions_found += 1
            num_votes = len(decision.get("strategy_votes_json", []))
            print(f"   ✅ Trade #{trade_id}: Found decision with {num_votes} strategy votes")
            
            # Show first 3 strategy votes
            for vote in decision.get("strategy_votes_json", [])[:3]:
                print(f"      - {vote.get('strategy_name')}: {vote.get('direction')} "
                      f"(weight={vote.get('weight', 0):.3f})")
        else:
            decisions_missing += 1
            print(f"   ❌ Trade #{trade_id}: NO EnsembleDecision found!")
    
    print(f"\n   Summary: {decisions_found} decisions found, {decisions_missing} missing")
    
    if decisions_missing > 0:
        print("\n   ⚠️  PROBLEM IDENTIFIED: EnsembleDecisions are not being created!")
        print("   This means score feedback cannot run because it can't find voting data.")

# 4. Check current strategy live_scores
print("\n4. Current Strategy Live Scores:")
print("-" * 40)
strategies = list(db.strategies.find(
    {},
    {"name": 1, "live_score": 1, "is_active": 1}
).sort("live_score", -1))

all_zero = all(s.get("live_score", 0) == 0.0 for s in strategies)
if all_zero:
    print("   ❌ ALL live_scores are 0.00 - no updates have been applied")
else:
    print("   ✅ Some live_scores have been updated:")

for s in strategies:
    status = "✅" if s.get("live_score", 0) != 0.0 else "  "
    active = "ACTIVE" if s.get("is_active") else "inactive"
    print(f"   {status} {s['name']:<20} live_score={s.get('live_score', 0):>6.2f} ({active})")

# 5. Check if trades are actually closing (not stuck OPEN)
print("\n5. Trade Status Distribution:")
print("-" * 40)
total_trades = db.trades.count_documents({})
open_trades = db.trades.count_documents({"result": None})
win_trades = db.trades.count_documents({"result": "WIN"})
loss_trades = db.trades.count_documents({"result": "LOSS"})

print(f"   Total trades: {total_trades}")
print(f"   OPEN: {open_trades}")
print(f"   WIN: {win_trades}")
print(f"   LOSS: {loss_trades}")

if win_trades + loss_trades == 0:
    print("\n   ❌ PROBLEM: No trades have closed with WIN/LOSS results!")
    print("   Possible causes:")
    print("   - Trades are not being closed by MT5")
    print("   - Position stream/webhook not receiving close events")
    print("   - Trade result not being set when position closes")

# 6. Check ensemble_decisions collection size
print("\n6. EnsembleDecisions Status:")
print("-" * 40)
total_decisions = db.ensemble_decisions.count_documents({})
decisions_with_trades = db.ensemble_decisions.count_documents({"trade_id": {"$ne": None}})
decisions_with_votes = db.ensemble_decisions.count_documents({
    "strategy_votes_json": {"$exists": True, "$ne": []}
})

print(f"   Total decisions: {total_decisions}")
print(f"   Linked to trades: {decisions_with_trades}")
print(f"   With strategy votes: {decisions_with_votes}")

if total_decisions == 0:
    print("\n   ❌ NO EnsembleDecisions at all - voting system not creating decisions!")
elif decisions_with_trades == 0:
    print("\n   ⚠️  EnsembleDecisions exist but none are linked to trades (trade_id=None)")
    print("   This breaks the feedback loop!")

# 7. Diagnostic summary
print("\n" + "=" * 80)
print("DIAGNOSIS SUMMARY:")
print("=" * 80)

issues = []
if enabled.lower() in ("false", "0", "no", "off"):
    issues.append("Score feedback is DISABLED in settings")
if not trades:
    issues.append("No closed trades found (trades not closing with WIN/LOSS)")
if trades and decisions_missing > 0:
    issues.append(f"EnsembleDecisions missing for {decisions_missing} trades")
if win_trades + loss_trades == 0:
    issues.append("No trades have WIN/LOSS results (all stuck OPEN)")
if total_decisions == 0:
    issues.append("No EnsembleDecisions being created")
if decisions_with_trades == 0 and total_decisions > 0:
    issues.append("EnsembleDecisions not linked to trades (trade_id field not set)")
if all_zero:
    issues.append("No live_score updates have been applied (all still 0.00)")

if issues:
    print("\n❌ ISSUES FOUND:")
    for i, issue in enumerate(issues, 1):
        print(f"   {i}. {issue}")
else:
    print("\n✅ No obvious issues found - score feedback should be working")
    print("   If live_scores are still 0.00, check application logs for errors")

print("\n" + "=" * 80)
