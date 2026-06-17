#!/usr/bin/env python3
"""
Check project-maintainers.csv for data consistency issues.

Errors (exit code 1 in default mode):
  - Duplicate GitHub handle within the same project
  - Malformed GitHub handle (@ prefix, spaces, non-handle characters)
  - Name field empty for a record that has a GitHub handle

Warnings (exit code 0, reported for review):
  - Same GitHub handle with different name spelling across projects
  - Same GitHub handle with different company across projects
  - Company name differs only in capitalisation
  - A person's full name maps to two different GitHub handles

Usage:
  Default (Markdown, for CI):
    python3 check_maintainer_consistency.py [csv_file]

  Flycheck mode (for Emacs):
    python3 check_maintainer_consistency.py --flycheck [csv_file]
    Each finding is printed as:  FILE:LINE: LEVEL: MESSAGE

  Print Emacs setup:
    python3 check_maintainer_consistency.py --print-elisp
"""

import argparse
import csv
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Default CSV location: three directories up from this script
# (.github/scripts/ -> .github/ -> repo root)
_DEFAULT_CSV    = Path(__file__).parent.parent.parent / "project-maintainers.csv"
# Sibling repo: <gh>/foundation/../gitdm  →  <gh>/gitdm
_DEFAULT_GITDM  = _DEFAULT_CSV.parent.parent / "gitdm"

# GitHub username rules: 1-39 chars, alphanumeric or hyphens,
# cannot start or end with a hyphen (consecutive hyphens are permitted).
_HANDLE_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9-]{0,37}[a-zA-Z0-9]$|^[a-zA-Z0-9]$')

# Strip **bold** and `code` markdown so flycheck overlays look clean.
_MD_RE = re.compile(r'\*\*(.+?)\*\*|`(.+?)`')


def _strip_md(text: str) -> str:
    """Remove **bold** and `code` markdown markers."""
    return _MD_RE.sub(lambda m: m.group(1) or m.group(2), text)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_csv(path: Path) -> list[dict]:
    """
    Parse the CSV, carrying forward maturity and project values when a row
    leaves those columns empty (the file's sparse encoding).

    Returns a list of dicts with keys:
      line, maturity, project, name, company, github_raw, github
    Skips the header row and any row without a GitHub handle.
    """
    records = []
    current_maturity = ""
    current_project = ""

    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        for lineno, row in enumerate(reader, start=1):
            if lineno == 1:          # header
                continue
            while len(row) < 5:     # pad short rows
                row.append("")

            maturity = row[0].strip()
            project  = row[1].strip()
            name     = row[2].strip()
            company  = row[3].strip()
            github   = row[4].strip()

            if maturity:
                current_maturity = maturity
            if project:
                current_project = project

            if not github:
                continue

            records.append({
                "line":       lineno,
                "maturity":   current_maturity,
                "project":    current_project,
                "name":       name,
                "company":    company,
                "github_raw": github,
                "github":     github.lstrip("@").lower(),
            })

    return records


# ---------------------------------------------------------------------------
# Checks — each returns a list of finding dicts:
#   {level, check, line, message}
# `line` is the most relevant CSV line number for that finding.
# ---------------------------------------------------------------------------

def check_duplicates_within_project(records: list[dict]) -> list[dict]:
    """Same GitHub handle appears more than once inside the same project."""
    findings = []
    seen: dict[tuple, list] = defaultdict(list)
    for r in records:
        seen[(r["project"], r["github"])].append(r)

    for (project, github), recs in sorted(seen.items()):
        if len(recs) > 1:
            line_nums = ", ".join(str(r["line"]) for r in recs)
            findings.append({
                "level": "error",
                "check": "duplicate-within-project",
                "line":  recs[0]["line"],
                "message": (
                    f"**Duplicate handle** `{recs[0]['github_raw']}` "
                    f"({recs[0]['name']}) appears {len(recs)}× "
                    f"in project **{project}** — lines {line_nums}."
                ),
            })
    return findings


