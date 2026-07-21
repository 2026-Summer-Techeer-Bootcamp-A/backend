import os
import sys
import json

# Force LLM invalid key to simulate LLM API failure (degraded mode)
os.environ['GEMINI_API_KEY'] = 'INVALID_EXPIRED_GEMINI_KEY_99999'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://appuser:change-me@localhost:5432/appdb_load'
os.environ['ENABLE_VECTOR_SEARCH'] = 'true'

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import SessionLocal
from app.services.rag.pipeline import run_chat

def main():
    db = SessionLocal()
    question = "React Native로 모바일 앱 만드는 공고 추천"
    
    print("=" * 60)
    print("🤖 REAL BACKEND RAG DEGRADED TEST RUNNER (AFTER BUGFIX)")
    print(f"QUESTION: {question}")
    print(f"LLM API KEY: {os.environ.get('GEMINI_API_KEY')}")
    print("=" * 60)

    res = run_chat(db, question)

    print("\n[RAG PIPELINE LIVE OUTPUT AFTER FIX]")
    print(f"• Degraded Flag     : {res.degraded} (LLM Fallback Mode)")
    print(f"• Output Answer     :\n{res.answer}")
    print("=" * 60)

    # Save output snapshot
    output_dict = res.model_dump() if hasattr(res, 'model_dump') else res.dict()
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs/ppt-demo")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "degraded_result_fixed_snapshot.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_dict, f, ensure_ascii=False, indent=2, default=str)

if __name__ == "__main__":
    main()
