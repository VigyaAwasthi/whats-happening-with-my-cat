# Whisker Rooms

### A safety-first, retrieval-grounded AI companion for understanding your cat

[![Backend](https://img.shields.io/badge/backend-FastAPI-009688)](https://backend-production-d078b.up.railway.app/docs)
[![Frontend](https://img.shields.io/badge/frontend-Cloudflare%20Workers-F38020)](https://whisker-rooms-frontend.vigya-apps.workers.dev)
[![Database](https://img.shields.io/badge/database-PostgreSQL%20%2B%20pgvector-4169E1)](#technology-stack)
[![Type Safety](https://img.shields.io/badge/type%20safety-Pydantic%20%2B%20TypeScript-blue)](#verification)
[![Safety](https://img.shields.io/badge/emergency%20recall-100%25-success)](#evaluation-snapshot)

Whisker Rooms is a full-stack cat companion that combines deterministic safety systems, hybrid retrieval, structured LLM generation, cat-specific memory, and generation-level observability.

It helps cat owners:

- Understand everyday behavior without pretending every question has one certain answer
- Navigate health concerns using curated veterinary information and deterministic emergency screening
- Maintain separate profiles, conversations, photos, and memories for multiple cats
- See where an answer came from and provide feedback linked to the exact generated response

The governing principle is:

> **The model proposes, code disposes.**

The language model can generate structured proposals. Code owns routing, safety decisions, evidence requirements, authorization, cost limits, and final response validation.

---

## Live deployment

| Service | URL |
| --- | --- |
| Web application | [whisker-rooms-frontend.vigya-apps.workers.dev](https://whisker-rooms-frontend.vigya-apps.workers.dev) |
| Backend API | [backend-production-d078b.up.railway.app](https://backend-production-d078b.up.railway.app) |
| Interactive API documentation | [backend-production-d078b.up.railway.app/docs](https://backend-production-d078b.up.railway.app/docs) |
| OpenAPI specification | [backend-production-d078b.up.railway.app/openapi.json](https://backend-production-d078b.up.railway.app/openapi.json) |

### Current deployment

- **Frontend:** React, TypeScript, vinext, Vite, and Cloudflare Workers
- **Backend:** FastAPI deployed on Railway
- **Authentication:** Supabase Auth with production email confirmation
- **Database:** Supabase PostgreSQL with pgvector
- **Media:** Private Supabase Storage with owner-scoped access policies
- **Model orchestration:** Anthropic structured generation with deterministic development substitutes
- **Retrieval:** pgvector, PostgreSQL full-text search, reciprocal rank fusion, and cross-encoder reranking

---

## Product experience

### Behavior Corner

The Behavior Corner explains everyday cat behavior while separating evidence-backed interpretation from general feline knowledge.

It supports two explicit answer modes:

- **`corpus_grounded`**  
  Used only when deterministic retrieval evidence is strong enough. The response includes resolved source metadata and corpus-backed clarifying questions.

- **`general_knowledge`**  
  Used when the corpus does not contain sufficiently strong evidence. The response is personalized using the active cat profile, carries no citations, and is capped at `varies-by-cat` confidence.

The model does not choose the mode. Deterministic code compares lexical, semantic, reranking, and query-coverage evidence.

### Health Corner

The Health Corner provides safety-first triage support from curated veterinary material.

It can return:

- `triage`
- `emergency_canned`
- `no_reliable_information`

Emergency decisions are made by deterministic code before generative model execution. The system never infers urgency from generated prose.

Medical claims must be supported by retrieved veterinary corpus entries. If grounding fails, the system performs one bounded retry and then strips unsupported claims or refuses to answer.

### Cat profiles

Each cat has an isolated profile containing information such as:

- Name and age
- Breed
- Energy level
- Health context
- Theme and display preferences
- Profile photo

The frontend supports profile editing, photo updates, browser-side media checks, and HEIC-aware upload validation.

### Moments and fun facts

- **Moments** provide a private scrapbook for cat memories and media.
- **Fun facts** are selected from curated, tagged records.
- Moment content is deliberately excluded from AI retrieval and conversational memory.

---

## System architecture

```mermaid
flowchart LR
    U[Cat owner] --> UI[React and TypeScript frontend]

    UI --> AUTH[Supabase Auth]
    UI --> STORAGE[Private Supabase Storage]
    UI --> API[FastAPI backend]

    API --> SAFETY[Deterministic safety gates]
    API --> MEMORY[Cat-scoped session and long-term memory]
    API --> RETRIEVAL[Hybrid retrieval]
    API --> TRACE[Generation tracing]
    API --> FEEDBACK[Generation-linked feedback]

    RETRIEVAL --> VECTOR[pgvector semantic search]
    RETRIEVAL --> FTS[PostgreSQL full-text search]
    VECTOR --> RRF[Reciprocal rank fusion]
    FTS --> RRF
    RRF --> RERANK[Cross-encoder reranking]

    SAFETY --> ORCH[Plain-Python orchestration]
    MEMORY --> ORCH
    RERANK --> ORCH

    ORCH --> LLM[Schema-constrained model calls]
    LLM --> VALIDATION[Pydantic validation and grounding gates]
    VALIDATION --> API

    TRACE --> DB[(PostgreSQL)]
    FEEDBACK --> DB
    MEMORY --> DB
