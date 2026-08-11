"""
Diagnostic endpoint for troubleshooting live_score feedback.
"""
from fastapi import APIRouter, Depends
from pymongo.database import Database

from ..db import get_db
from .. import crud
from ..models import Trade
from ..services.score_feedback import apply_score_feedback

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.get("/live-score-feedback")
def diagnose_live_score_feedback(db: Database = Depends(get_db)):
    """
    Diagnose why live_score (R-EWMA) is not updating.
    Returns detailed information about configuration, trades, and score updates.
    """
    from ..db import COLL_TRADES, COLL_ENSEMBLE_DECISIONS
    
    result = {
        "configuration": {},
        "trades": {},
        "ensemble_decisions": {},
        "strategies": {},
        "test_feedback": {},
        "issues": [],
    }
    
    # 1. Configuration
    enabled = crud.get_setting(db, "score_feedback_enabled") or "true"
    alpha = crud.get_setting(db, "score_feedback_alpha") or "0.2"
    bound = crud.get_setting(db, "score_feedback_score_bound") or "3.0"
    
    result["configuration"] = {
        "score_feedback_enabled": enabled,
        "score_feedback_alpha": float(alpha),
        "score_feedback_score_bound": float(bound),
        "is_enabled": str(enabled).lower() in ("1", "true", "yes", "on"),
    }
    
    if not result["configuration"]["is_enabled"]:
        result["issues"].append("Score feedback is DISABLED in settings")
    
    # 2. Trade statistics
    total_trades = db[COLL_TRADES].count_documents({})
    open_trades = db[COLL_TRADES].count_documents({"result": None})
    win_trades = db[COLL_TRADES].count_documents({"result": "WIN"})
    loss_trades = db[COLL_TRADES].count_documents({"result": "LOSS"})
    closed_trades = win_trades + loss_trades
    
    result["trades"] = {
        "total": total_trades,
        "open": open_trades,
        "win": win_trades,
        "loss": loss_trades,
        "closed": closed_trades,
    }
    
    if closed_trades == 0:
        result["issues"].append("No closed trades found (trades not closing with WIN/LOSS results)")
    
    # 3. Recent closed trades with decision check
    recent_closed = list(db[COLL_TRADES].find(
        {"result": {"$in": ["WIN", "LOSS"]}},
        {
            "_id": 1, "strategy_name": 1, "direction": 1, "result": 1, 
            "closed_at": 1, "pnl": 1, "entry_price": 1, "exit_price": 1, "stop_loss": 1
        }
    ).sort("closed_at", -1).limit(5))
    
    recent_with_decisions = []
    decisions_missing = 0
    decisions_no_votes = 0
    
    for t in recent_closed:
        trade_id = t["_id"]
        decision_doc = db[COLL_ENSEMBLE_DECISIONS].find_one(
            {"trade_id": trade_id},
            {"trade_id": 1, "strategy_votes_json": 1, "resolved_direction": 1}
        )
        
        trade_info = {
            "trade_id": trade_id,
            "strategy_name": t.get("strategy_name"),
            "direction": t.get("direction"),
            "result": t.get("result"),
            "pnl": t.get("pnl"),
            "entry_price": t.get("entry_price"),
            "exit_price": t.get("exit_price"),
            "stop_loss": t.get("stop_loss"),
            "has_decision": False,
            "num_votes": 0,
            "sample_votes": [],
        }
        
        if decision_doc:
            votes = decision_doc.get("strategy_votes_json", [])
            if votes:
                trade_info["has_decision"] = True
                trade_info["num_votes"] = len(votes)
                trade_info["sample_votes"] = [
                    {
                        "strategy": v.get("strategy_name"),
                        "direction": v.get("direction"),
                        "confidence": v.get("raw_confidence", v.get("confidence")),
                        "weight": v.get("weight"),
                    }
                    for v in votes[:3]
                ]
            else:
                decisions_no_votes += 1
        else:
            decisions_missing += 1
        
        recent_with_decisions.append(trade_info)
    
    result["ensemble_decisions"] = {
        "total_decisions": db[COLL_ENSEMBLE_DECISIONS].count_documents({}),
        "with_trade_id": db[COLL_ENSEMBLE_DECISIONS].count_documents({"trade_id": {"$ne": None}}),
        "recent_closed_trades_checked": len(recent_closed),
        "decisions_missing": decisions_missing,
        "decisions_no_votes": decisions_no_votes,
        "recent_trades": recent_with_decisions,
    }
    
    if decisions_missing > 0:
        result["issues"].append(f"{decisions_missing} closed trades missing EnsembleDecisions")
    if decisions_no_votes > 0:
        result["issues"].append(f"{decisions_no_votes} EnsembleDecisions have no strategy votes")
    
    # 4. Current strategy live_scores
    strategies = crud.get_all_strategies(db)
    strategy_scores = [
        {
            "name": s.name,
            "live_score": s.live_score,
            "is_active": s.is_active,
        }
        for s in sorted(strategies, key=lambda x: x.live_score, reverse=True)
    ]
    
    all_zero = all(s["live_score"] == 0.0 for s in strategy_scores)
    
    result["strategies"] = {
        "total": len(strategy_scores),
        "all_scores_zero": all_zero,
        "scores": strategy_scores,
    }
    
    if all_zero and closed_trades > 0:
        result["issues"].append("All live_scores are 0.00 despite closed trades existing")
    
    # 5. Test score feedback on most recent closed trade
    if recent_closed:
        test_trade_doc = recent_closed[0]
        test_trade = Trade.from_doc(test_trade_doc)
        
        try:
            feedback_result = apply_score_feedback(db, test_trade)
            
            result["test_feedback"] = {
                "trade_id": test_trade.id,
                "success": bool(feedback_result),
                "strategies_updated": len(feedback_result) if feedback_result else 0,
                "updates": feedback_result if feedback_result else None,
            }
            
            if not feedback_result:
                # Diagnose why it failed
                failure_reasons = []
                
                if str(enabled).lower() not in ("1", "true", "yes", "on"):
                    failure_reasons.append(f"score_feedback_enabled is '{enabled}'")
                
                if test_trade.result not in ("WIN", "LOSS"):
                    failure_reasons.append(f"trade result is '{test_trade.result}' (expected WIN or LOSS)")
                
                if not test_trade.direction:
                    failure_reasons.append(f"trade direction is empty")
                
                try:
                    trade_id = int(test_trade.id)
                    decision = crud.get_ensemble_decision_by_trade_id(db, trade_id)
                    if not decision:
                        failure_reasons.append(f"No EnsembleDecision found for trade #{trade_id}")
                    elif not decision.strategy_votes_json:
                        failure_reasons.append(f"EnsembleDecision.strategy_votes_json is empty")
                except Exception as e:
                    failure_reasons.append(f"Error checking decision: {str(e)}")
                
                result["test_feedback"]["failure_reasons"] = failure_reasons
                result["issues"].extend(failure_reasons)
        
        except Exception as e:
            result["test_feedback"] = {
                "trade_id": test_trade.id,
                "error": str(e),
                "success": False,
            }
            result["issues"].append(f"Score feedback error: {str(e)}")
    else:
        result["test_feedback"] = {"note": "No closed trades to test with"}
    
    # Summary
    result["summary"] = {
        "has_issues": len(result["issues"]) > 0,
        "issue_count": len(result["issues"]),
        "diagnosis": (
            "Score feedback appears functional" if not result["issues"]
            else "Issues detected preventing score feedback"
        ),
    }
    
    return result
