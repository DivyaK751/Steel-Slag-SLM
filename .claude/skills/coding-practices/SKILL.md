---
name: coding-practices
description: Coding style, modularity, and repository structure practices for a 5-person data science capstone team working in Python on GitHub (LLM, RAG, and agentic pipelines). Use this skill whenever writing, refactoring, moving, or reviewing ANY code or files for the team repo, including deciding where a file, dataset, prompt, config, notebook, or test belongs, naming things, splitting a module, turning notebook code into source code, writing commit messages, creating branches, or opening/reviewing pull requests. Trigger even when the user doesn't mention "practices" or "standards", e.g. "add a function that...", "where should this go?", "clean up this file", "write a commit message", or "review this PR". Emphasizes modularity, reusable functions, light abstraction, and short well-placed comments. Every commit must stay at or under 500 changed lines.
---

# Coding Practices

Five people sharing one repo is like five cooks sharing one kitchen. If every station keeps its own
labels and habits, nobody finds anything. These practices are the shared kitchen layout: where
things go, how code reads, and how changes reach `main`.

## Scope

**Covered here:** repository layout, where data/configs/prompts/notebooks/tests live, module
boundaries, naming, function and file size, type hints, docstrings, error handling, notebook
hygiene, and the Git/GitHub workflow.

**Not covered here:** implementation choices such as which model, framework, vector store,
chunking strategy, agent design, or metric to use. The team decides those together and records
each decision in `docs/adr/`. When implementing something, follow the team's decisions for *what*
to build and these practices for *how the code is shaped and where it lives*.

If the repo's `CONTRIBUTING.md` disagrees with this skill, the repo file wins.

---

## 1. Principles

1. **Readable beats clever.** Code gets read far more often than it gets written. Write for the
   teammate who opens the file next month.
2. **Every file has one obvious home.** If you hesitate about where something goes, the structure
   needs a rule, not a guess. Add the rule to `CONTRIBUTING.md`.
3. **Modularity comes first.** This is the team's top priority. Focused functions and files, one
   job each. Split by responsibility, not by line count, and don't oversplit.
4. **Write it once, reuse it everywhere.** Any logic used more than once becomes a function that
   everyone imports. Copy-pasted code is a bug waiting to diverge.
5. **Keep abstraction light.** Plain functions first. No classes, base classes, or layers that
   exist "just in case".
6. **Dependencies flow one way.** High-level code uses low-level code, never the reverse.
7. **Raw data is immutable.** Read it, copy it, transform it into new files. Never edit it in place.
8. **Notebooks explore; source files repeat.** Code you run twice belongs in `src/`.
9. **Config and secrets live outside code.** No hardcoded paths, model names, or keys.
10. **Short, well-placed comments.** Docstrings explain what a function does; brief comments sit
    next to the lines that need them and explain why.
11. **Small commits.** At most 500 changed lines per commit, and per PR. Smaller is better.
12. **Tools settle style debates.** Formatting and linting run automatically, so reviews can focus
    on design and clarity.

---

## 2. Repository structure

The layout follows Cookiecutter Data Science v2 with a **src layout**. The src layout forces the
package to be installed, so tests can't accidentally import stray local files.

