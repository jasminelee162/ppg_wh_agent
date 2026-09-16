"""
rag/document_loader.py
知识文档加载器：递归扫描 knowledge/ 目录，按 Markdown 标题进行语义 Chunking，
并为每个 Chunk 附加 Metadata（category / heading / source_file / chunk_id）。
"""
import os
import re
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# knowledge/ 目录下的一级子目录名 → category 标签映射
_CATEGORY_MAP = {
    "transport_modes":      "transport_modes",
    "goods_classification": "goods_classification",
    "sop":                  "sop",
}


def _infer_category(rel_path: str) -> str:
    """根据相对路径推断 category 标签，未匹配时返回 'general'。"""
    parts = rel_path.replace("\\", "/").split("/")
    for part in parts:
        if part in _CATEGORY_MAP:
            return _CATEGORY_MAP[part]
    return "general"


def _heading_level(line: str) -> int:
    """返回 Markdown 标题级别（1~6），非标题行返回 0。"""
    m = re.match(r'^(#{1,6})\s', line)
    return len(m.group(1)) if m else 0


def _chunk_markdown(text: str, min_chars: int = 60) -> List[Dict[str, str]]:
    """
    按 Markdown 标题（## 及以上）将文本切割为语义 Chunk。

    策略：
    - 遇到 ## 或更高级标题时开始新 Chunk
    - 若某段落文字太短（< min_chars），合并到下一个 Chunk 避免碎片
    - 保留标题文本作为 heading 元数据

    Returns:
        List of {"heading": str, "text": str}
    """
    lines = text.split("\n")
    chunks: List[Dict[str, str]] = []
    current_heading = ""
    current_lines: List[str] = []

    def _flush(heading: str, lines_buf: List[str]):
        body = "\n".join(lines_buf).strip()
        if body:
            chunks.append({"heading": heading, "text": body})

    for line in lines:
        lvl = _heading_level(line)
        if lvl in (1, 2):           # H1 / H2 触发新 Chunk
            _flush(current_heading, current_lines)
            current_heading = line.lstrip("#").strip()
            current_lines = []
        elif lvl == 3:              # H3 嵌套在当前 Chunk 内（不分割）
            current_lines.append(line)
        else:
            current_lines.append(line)

    _flush(current_heading, current_lines)

    # 合并过短的碎片 Chunk（避免 embedding 输入过短导致语义失真）
    merged: List[Dict[str, str]] = []
    for chunk in chunks:
        if merged and len(merged[-1]["text"]) < min_chars:
            merged[-1]["text"] += "\n\n" + chunk["text"]
            if not merged[-1]["heading"]:
                merged[-1]["heading"] = chunk["heading"]
        else:
            merged.append(chunk)

    return merged


def load_knowledge_documents(knowledge_dir: str) -> List[Dict[str, Any]]:
    """
    递归扫描 knowledge_dir，加载所有 .md 文件并切割为带 Metadata 的 Chunk 列表。

    Args:
        knowledge_dir: knowledge/ 目录的绝对路径

    Returns:
        List of chunk dicts：
        {
            "chunk_id":    str,   # 唯一 ID，用于向量库 upsert
            "text":        str,   # Chunk 正文（含标题）
            "metadata": {
                "source_file": str,   # 相对路径，如 transport_modes/shipping_methods.md
                "category":    str,   # transport_modes / goods_classification / sop / general
                "heading":     str,   # 本 Chunk 的 Markdown 标题
            }
        }
    """
    if not os.path.isdir(knowledge_dir):
        logger.warning(f"knowledge 目录不存在: {knowledge_dir}")
        return []

    all_chunks: List[Dict[str, Any]] = []

    for root, _dirs, files in os.walk(knowledge_dir):
        for filename in sorted(files):
            if not filename.lower().endswith(".md"):
                continue

            abs_path = os.path.join(root, filename)
            rel_path = os.path.relpath(abs_path, knowledge_dir)
            category = _infer_category(rel_path)

            try:
                with open(abs_path, encoding="utf-8") as f:
                    text = f.read()
            except Exception as e:
                logger.error(f"读取知识文档失败 {abs_path}: {e}")
                continue

            raw_chunks = _chunk_markdown(text)
            base_id = rel_path.replace("\\", "/").replace("/", "_").replace(".md", "")

            for idx, chunk in enumerate(raw_chunks):
                chunk_id = f"{base_id}__{idx}"
                # 将标题拼入正文，方便 embedding 捕获语义
                full_text = f"# {chunk['heading']}\n\n{chunk['text']}" if chunk["heading"] else chunk["text"]
                all_chunks.append({
                    "chunk_id": chunk_id,
                    "text":     full_text,
                    "metadata": {
                        "source_file": rel_path.replace("\\", "/"),
                        "category":    category,
                        "heading":     chunk["heading"],
                    },
                })

            logger.debug(f"加载 {rel_path}：{len(raw_chunks)} 个 Chunk")

    logger.info(f"知识文档加载完成：共 {len(all_chunks)} 个 Chunk，来自 {knowledge_dir}")
    return all_chunks
