import json
import asyncio
from typing import List, Dict, Any
from app.qa import answer_question
from app.models.schemas import AskRequest

EVAL_QUESTIONS = [
    {
        "question": "What is the standard reversal window for settled transactions?",
        "scope": "compliance",
        "expected_source_types": ["POLICY_DOC", "REGULATORY_CIRCULAR"],
        "keywords": ["24 hours", "T+1", "reversal"]
    },
    {
        "question": "What is the extended reversal window for disputed transactions?",
        "scope": "compliance",
        "expected_source_types": ["POLICY_DOC", "REGULATORY_CIRCULAR"],
        "keywords": ["48 hours", "T+2", "disputed"]
    },
    {
        "question": "What approval is needed for emergency reversals beyond 48 hours?",
        "scope": "compliance",
        "expected_source_types": ["POLICY_DOC", "REGULATORY_CIRCULAR"],
        "keywords": ["compliance officer", "approval", "audit trail"]
    },
    {
        "question": "What are the exception types in reconciliation?",
        "scope": "compliance",
        "expected_source_types": ["POLICY_DOC"],
        "keywords": ["MISSING_IN_LEDGER", "AMOUNT_MISMATCH", "DUPLICATE"]
    },
    {
        "question": "What is the amount tolerance for INR transactions in reconciliation?",
        "scope": "compliance",
        "expected_source_types": ["POLICY_DOC"],
        "keywords": ["0 minor units", "exact match", "tolerance"]
    },
    {
        "question": "What is the fee for domestic payments?",
        "scope": "compliance",
        "expected_source_types": ["POLICY_DOC"],
        "keywords": ["0.5%", "min", "max"]
    },
    {
        "question": "What happens when a transaction is missing in the ledger but present in bank file?",
        "scope": "ops",
        "expected_source_types": ["RECONCILIATION_EXCEPTION", "POLICY_DOC"],
        "keywords": ["MISSING_IN_LEDGER", "webhook", "re-post"]
    },
    {
        "question": "What is the timeline for Level 1 exception resolution?",
        "scope": "ops",
        "expected_source_types": ["POLICY_DOC"],
        "keywords": ["4 business hours", "Level 1"]
    },
    {
        "question": "What corrective action for amount mismatch in reconciliation?",
        "scope": "ops",
        "expected_source_types": ["POLICY_DOC", "RECONCILIATION_EXCEPTION"],
        "keywords": ["verify fees", "FX rates", "rounding", "adjustment"]
    },
    {
        "question": "What is the idempotency key requirement for payment APIs?",
        "scope": "compliance",
        "expected_source_types": ["REGULATORY_CIRCULAR"],
        "keywords": ["idempotency", "24 hours", "duplicate"]
    },
    {
        "question": "Are ledger entries append-only?",
        "scope": "compliance",
        "expected_source_types": ["REGULATORY_CIRCULAR"],
        "keywords": ["append-only", "WORM", "immutable", "audit trail"]
    },
    {
        "question": "What is the penalty for non-compliance with RBI guidelines?",
        "scope": "compliance",
        "expected_source_types": ["REGULATORY_CIRCULAR"],
        "keywords": ["penalties", "Section 30", "PSS Act"]
    },
    {
        "question": "What is the transaction fee for UPI P2M payments?",
        "scope": "compliance",
        "expected_source_types": ["POLICY_DOC"],
        "keywords": ["0.3%", "P2M", "UPI"]
    },
    {
        "question": "What happens when reconciliation finds a duplicate transaction?",
        "scope": "ops",
        "expected_source_types": ["POLICY_DOC", "RECONCILIATION_EXCEPTION"],
        "keywords": ["DUPLICATE", "reverse duplicate", "retry storm"]
    },
    {
        "question": "What is the three-way reconciliation process?",
        "scope": "compliance",
        "expected_source_types": ["REGULATORY_CIRCULAR", "POLICY_DOC"],
        "keywords": ["Internal Ledger", "Payment Gateway", "Bank Settlement"]
    }
]

async def run_evaluation():
    results = []
    
    for i, eval_q in enumerate(EVAL_QUESTIONS):
        print(f"\nEvaluating question {i+1}/{len(EVAL_QUESTIONS)}: {eval_q['question'][:60]}...")
        
        request = AskRequest(question=eval_q["question"], scope=eval_q["scope"])
        response = await answer_question(request)
        
        has_expected_source = any(
            s.source_type in eval_q["expected_source_types"] 
            for s in response.sources
        )
        
        has_keywords = any(
            kw.lower() in response.answer.lower() 
            for kw in eval_q["keywords"]
        )
        
        result = {
            "question": eval_q["question"],
            "scope": eval_q["scope"],
            "answer": response.answer,
            "sources": [{"type": s.source_type, "ref": s.source_ref, "sim": s.similarity} for s in response.sources],
            "has_expected_source": has_expected_source,
            "has_keywords": has_keywords,
            "passed": has_expected_source and has_keywords
        }
        
        results.append(result)
        print(f"  Passed: {result['passed']}")
        print(f"  Sources: {[s['type'] for s in result['sources']]}")
    
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    
    print(f"\n{'='*50}")
    print(f"EVALUATION RESULTS: {passed}/{total} passed ({passed/total*100:.1f}%)")
    print(f"{'='*50}")
    
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['question'][:60]}...")
    
    return results

if __name__ == "__main__":
    asyncio.run(run_evaluation())