def check_malformed_handles(records: list[dict]) -> list[dict]:
    """GitHub handle column contains an @ prefix, spaces, or invalid chars."""
    findings = []
    for r in records:
        raw = r["github_raw"]
        if raw.startswith("@"):
            findings.append({
                "level": "error",
                "check": "malformed-handle",
                "line":  r["line"],
                "message": (
                    f"**`@` prefix** in GitHub handle `{raw}` "
                    f"(project **{r['project']}**, name {r['name']!r}). "
                    "Remove the `@`."
                ),
            })
        elif " " in raw or "\t" in raw:
            findings.append({
                "level": "error",
                "check": "malformed-handle",
                "line":  r["line"],
                "message": (
                    f"**Whitespace** in GitHub handle `{raw!r}` "
                    f"(project **{r['project']}**, name {r['name']!r})."
                ),
            })
        elif not _HANDLE_RE.match(raw):
            findings.append({
                "level": "error",
                "check": "malformed-handle",
                "line":  r["line"],
                "message": (
                    f"**Invalid GitHub handle** `{raw}` "
                    f"(project **{r['project']}**, name {r['name']!r}). "
                    "Handles may only contain alphanumeric characters and hyphens."
                ),
            })
    return findings


def check_missing_names(records: list[dict]) -> list[dict]:
    """A record has a GitHub handle but an empty name field."""
    findings = []
    for r in records:
        if not r["name"]:
            findings.append({
                "level": "error",
                "check": "missing-name",
                "line":  r["line"],
                "message": (
                    f"**Missing name** for GitHub handle `{r['github_raw']}` "
                    f"(project **{r['project']}**)."
                ),
            })
    return findings


def check_cross_project_name_consistency(records: list[dict]) -> list[dict]:
    """Same GitHub handle used with different name spellings across projects."""
    findings = []
    by_handle: dict[str, list] = defaultdict(list)
    for r in records:
        by_handle[r["github"]].append(r)

    for handle, recs in sorted(by_handle.items()):
        names = {r["name"] for r in recs if r["name"]}
        if len(names) > 1:
            project_list = ", ".join(
                f"**{r['project']}** ({r['name']!r}, line {r['line']})"
                for r in recs
            )
            findings.append({
                "level": "warning",
                "check": "name-mismatch",
                "line":  min(r["line"] for r in recs),
                "message": (
                    f"Handle `{recs[0]['github_raw']}` has "
                    f"{len(names)} name variants {sorted(names)}: {project_list}."
                ),
            })
    return findings


def check_cross_project_company_consistency(records: list[dict]) -> list[dict]:
    """Same GitHub handle listed with different companies across projects."""
    findings = []
    by_handle: dict[str, list] = defaultdict(list)
    for r in records:
        by_handle[r["github"]].append(r)

    for handle, recs in sorted(by_handle.items()):
        # Normalise to lower-case so casing alone doesn't trigger this check.
        companies = {r["company"].lower(): r["company"] for r in recs if r["company"]}
        if len(companies) > 1:
            display = sorted(set(companies.values()))
            project_list = ", ".join(
                f"**{r['project']}** ({r['company']!r}, line {r['line']})"
                for r in recs if r["company"]
            )
            findings.append({
                "level": "warning",
                "check": "company-mismatch",
                "line":  min(r["line"] for r in recs),
                "message": (
                    f"Handle `{recs[0]['github_raw']}` ({recs[0]['name']}) "
                    f"has {len(display)} company variants {display}: {project_list}."
                ),
            })
    return findings


def check_company_casing(records: list[dict]) -> list[dict]:
    """Company names that differ only in capitalisation across the whole file."""
    # Track each distinct spelling and the first line it appears on.
    by_lower: dict[str, dict] = defaultdict(dict)   # lower -> {spelling: first_line}
    for r in records:
        if r["company"]:
            lower = r["company"].lower()
            if r["company"] not in by_lower[lower]:
                by_lower[lower][r["company"]] = r["line"]

    findings = []
    for lower, variant_lines in sorted(by_lower.items()):
        if len(variant_lines) > 1:
            variants = sorted(variant_lines)
            first_line = min(variant_lines.values())
            findings.append({
                "level": "warning",
                "check": "company-casing",
                "line":  first_line,
                "message": (
                    f"Company name casing variants: {variants}. "
                    "Pick one spelling and apply it consistently."
                ),
            })
    return findings


