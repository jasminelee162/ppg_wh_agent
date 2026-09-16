"""
rag/build_index.py
一键构建 / 重建物流 RAG 知识库向量索引。

用法：
    # 全量重建（清空旧索引）
    python rag/build_index.py --rebuild

    # 增量更新（追加/覆盖变更的 Chunk）
    python rag/build_index.py

    # 构建后进行交互式测试查询
    python rag/build_index.py --test
"""
import sys
import os
import argparse
import logging

# 确保项目根目录在 Python 路径中（从任意工作目录运行均可）
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("build_index")


def run_build(force_rebuild: bool):
    """执行索引构建。"""
    from rag.rag_pipeline import RAGPipeline

    pipeline = RAGPipeline()
    pipeline.build(force_rebuild=force_rebuild)
    return pipeline


def run_interactive_test(pipeline):
    """构建完成后进行交互式查询测试。"""
    print("\n" + "=" * 60)
    print("RAG 知识库交互式测试（输入 q 退出）")
    print("=" * 60)
    print("可用 category 过滤：transport_modes | goods_classification | sop | (回车=全库)")
    print()

    while True:
        try:
            query = input("查询> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            break

        if query.lower() in ("q", "quit", "exit", ""):
            if query.lower() in ("q", "quit", "exit"):
                break
            continue

        cat_input = input("category（回车=全库）> ").strip() or None

        hits = pipeline.query(query, category=cat_input, top_k=3)
        if not hits:
            print("  ⚠ 无相关结果\n")
            continue

        print(f"\n  找到 {len(hits)} 条结果：")
        for i, h in enumerate(hits, 1):
            meta = h["metadata"]
            print(f"\n  [{i}] 分数={h['score']:.4f}  "
                  f"文件={meta.get('source_file','')}  "
                  f"标题={meta.get('heading','')}")
            # 只打印前 300 字符
            preview = h["text"][:300].replace("\n", " ")
            print(f"      {preview}{'...' if len(h['text']) > 300 else ''}")
        print()


def main():
    parser = argparse.ArgumentParser(description="构建物流 RAG 知识库向量索引")
    parser.add_argument(
        "--rebuild", action="store_true",
        help="全量重建（清空旧索引后重新构建）；不加此参数则增量更新",
    )
    parser.add_argument(
        "--test", action="store_true",
        help="构建完成后进入交互式测试查询",
    )
    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("物流 RAG 知识库索引构建")
    logger.info("=" * 50)

    if args.rebuild:
        logger.info("模式：全量重建（--rebuild）")
    else:
        logger.info("模式：增量更新（如需全量重建请加 --rebuild 参数）")

    try:
        pipeline = run_build(force_rebuild=args.rebuild)
        logger.info("✅ 索引构建成功！")

        if args.test:
            run_interactive_test(pipeline)

    except ImportError as e:
        logger.error(f"❌ 缺少依赖库: {e}")
        logger.error("请运行：pip install chromadb>=0.4.0 sentence-transformers>=2.2.0")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ 构建失败: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