```
repo/
├── README.md               # What it is, how to install, how to run. Kept current.
├── CONTRIBUTING.md         # Team rules
├── pyproject.toml          # Dependencies + all tool config (ruff, mypy, pytest)
├── uv.lock                 # Committed lockfile for identical environments
├── Makefile                # Short commands: make setup / lint / test
├── .env.example            # Every env var name with fake values (committed)
├── .env                    # Real secrets (NEVER committed)
├── .pre-commit-config.yaml
├── .github/                # CI workflow, PR template, CODEOWNERS
├── configs/                # YAML settings: paths, model names, parameters
│   ├── base.yaml
│   └── experiments/        # One file per experiment, overriding base.yaml
├── prompts/                # Prompt templates, one file per prompt
├── data/                   # Git-ignored. Shared via DVC or cloud storage.
│   ├── raw/                # Original, immutable inputs (e.g. sponsor documents)
│   ├── external/           # Third-party data
│   ├── interim/            # Intermediate outputs of pipeline steps
│   └── processed/          # Final datasets ready for use
├── models/                 # Git-ignored. Trained artifacts, built indexes, checkpoints.
├── notebooks/
│   ├── exploratory/        # Personal scratch work
│   └── reports/            # Polished, rerunnable notebooks
├── reports/
│   └── figures/            # Weekly report assets
├── docs/
│   └── adr/                # Decision records: NNN-short-title.md
├── scripts/                # Small dev utilities. No pipeline logic.
├── src/<package>/
│   ├── __init__.py
│   ├── config.py           # Loads configs/ + .env into one settings object
│   ├── schemas.py          # Shared record types used across modules
│   ├── data/               # Loading, parsing, cleaning
│   ├── llm/                # Everything that talks to model providers
│   ├── rag/                # Retrieval components
│   ├── agents/             # Agent logic and tool definitions
│   ├── evaluation/         # Metrics and evaluation runners
│   ├── pipelines/          # Thin orchestration composing the pieces above
│   └── cli.py              # Entry points only; no logic
└── tests/
    ├── conftest.py
    ├── fixtures/           # Tiny committed sample files
    ├── unit/               # Mirrors src/<package>/ folder for folder
    └── integration/        # Tests that touch real external services
```

Delete folders you don't need and add ones you do, but agree as a team and update the tree in
`CONTRIBUTING.md` so everyone keeps the same mental map.

### Where things go

| You have... | It goes in... |
|---|---|
| Documents or datasets from the sponsor | `data/raw/` (never edited) |
| A cleaned or transformed dataset | `data/interim/` or `data/processed/`, written by code |
| A built index, embeddings, model weights | `models/` |
| A reusable function | the matching module in `src/<package>/` |
| Code that runs a sequence of steps | `src/<package>/pipelines/` |
| A command someone runs | `src/<package>/cli.py` (calls a pipeline) + a `Makefile` target |
| A prompt | `prompts/<task_name>.yaml`, never a long string inside Python |
| A model name, path, threshold, or parameter | `configs/*.yaml` |
| An API key or password | `.env` only |
| Exploration | `notebooks/exploratory/` |
| A figure for the weekly report | `reports/figures/`, generated by code or a report notebook |
| A team decision and its reasoning | `docs/adr/` |
| A tiny sample file for tests | `tests/fixtures/` |

### Data rules

- `data/raw/` is read-only. Need to fix a typo in a raw file? Write code that fixes it on the way
  to `interim/`. That keeps every output reproducible from raw data plus code.
- Every file in `interim/` and `processed/` comes from code in `src/`. If you can't name the
  function that created a file, it shouldn't exist.
- `data/` and `models/` stay out of git. Share them through the team's agreed remote (DVC or a
  bucket), with copy-paste setup steps in the README.
- Name data files in `snake_case` by content, not status: `slag_test_results.parquet`, not
  `final_v2_REAL.csv`. Version through folders or the data tool, not filename suffixes.

---

## 3. Modularity (top priority)

Modular code is code you can understand one small piece at a time. Think of LEGO bricks: each
brick is simple, does one thing, and snaps into many builds. A giant molded block does exactly one
thing, and you have to break it to change anything.

### Size: split by responsibility, not by line count

There are no fixed line limits. The right length depends on what the code does: a function that
walks through one tightly connected sequence of steps may reasonably be long, while a helper may be
a single line. Splitting for its own sake makes code *harder* to read, because the reader has to
jump between five tiny functions to follow one idea.

**Split when** a piece of code:
- does more than one job (you need "and" to describe it),
- contains a chunk that's reused, or likely to be reused elsewhere,
- contains a block you'd want to test on its own,
- has a block that needs a comment like `# step 2: ...` to explain where it starts (that block
  usually wants to be a named function),
- has deep nesting that an early return or a named helper would flatten,
- or makes you scroll back and forth to follow it.

**Don't split when** the pieces:
- only make sense together and would never be called on their own,
- would need many arguments passed back and forth just to share state,
- or would end up as one-line wrappers whose names say less than the code itself.

The same goes for files: split a module when it holds two separate responsibilities, not because
it crossed some line count. A long file with one clear purpose is fine; a short file mixing two
purposes isn't.

