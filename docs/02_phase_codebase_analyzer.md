# Phase 2: Codebase Analyzer

## Objective

Add a local-repo analyzer that coordinates existing indexer foundations and extracts project architecture from source code.

## Analyzer Responsibilities

- Discover source files safely.
- Ignore dependency/build/cache/secret folders.
- Parse Python, JavaScript, TypeScript, and Solidity-like files where possible.
- Extract software primitives.
- Produce deterministic `project_id` and `scan_id`.
- Return an `IndexedSystem` that can be written through `GraphWriter`.

## Extracted Primitives

- `Project`
- `Repository`
- `Package`
- `File`
- `Module`
- `Route`
- `Controller`
- `Service`
- `Function`
- `DatabaseModel`
- `DatabaseTable`
- `Entity`
- `Workflow`
- `Integration`
- `Skill`
- `Artifact`
- `Risk`

## First Tasks

1. Add `CodebaseAnalyzer`.
2. Extend indexer dataclasses for generic code nodes and relationships.
3. Add safe file discovery.
4. Add route/function/model/integration/risk heuristics.
5. Test against the sibling `fundraising_app` repository.
