**Overview**
DataCompare AI eliminates the manual effort of comparing datasets by using LLM-based agents to automatically ingest, align, and analyze data at scale. It delivers explainable insights through a self-serve dashboard — no technical expertise required.

**Features**

- Drag & drop upload — supports CSV and Excel files
- Auto schema detection — identifies data types and key columns
- AI-powered comparison — cross-dataset analysis using LangGraph + OLLAMA
- Results dashboard — Summary, Analysis, Compare, and Insights tabs
- Export — download results as PDF, Excel, or ZIP
- History — reload and revisit past analysis sessions


**Tech Stack**
LayerTechnologiesAI / LLMOLLAMA, LangGraph, ChromaDB (RAG)BackendPython, MySQL / PostgreSQL, RedisML PipelineK-means, DBSCAN, IQR / Z-score outlier detectionExportPDF generator, Excel writer, ZIP packagerStorageFile system (hierarchical), ChromaDB vectors

**Architecture**

┌──────────────────────────────────────────────────────┐
│                  User Interface Layer                │
│     Upload · View Results · Export · History         │
└────────────────────┬─────────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────────┐
│                Orchestration Layer                   │
│  Session Manager · File Processor · Analysis Engine  │
│  Comparison Engine · Export Builder                  │
└────────────────────┬─────────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────────┐
│             Enterprise AI Platform                   │
│   LangGraph Agent · LLM (OLLAMA) · RAG (ChromaDB)   │
│   ML Pipeline (Phase 2)                              │
└────────────────────┬─────────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────────┐
│               Data Services Layer                    │
│     MySQL/PostgreSQL · ChromaDB · Redis · FileSystem │
└──────────────────────────────────────────────────────┘

**How It Works**
1. Upload datasets (CSV / Excel)
       ↓
2. File validation & schema detection
       ↓
3. Review preview → confirm or modify schema
       ↓
4. Click Analyze → AI processes & compares datasets
       ↓
5. View results on dashboard (Summary / Analysis / Compare / Insights)
       ↓
6. Export (PDF / Excel / ZIP) → saved to history

**File Storage Structure**
/sessions/{session_id}/
    ├── processed/       # cleaned input files
    └── results/         # analysis output

/exports/{user_id}/      # temp download links (48 hr expiry)