### Write it once, reuse it everywhere

- **The second time you write the same logic, extract it into a function.** Don't wait for a third
  copy. Repeated code drifts apart: someone fixes a bug in one copy and the other copies keep it.
- Put the function in the module that owns that responsibility, then import it wherever you need
  it. Every teammate builds on the same function instead of rewriting it.
- Small, focused functions are easy to reuse. A function that cleans text, splits it, *and* saves
  it can't be reused by someone who only needs cleaning.
- Before writing something new, check whether a function for it already exists. If one almost
  fits, extend it with a parameter rather than forking a copy.
- Reusable helpers stay general: they take inputs as arguments and return outputs, with no
  hidden reads from globals or files.

```python
# Before: the same normalization copy-pasted in three files
query = query.lower().strip().replace("\n", " ")
...
title = title.lower().strip().replace("\n", " ")

# After: one function in src/<package>/data/text_cleaning.py, imported everywhere
def normalize_text(text: str) -> str:
    """Lowercase text, trim it, and replace newlines with spaces."""
    return text.lower().strip().replace("\n", " ")


query = normalize_text(query)
title = normalize_text(title)
```

### Keep abstraction light

Abstraction is a cost you pay up front, hoping to save later. Pay it only when you can see the
savings.

- **Default to plain functions grouped in a well-named module.** A module already *is* a namespace;
  you don't need a class to group related functions.
- **Don't wrap functions in a class just to hold them.** A class with no state, or whose methods
  never use `self`, should be a module of functions instead.
- Use a class only when it earns its place:
  - it bundles state with the behavior that uses that state (e.g. a loaded index plus its
    search method), or
  - several implementations must share the same interface so they can be swapped.
- Use inheritance sparingly. One level (a base and its implementations) is fine; deeper chains
  make behavior hard to trace. Prefer passing functions or objects in over inheriting from them.
- No speculative layers: no base class with one subclass, no factory for one product, no manager
  class wrapping a single function, no config options nobody uses.
- Generalize from real cases. Once two or three concrete uses exist, the right shape is obvious.

```python
# Over-abstracted: a class that only wraps functions
class TextUtils:
    def __init__(self):
        pass

    def normalize(self, text: str) -> str:
        return text.lower().strip()

    def word_count(self, text: str) -> int:
        return len(text.split())


TextUtils().normalize(title)


# Simpler: a module of functions (src/<package>/data/text_cleaning.py)
def normalize_text(text: str) -> str:
    """Lowercase and trim text."""
    return text.lower().strip()


def count_words(text: str) -> int:
    """Count whitespace-separated words."""
    return len(text.split())
```

```python
# A class that earns its place: it owns state that its methods need
class DocumentIndex:
    """Search over a fixed set of embedded chunks."""

    def __init__(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        self._chunks = chunks
        self._vectors = vectors

    def search(self, query_vector: np.ndarray, top_k: int) -> list[Chunk]:
        """Return the top_k chunks closest to the query vector."""
        ...
```

### Dependency direction

```
cli.py → pipelines/ → agents/, rag/, evaluation/ → llm/, data/ → schemas.py, config.py
```

- A module may import anything to its right, never anything to its left. `rag/` never imports
  from `pipelines/`.
- `schemas.py` and `config.py` import nothing from the package. They're the foundation.
- Each external service gets exactly one home. All model-provider calls live in `llm/`, all
  storage access in its own module, and everyone else imports from that home. Swapping a provider
  then touches one folder instead of twenty files.
- A circular import means two modules share something that belongs lower down. Move the shared
  type or function into `schemas.py` or a lower module. Don't patch it with function-level imports.

```python
# Wrong: a low-level module reaching up into orchestration
# src/<package>/rag/chunking.py
from <package>.pipelines.prepare_corpus import DEFAULT_SETTINGS

# Right: shared settings live at the bottom
from <package>.config import ChunkingSettings
```

### Modules

- One responsibility per module, named for what it holds: `chunking.py`, `pdf_parsing.py`,
  `citation_checks.py`.
