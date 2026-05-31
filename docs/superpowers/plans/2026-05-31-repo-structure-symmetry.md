# Repo Structure Symmetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all 4 collectors and their data consistent — collectors in `collect/`, data in `<type>/latest.json` + `<type>/history/`, logs in `<type>/`.

**Architecture:** Move `collect_sentiment.py` into `collect/` package and move root-level `latest.json` + `history/` into a new `sentiment/` folder. Update all internal path references, crontab entries, SniperBoard env vars, and both repos' documentation. No behavior change — only file locations change.

**Tech Stack:** Python 3, git, crontab, GitHub raw URLs

---

## File Map

### market-sentiment-data

| Action | Path |
|--------|------|
| Move | `collect_sentiment.py` → `collect/collect_sentiment.py` |
| Create | `sentiment/` directory |
| Move | `latest.json` → `sentiment/latest.json` |
| Move | `history/` contents → `sentiment/history/` |
| Move | `sentiment.log` → `sentiment/sentiment.log` |
| Modify | `collect/collect_sentiment.py` — REPO_PATH, output paths, git file list |
| Modify | `collect/collect_brief.py` — reads `sentiment/latest.json` instead of `latest.json` |
| Modify | `collect/collect_earnings.py` — remove `sys.path.insert`, use `from collect.git_utils` |
| Modify | `collect/test_collect_sentiment.py` — update import to `collect.collect_sentiment` |
| Delete | `collect/brief.log`, `collect/earnings.log` (stray files) |
| Update | `PROJECT_CONTEXT.md`, `CLAUDE.md`, `README.md` |

### sniperboard

| Action | Path |
|--------|------|
| Modify | `.env` — `SENTIMENT_DATA_URL`, `SENTIMENT_DATA_HISTORY_BASE` values |
| Update | `PROJECT_CONTEXT.md`, `README.md` |

---

## Task 1: Create `sentiment/` directory and move data files

**Files:**
- Create: `sentiment/.gitkeep`, `sentiment/history/`
- Move: `latest.json`, `history/*.json`

- [ ] **Step 1: Create sentiment/ directory structure**

```bash
cd /Users/jerry/dev/market-sentiment-data
mkdir -p sentiment/history
```

- [ ] **Step 2: Move data files**

```bash
cd /Users/jerry/dev/market-sentiment-data
git mv latest.json sentiment/latest.json
git mv history/* sentiment/history/
git mv history/.gitkeep sentiment/history/.gitkeep 2>/dev/null || true
```

- [ ] **Step 3: Move sentiment.log (if exists)**

```bash
cd /Users/jerry/dev/market-sentiment-data
[ -f sentiment.log ] && mv sentiment.log sentiment/sentiment.log || touch sentiment/sentiment.log
```

- [ ] **Step 4: Remove stray log files inside collect/**

```bash
cd /Users/jerry/dev/market-sentiment-data
[ -f collect/brief.log ] && rm collect/brief.log
[ -f collect/earnings.log ] && rm collect/earnings.log
```

- [ ] **Step 5: Verify sentiment/ structure**

Run: `find sentiment/ -not -path '*/.git*' | sort`
Expected output (similar to):
```
sentiment/
sentiment/history
sentiment/history/2026-05-21.json
sentiment/history/2026-05-22_post_close.json
...
sentiment/latest.json
sentiment/sentiment.log
```

- [ ] **Step 6: Commit data restructure**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add sentiment/
git rm -r history/ 2>/dev/null || true
git commit -m "refactor: move sentiment data to sentiment/ folder

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Move `collect_sentiment.py` into `collect/` and fix paths

**Files:**
- Move: `collect_sentiment.py` → `collect/collect_sentiment.py`
- Modify: `collect/collect_sentiment.py`

The file currently lives at the repo root. After moving it into `collect/`, three things must change:
1. `REPO_PATH` default: `Path(__file__).parent` → `Path(__file__).parent.parent` (up one level)
2. `latest.json` output path: `REPO_PATH / "latest.json"` → `REPO_PATH / "sentiment" / "latest.json"`
3. History path helper: `REPO_PATH / "history" / ...` → `REPO_PATH / "sentiment" / "history" / ...`
4. git `files_to_add`: `["latest.json", ...]` → `["sentiment/latest.json", ...]`

- [ ] **Step 1: Move the file**

```bash
cd /Users/jerry/dev/market-sentiment-data
git mv collect_sentiment.py collect/collect_sentiment.py
```

- [ ] **Step 2: Fix REPO_PATH default (line ~26)**

In `collect/collect_sentiment.py`, change:
```python
# OLD
REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent)).resolve()
```
To:
```python
# NEW
REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
```

- [ ] **Step 3: Fix history path helper**

