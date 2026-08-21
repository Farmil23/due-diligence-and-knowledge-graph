---
title: Autonomous Due Diligence & Knowledge Graph
emoji: 🕵️
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: true
---

<div align="center">

# FinAgent — Autonomous B2B Due Diligence AI

### Forensic Knowledge Graph Investigator

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-Agentic_Workflow-FF6C37?style=for-the-badge&logo=langchain&logoColor=white)](https://langchain.com)
[![Neo4j](https://img.shields.io/badge/Neo4j_AuraDB-008CC1?style=for-the-badge&logo=neo4j&logoColor=white)](https://neo4j.com)
[![OpenAI](https://img.shields.io/badge/GPT--4o-412991?style=for-the-badge&logo=openai&logoColor=white)](https://openai.com)

<p align="center">
  <b>A powerful AI Agent for forensic corporate analysis.</b><br>
  Upload a corporate document → LLM extracts entities & relationships → Neo4j stores the graph → ask anything via natural language.
</p>

</div>

---

## 📖 Overview

**FinAgent** is an autonomous B2B Due Diligence AI Agent built on **LangGraph**. It transforms unstructured corporate documents into a forensic **Knowledge Graph** stored in **Neo4j AuraDB**, then lets analysts query it using natural language.

The core agent logic is cleanly encapsulated and exposed via a robust **FastAPI** backend, allowing for seamless programmatic document ingestion and deep investigative queries.

### Key Capabilities

| Capability | Detail |
|---|---|
| **Entity Extraction** | LLM extracts `Company`, `Person`, `Address`, `Document` nodes with roles & properties |
| **Relationship Mapping** | `OWNS_SHARE`, `DIRECTS`, `BORROWS_FROM`, `LENDS_TO`, `REGISTERED_AT`, `TRANSFERRED_TO` |
| **Shell Company Detection** | Flags entities registered in tax-haven jurisdictions |
| **Beneficial Ownership** | Multi-hop graph traversal reveals hidden controllers |
| **REST API** | Clean API endpoints for document upload (`/api/upload`) and querying (`/api/investigate`) |

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                           Client / UI                           │
│                 Upload Documents | Ask Questions                │
└──────────────┬──────────────────────────────────────────────────┘
               │ HTTP REST
┌──────────────▼──────────────────────────────────────────────────┐
               │    FastAPI Gateway (api.py / port 8000)          │
               │  POST /api/upload  │  POST /api/investigate      │
└──────────────┬──────────────────────────────────────────────────┘
               │ 
┌──────────────▼──────────────────────────────────────────────────┐
│              LangGraph Agentic Workflow (main.py)               │
│                                                                 │
│  ┌──────────▼──────────┐                                        │
│  │      planning       │  LLM decomposes the question           │
│  └──────────┬──────────┘                                        │
│  ┌──────────▼──────────┐                                        │
│  │     write_query     │  LLM generates Cypher query            │
│  └──────────┬──────────┘                                        │
│  ┌──────────▼──────────┐                                        │
│  │      run_query      │  Execute against Neo4j AuraDB          │
│  └──────────┬──────────┘                                        │
│  ┌──────────▼──────────┐                                        │
│  │     answer_user     │  GPT-4o synthesises KYC report         │
│  └──────────┬──────────┘                                        │
│             ▼ END                                               │
└─────────────────────────────────────────────────────────────────┘
               │ read/write
┌──────────────▼──────────────────────────────────────────────────┐
│               Neo4j AuraDB (Cloud Knowledge Graph)              │
│  Nodes: Company │ Person │ Address │ Document                   │
│  Edges: OWNS_SHARE │ DIRECTS │ BORROWS_FROM │ REGISTERED_AT     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🗂️ Project Structure

```
due-diligence-and-knowledge-graph/
├── api.py                      # FastAPI REST server entry point
├── main.py                     # CLI and LangGraph workflow entry point
├── text_extraction.py          # Direct text extraction utility script
├── app/
│   ├── core/
│   │   ├── config.py           # Settings from env vars
│   │   └── logging.py          # Logger configuration
│   ├── db/
│   │   └── neo4j_client.py     # Neo4j database driver connection
│   └── services/
│       ├── workflow.py         # LangGraph graph definition
│       ├── graph_extractor.py  # PDF → LLM → Neo4j entity extraction
│       ├── graph_retriever.py  # Natural language → Cypher translation
│       └── llm_service.py      # OpenAI / Groq LLM client
├── testing/
│   ├── dummy_report.txt        # Sample report for testing extraction
│   └── test_upload_doc.py      # Script to test the upload pipeline
├── requirements.txt
└── .env.example                # Environment variable template
```

---

## 🚀 Quick Start (Local)

### 1. Prerequisites
- Python 3.10+
- Conda (optional but recommended)
- Neo4j AuraDB account (free tier)
- OpenAI API key

### 2. Install
```bash
git clone https://github.com/Farmil23/due-diligence-and-knowledge-graph.git
cd due-diligence-and-knowledge-graph

# Using Conda
conda create -y -n auto_graph python=3.11
conda activate auto_graph
pip install -r requirements.txt
```

### 3. Configure
```bash
cp .env.example .env
# Edit .env with your actual keys (Neo4j and OpenAI)
```

### 4. Run API Server
```bash
conda activate auto_graph
python api.py
```

Open `http://localhost:8000/docs` to access the interactive Swagger UI API documentation.

### 5. Alternative: Run via CLI
You can bypass the API and run the agent directly in the terminal:
```bash
python main.py
```
Or test the document extraction script directly:
```bash
python testing/test_upload_doc.py
```

---

## 🔐 Environment Variables

| Variable | Description |
|---|---|
| `NEO4J_URI` | AuraDB connection URI (`neo4j+s://...`) |
| `NEO4J_USERNAME` | AuraDB username |
| `NEO4J_PASSWORD` | AuraDB password |
| `NEO4J_DATABASE` | AuraDB database name |
| `OPENAI_API_KEY` | OpenAI API key (GPT-4o) |
| `GROQ_API_KEY` | Groq API key (fallback LLM) |

---

## 📊 Knowledge Graph Schema

```
(Company)-[:OWNS_SHARE]->(Company)
(Company)-[:BORROWS_FROM]->(Company)
(Company)-[:LENDS_TO]->(Company)
(Company)-[:PAYS_DEBT_TO]->(Company)
(Company)-[:TRANSFERRED_TO]->(Company)
(Company)-[:REGISTERED_AT]->(Address)
(Company)-[:MENTIONED_IN]->(Document)
(Person)-[:DIRECTS]->(Company)
(Person)-[:WORKS_FOR]->(Company)
(Person)-[:MENTIONED_IN]->(Document)
```

---

<div align="center">
Made with ❤️ by <strong>Farhan Kamil Hermansyah</strong>
</div>