- Banned names: `utils.py`, `helpers.py`, `misc.py`, `common.py`. They become junk drawers. If
  something is truly generic, name the module for its job, e.g. `text_cleaning.py`.
- A file that keeps growing is often two responsibilities sharing one name; if so, split it
  along that seam. If it still has one clear purpose, leave it whole.
- Prefix internals with `_` (`_normalize_whitespace`). Other modules use only public names.

### Functions

- One function, one job. If you need the word "and" to describe it, split it.
- A long function is a list of steps. Turn each step into a well-named helper, and the main
  function reads like a table of contents.
- Pass collaborators in as arguments instead of importing module-level globals. A function that
  receives its data source can be tested with a fake one.
- Keep the core pure (same input, same output, no I/O). Push side effects like reading files,
  calling APIs, and writing outputs to the edges: pipelines and CLI.
- Use early returns to avoid deep nesting.
- If a function needs a long list of parameters, group the related ones into a config record.
- Define the contract at component boundaries (input type → output type) so one implementation
  can replace another without the caller changing.
- No mutable default arguments (`def f(items=[])`). Default to `None` and create inside.

```python
# Before: load + clean + split + save tangled together
def process(path):
    text = open(path).read().replace("\x0c", " ").strip()
    chunks = [text[i : i + 800] for i in range(0, len(text), 700)]
    with open(path.replace("raw", "interim") + ".json", "w") as f:
        json.dump(chunks, f)


# After: pure, testable pieces; only the pipeline touches files
def clean_text(text: str) -> str:
    """Remove form feeds and surrounding whitespace."""
    return text.replace("\x0c", " ").strip()


def split_into_chunks(text: str, max_chars: int, overlap: int) -> list[str]:
    """Split text into overlapping chunks of at most max_chars."""
    if overlap >= max_chars:
        raise ValueError("overlap must be smaller than max_chars")
    step = max_chars - overlap
    return [text[i : i + max_chars] for i in range(0, len(text), step)]
```

---

## 4. Coding style

### Formatting and tooling
- **ruff** formats and lints, **mypy** type-checks, and **pre-commit** runs them on every commit.
  Nobody formats by hand, and reviews don't argue about formatting.
- Line length 100, double quotes, imports sorted by ruff.
- Manage environments and dependencies with **uv**: `uv add <pkg>`, `uv add --dev <pkg>`,
  `uv run ...`. Commit `uv.lock`. No bare `pip install` into the project.

### Naming

| Kind | Style | Example |
|---|---|---|
| Module, function, variable | `snake_case` | `load_documents`, `chunk_size` |
| Class | `PascalCase` | `DocumentChunk` |
| Constant | `UPPER_SNAKE` | `DEFAULT_TOP_K` |
| Private | leading `_` | `_strip_headers` |
| Boolean | question form | `is_valid`, `has_citations` |
| Function | verb first | `parse_pdf`, `build_index`, `score_answer` |

- Name things by meaning, not type: `retrieved_chunks`, not `list1` or `df2`.
- No abbreviations a newcomer can't guess. `doc` and `idx` are fine; `prc_rslt_tmp` isn't.
- Single letters only in tiny comprehensions, loop indices, and math (`k` for top-k is fine).
- Prompt, config, and data files use `snake_case` too.

```python
# Before
def proc(d, n=5):
    l = []
    for x in d:
        if len(x) > n:
            l.append(x.strip())
    return l


# After
def keep_long_paragraphs(paragraphs: list[str], min_chars: int = 5) -> list[str]:
    """Return stripped paragraphs longer than min_chars."""
    return [p.strip() for p in paragraphs if len(p) > min_chars]
```

### Type hints
- Type-hint every function signature with modern syntax: `list[str]`, `dict[str, float]`, `X | None`.
- Types already say *what kind* of value goes in and out, so docstrings don't repeat them.

### Docstrings
A docstring tells a caller how to use the function without reading its body.

- Every public function and class gets a Google-style docstring. Methods get one when their
  purpose isn't obvious from the name.
- **Start with one line** in the imperative mood that says what the function does:
  `"""Split text into overlapping chunks."""`. For simple functions, that one line is enough.
- Add `Args:`, `Returns:`, and `Raises:` sections only when they say something the name and type
  hints don't, such as units, valid ranges, ordering, or what `None` means.