Find the function `get_history_path` or the line that builds history file path (contains `"history"`). Change:
```python
# OLD
return REPO_PATH / "history" / f"{date_str}_{slot}.json"
```
To:
```python
# NEW
return REPO_PATH / "sentiment" / "history" / f"{date_str}_{slot}.json"
```

- [ ] **Step 4: Fix latest.json output path**

Find `latest_path = REPO_PATH / "latest.json"` (around line 581). Change to:
```python
latest_path = REPO_PATH / "sentiment" / "latest.json"
```

- [ ] **Step 5: Fix git files_to_add**

Find `files_to_add=["latest.json", rel_history]` (around line 441). Change to:
```python
files_to_add=["sentiment/latest.json", rel_history],
```

- [ ] **Step 6: Verify the module can be imported**

```bash
cd /Users/jerry/dev/market-sentiment-data
PYTHONPATH=/Users/jerry/dev/market-sentiment-data /opt/homebrew/bin/python3 -c "from collect import collect_sentiment; print('OK')"
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add collect/collect_sentiment.py
git commit -m "refactor: move collect_sentiment.py into collect/ package

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Update `collect/collect_brief.py` — reads `sentiment/latest.json`

**Files:**
- Modify: `collect/collect_brief.py` (~line 160)

- [ ] **Step 1: Fix the latest.json read path**

Find the function that loads sentiment data (around line 159, `load_latest_sentiment` or similar). Change:
```python
# OLD
latest_path = REPO_PATH / "latest.json"
```
To:
```python
# NEW
latest_path = REPO_PATH / "sentiment" / "latest.json"
```

- [ ] **Step 2: Verify import is clean**

```bash
cd /Users/jerry/dev/market-sentiment-data
PYTHONPATH=/Users/jerry/dev/market-sentiment-data /opt/homebrew/bin/python3 -c "from collect import collect_brief; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add collect/collect_brief.py
git commit -m "fix(brief): read sentiment data from sentiment/latest.json

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Standardize `collect/collect_earnings.py` imports

**Files:**
- Modify: `collect/collect_earnings.py` (top of file, ~lines 22-23)

Currently uses `sys.path.insert` + bare `from git_utils import`. Standardize to match the other collectors.

- [ ] **Step 1: Remove sys.path.insert and update import**

Find these lines:
```python
sys.path.insert(0, str(Path(__file__).parent))
from git_utils import commit_and_push
```

Replace with:
```python
from collect.git_utils import commit_and_push
```

- [ ] **Step 2: Verify import is clean**

