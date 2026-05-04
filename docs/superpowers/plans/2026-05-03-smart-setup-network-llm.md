# Smart Setup Network LLM Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add setup-time Google AI Studio decisioning over DOM and network candidates, while saving deterministic monitor recipes that survive dynamic list reordering.

**Architecture:** Browser monitor mode keeps the existing DOM selection flow, captures recent JSON network responses, generates network candidates, asks a setup advisor for a source/recipe decision, verifies the recipe locally, and stores a deterministic target. Monitor checks use the saved recipe only and never call the LLM.

**Tech Stack:** Python/FastAPI/Playwright, urllib for Google AI Studio REST calls, existing browser overlay JavaScript.

---

## Chunk 1: Core Smart Setup Engine

### Task 1: Candidate and Recipe Model

**Files:**
- Create: `backend/src/openpulse/smart_setup.py`
- Test: `backend/tests/test_smart_setup.py`

- [ ] **Step 1: Write failing tests** for network candidate generation, LLM decision validation, reorder-safe extraction, and missing-entity behavior.
- [ ] **Step 2: Run tests** with `.venv/bin/pytest backend/tests/test_smart_setup.py -q` and verify failures are for missing module/functions.
- [ ] **Step 3: Implement minimal core module** with JSON path helpers, candidate generation, smart setup service, and network recipe extraction.
- [ ] **Step 4: Run focused tests** until they pass.

## Chunk 2: Google AI Studio Advisor

### Task 2: Setup Advisor Provider

**Files:**
- Modify: `backend/src/openpulse/smart_setup.py`
- Test: `backend/tests/test_smart_setup.py`

- [ ] **Step 1: Add tests** for request payload shape and response parsing using a fake URL opener.
- [ ] **Step 2: Implement `GoogleAIStudioAdvisor`** using `GOOGLE_API_KEY`/`GEMINI_API_KEY`, strict JSON output, and a configurable model.
- [ ] **Step 3: Run focused tests** until provider behavior is covered without real network calls.

## Chunk 3: Browser Integration

### Task 3: Capture Network During Setup and Checks

**Files:**
- Modify: `backend/src/openpulse/browser.py`
- Test: `backend/tests/test_browser_controller.py`

- [ ] **Step 1: Add tests** for selection enrichment through a fake smart setup service and network recipe extraction during checks.
- [ ] **Step 2: Attach response capture** to managed browser pages and enrich selections in the existing binding.
- [ ] **Step 3: Try network recipe extraction before DOM extraction** for network-backed targets.
- [ ] **Step 4: Run focused browser tests.**

## Chunk 4: API/UI Summary

### Task 4: Surface Smart Setup Decisions

**Files:**
- Modify: `backend/src/openpulse/app.py`
- Modify: `backend/src/openpulse/static/app.js`
- Test: `backend/tests/test_app.py`

- [ ] **Step 1: Update monitor summaries** so saved network monitors are readable.
- [ ] **Step 2: Preserve existing DOM save flow** for static selections and for setup failures.
- [ ] **Step 3: Run backend tests.**