- Describe behavior, not implementation. "Return chunks in document order" helps callers;
  "loops over the text with a step size" doesn't.
- Keep docstrings proportional to the function: one line for simple ones, a few more for
  functions with real subtleties. A docstring that needs a page signals that the
  function does too much.
- Update the docstring in the same commit that changes the behavior. A wrong docstring is worse
  than none.

A full docstring, for a function where the details matter:

```python
def split_into_chunks(text: str, max_chars: int, overlap: int = 0) -> list[str]:
    """Split text into chunks of at most max_chars characters.

    Args:
        text: The full document text.
        max_chars: Maximum characters per chunk.
        overlap: Characters shared between consecutive chunks.

    Returns:
        Chunks in document order.

    Raises:
        ValueError: If overlap is not smaller than max_chars.
    """
```

### Comments
Good comments are like road signs: short, placed right where you need them, and only where the
road isn't obvious.

- **Explain why, not what.** The code already says what it does. Comment on intent, constraints,
  surprises, and trade-offs.
- **Keep them short.** One line is the norm, two or three at most. If a comment needs a paragraph,
  the code probably needs a clearer name or its own function with a docstring.
- **Put the comment next to the line it explains**, directly above it or at the end of a short
  line. Never collect explanations in a block at the top of a function, far from the code.
- **Comment the meaningful lines only:** non-obvious formulas, magic-looking values, workarounds,
  edge cases, ordering that matters. Plain assignments and obvious calls need no comment.
- **Prefer a better name over a comment.** `max_chunk_chars = 800` beats `n = 800  # max chars`.
- **No changelogs, author names, or dates in comments.** Git history records those.
- TODOs carry an owner and an issue: `# TODO(yj, #42): handle scanned PDFs`.
- Delete commented-out code. Git remembers it.
- When code changes, update or delete its comments. A stale comment actively misleads.

```python
# Bad: a long block far from the code it explains, plus comments that restate the code
def split_into_chunks(text: str, max_chars: int, overlap: int) -> list[str]:
    # This function takes the text and splits it into chunks. First we compute the
    # step by subtracting overlap from max_chars, then we loop through the text using
    # that step, and for each position we slice max_chars characters. We need overlap
    # because otherwise sentences at chunk boundaries get cut in half, which hurts search.
    step = max_chars - overlap  # compute step
    chunks = []  # create list
    for start in range(0, len(text), step):  # loop
        chunks.append(text[start : start + max_chars])  # append chunk
    return chunks  # return


# Good: a docstring for the what, one short comment beside the line that needs a why
def split_into_chunks(text: str, max_chars: int, overlap: int) -> list[str]:
    """Split text into chunks of at most max_chars, each sharing `overlap` chars with the last."""
    step = max_chars - overlap  # overlap keeps sentences at boundaries intact in one chunk
    return [text[start : start + max_chars] for start in range(0, len(text), step)]
```

### Imports
- Absolute imports from the package: `from <package>.rag.chunking import split_into_chunks`.
- No wildcard imports (`from x import *`) and no relative imports.
- Imports go at the top of the file. The only exception is deferring a heavy optional dependency,
  with a comment saying why.

### Constants and configuration
- No magic numbers or strings buried in logic. Name them as module-level constants, or put
  tunable values in `configs/`.
- Code reads settings through one typed settings object built in `config.py`. Nothing else reads
  YAML files or `os.environ` directly.
- Build paths from a project-root constant in `config.py` using `pathlib.Path`, so code runs from
  any working directory. No `/Users/yourname/...`, no string-concatenated paths.

```python
# Before
results = retrieve(query, 7)
out = "/Users/sam/Desktop/capstone/data/processed/answers.json"

# After (top_k lives in configs/base.yaml; PROCESSED_DIR is built in config.py)
results = retrieve(query, settings.retrieval.top_k)
out = PROCESSED_DIR / "answers.json"
```

### Data shapes
- Data with a known shape gets a named record type, not a loose dict or a long tuple.
  - Data entering from outside (files, APIs, model output, config) → a validated model, such as
    Pydantic, at the boundary.
  - Internal records → `@dataclass(frozen=True, slots=True)`.
  - Shared record types live in `schemas.py`; module-specific ones stay in their module.
