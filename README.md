# tree (rebuild)

**资料驱动、以考促写的自动化教材生成引擎** —— 新架构重建。

> 权威设计见 [docs/REBUILD-DESIGN.md](docs/REBUILD-DESIGN.md)。旧实现审阅（仅作接口/prompt 参考）见 [docs/LEGACY-DESIGN.md](docs/LEGACY-DESIGN.md)。

## 流程

```text
materials/ → OCR(PaddleOCR-VL，接口不变)
           → Archivist 切最小可教学单元(MTU) + 命名/关键词/摘要
           → Dagger 合并 canonical 节点 + 一次性建 DAG
           → 确定性切 KnowledgeBranch
           → Qwen3-4B embedding 建 RAG(接口不变)
           → Examiner / Student / Writer 循环
           → outputs/
```

## 开发

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[rag,dev]"

python -m pytest
ruff check tree_engine tests
python -m compileall tree_engine/tree
```

## 状态

按 `docs/REBUILD-DESIGN.md` §9 的十步路线增量实现。

- ✅ Step 1 基础层：config / paths / ids / store / state / observability / model client
- ✅ Step 2 摄入提取：OCR engine + pdf/image/docx/presentation extractors + extract_text
- ✅ Step 3 RAG：embed client / 本地 server / 精简 chunker（MTU 边界）/ RAGClient / RAGIndexer
- ⬜ Step 4 Archivist 切 MTU
- ⬜ Step 5 Dagger 建 DAG
- ⬜ Step 6 planner pipeline + schedule
- ⬜ Step 7 examiner/student/writer + branch_run
- ⬜ Step 8 engine 编排 + ingest_driver
- ⬜ Step 9 CLI + dashboard
- ⬜ Step 10 端到端验收

OCR 与 embedding 服务接口与旧引擎保持一致（可直接复用本地 embedding server 和 PaddleOCR 配置）。

## License

MIT
