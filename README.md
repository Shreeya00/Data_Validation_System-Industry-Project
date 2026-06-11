# DataCompare AI

**Automated AI-powered dataset comparison — from hours to minutes.**

[![Status](https://img.shields.io/badge/status-in%20development-yellow)](.)
[![Timeline](https://img.shields.io/badge/timeline-12%20weeks-blue)](.)
[![Phase](https://img.shields.io/badge/phase-discovery-lightgrey)](.)

---

## Overview

DataCompare AI eliminates the manual effort of comparing datasets by using LLM-based agents to automatically ingest, align, and analyze data at scale. It delivers explainable insights through a self-serve dashboard — no technical expertise required.

---

## Features

- **Drag & drop upload** — supports CSV and Excel files
- **Auto schema detection** — identifies data types and key columns
- **AI-powered comparison** — cross-dataset analysis using LangGraph + OLLAMA
- **Results dashboard** — Summary, Analysis, Compare, and Insights tabs
- **Export** — download results as PDF, Excel, or ZIP
- **History** — reload and revisit past analysis sessions

---

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| **AI / LLM** | OLLAMA, LangGraph, ChromaDB (RAG) |
| **Backend** | Python, MySQL / PostgreSQL, Redis |
| **ML Pipeline** | K-means, DBSCAN, IQR / Z-score outlier detection |
| **Export** | PDF generator, Excel writer, ZIP packager |
| **Storage** | File system (hierarchical), ChromaDB vectors |

---

## Architecture

```
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
```

---

## How It Works

```
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
```

---

## Who Is This For?

| Persona | Role | What they get |
|---------|------|---------------|
| **Business / Leadership** | Operations Leads, Finance Controllers, Executives | Decision summaries, compliance reports, automated reconciliation |
| **Technical / Functional** | Data Analysts, Business Analysts, Marketing Managers | Automated workflows, cross-platform comparison, accurate output |

---

## Impact

| Metric | Before | After | Gain |
|--------|--------|-------|------|
| Time per analysis | 4–8 hrs | 5–10 min | 95% faster |
| Error rate | 15–20% | < 2% | 90% reduction |
| User accessibility | 20% | 100% | 5× more users |
| Data coverage | 10–20% | 100% | 5–10× more data |
| Cost per analysis | $50–150 | $2–5 | 95% cheaper |

---

## Known Edge Cases

<details>
<summary>Click to expand</summary>

| Category | Handled Scenarios |
|----------|------------------|
| File Upload | Password-protected, corrupted, or oversized (> 500MB) files |
| Data Quality | Empty datasets post-cleaning, < 100 rows |
| Data Types | Mixed types, ambiguous formats, stripped leading zeros |
| Comparison | Zero column matches, 1000× size difference between datasets |
| AI / LLM | Context overflow, rate limits, API timeouts, hallucinated columns |
| Database | Connection failures, disk < 1GB, Redis down, session not found |
| Security | SQL injection, malicious file uploads, extremely long field names |
| Concurrency | Session deleted mid-run, simultaneous access, stale cache |

</details>

---

## Roadmap

```
Week 1   ░░ Discovery & Design
Week 2   ████ Foundation (DB setup, file upload, session management)
Week 3-4 ████████ Analysis Engine (stats, outliers, clustering)
Week 5-6 ████████████ AI Integration (LLM, embeddings, NLP)
Week 7-8 ████████████████ Data Comparison Engine
Week 9-10 ████████████████████ History & Export
Week 11  ██████████████████████ Testing & Bug Fixes
Week 12  ████████████████████████ Deployment & Soft Launch
```

---

## Risks

| Risk | Impact | Priority |
|------|--------|----------|
| Market & Competition | Revenue / market share loss | Critical |
| Operational delays | Quality issues, slow rollout | Critical |
| Low user adoption | Underutilization | Critical |
| Vendor / dependency issues | Service disruption | Important |

---

## File Storage Structure

```
/sessions/{session_id}/
    ├── processed/       # cleaned input files
    └── results/         # analysis output

/exports/{user_id}/      # temp download links (48 hr expiry)
```

---

##  Phase 2 — Business Rule Validation
 
Allows users to define plain-language business rules against their dataset. The system validates every row and flags violations — returning a clear **PASS** or **FAIL** result per rule.
 
### How It Works
 
```
User defines a rule (natural language)
        ↓
System parses & maps rule to dataset columns
        ↓
Validates every row against the rule
        ↓
        ├── PASS — All rows satisfy the rule
        └── FAIL — Returns violating rows with rule context
```
 
### Example
 
**Rule entered by user:**
```
All products should be delivered.
```
 
**Result — Rule FAILED:**
 
| Row | Product ID | Product Name   | Status      | Violation              |
|-----|------------|----------------|-------------|------------------------|
| 4   | P-1042     | Wireless Mouse | Pending     |  Not delivered        |
| 9   | P-1087     | USB Hub        | In Transit  |  Not delivered        |
| 15  | P-1093     | Keyboard       | Cancelled   |  Not delivered        |
 
>  **Business Rule FAILED** — 3 of 15 rows violate the rule: `All products should be delivered`
 
**Result — Rule PASSED:**
>  **Business Rule PASSED** — All 15 rows satisfy the rule: `All products should be delivered`
 
---
 
### Supported Rule Patterns
 
| Rule Type | Example |
|-----------|---------|
| **Completeness** | `All orders must have a customer ID` |
| **Status check** | `All products should be delivered` |
| **Range / threshold** | `Invoice amount must be greater than 0` |
| **Uniqueness** | `No duplicate order IDs should exist` |
| **Conditional** | `If order status is Shipped, tracking ID must not be empty` |
 
### Key Capabilities
 
-  **Natural language input** — users write rules in plain English, no query language needed
-  **Row-level violation detection** — pinpoints exactly which rows fail and why
-  **Compilation report** — downloadable list of all violating rows with rule context
-  **Pass / Fail badge** — instant visual verdict per rule
-  **Multi-rule support** — run multiple rules in a single validation pass
-  **Export violations** — download failing rows as CSV / Excel for follow-up