- Never return a tuple of 4+ items. Return a record, so callers write `hit.page` instead of `hit[3]`.
- Pick the container that matches the access pattern:

| Need | Use | Avoid |
|---|---|---|
| Lookup by key | `dict` | scanning a list |
| Membership, dedupe | `set` | `x in some_list` inside a loop |
| Queue, sliding window | `collections.deque` | `list.pop(0)` |
| Top-k | `heapq.nlargest` | sorting everything for a few items |
| Counting | `collections.Counter` | manual increments |
| Grouping | `collections.defaultdict(list)` | key-existence checks |
| Numeric arrays, tables | NumPy / pandas / polars, vectorized | row-by-row Python loops |

### Errors and logging
- Raise specific exceptions with helpful messages. Custom exceptions live in `exceptions.py`.
- Never use a bare `except:`. Catch only what you can handle, and let everything else fail loudly.
  Swallowing errors just moves the failure somewhere further away and more confusing.
- Use `logging` in `src/`, never `print`. One logger per module:
  `logger = logging.getLogger(__name__)`. `print` is fine in `cli.py` output and notebooks.
- Set and log random seeds wherever randomness affects results.

---

## 5. Prompts and configs

- One prompt per file in `prompts/`, named by task: `answer_with_citations.yaml`.
- Each prompt file declares its name, a version number, a one-line description, and the variables
  it expects. Bump the version whenever its behavior changes.
- Keep instructions and inserted data visibly separate inside templates, e.g. in tagged sections.
- Prompt and config changes go through PRs, just like code.
- `configs/base.yaml` holds defaults. An experiment is a small file in `configs/experiments/` that
  overrides only what changes, so any old result can be rerun from a commit plus a config file.

---

## 6. Notebooks

- Name them `<step>-<initials>-<description>.ipynb`, e.g. `1.2-yj-chunk-size-sweep.ipynb`.
- `exploratory/` can be messy. `reports/` must run top to bottom on a fresh kernel.
- Strip outputs before committing (nbstripout in pre-commit). That keeps diffs readable.
- One owner per notebook. Notebook JSON merges badly, so don't co-edit the same file.
- Import from the package instead of redefining functions:
  `%load_ext autoreload`, `%autoreload 2`, then `from <package>.data import loading`.
- Copied a cell into a second notebook, or wrote a class inside one? Move it to `src/` with a test.
- No secrets and no absolute paths in notebooks.

---

## 7. Tests

- `tests/unit/` mirrors `src/<package>/`: `src/<package>/rag/chunking.py` →
  `tests/unit/rag/test_chunking.py`.
- Name tests by behavior: `test_split_into_chunks_rejects_overlap_larger_than_chunk`.
- Every new public function gets a unit test. Every bug fix gets a test that would have caught it.
- Unit tests never touch the network or real data. Use `tests/fixtures/` and fakes.
- Tests that hit real external services go in `tests/integration/`, marked
  `@pytest.mark.integration` and skipped by default.

---

## 8. Git and GitHub workflow

### Branches
- **GitHub Flow**: `main` always works. All work happens on short-lived branches that merge back
  through PRs. Nobody pushes directly to `main`.
- Name branches `<type>/<issue#>-<short-desc>`, e.g. `feat/42-pdf-parser`, `fix/57-chunk-overlap`.
- Rebase your own branch on `main` at least daily. Never force-push a branch someone else works on.

### Commit messages
Use Conventional Commits: `<type>(<scope>): <imperative summary>`.
- Types: `feat`, `fix`, `refactor`, `test`, `docs`, `style`, `chore`, `ci`, `build`, `exp`.
- Scopes match top-level modules: `data`, `llm`, `rag`, `agents`, `eval`, `pipelines`, `prompts`,
  `config`, `cli`, `notebooks`.
- Summary ≤ 72 characters, imperative mood, no trailing period. Add a body explaining *why* when
  it isn't obvious.

```
feat(data): add PDF table extractor
fix(rag): stop dropping the last chunk of each document
refactor(data): move table extraction from notebook into data/pdf_parsing.py
style(rag): rename vague variables in chunking.py
```

