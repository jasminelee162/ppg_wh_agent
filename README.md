[中文](README-ZH.md) | English

# PPG WH Agent

> An automated email parsing and Feishu (Lark) Bitable synchronization system designed for daily shipping-order processing at the PPG Wuhu Warehouse.

---

## Table of Contents

- [System Overview](#system-overview)
- [Architecture](#architecture)
- [Directory Structure](#directory-structure)
- [Environment Configuration](#environment-configuration)
- [Usage](#usage)
- [Core Workflows](#core-workflows)
  - [Email Processing (`main.py`)](#email-processing-mainpy)
  - [Bitable Synchronization (`sync_orders.py`)](#bitable-synchronization-sync_orderspy)
- [Business Instruction Handling](#business-instruction-handling)
- [Shipping Cache](#shipping-cache)
- [Staging Area (`PendingOrders`)](#staging-area-pendingorders)
- [Field Normalization Pipeline](#field-normalization-pipeline)
- [Bitable Field Mapping](#bitable-field-mapping)
- [Idempotency Guarantees](#idempotency-guarantees)
- [RAG Knowledge Base](#rag-knowledge-base)
  - [Architecture Overview](#architecture-overview)
  - [Knowledge Directory Structure](#knowledge-directory-structure)
  - [Module Descriptions](#module-descriptions)
  - [Index Building](#index-building)
  - [API Usage](#api-usage)
  - [Embedding Backend Configuration](#embedding-backend-configuration)
  - [Unit Tests](#unit-tests)

---

## System Overview

This system automates the following daily workflow:

1. **Fetch Emails** — Pulls all emails from the inbox via IMAP.
2. **Update Shipping Cache** — Extracts transport modes and hazmat (dangerous goods) information from `wenjuan` / `SHIPPING_INFO` emails and persists them to `shipping_all.json`.
3. **Detect Business Instructions** — Parses email bodies to automatically handle order cancellations, modifications, and splits.
4. **Parse PDF Shipping Orders** — Extracts structured fields including order numbers, addresses, weights, quantities, and contacts.
5. **Normalize Fields** — Standardizes addresses, contacts, transport modes, and other fields through a multi-stage deterministic normalization pipeline.
6. **Stage Orders** — Writes newly parsed orders to `pending_orders.json`.
7. **Sync to Feishu Bitable** — Batch-writes staged orders to Feishu Bitable and synchronizes status updates such as cancellations and modifications.

---

## Architecture

```text
Emails (IMAP)
    │
    ▼
main.py
    ├── 1. Update Shipping Cache (wenjuan / SHIPPING_INFO emails)
    ├── 2. Detect Business Instructions (Cancel / Modify / Split)
    └── 3. Parse PDF_ORDER → FieldNormalizer → PendingOrdersManager

sync_orders.py
    ├── Process Cancelled ("已取消") → Update Bitable status
    ├── Process Updated ("已更新") → Overwrite existing Bitable records
    ├── Write pending / Split ("拆单") → Insert new Bitable rows
    └── Export output/orders_YYYY-MM-DD.json
```

---

## Directory Structure

```text
ppg_wh_agent/
├── main.py                    # Main entry point for email fetching & parsing
├── sync_orders.py             # Entry point for Bitable synchronization
├── inspect_orders.py          # Staging area inspection tool (dev helper)
├── requirements.txt
├── .env                       # Environment variables & secrets (gitignored)
│
├── mail/
│   ├── mail_reader.py         # IMAP email fetcher
│   ├── mail_filter.py         # Email classification (PDF_ORDER / SHIPPING_INFO / …)
│   └── email_saver.py         # Attachment downloader
│
├── parser/
│   ├── pdf_parser.py          # PDF text extraction + coordinate-based order number extraction
│   ├── content_parser.py      # Email body / PDF text field parser
│   └── schema.py              # Field schema definitions
│
├── business/
│   ├── field_normalizer.py    # Normalization pipeline orchestrator
│   ├── rule_engine.py         # Deterministic rule engine
│   └── normalizers/
│       ├── order_info_normalizer.py   # Date standardization
│       ├── requirement_normalizer.py  # Customer requirement denoising
│       ├── contact_normalizer.py      # Contact cleaning & phone extraction
│       ├── address_normalizer.py      # Address normalization / consignee matching / province & city
│       └── logistics_normalizer.py    # Transport mode & hazmat classification
│
├── feishu/
│   └── bitable.py             # Feishu Bitable API wrapper
│
├── utils/
│   ├── cache_manager.py       # CacheManager / PendingOrdersManager / OrdersManager
│   ├── seen_mails.py          # Processed email UID persistence
│   └── config.py              # Configuration loader
│
├── rag/                       # RAG knowledge base module
│   ├── __init__.py
│   ├── rag_pipeline.py        # Unified RAG entry point (build / query / explain)
│   ├── build_index.py         # CLI tool for building the vector index
│   ├── document_loader.py     # Knowledge document loader & Markdown chunking
│   ├── embedder.py            # Embedding wrapper (local / openai)
│   ├── vector_store.py        # ChromaDB vector store wrapper
│   └── retriever.py           # Semantic retrieval (similarity & category filtering)
│
├── knowledge/                 # Domain knowledge documents (Markdown, organized by category)
│   ├── transport_modes/       # Transport mode rules
│   ├── goods_classification/  # Hazmat / goods classification rules
│   └── sop/                   # Standard Operating Procedures (SOPs)
│
├── test/
│   └── test_rag.py            # RAG unit tests
│
├── file/                      # Directory for saved PDF attachments
├── output/
│   ├── orders_YYYY-MM-DD.json           # Daily sorted orders ready for sync
│   ├── rag_index/                       # Persisted RAG vector index (ChromaDB)
│   └── cache/
│       ├── shipping_YYYY-MM-DD.json     # Daily shipping cache snapshot
│       ├── shipping_all.json            # Cumulative historical shipping cache
│       └── pending_orders.json          # Staging area (orders across all statuses)
└── WH_check/                  # Standalone verification scripts
```

---

## Environment Configuration

Copy and populate the `.env` file:

```env
# Email (IMAP)
MAIL_HOST=imap.feishu.cn
MAIL_PORT=993
MAIL_USER=<email_account>
MAIL_PASSWORD=<email_password>

# Feishu Bitable
FEISHU_APP_ID=<app_id>
FEISHU_APP_SECRET=<app_secret>
FEISHU_BITABLE_APP_TOKEN=<bitable_app_token>
FEISHU_BITABLE_TABLE_ID=<orders_table_id>
FEISHU_RECEIVER_TABLE_ID=<receivers_table_id>
```

Install dependencies:

```bash
pip install -r requirements.txt
```

> `jionlp` is automatically detected and installed on the first run of `main.py` (attempting the Tsinghua PyPI mirror first, with Alibaba Cloud mirror as fallback).

---

## Usage

### Step 1: Email Parsing

```bash
python main.py
```

- Fetches all emails (`ALL`) via IMAP and automatically skips previously processed UIDs.
- Updates the shipping cache and detects/executes business instructions.
- Writes newly parsed PDF orders to `pending_orders.json`.

### Step 2: Bitable Synchronization

```bash
python sync_orders.py
```

- Reads all pending orders from `pending_orders.json`.
- Processes records in batches by status: Cancel → Overwrite Updated → Insert New.
- Exports `output/orders_YYYY-MM-DD.json` and marks records as `synced`.

---

## Core Workflows

### Email Processing (`main.py`)

```text
Fetch all emails (up to 3 retries)
    │
    ├─ Pass 1 (all emails): Update Shipping Cache
    │       ├─ wenjuan emails (empty body + PDF): extract transport mode from subject, write/overwrite cache
    │       └─ SHIPPING_INFO emails: write on first seen, do not overwrite
    │
    ├─ Pass 2: Detect Business Instructions
    │       ├─ Contains cancel keywords ("取消/停止/作废/不用发") → mark as "已取消" (Cancelled)
    │       ├─ Contains update keywords ("更新/以此为准/重新提供") → parse PDF → mark as "已更新" (Updated)
    │       └─ Contains split keyword ("拆") & ≥ 2 PDF attachments → original as "已更新", child as "拆单" (Split)
    │
    └─ Pass 3 (skip seen UIDs): Parse PDF_ORDER
            ├─ save_attachments → save PDFs to file/
            ├─ PDFParser.parse_pdf + extract_order_no_coord → text + coordinate-based order number
            ├─ ContentParser.parse_pdf_text → raw field dict
            ├─ FieldNormalizer.normalize → deterministic business normalization
            └─ PendingOrdersManager.add_orders → write to staging area
```

**Reply Chain Truncation**: Before running business instruction detection, email bodies are truncated at the start of any reply/forward chain to ensure instructions are parsed strictly from the latest message. Supported headers include:
- `-----Original Message-----`
- English Outlook/Feishu forward headers (`From: … Sent: …`)
- Chinese forward headers (`发件人: … 发送时间: …`)

---

### Bitable Synchronization (`sync_orders.py`)

```text
Load pending_orders.json
    │
    ├─ Build "order_no → record_id" mapping (fetch all existing Bitable records)
    │
    ├─ Cancelled ("已取消"): batch_update_records → update status field only
    ├─ Updated ("已更新", existing record): batch_update_records → overwrite all fields
    ├─ pending / Split ("拆单") / Updated (no existing record): write_records → insert new rows
    │
    ├─ Export output/orders_YYYY-MM-DD.json (sorted by province/city, address, order_no)
    └─ mark_synced → update staging area status to synced
```

Orders with unparseable dates are automatically flagged as `anomaly` and skipped during synchronization.

---

## Business Instruction Handling

| Instruction Type | Trigger Keywords (New Body or Subject) | Handling Logic |
|------------------|----------------------------------------|----------------|
| **Cancel** | 停止, 作废, 不用发, 取消 | Extracts order numbers from truncated body + subject; sets `sync_status = 已取消`. |
| **Modify** | 更新, 以此为准, 重新提供 | Parses attached PDF, overwrites staged fields, and sets `sync_status = 已更新`. |
| **Split** | 拆 (body or subject) + ≥ 2 PDF attachments | First PDF updates the original order (`已更新`); second PDF creates a child order (`拆单`), appending `-1` if the order number matches the parent. |

---

## Shipping Cache

- **Write Rules**:
  - Emails from `wenjuan` (empty body + PDF attachment): extracts transport mode (LTL / Temperature-Controlled / Chartered / Self-Pickup) from the **subject** and **overwrites** existing entries.
  - `SHIPPING_INFO` emails from other senders: parses body tables and writes on **first seen** without overwriting.
- **Storage Paths**:
  - `output/cache/shipping_YYYY-MM-DD.json` (daily snapshot)
  - `output/cache/shipping_all.json` (cumulative history; merged without deleting past entries)
- **Usage**: After PDF parsing, if transport mode or hazmat classification is missing, values are backfilled from the cache.

---

## Staging Area (`PendingOrders`)

File path: `output/cache/pending_orders.json`

Format: `{ order_no: { ...fields..., sync_status, synced_at } }`

| `sync_status` | Meaning |
|---------------|---------|
| `pending` | Not yet written to Bitable |
| `synced` | Successfully synced to Bitable |
| `已更新` | Modified order awaiting overwrite in Bitable |
| `已取消` | Cancelled order awaiting status update in Bitable |
| `拆单` | Split child order awaiting insertion into Bitable |
| `anomaly` | Unparseable date; skipped as an exception |

**`add_orders` Merge Rules**:

| Existing Status | Incoming Data | Outcome |
|-----------------|---------------|---------|
| Not present | Any | Insert as `pending` |
| `synced` | Content changed | Overwrite and set to `已更新` |
| `synced` | No change | Keep unchanged |
| `pending` | Any | Overwrite directly |
| `anomaly` / `已取消` | Any | Keep unchanged (do not overwrite) |

---

## Field Normalization Pipeline

`FieldNormalizer.normalize()` executes the following stages sequentially:

```text
OrderInfoNormalizer.normalize_date      # Date standardization (→ YYYY/MM/DD)
RequirementNormalizer.normalize         # Customer requirement denoising
ContactNormalizer.normalize             # Contact cleaning & phone number extraction
AddressNormalizer.normalize_address     # Address normalization (exact / fuzzy matching)
AddressNormalizer.normalize_receiver    # Receiving unit (consignee) matching
AddressNormalizer.normalize_city        # Destination province & city extraction (jionlp NLP)
LogisticsNormalizer.normalize_shipping  # Transport mode standardization
LogisticsNormalizer.normalize_danger    # Hazmat category identification
```

---

## Bitable Field Mapping

| Bitable Field (CN) | Source | Notes |
|--------------------|--------|-------|
| 客户名 (Client) | Constant `"芜湖PPG"` | |
| 单号 (Order No.) | `order_no` | |
| 订单状态 (Order Status) | Mapped from `sync_status` | Normal / Updated / Cancelled / Split |
| 下单日期 (Order Date) | `order_date` | Converted to Unix timestamp (ms) |
| 地址状态 (Address Match) | `address_exact_match` | Exact Match / Fuzzy Match |
| 收货单位 (Receiving Unit) | `receiver` | |
| 收货公司名 (Company Name) | `company_name` | |
| 收货地址 (Address) | `address` | |
| 收货人 (Contact) | `contact` | |
| 客户要求 (Requirements) | `requirement` | |
| 数量 (Quantity) | `quantity` | `float` |
| 重量 (Weight) | `weight` | `float` (strips `KG` suffix) |
| 发运方式 (Transport Mode) | `发运方式` | |
| 始发城市 (Origin) | Constant `"马鞍山库"` | |
| 到货城市 (Dest. City) | `到货城市` | |
| 到货省份 (Dest. Province) | `到货省份` | |
| 产品特性 (Product Property) | `危险品类别` | Hazmat category |

---

## Idempotency Guarantees

- **Email Deduplication**: Processed UIDs are persisted in `output/cache/seen_mails.json` and skipped on subsequent runs.
- **Order Deduplication**: `PendingOrdersManager.add_orders` deduplicates by `order_no` and avoids overwriting unchanged `synced` records.
- **Bitable Writes**: `sync_orders.py` builds an `order_no → record_id` index before syncing, routing cancellations and modifications through `batch_update_records` to prevent duplicate rows.
- **Shipping Cache**: Non-`wenjuan` `SHIPPING_INFO` records are write-once to prevent repeated runs from corrupting historical data.

---

## RAG Knowledge Base

The `rag/` module provides a **semantic retrieval fallback** for logistics field normalization. When deterministic rules cannot cover long-tail edge cases, the RAG pipeline retrieves the most relevant excerpts from internal knowledge documents via vector similarity to assist with transport mode inference, hazmat classification, and SOP reminders.

> **Design Principle**: RAG outputs are emitted strictly as `_rag_hint` advisory fields and **never overwrite** core fields determined by the rule engine.

---

### Architecture Overview

```text
knowledge/ (Markdown documents)
    │
    ▼
document_loader.py   ── Recursively loads .md files & chunks by headings (H1/H2)
    │
    ▼
embedder.py          ── Embeddings (local: MiniLM-L12 | openai: text-embedding-3-small)
    │
    ▼
vector_store.py      ── ChromaDB persistence (output/rag_index/)
    │
    ▼
retriever.py         ── Cosine similarity search + category & score_threshold filtering
    │
    ▼
rag_pipeline.py      ── Unified interface: build() / query() / explain(order_dict)
```

---

### Knowledge Directory Structure

Knowledge documents are stored under `knowledge/` and organized by business category:

| Subdirectory | `category` Tag | Content |
|--------------|----------------|---------|
| `transport_modes/` | `transport_modes` | Rules for LTL, FTL, temperature-controlled, chartered, and self-pickup transport |
| `goods_classification/` | `goods_classification` | Hazmat categories, packaging groups, and chemical identification rules |
| `sop/` | `sop` | Standard operating procedures and client-specific handling reminders |

- **Format**: `.md` (UTF-8)
- **Chunking Strategy**: Splits by `##` (H2) and higher headings; short fragments (< 60 characters) are automatically merged into the next chunk.
- **Metadata**: Each chunk includes `chunk_id`, `source_file`, `category`, and `heading`.

**Extending the Knowledge Base**: Add new `.md` files to the appropriate subdirectory and re-run the index builder.

---

### Module Descriptions

| File | Description |
|------|-------------|
| `rag_pipeline.py` | Unified RAG entry point with lazy singleton initialization; exposes `build()`, `query()`, and `explain()`. |
| `build_index.py` | CLI for index building, supporting full rebuilds (`--rebuild`) and interactive testing (`--test`). |
| `document_loader.py` | Recursively scans `knowledge/`, chunks Markdown by headings, and emits chunks with metadata. |
| `embedder.py` | Unified embedding interface supporting both local `sentence-transformers` and OpenAI API backends. |
| `vector_store.py` | ChromaDB wrapper supporting `upsert` (incremental updates), `clear`, and `count`. |
| `retriever.py` | Semantic retriever supporting `category` metadata filtering and `score_threshold` cutoffs. |

---

### Index Building

**Build the index before first use**, and rebuild whenever documents under `knowledge/` are updated.

```bash
# Install dependencies (if not already installed)
pip install chromadb>=0.4.0 sentence-transformers>=2.2.0

# Full rebuild (clears old index; recommended for initial setup or major updates)
python rag/build_index.py --rebuild

# Incremental update (upserts changed chunks only)
python rag/build_index.py

# Build and launch interactive query testing
python rag/build_index.py --rebuild --test
```

Interactive mode supports filtering by `category` (`transport_modes` / `goods_classification` / `sop` / Enter for all). Type `q` to quit.

The vector index is persisted to `output/rag_index/` (ChromaDB format) and does not need to be rebuilt on every run.

---

### API Usage

#### Single Semantic Query

```python
from rag.rag_pipeline import RAGPipeline

rag = RAGPipeline()

# category options: "transport_modes" / "goods_classification" / "sop" / None (all)
hits = rag.query("零担运输危险品", category="transport_modes", top_k=3)

for hit in hits:
    print(hit["score"])            # Cosine similarity [0, 1]
    print(hit["text"])             # Chunk content
    print(hit["metadata"])         # {category, source_file, heading}
```

#### Comprehensive Order Inference

```python
order = {
    "address":      "上海市浦东新区某路1号",
    "requirement":  "保温车运输，冬季防冻",
    "company_name": "上汽大众",
    "weight":       "1500",
    "发运方式":     "",
}

hints = rag.explain(order)
# Returns:
# {
#   "shipping_rag_hint": "...",   # Transport mode advisory
#   "danger_rag_hint":   "...",   # Hazmat classification advisory
#   "sop_rag_hint":      "...",   # SOP handling reminder
# }
```

`explain()` internally executes three `query()` calls:

| Hint Field | Query Source | Category Filter |
|------------|--------------|-----------------|
| `shipping_rag_hint` | `requirement + address + weight + 发运方式` | `transport_modes` |
| `danger_rag_hint` | `company_name + requirement + 危险品类别` | `goods_classification` |
| `sop_rag_hint` | `requirement + address + 发运方式 + 危险品类别` | `sop` |

---

### Embedding Backend Configuration

Configure in `.env` (defaults to the offline local model):

```env
# local (default): uses sentence-transformers, runs offline (~120 MB)
EMBEDDING_BACKEND=local

# openai: uses text-embedding-3-small, requires an API key
# EMBEDDING_BACKEND=openai
# OPENAI_API_KEY=<your-api-key>
```

| Backend | Model | Dimensions | Internet Required |
|---------|-------|------------|-------------------|
| `local` (default) | `paraphrase-multilingual-MiniLM-L12-v2` | 384 | No (auto-downloads on first run) |
| `openai` | `text-embedding-3-small` | 1536 | Yes |

> The local model natively supports mixed Chinese-English text and is recommended for this logistics workflow.

---

### Unit Tests

```bash
# Run with pytest
python -m pytest test/test_rag.py -v

# Or run directly
python test/test_rag.py
```

Test coverage:

| Test Class | Coverage |
|------------|----------|
| `TestDocumentLoader` | Directory existence, non-empty chunk loading, field integrity, presence of all three categories, non-empty text |
| `TestEmbedder` | Single/batch embedding output, vector dimension consistency, empty input handling |
| `TestRAGPipeline` | Post-build `count > 0`, relevant query hits, result schema, score bounds, category filtering, empty-string resilience, `explain()` field completeness |

> `TestEmbedder` and `TestRAGPipeline` are automatically skipped if `sentence-transformers` or `chromadb` is not installed.