# Steel Slag Small Language Model (SLM) — Project Brief

Transcribed from the sponsor documents *Project Overview.pdf* and *WorkFlow Plan.pdf* (sponsor: Carbon Negative Solutions, CNS).

## Objective

Build a Steel Slag (non-GGBFS) SLM Minimal Viable Product (MVP) with:

- Curated knowledge database
- Automated retrieval pipeline
- Evaluation report: retrieval accuracy, citation accuracy, hallucination rate
- Technical reports (for handoff)

## Overview

Cement is ~8% of global CO₂ emissions. Replacing Portland cement with supplementary cementitious materials (SCMs) is a major decarbonization lever. GGBFS is well studied; **non-GGBFS steel slags** — electric arc furnace (EAF) slag, basic oxygen furnace (BOF) slag, ladle slag, stainless steel slag and related byproducts — are underutilized due to variable chemistry and poorly understood processing pathways.

The project builds a domain-specific SLM grounded exclusively in public literature, technical reports, patents, ASTM standards, conference proceedings and government datasets on steel slag in cementitious materials. It is an R&D research assistant (not a general chatbot) that accelerates literature review, identifies knowledge gaps, summarizes processing methods, and serves as a foundation for future predictive ML models.

## Data sources

Peer-reviewed journal articles · patents · ASTM standards · DOE reports · USGS publications · conference papers · university theses · technical manuals

## Data ingestion fields

- Steel slag type and source
- Chemical composition (CaO, SiO₂, Al₂O₃, MgO, FeO/Fe₂O₃, etc.)
- Mineralogical characteristics
- Particle size / fineness and processing conditions
- Grinding, activation, carbonation, or other treatment methods
- Cement replacement percentage
- Water demand and workability
- Setting time
- Compressive strength and strength development
- Pozzolanic / reactivity indicators
- CO₂ uptake / carbonation data, where available
- Expansion / stability and durability measurements
- Relevant ASTM or other testing methodologies

## Data engineering

Automated pipelines that:

- clean PDFs
- remove duplicate publications
- extract tables
- preserve citations
- organize metadata
- classify publications by topic
- index figures and equations where practical

## Domain knowledge graph

Automatically identify and connect relationships along this flow:

Feedstock (e.g. EAF slag) → Chemical composition (oxides) → Processing method (e.g. wet milling) → Particle size (e.g. d50 40 µm) → Activation technique (e.g. calcination) → Concrete performance (e.g. compressive strength) → Durability (e.g. permeability) → Carbon uptake (CO₂ uptake %, when applicable) → Relevant citations (extracted studies)

## Target questions and answer format

Adapt an open-source LLM via RAG and/or parameter-efficient fine-tuning to answer questions such as:

- What causes volumetric instability in BOF slag?
- Which activation methods improve hydraulic reactivity?
- How does MgO influence expansion?
- Which grinding methods maximize cement replacement?
- Which ASTM standards evaluate SCM performance?
- Which papers report successful >30% cement replacement?

Every technical response must cite: source paper, authors, publication year, DOI (if available), and a confidence score. The goal is trustworthy, traceable answers, not hallucinations.

The model should also identify research gaps: underexplored processing pathways, conflicting literature, inconsistent experimental results, and opportunities for future experimentation.

## Compute / infrastructure

MVP scope — no foundation-model training from scratch. Columbia / MSDS compute and cloud resources may be used. The idea is to "fork" an existing LLM (e.g. ChatGPT Deep Research) into this domain; CNS covers the platform subscription cost.

## Workflow plan (milestones)

| Date | Milestone | Key deliverables |
|---|---|---|
| 2026-10-02 | Project definition & data pipeline established | Finalize scope and success metrics. Identify and prioritize public data sources. Establish ingestion pipeline, repo structure, dev environment. |
| 2026-10-16 | Knowledge base & vector database completed | Curate/preprocess corpus. Extract metadata and citations, generate embeddings, build searchable vector DB. Demo semantic search returning relevant publications. |
| 2026-10-30 | Domain-specific SLM & RAG prototype | Domain-forked LLM with RAG pipeline. Demo citation-backed answers on non-GGBFS steel slag SCM questions. |
| 2026-11-20 | Model evaluation & feature refinement | Evaluate retrieval accuracy, citation fidelity, response quality, hallucination rate. Refine prompts/retrieval/model. Deliver beta to CNS for user testing. |
| 2026-12-11 | Final MVP delivery & capstone presentation | Production-ready MVP, curated literature DB, automated ingestion pipeline, technical docs, evaluation report, final presentation with future/commercialization recommendations. |

## KPIs

1. **10k steel slag (non-GGBFS) research papers scrubbed.** Starting point: ~3,600 studies compiled in *Bibliometric Analysis of Steelmaking Slag-Related Studies for Research Trends and Future Directions* — https://www.researchgate.net/publication/365817390
2. **≥1,000 steel-slag observations/experiments** extracted as structured data records
3. **≥90% retrieval accuracy** on curated test questions
4. **≥90% technical answer accuracy** (CNS will assess on the backend)
5. **<5% hallucination rate** on curated test questions
6. **100% reproducibility** outside Columbia student access

## Final deliverable — Technology Transfer Package

All source code, structured datasets, permitted source documents/metadata, knowledge graph files, vector indexes or reproducible embedding-generation scripts, prompts, configuration files, evaluation datasets, model/API specifications, environment specifications, and technical documentation needed for CNS to reproduce and continue development in a CNS-controlled cloud environment. **The final system must be deployable independently of the student team's personal accounts or computing environment.**