```bash
cd /Users/jerry/dev/market-sentiment-data
PYTHONPATH=/Users/jerry/dev/market-sentiment-data /opt/homebrew/bin/python3 -c "from collect import collect_earnings; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add collect/collect_earnings.py
git commit -m "refactor(earnings): standardize import to collect.git_utils

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 5: Fix `collect/test_collect_sentiment.py` imports

**Files:**
- Modify: `collect/test_collect_sentiment.py`

Currently does:
```python
sys.path.insert(0, str(Path(__file__).parent.parent))
import collect_sentiment as cs
```

After the move, `collect_sentiment` is inside the `collect` package, not at repo root.

- [ ] **Step 1: Update import in test file**

Change:
```python
sys.path.insert(0, str(Path(__file__).parent.parent))
import collect_sentiment as cs
```
To:
```python
sys.path.insert(0, str(Path(__file__).parent.parent))
from collect import collect_sentiment as cs
```

(Keep the `sys.path.insert` so PYTHONPATH is consistent with the other test files.)

- [ ] **Step 2: Run the tests and verify they pass**

```bash
cd /Users/jerry/dev/market-sentiment-data
PYTHONPATH=/Users/jerry/dev/market-sentiment-data /opt/homebrew/bin/python3 -m pytest collect/ -v 2>&1 | tail -20
```
Expected: all tests pass, no import errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add collect/test_collect_sentiment.py
git commit -m "fix(test): update import after moving collect_sentiment into collect/

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 6: Update crontab

**Files:**
- Modify: crontab (via `crontab -e` or `crontab -` stdin)

Four entries need updating:
1. Sentiment: run as module, log to `sentiment/sentiment.log`
2. Brief: run as module (was `python collect/collect_brief.py`), log to `brief/brief.log`
3. Earnings: run as module (was `python collect/collect_earnings.py`), log to `earnings/earnings.log`
4. Macro: run as module (was `python collect/collect_macro_insight.py`), log to `macro/macro.log`

- [ ] **Step 1: Print current crontab to confirm entries**

```bash
crontab -l
```

- [ ] **Step 2: Apply new crontab**

```bash
crontab - << 'EOF'
00 6,22 * * * cd /Users/jerry/dev/market-sentiment-data && GIT_SSH_COMMAND="ssh -F /Users/jerry/.ssh/config -o StrictHostKeyChecking=no" PYTHONPATH=/Users/jerry/dev/market-sentiment-data HERMES_TIMEOUT=300 /opt/homebrew/bin/python3 -m collect.collect_sentiment >> sentiment/sentiment.log 2>&1
30 6,22 * * * cd /Users/jerry/dev/market-sentiment-data && GIT_SSH_COMMAND="ssh -F /Users/jerry/.ssh/config -o StrictHostKeyChecking=no" PYTHONPATH=/Users/jerry/dev/market-sentiment-data HERMES_TIMEOUT=300 /opt/homebrew/bin/python3 -m collect.collect_brief >> brief/brief.log 2>&1
00 7 * * * cd /Users/jerry/dev/market-sentiment-data && GIT_SSH_COMMAND="ssh -F /Users/jerry/.ssh/config -o StrictHostKeyChecking=no" PYTHONPATH=/Users/jerry/dev/market-sentiment-data /opt/homebrew/bin/python3 -m collect.collect_earnings >> earnings/earnings.log 2>&1
45 6,22 * * * cd /Users/jerry/dev/market-sentiment-data && GIT_SSH_COMMAND="ssh -F /Users/jerry/.ssh/config -o StrictHostKeyChecking=no" PYTHONPATH=/Users/jerry/dev/market-sentiment-data HERMES_TIMEOUT=300 /opt/homebrew/bin/python3 -m collect.collect_macro_insight >> macro/macro.log 2>&1
EOF
```

- [ ] **Step 3: Verify new crontab**

```bash
crontab -l
```
Expected: 4 lines with updated paths and module invocations.

- [ ] **Step 4: Dry-run sentiment collector to verify it can start**

```bash
cd /Users/jerry/dev/market-sentiment-data
PYTHONPATH=/Users/jerry/dev/market-sentiment-data SENTIMENT_SLOT=pre_open /opt/homebrew/bin/python3 -m collect.collect_sentiment --help 2>&1 || \
PYTHONPATH=/Users/jerry/dev/market-sentiment-data /opt/homebrew/bin/python3 -c "from collect import collect_sentiment; print('import OK')"
```

---

## Task 7: Update SniperBoard `.env`

**Files:**
- Modify: `/Users/jerry/dev/sniperboard/.env`

The GitHub raw URLs for sentiment data must now point to `sentiment/latest.json` and `sentiment/history/`.

- [ ] **Step 1: Show current sentiment URL values**

```bash
grep -i "SENTIMENT_DATA_URL\|SENTIMENT_DATA_HISTORY" /Users/jerry/dev/sniperboard/.env
```

- [ ] **Step 2: Update URLs**

In `/Users/jerry/dev/sniperboard/.env`, update:
- `SENTIMENT_DATA_URL` — change the path segment from `/main/latest.json` to `/main/sentiment/latest.json`
- `SENTIMENT_DATA_HISTORY_BASE` — change from `/main/history` to `/main/sentiment/history`

Example (replace `<token>` with actual value from existing .env):
```
SENTIMENT_DATA_URL=https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/sentiment/latest.json
SENTIMENT_DATA_HISTORY_BASE=https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/sentiment/history
```

- [ ] **Step 3: Verify SniperBoard backend can fetch the new URL**

Start SniperBoard backend and hit the sentiment endpoint, or run:
```bash
cd /Users/jerry/dev/sniperboard
source .env && python3 -c "
import os, requests
url = os.environ['SENTIMENT_DATA_URL']
token = os.environ.get('SENTIMENT_DATA_TOKEN','')
headers = {'Authorization': f'token {token}'} if token else {}
r = requests.get(url, headers=headers, timeout=10)
print(r.status_code, r.json().get('schema_version', 'missing'))
"
```
Expected: `200 2.0`

---

## Task 8: Update documentation

**Files:**
- Modify: `market-sentiment-data/PROJECT_CONTEXT.md`
- Modify: `market-sentiment-data/CLAUDE.md`
- Modify: `market-sentiment-data/README.md` (if path references exist)
- Modify: `sniperboard/PROJECT_CONTEXT.md`
- Modify: `sniperboard/README.md` (if path references exist)

- [ ] **Step 1: Update `market-sentiment-data/PROJECT_CONTEXT.md`**

Update:
1. Section 2 "Repository File Map" — replace old file tree with new structure showing `collect/collect_sentiment.py` and `sentiment/` folder
2. Section 4 "Collector 1" — update `REPO_PATH` note (now `parent.parent`)
3. Section 8 "Data Schema Reference" — update `latest.json` path to `sentiment/latest.json`
4. Section 10 "Cron Schedule" — update all 4 cron lines to match new module invocations and log paths
5. AUTO-GENERATED date → `2026-05-31`

- [ ] **Step 2: Update `market-sentiment-data/CLAUDE.md`**

In the "Key Project Entry Points" section, update the file map to show:
- `collect/collect_sentiment.py` (not root-level)
- `sentiment/latest.json` (not root `latest.json`)

- [ ] **Step 3: Update `sniperboard/PROJECT_CONTEXT.md`**

Find the "AI Pipeline + Cross-Repo Linkage" section (~line 383). Update path references:
- `market-sentiment-data/latest.json` → `market-sentiment-data/sentiment/latest.json`
- Any mention of `history/` for sentiment → `sentiment/history/`
- Update env var table to note new URL shapes

- [ ] **Step 4: Commit documentation updates (market-sentiment-data)**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add PROJECT_CONTEXT.md CLAUDE.md README.md
git commit -m "docs: update paths after sentiment/ restructure

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

- [ ] **Step 5: Commit documentation updates (sniperboard)**

```bash
cd /Users/jerry/dev/sniperboard
git add PROJECT_CONTEXT.md README.md
git commit -m "docs: update market-sentiment-data path references after restructure

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 9: End-to-end verification

