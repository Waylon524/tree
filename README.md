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

骨架阶段。按 `docs/REBUILD-DESIGN.md` §9 的十步路线增量实现。
已实现：基础层（config / paths / ids / store / state / observability / model client）。
待实现（stub）：agents / planner / ingest / rag / engine / cli。

## License

MIT
