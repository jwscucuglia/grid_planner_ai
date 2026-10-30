# REV06 numeric and documentary maintenance

> **For agentic workers:** Use superpowers:executing-plans to implement the checked tasks in this session.

**Goal:** Close the three reproducible residual input-domain findings without changing the audited benchmark or widening electrical claims.

**Architecture:** Bound raw JSON nesting before parsing, and scan finite values iteratively. Compute crew travel from distance/speed before rounding; the independent verifier reconstructs that value separately and reports nonrepresentable derived times. Preserve the audited REV06 release and publish a separately identified maintenance supplement.

**Tech Stack:** Python, pytest, existing SciPy MILP, Google Docs/Slides connector.

**Spec:** Public acceptance requirements summarized below from the independent review of cf386439; private audit evidence stays outside Git.

## Global constraints

- No D06 code/data; no telecomandos, production deployment, merge or submission.
- Same 12 known synthetic inputs, seeds, weights and B1 radius 0.75 km.
- No mandatory order may disappear or receive a false feasibility/optimality claim.
- Preserve the independent verifier and physical radial scope.
- Preserve sharing state; prepare attachments instead of opening private documents publicly.

## Review focus

- JSON depth in manifest, JSON/GeoJSON tables and JSON-in-CSV must report JSON_DEPTH_EXCEEDED.
- Quoted brackets and escaped quotes must not count as nesting.
- 400 and 512 nested containers remain parseable; depth 513 and 1500 are rejected.
- Tiny distance with tiny speed must retain a one- or ten-hour travel duration.
- Overflowing travel or coordinate differences must produce finite serializable infeasibility reports.

## Task 1: JSON boundary

Files: src/kgp/contracts.py; tests/test_review_residuals.py; docs/CONTRATO_DADOS_INGESTAO.md if present.
Interface: strict_json_loads(str) -> Any, JSONDepthError(ValueError).code = JSON_DEPTH_EXCEEDED; maximum 512 containers.

- [x] Add direct, plan-reader, four import-location and CLI regression tests; run RED.
- [x] Add a string-aware lexical depth bound and iterative nonfinite_paths scan; no generic exception catch.
- [x] Run JSON regressions GREEN, retaining duplicate-key/nonfinite/underflow checks.

## Task 2: Travel arithmetic

Files: src/kgp/scheduling.py; src/kgp/feasibility.py; tests/test_review_residuals.py.
Interfaces: compute_travel_hours and independent _canonical_travel_hours retain existing signatures and 0.0001 h resolution.

- [x] Test four engines for 10 h / 1 h ratios, normal-speed controls and overflowing travel; test forged complete plans and finite serialization; run RED.
- [x] Remove distance epsilon in both implementations; catch only rejected derived operational time at the verifier calculation site.
- [x] Run GREEN and the full suite; independently replay all 48 frozen plans/objectives and compare reproduction metrics.

## Task 3: Documents and delivery

- [x] Replace the exact overclaim in the spoken script and slide 3 notes; correct the protocol label; qualify slide 5 comparison as synthetic.
- [x] Read back native edits; export changed PDFs and inspect affected pages/slides.
- [x] Record access metadata and prepare a local attachment set; preserve private sharing and user-owned company/team/accounting fields.
- [x] Publish maintenance evidence and update the existing draft PR, preserving the immutable audited REV06 ZIP and main branch.