- [ ] **Step 1: Run full test suite**

```bash
cd /Users/jerry/dev/market-sentiment-data
PYTHONPATH=/Users/jerry/dev/market-sentiment-data /opt/homebrew/bin/python3 -m pytest collect/ -v 2>&1 | tail -30
```
Expected: all tests pass (previously 48 tests).

- [ ] **Step 2: Dry-run all 4 collectors (import + init only)**

```bash
cd /Users/jerry/dev/market-sentiment-data
PYTHONPATH=/Users/jerry/dev/market-sentiment-data /opt/homebrew/bin/python3 -c "
from collect import collect_sentiment; print('sentiment OK')
from collect import collect_brief;     print('brief OK')
from collect import collect_earnings;  print('earnings OK')
from collect import collect_macro_insight; print('macro OK')
"
```
Expected: 4 OK lines, no import errors.

- [ ] **Step 3: Verify SniperBoard sentiment API returns data**

With SniperBoard backend running:
```bash
curl -s http://localhost:5001/api/sentiment | python3 -m json.tool | grep -E '"available"|"schema_version"|"slot"'
```
Expected:
```json
"available": true,
"schema_version": "2.0",
"slot": "pre_open"
```

- [ ] **Step 4: Verify SniperBoard brief/earnings/macro APIs (unchanged paths)**

```bash
curl -s http://localhost:5001/api/brief | python3 -m json.tool | grep '"available"'
curl -s http://localhost:5001/api/earnings | python3 -m json.tool | grep '"available"'
curl -s http://localhost:5001/api/macro-insight | python3 -m json.tool | grep -c '"text"'
```
Expected: brief `"available": true`, earnings `"available": true`, macro-insight shows multiple `"text"` fields.

- [ ] **Step 5: Verify crontab dry-run (module-level invocation)**

```bash
cd /Users/jerry/dev/market-sentiment-data
# Should print module docstring or help, not ImportError
PYTHONPATH=/Users/jerry/dev/market-sentiment-data /opt/homebrew/bin/python3 -m collect.collect_sentiment 2>&1 | head -5
PYTHONPATH=/Users/jerry/dev/market-sentiment-data /opt/homebrew/bin/python3 -m collect.collect_brief 2>&1 | head -5
PYTHONPATH=/Users/jerry/dev/market-sentiment-data /opt/homebrew/bin/python3 -m collect.collect_earnings --dry-run 2>&1 | head -10
PYTHONPATH=/Users/jerry/dev/market-sentiment-data /opt/homebrew/bin/python3 -m collect.collect_macro_insight 2>&1 | head -5
```

- [ ] **Step 6: Check for any remaining root-level stale references**

```bash
grep -rn "\"latest.json\"\|/ \"history\"\|REPO_PATH / .history" \
  /Users/jerry/dev/market-sentiment-data/collect/ \
  --include="*.py" | grep -v ".pyc"
```
Expected: no matches (all old root-level paths replaced).

- [ ] **Step 7: Final cleanup — remove stale root-level log files**

```bash
cd /Users/jerry/dev/market-sentiment-data
# Only remove if they have been superseded by data-folder logs
ls -la brief.log earnings.log macro.log 2>/dev/null
rm -f brief.log earnings.log macro.log
```

- [ ] **Step 8: Final commit (if any cleanup files)**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add -u
git status
git commit -m "chore: remove stale root-level log files after restructure

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>" 2>/dev/null || echo "nothing to commit"
```