Not: `update`, `fixed stuff`, `WIP`, `final version v2 REAL`.

Every commit should leave the code runnable with tests passing.

### The 500-line rule
- **Every commit changes at most 500 lines** (added + deleted).
- **Every PR also stays within 500 lines in total**, because PRs get squash-merged into a single
  commit on `main`.
- Lockfiles (`uv.lock`) and `tests/fixtures/` don't count. Pure renames and moves barely count,
  since Git detects them. Notebooks do count.
- Check before committing: `git diff --cached --shortstat` (staged changes), or
  `git diff --shortstat origin/main...HEAD` (whole branch).
- Small changes get real reviews. A 2,000-line PR gets a skim and an "LGTM", which defeats the
  point of reviewing.

**Too big? Split it:**
- **Move, then change.** One commit only moves or renames files; the next one edits them.
- **Interface, then implementation, then wiring.** Record types and signatures with tests first,
  the implementation second, hooking it into pipelines and the CLI third.
- **Refactors and features in separate PRs.** Never mix "rename everything" with "add behavior".
- **Tests ship with their code**, not in a separate PR at the end.
- Already staged too much? `git reset`, then `git add -p` to stage hunk by hunk.

Genuine exceptions, such as the initial repo scaffold, need team agreement in the PR thread and a
note in the PR description.

### Pull requests
- One logical change, linked to an issue (`Closes #42`), with a 2–4 sentence description of what
  changed and why, plus how to check it.
- PR title in Conventional Commit format, since it becomes the squash commit on `main`.
- Read your own diff on GitHub before requesting review. You'll catch stray `print` calls yourself.
- Merge requires ≥ 1 approval from someone other than the author, green CI, and all conversations
  resolved. Squash-merge, then delete the branch.
- Respond to review requests within one working day.
- Never commit secrets, `.env`, data, model files, notebook outputs, caches, or `.venv`. If a
  secret leaks, rotate it first, then clean the history.

---

## 9. Definition of done

- [ ] Every new file sits where §2 says it belongs.
- [ ] Imports respect the dependency direction, with no circular imports.
- [ ] Functions and files each have one clear job, without being oversplit; no junk-drawer modules.
- [ ] No duplicated logic: anything used more than once is a shared function.
- [ ] No unnecessary classes or layers; every class owns state or a shared interface.
- [ ] Type hints and short, accurate docstrings on public functions and classes.
- [ ] Comments are brief, sit next to the lines they explain, and say why.
- [ ] No hardcoded paths, magic numbers, or secrets.
- [ ] Unit tests in the mirrored location; lint and tests pass.
- [ ] Each commit and the PR as a whole ≤ 500 changed lines.
- [ ] README or CONTRIBUTING updated if structure or usage changed.

---

## 10. Code review checklist

Review structure first and details last. Ruff handles formatting, so don't spend comments on it.

1. **Placement**: Is every file in the right folder? Is any data, prompt, config, or secret hiding
   in code?
2. **Modularity**: One job per function and file? Anything doing two jobs that should be split,
   or anything chopped into pieces so small it's harder to follow? Dependency direction respected?
3. **Reuse**: Is any logic duplicated, in this PR or with existing code? Does a function for this
   already exist?
4. **Abstraction**: Any class that just wraps functions, base class with one subclass, or layer
   added "just in case"? Ask for the simpler version.
5. **Clarity**: Do names say what things mean? Could a teammate follow it without the author
   explaining?
6. **Docs and comments**: Short, accurate docstrings? Comments brief, placed beside the lines they
   explain, and about why rather than what? No commented-out code?
7. **Contracts**: Type hints and record types instead of loose dicts or long tuples?
8. **Robustness**: Specific exceptions, no silent failures, logging instead of print?
9. **Tests**: In the mirrored location, named by behavior, no network calls in unit tests?
10. **Size and history**: ≤ 500 lines? One logical change? Clear commit messages?

Comment on the code, not the person. Mark optional suggestions with `nit:`. Point out what's done
well, too. Approve when the change makes the codebase better, even if it isn't perfect.