def check_duplicate_handles_for_same_name(records: list[dict]) -> list[dict]:
    """The same full name is associated with two different GitHub handles."""
    findings = []
    by_name: dict[str, set]  = defaultdict(set)
    by_name_recs: dict[str, list] = defaultdict(list)
    for r in records:
        if r["name"]:
            key = r["name"].lower()
            by_name[key].add(r["github"])
            by_name_recs[key].append(r)

    for name_lower, handles in sorted(by_name.items()):
        if len(handles) > 1:
            recs = by_name_recs[name_lower]
            sample_name = recs[0]["name"]
            project_list = ", ".join(
                f"**{r['project']}** (`{r['github_raw']}`, line {r['line']})"
                for r in recs
            )
            findings.append({
                "level": "warning",
                "check": "name-with-multiple-handles",
                "line":  min(r["line"] for r in recs),
                "message": (
                    f"Name {sample_name!r} is associated with "
                    f"{len(handles)} GitHub handles {sorted(handles)}: "
                    f"{project_list}. "
                    "This may indicate a handle change or two different people."
                ),
            })
    return findings


def run_all_checks(records: list[dict]) -> list[dict]:
    findings: list[dict] = []
    findings += check_duplicates_within_project(records)
    findings += check_malformed_handles(records)
    findings += check_missing_names(records)
    findings += check_cross_project_name_consistency(records)
    findings += check_cross_project_company_consistency(records)
    findings += check_company_casing(records)
    findings += check_duplicate_handles_for_same_name(records)
    return findings


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_markdown(findings: list[dict]) -> str:
    errors   = [f for f in findings if f["level"] == "error"]
    warnings = [f for f in findings if f["level"] == "warning"]

    lines = ["## Maintainer CSV Consistency Check\n"]

    if not errors and not warnings:
        lines.append("✅ No inconsistencies found.")
        return "\n".join(lines)

    if errors:
        lines.append(f"### ❌ Errors ({len(errors)}) — must be fixed before merging\n")
        for f in errors:
            lines.append(f"- {f['message']}")
        lines.append("")

    if warnings:
        lines.append(f"### ⚠️ Warnings ({len(warnings)}) — review and update if stale\n")

        by_check: dict[str, list] = defaultdict(list)
        for f in warnings:
            by_check[f["check"]].append(f)

        check_titles = {
            "name-mismatch":              "Name spelling differs across projects",
            "company-mismatch":           "Company differs across projects (may reflect a job change)",
            "company-casing":             "Company name capitalisation inconsistencies",
            "name-with-multiple-handles": "Same name associated with different GitHub handles",
        }

        for check, group in sorted(by_check.items()):
            title = check_titles.get(check, check)
            lines.append(f"#### {title}\n")
            for f in group:
                lines.append(f"- {f['message']}")
            lines.append("")

    return "\n".join(lines)


def render_fixable_markdown(issues: list[dict]) -> str:
    """Render fixable issues as a Markdown section for the CI report.

    Produces tables for company-name variants (with git-blame dates and the
    gitdm canonical employer) and given-name spelling variants.
    """
    if not issues:
        return ""

    company_issues = [i for i in issues
                      if i["type"] in ("company-casing", "company-mismatch")]
    name_issues    = [i for i in issues if i["type"] == "name-mismatch"]

    lines: list[str] = [
        "---",
        "",
        "## 🔧 Fixable Issues",
        "",
        "These can be resolved interactively with the Emacs fixer "
        "(`C-c f c` for company names, `C-c f n` for given names).",
        "",
    ]

    if company_issues:
        lines.append(f"### Company name variants ({len(company_issues)})\n")
        lines.append("| Handle | Variants (date first introduced) | gitdm canonical |")
        lines.append("|--------|----------------------------------|-----------------|")
        for issue in company_issues:
            handle   = issue.get("handle")
            variants = issue.get("variants", [])
            dates    = issue.get("variant_dates") or {}

            parts = []
            for v in variants:
                d = dates.get(v)
                parts.append(f"`{v}` ({d})" if d else f"`{v}`")
            variants_cell = ", ".join(parts)

            gitdm_co   = issue.get("gitdm_company")
            gitdm_from = issue.get("gitdm_from")
            if gitdm_co:
                gitdm_cell = gitdm_co + (f" (since {gitdm_from})" if gitdm_from else "")
            else:
                gitdm_cell = "—"

            handle_cell = f"@{handle}" if handle else f"_{variants[0].lower()}_"
            lines.append(f"| {handle_cell} | {variants_cell} | {gitdm_cell} |")
        lines.append("")

    if name_issues:
        lines.append(f"### Given-name spelling variants ({len(name_issues)})\n")
        lines.append("| Handle | Variants |")
        lines.append("|--------|----------|")
        for issue in name_issues:
            handle   = issue.get("handle", "—")
            variants = issue.get("variants", [])
            lines.append("| @{} | {} |".format(
                handle,
                ", ".join(f"`{v}`" for v in variants),
            ))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fixable-issue listing and in-place patching
