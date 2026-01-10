from pathlib import Path

from rag.rag_store import TextRAG

# 强制设置 CPU
rag = TextRAG(Path(__file__).resolve().parent / "rag" / "ai.txt")
rag.build()

q = "AI在金融风控中怎么落地？"
print(rag.build_injection(q, top_k=4))