# ---------------------------------------------------------------------------

_BLAME_MONTHS = ["JAN","FEB","MAR","APR","MAY","JUN",
                 "JUL","AUG","SEP","OCT","NOV","DEC"]


def _git_blame_date(csv_path: Path, line_num: int) -> str | None:
    """Return 'DD-MON-YYYY' for the commit that introduced line_num, or None.

    Blames HEAD explicitly so uncommitted working-tree changes do not shadow
    historical dates with today's timestamp.
    """
    try:
        out = subprocess.check_output(
            ["git", "blame", "HEAD", f"-L{line_num},{line_num}",
             "--porcelain", "--", str(csv_path)],
            stderr=subprocess.DEVNULL,
            cwd=csv_path.parent,
        ).decode()
        for ln in out.splitlines():
            if ln.startswith("author-time "):
                ts = int(ln.split()[1])
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                return (f"{dt.day:02d}-{_BLAME_MONTHS[dt.month - 1]}"
                        f"-{dt.year}")
    except Exception:
        pass
    return None


def list_fixable_issues(
    records: list[dict],
    csv_path: Path | None = None,
    gitdm_dir: Path | None = None,
) -> list[dict]:
    """
    Return a JSON-serialisable list of issues the interactive fixer can handle:
      - company-casing:   same company name, different capitalisation across the file
      - company-mismatch: same GitHub handle, genuinely different companies across projects
      - name-mismatch:    same GitHub handle, different given-name spelling

    Each entry has:
      type           "company-casing" | "company-mismatch" | "name-mismatch"
      prompt         Human-readable completing-read prompt
      first_line     Line number of the earliest variant (for buffer navigation)
      variants       List of known spellings (sorted)
      variant_dates  {spelling: "DD-MON-YYYY" | null} — git-blame date of first occurrence
      handle         (company-mismatch and name-mismatch only) The raw GitHub handle
      gitdm_company  (company-mismatch only, when gitdm available) Current company per gitdm
      gitdm_from     (company-mismatch only) Start date of the current gitdm affiliation
    """
    issues: list[dict] = []

    # ── company-casing ──────────────────────────────────────────────────────
    by_lower: dict[str, dict] = defaultdict(dict)   # lower → {spelling: first_line}
    for r in records:
        if r["company"]:
            low = r["company"].lower()
            if r["company"] not in by_lower[low]:
                by_lower[low][r["company"]] = r["line"]
    for low, variant_lines in sorted(by_lower.items()):
        if len(variant_lines) > 1:
            variant_dates: dict[str, str | None] = {}
            if csv_path is not None:
                for spelling, line_no in variant_lines.items():
                    variant_dates[spelling] = _git_blame_date(csv_path, line_no)
            issues.append({
                "type":          "company-casing",
                "prompt":        f"Canonical spelling for company '{low}'",
                "first_line":    min(variant_lines.values()),
                "variants":      sorted(variant_lines.keys()),
                "variant_dates": variant_dates,
            })

    # ── company-mismatch and name-mismatch ────────────────────────────────────
    by_handle: dict[str, list] = defaultdict(list)
    for r in records:
        by_handle[r["github"]].append(r)

    for _norm, recs in sorted(by_handle.items()):
        raw = recs[0]["github_raw"].lstrip("@")

        # company-mismatch: 2+ distinct companies (differ beyond capitalisation)
        company_first: dict[str, int] = {}   # exact_spelling → first_line_seen
        for r in recs:
            if r["company"] and r["company"] not in company_first:
                company_first[r["company"]] = r["line"]
        if len({s.lower() for s in company_first}) > 1:
            vdates: dict[str, str | None] = {}
            if csv_path is not None:
                for spelling, line_no in company_first.items():
                    vdates[spelling] = _git_blame_date(csv_path, line_no)
            entry: dict = {
                "type":          "company-mismatch",
                "prompt":        f"Canonical company for @{raw}",
                "handle":        raw,
                "first_line":    min(company_first.values()),
                "variants":      sorted(company_first.keys()),
                "variant_dates": vdates,
            }
            if gitdm_dir and gitdm_dir.is_dir():
                gitdm = lookup_gitdm_affiliation(raw, gitdm_dir)
                if gitdm:
                    entry["gitdm_company"] = gitdm["current_company"]
                    entry["gitdm_from"]    = gitdm["current_from"]
            issues.append(entry)

        # name-mismatch: 2+ distinct name spellings for the same handle
        names = {r["name"] for r in recs if r["name"]}
        if len(names) > 1:
            issues.append({
                "type":       "name-mismatch",
                "prompt":     f"Canonical given name for @{raw}",
                "handle":     raw,
                "first_line": min(r["line"] for r in recs if r["name"]),
                "variants":   sorted(names),
            })

    return issues


def apply_fix(
    csv_path: Path,
    field: str,
    from_val: str,
    to_val: str,
    handle: str | None = None,
) -> int:
    """
    Replace every occurrence of FROM_VAL with TO_VAL in the named field column,
    writing the result back to CSV_PATH in-place.

    field:    "name" (column 2) or "company" (column 3)
    handle:   when set, only rows whose GitHub-handle column matches are touched
              (use this for name fixes to avoid changing a different person)

    Returns the number of rows changed.
    """
    field_index = {"name": 2, "company": 3}[field]

    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))

    changed = 0
    for i, row in enumerate(rows):
        if i == 0:                          # header — never touch
            continue
        if len(row) <= field_index:
            continue
        if row[field_index].strip() != from_val:
            continue
        if handle is not None:
            if len(row) <= 4:
                continue
            if row[4].strip().lstrip("@").lower() != handle.lower():
                continue
        row[field_index] = to_val
        changed += 1

    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerows(rows)

    return changed


def _parse_affiliation_lines(lines: list[str]) -> list[dict]:
    """Parse the indented body lines of a developers_affiliations entry.

    Each line has the form:
      Company Name
      Company Name until YYYY-MM-DD
      Company Name from YYYY-MM-DD
      Company Name from YYYY-MM-DD until YYYY-MM-DD
    Returns a list of dicts with keys 'company', 'from', 'until'.
    """
    result = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        from_date = until_date = None
        m = re.search(r'\buntil\s+(\d{4}-\d{2}-\d{2})', line)
        if m:
            until_date = m.group(1)
            line = (line[:m.start()] + line[m.end():]).strip()
        m = re.search(r'\bfrom\s+(\d{4}-\d{2}-\d{2})', line)
        if m:
            from_date = m.group(1)
            line = (line[:m.start()] + line[m.end():]).strip()
        result.append({"company": line, "from": from_date, "until": until_date})
    return result


def lookup_gitdm_affiliation(handle: str, gitdm_dir: Path) -> dict | None:
    """Search developers_affiliations*.txt files for handle.

    Returns a dict with keys:
      handle          The handle as stored in the file (may differ in case)
      emails          List of email strings
      current_company Company with no 'until' date (or the last entry)
      current_from    'from' date for the current company, or None
      history         Full list of affiliation dicts (company, from, until)
    Returns None if the handle is not found.
    """
    import glob as _glob
    handle_lower = handle.lower()
    for filepath in sorted(_glob.glob(str(gitdm_dir / "developers_affiliations*.txt"))):
        try:
            with open(filepath, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        i = 0
        while i < len(lines):
            raw = lines[i]
            colon = raw.find(":")
            if colon > 0 and not raw[0].isspace() and not raw[0] == "#":
                if raw[:colon].lower() == handle_lower:
                    file_handle = raw[:colon]
                    emails = [e.strip() for e in raw[colon + 1:].split(",") if e.strip()]
                    aff_lines: list[str] = []
                    i += 1
                    while i < len(lines) and lines[i].startswith("\t"):
                        aff_lines.append(lines[i])
                        i += 1
                    history = _parse_affiliation_lines(aff_lines)
                    current_company = current_from = None
                    for entry in reversed(history):
                        if entry["until"] is None:
                            current_company = entry["company"]
                            current_from    = entry["from"]
                            break
                    if current_company is None and history:
                        current_company = history[-1]["company"]
                        current_from    = history[-1]["from"]
                    return {
                        "handle":          file_handle,
                        "emails":          emails,
                        "current_company": current_company,
                        "current_from":    current_from,
                        "history":         history,
                    }
            i += 1
    return None


def render_flycheck(findings: list[dict], csv_path: Path) -> str:
    """
    Emit one line per finding in GNU-style format consumed by flycheck:
      FILE:LINE: LEVEL: MESSAGE
    Markdown formatting is stripped so overlays stay readable.
    """
    out_lines = []
    filename = str(csv_path)
    for f in sorted(findings, key=lambda x: x["line"]):
        msg = _strip_md(f["message"])
        out_lines.append(f"{filename}:{f['line']}: {f['level']}: {msg}")
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Emacs / flycheck setup snippet
# ---------------------------------------------------------------------------

_ELISP_TEMPLATE = r"""
;;; flycheck-maintainer-consistency.el --- flycheck checker for project-maintainers.csv

;; Usage — add to your Doom Emacs config.el (or init.el after flycheck loads):
;;
;;   (after! flycheck
;;     (load! "path/to/.github/scripts/flycheck-maintainer-consistency"))
;;
;; Or inline:
;;   (after! flycheck
;;     (require 'flycheck-maintainer-consistency
;;              "~/.../foundation/.github/scripts/flycheck-maintainer-consistency"))

(require 'flycheck)

(defun flycheck-maintainer--find-script ()
  "Walk up from the current buffer's directory to find the checker script.
Returns the absolute path as a string, or nil if not found."
  (when-let* ((root (locate-dominating-file default-directory ".github")))
    (let ((script (expand-file-name
                   ".github/scripts/check_maintainer_consistency.py"
                   root)))
      ;; Use file-exists-p — the script is invoked via `python3 script.py',
      ;; so the executable bit is not required.
      (when (file-exists-p script) script))))

(flycheck-define-checker cncf-maintainer-csv
  "Consistency checker for the CNCF project-maintainers.csv file.
Errors block merging; warnings flag stale data (company changes, name variants)."
  :command ("python3"
            (eval (or (flycheck-maintainer--find-script)
                      (error "cncf-maintainer-csv: checker script not found")))
            "--flycheck"
            source-original)
  :error-patterns
  ((error   line-start (file-name) ":" line ": error: "   (message) line-end)
   (warning line-start (file-name) ":" line ": warning: " (message) line-end))
  :modes (csv-mode)
  :predicate (lambda ()
               (and buffer-file-name
                    (string= (file-name-nondirectory buffer-file-name)
                             "project-maintainers.csv")
                    (flycheck-maintainer--find-script))))

(add-to-list 'flycheck-checkers 'cncf-maintainer-csv)

(provide 'flycheck-maintainer-consistency)
;;; flycheck-maintainer-consistency.el ends here
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "csv_file",
        nargs="?",
        help="Path to project-maintainers.csv (default: auto-detect from script location).",
    )

    # ── read-only modes ──────────────────────────────────────────────────────
    p.add_argument(
        "--flycheck",
        action="store_true",
        help="Emit findings in GNU FILE:LINE: LEVEL: MESSAGE format for flycheck.",
    )
    p.add_argument(
        "--print-elisp",
        action="store_true",
        help="Print the Emacs flycheck checker definition and exit.",
    )
    p.add_argument(
        "--list-fixable",
        action="store_true",
        help=(
            "Print fixable issues (company-casing, company-mismatch, name-mismatch) "
            "as JSON for consumption by the interactive Emacs fixer."
        ),
    )
    p.add_argument(
        "--report-fixable",
        action="store_true",
        help=(
            "Print fixable issues as a Markdown section suitable for appending "
            "to the CI report (includes gitdm canonical employers when available)."
        ),
    )

    # ── in-place fix mode ────────────────────────────────────────────────────
    p.add_argument(
        "--apply-fix",
        action="store_true",
        help="Apply a single field substitution to the CSV file in-place.",
    )
    p.add_argument(
        "--field",
        choices=["name", "company"],
        help="Which field to patch (required with --apply-fix).",
    )
    p.add_argument(
        "--from",
        dest="from_val",
        metavar="VALUE",
        help="Current (wrong) field value to replace (required with --apply-fix).",
    )
    p.add_argument(
        "--to",
        dest="to_val",
        metavar="VALUE",
        help="Canonical replacement value (required with --apply-fix).",
    )
    p.add_argument(
        "--handle",
        metavar="GITHUB_HANDLE",
        help=(
            "Limit --apply-fix to rows matching this GitHub handle. "
            "Use with --field name to avoid touching a different person "
            "who happens to share the same name spelling."
        ),
    )

    # ── gitdm integration ────────────────────────────────────────────────────
    p.add_argument(
        "--gitdm-dir",
        metavar="PATH",
        help=(
            "Path to the CNCF gitdm repo clone "
            f"(default: {_DEFAULT_GITDM})."
        ),
    )
    p.add_argument(
        "--lookup-handle",
        metavar="GITHUB_HANDLE",
        help=(
            "Look up a GitHub handle in the gitdm affiliation database "
            "and print the full history as JSON."
        ),
    )
    p.add_argument(
        "--handle-at-line",
        type=int,
        metavar="LINE",
        help=(
            "Parse the CSV at LINE and print the GitHub handle found there "
            "(used by the Emacs lookup command)."
        ),
    )
    return p


def main() -> int:
    import json as _json

    args = build_arg_parser().parse_args()

    if args.print_elisp:
        print(_ELISP_TEMPLATE.strip())
        return 0

    csv_path  = Path(args.csv_file) if args.csv_file else _DEFAULT_CSV
    gitdm_dir = Path(args.gitdm_dir) if args.gitdm_dir else _DEFAULT_GITDM

    # ── --handle-at-line ─────────────────────────────────────────────────────
    if args.handle_at_line:
        if not csv_path.exists():
            print("null")
            return 0
        records = parse_csv(csv_path)
        match   = next((r for r in records if r["line"] == args.handle_at_line), None)
        print(match["github_raw"].lstrip("@") if match else "null")
        return 0

    # ── --lookup-handle ──────────────────────────────────────────────────────
    if args.lookup_handle:
        result = lookup_gitdm_affiliation(args.lookup_handle, gitdm_dir)
        print(_json.dumps(result, ensure_ascii=False, indent=2)
              if result else "null")
        return 0

    if not csv_path.exists():
        print(f"ERROR: CSV not found at {csv_path}", file=sys.stderr)
        return 1

    # ── --list-fixable / --report-fixable ────────────────────────────────────
    if args.list_fixable or args.report_fixable:
        records = parse_csv(csv_path)
        issues  = list_fixable_issues(records, csv_path, gitdm_dir)
        if args.list_fixable:
            print(_json.dumps(issues, ensure_ascii=False, indent=2))
        else:
            md = render_fixable_markdown(issues)
            if md:
                print(md)
        return 0

    # ── --apply-fix ──────────────────────────────────────────────────────────
    if args.apply_fix:
        missing = [f for f in ("field", "from_val", "to_val") if not getattr(args, f)]
        if missing:
            print(
                f"ERROR: --apply-fix requires --field, --from, and --to "
                f"(missing: {', '.join(missing)})",
                file=sys.stderr,
            )
            return 1
        n = apply_fix(csv_path, args.field, args.from_val, args.to_val, args.handle)
        print(_json.dumps({"changed": n}))
        return 0

    # ── check / flycheck ─────────────────────────────────────────────────────
    records  = parse_csv(csv_path)
    findings = run_all_checks(records)

    if args.flycheck:
        output = render_flycheck(findings, csv_path)
        if output:
            print(output)
        return 0

    print(render_markdown(findings))
    errors = [f for f in findings if f["level"] == "error"]
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
