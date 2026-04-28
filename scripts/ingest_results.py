#!/usr/bin/env python3
"""
ingest_results.py — Hydra LLM benchmark results ingestion script.

Parses llama-bench / llama-batched-bench markdown output and updates
benchmarkings.md with new rows in:
  - Section 12 (Run Log)
  - Section 2.6 (Phase 1 results summary)
  - Section 8.1 / 8.2 (Workload scenario tables), when a match is found

Usage:
    python scripts/ingest_results.py [OPTIONS] [bench_file]

    bench_file  Path to the bench output file. Reads stdin if omitted.

Options:
    --machine   Machine ID: p1-m2-8g | p1-m3-16g | p1-i7-rtx3050 |
                            m2-ultra  | dgx-spark
    --runtime   Runtime:    llamacpp | ollama | vllm-mlx
    --ngl       GPU layers used (optional; integer)
    --date      Run date in YYYY-MM-DD format (default: today)
    --dry-run   Print what would be changed without writing anything

Exit codes:
    0  success
    1  parse error
"""

import argparse
import re
import sys
import os
from datetime import date
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class BenchRow:
    """One row from a llama-batched-bench markdown table."""
    __slots__ = ("pp", "tg", "batch", "n_kv", "t_pp", "s_pp", "t_tg", "s_tg", "t", "s")

    def __init__(self, pp, tg, batch, n_kv, t_pp, s_pp, t_tg, s_tg, t, s):
        self.pp    = int(pp)
        self.tg    = int(tg)
        self.batch = int(batch)
        self.n_kv  = int(n_kv)
        self.t_pp  = float(t_pp)
        self.s_pp  = float(s_pp)   # PP throughput (tokens/s)
        self.t_tg  = float(t_tg)
        self.s_tg  = float(s_tg)   # TG throughput (tokens/s)
        self.t     = float(t)
        self.s     = float(s)


class LlamaBenchRow:
    """One row from a llama-bench markdown table."""
    __slots__ = ("model", "size", "params", "backend", "ngl", "n_ubatch",
                 "fa", "test", "tps", "tps_std",
                 # optional columns (not always present)
                 "threads", "mmap", "dio")

    def __init__(self, model, size, params, backend, test, tps, tps_std,
                 ngl=None, n_ubatch=None, fa=None,
                 threads=None, mmap=None, dio=None):
        self.model    = model.strip()
        self.size     = size.strip()
        self.params   = params.strip()
        self.backend  = backend.strip()
        self.ngl      = ngl
        self.n_ubatch = n_ubatch
        self.fa       = fa
        self.threads  = threads
        self.mmap     = mmap
        self.dio      = dio
        self.test     = test.strip()
        # "4505.82 ± 12.90" -> 4505.82
        self.tps      = float(tps.strip().split()[0])
        self.tps_std  = float(tps_std) if tps_std is not None else None


class ParsedResults:
    """All metrics extracted from a bench file."""

    def __init__(self):
        self.system_info: str = ""
        self.build_hashes: list[str] = []

        # Per-model sections keyed by model label (e.g. "gpt-oss 20B MXFP4 MoE")
        # Each value is a dict with keys 'batched' (list[BenchRow]) and
        # 'llama_bench' (list[LlamaBenchRow])
        self.models: dict[str, dict] = {}

        # Ordered list of model labels as they appear in the file
        self.model_order: list[str] = []

    def _ensure_model(self, label: str):
        if label not in self.models:
            self.models[label] = {"batched": [], "llama_bench": []}
            self.model_order.append(label)

    def add_batched_row(self, model_label: str, row: BenchRow):
        self._ensure_model(model_label)
        self.models[model_label]["batched"].append(row)

    def add_llama_bench_row(self, model_label: str, row: LlamaBenchRow):
        self._ensure_model(model_label)
        self.models[model_label]["llama_bench"].append(row)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _strip_cell(s: str) -> str:
    return s.strip()


def _parse_tps_cell(cell: str):
    """
    Parse a t/s cell that may look like:
        4505.82 ± 12.90
        83.43 ± 0.59
        29.10
    Returns (value: float, std: float | None)
    """
    cell = cell.strip()
    m = re.match(r"([\d.]+)\s*[±+/-]+\s*([\d.]+)", cell)
    if m:
        return float(m.group(1)), float(m.group(2))
    # plain number
    m2 = re.match(r"([\d.]+)", cell)
    if m2:
        return float(m2.group(1)), None
    return None, None


def parse_batched_bench_table(lines: list[str], model_label: str,
                              results: ParsedResults):
    """
    Parse a llama-batched-bench table.

    Header: |    PP |     TG |    B |   N_KV |   T_PP s | S_PP t/s |   T_TG s | S_TG t/s |      T s |    S t/s |
    """
    in_table = False
    header_seen = False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        cells = [c.strip() for c in stripped.split("|")]
        # Remove empty strings from leading/trailing |
        cells = [c for c in cells if c != ""]
        if not cells:
            continue

        # Detect header row
        if not header_seen:
            if "PP" in cells[0] and "TG" in cells[1]:
                header_seen = True
                in_table = True
                continue
            continue

        # Skip separator row (---|---|...)
        if re.match(r"^[-:]+$", cells[0]):
            continue

        # Data row: expect at least 10 columns
        if len(cells) < 10:
            continue

        try:
            row = BenchRow(
                pp    = cells[0],
                tg    = cells[1],
                batch = cells[2],
                n_kv  = cells[3],
                t_pp  = cells[4],
                s_pp  = cells[5],
                t_tg  = cells[6],
                s_tg  = cells[7],
                t     = cells[8],
                s     = cells[9],
            )
            results.add_batched_row(model_label, row)
        except (ValueError, IndexError):
            continue


def _detect_llama_bench_columns(header_cells: list[str]) -> dict[str, int]:
    """
    Map column names to indices. llama-bench has slightly different column
    sets on CUDA vs Metal (e.g. Metal uses 'threads' instead of 'ngl').
    Returns a dict {field_name: col_index}.
    """
    mapping = {}
    for i, cell in enumerate(header_cells):
        low = cell.strip().lower()
        if low == "model":
            mapping["model"] = i
        elif low == "size":
            mapping["size"] = i
        elif low == "params":
            mapping["params"] = i
        elif low == "backend":
            mapping["backend"] = i
        elif low == "ngl":
            mapping["ngl"] = i
        elif low == "n_ubatch":
            mapping["n_ubatch"] = i
        elif low == "fa":
            mapping["fa"] = i
        elif low == "threads":
            mapping["threads"] = i
        elif low == "mmap":
            mapping["mmap"] = i
        elif low == "dio":
            mapping["dio"] = i
        elif low == "test":
            mapping["test"] = i
        elif "t/s" in low:
            mapping["tps"] = i
    return mapping


def parse_llama_bench_table(lines: list[str], model_label: str,
                            results: ParsedResults):
    """
    Parse a llama-bench table.

    Typical header (CUDA):
      | model | size | params | backend | ngl | n_ubatch | fa | mmap | dio | test | t/s |
    Typical header (Metal):
      | model | size | params | backend | threads | n_ubatch | fa | test | t/s |
    """
    in_table = False
    col_map: dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        cells = [c.strip() for c in stripped.split("|")]
        cells = [c for c in cells if c != ""]
        if not cells:
            continue

        # Detect header row by presence of "model" and "t/s"
        if not in_table:
            if "model" in cells[0].lower() and any("t/s" in c.lower() for c in cells):
                col_map = _detect_llama_bench_columns(cells)
                in_table = True
                continue
            continue

        # Skip separator
        if re.match(r"^[-:]+$", cells[0]):
            continue

        if len(cells) < 5:
            continue

        try:
            def _get(field, default=None):
                idx = col_map.get(field)
                return cells[idx] if idx is not None and idx < len(cells) else default

            tps_raw = _get("tps", "0")
            # "4505.82 ± 12.90"
            tps_val, tps_std = _parse_tps_cell(tps_raw)
            if tps_val is None:
                continue

            row = LlamaBenchRow(
                model    = _get("model", model_label),
                size     = _get("size", ""),
                params   = _get("params", ""),
                backend  = _get("backend", ""),
                ngl      = _get("ngl"),
                n_ubatch = _get("n_ubatch"),
                fa       = _get("fa"),
                threads  = _get("threads"),
                mmap     = _get("mmap"),
                dio      = _get("dio"),
                test     = _get("test", ""),
                tps      = tps_raw,
                tps_std  = None,  # already parsed above
            )
            row.tps = tps_val
            row.tps_std = tps_std
            results.add_llama_bench_row(model_label, row)
        except (ValueError, IndexError):
            continue


# ---------------------------------------------------------------------------
# Top-level file parser
# ---------------------------------------------------------------------------

def parse_bench_file(text: str) -> ParsedResults:
    """
    Parse a complete bench output file (Markdown format with multiple model
    sections, each containing a llama-batched-bench table and a llama-bench
    table).

    The file structure is:
      ## System info
      ...
      ## <HF repo / model section heading>
      ...
      - `llama-batched-bench`
      <table>
      - `llama-bench`
      <table>
      build: <hash>
    """
    results = ParsedResults()
    lines = text.splitlines()
    n = len(lines)

    # --- Extract system info block ---
    sys_info_lines = []
    in_sys_info = False
    in_code_block = False
    for line in lines:
        if re.match(r"^##\s+System info", line, re.IGNORECASE):
            in_sys_info = True
            continue
        if in_sys_info:
            if line.strip().startswith("## ") and not re.match(
                    r"^##\s+System info", line, re.IGNORECASE):
                break
            if line.strip() == "```":
                in_code_block = not in_code_block
                continue
            if in_code_block:
                sys_info_lines.append(line)
    results.system_info = "\n".join(sys_info_lines).strip()

    # --- Collect build hashes ---
    for line in lines:
        m = re.match(r"^\s*build:\s*(\S+)", line)
        if m:
            h = m.group(1)
            if h not in results.build_hashes:
                results.build_hashes.append(h)

    # --- Split file into model sections ---
    # A model section starts with a level-2 heading that is NOT "System info"
    section_starts = []
    for i, line in enumerate(lines):
        m = re.match(r"^##\s+(.+)", line)
        if m:
            heading = m.group(1).strip()
            if re.match(r"system\s*info", heading, re.IGNORECASE):
                continue
            section_starts.append((i, heading))

    for sec_idx, (start_line, heading) in enumerate(section_starts):
        end_line = section_starts[sec_idx + 1][0] if sec_idx + 1 < len(section_starts) else n
        section_lines = lines[start_line:end_line]

        # The heading often contains a URL on the next line; the real model
        # label comes from the llama-bench 'model' column. We'll use the
        # heading as a fallback until we parse the tables.
        # Use the heading as the working model label.
        model_label = heading

        # Find llama-batched-bench and llama-bench subsections within this
        # section by looking for "- `llama-batched-bench`" and "- `llama-bench`"
        batched_start = None
        llama_bench_start = None

        for j, line in enumerate(section_lines):
            stripped = line.strip()
            if re.search(r"`llama-batched-bench`", stripped):
                batched_start = j + 1
            elif re.search(r"`llama-bench`", stripped) and not re.search(
                    r"`llama-batched-bench`", stripped):
                llama_bench_start = j + 1

        if batched_start is not None:
            parse_batched_bench_table(
                section_lines[batched_start:], model_label, results)

        if llama_bench_start is not None:
            parse_llama_bench_table(
                section_lines[llama_bench_start:], model_label, results)

    # --- Re-label models using the actual model name from llama-bench rows ---
    # (the heading may be a HF repo URL slug; the table 'model' column is cleaner)
    new_models = {}
    new_order = []
    for label in results.model_order:
        lb_rows = results.models[label]["llama_bench"]
        if lb_rows:
            # Use the first llama-bench row's model name as the canonical label
            canonical = lb_rows[0].model
        else:
            canonical = label
        # Avoid duplicate keys
        if canonical in new_models:
            # Merge
            new_models[canonical]["batched"].extend(
                results.models[label]["batched"])
            new_models[canonical]["llama_bench"].extend(lb_rows)
        else:
            new_models[canonical] = results.models[label]
            new_order.append(canonical)
    results.models = new_models
    results.model_order = new_order

    return results


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------

def extract_quant(model_label: str) -> str:
    """
    Extract quantization string from a model label.
    Examples:
        "gpt-oss 20B MXFP4 MoE"     -> "MXFP4"
        "qwen3moe 30B.A3B Q8_0"      -> "Q8_0"
        "qwen2 7B Q8_0"              -> "Q8_0"
        "gemma3 4B Q4_0"             -> "Q4_0"
        "deepseek2 30B.A3B Q8_0"     -> "Q8_0"
        "llama 8B Q4_K_M"            -> "Q4_K_M"
    """
    # Look for known quant patterns
    for pattern in (r"\bMXFP4\b", r"\bQ\d+_K_[MS]\b", r"\bQ\d+_\d+\b",
                    r"\bQ\d+_0\b", r"\bF16\b", r"\bQ8_0\b"):
        m = re.search(pattern, model_label, re.IGNORECASE)
        if m:
            return m.group(0).upper()
    return "unknown"


def extract_model_size(model_label: str) -> str:
    """
    Extract model size hint from label, e.g. "20B", "7B", "120B", "4B".
    Returns empty string if not found.
    """
    m = re.search(r"\b(\d+(?:\.\d+)?B(?:\.[A-Z0-9]+)?)\b", model_label, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return ""


class Metrics:
    """Extracted key metrics for a single model from a bench run."""

    def __init__(self):
        self.model_label: str = ""
        self.quant: str = ""
        self.model_size: str = ""
        self.backend: str = ""
        self.build_hash: str = ""

        # From llama-batched-bench (batch=1)
        self.tg_single_stream: Optional[float] = None   # TG t/s, B=1
        self.pp_single_stream: Optional[float] = None   # PP t/s, B=1 (best across PP sizes)
        self.ttft_p50_ms: Optional[float] = None        # estimated TTFT at pp=512, B=1

        # From llama-batched-bench (max batch)
        self.tg_max_batch: Optional[float] = None       # TG t/s at max B seen
        self.max_batch: Optional[int] = None

        # From llama-bench (tg32 test, B=1 equivalent)
        self.tg_lb: Optional[float] = None              # TG t/s from llama-bench tg32
        self.pp_lb: Optional[float] = None              # PP t/s from llama-bench pp* (lowest pp)

        # System info snippet
        self.ngl_detected: Optional[int] = None         # ngl from batched-bench header or llama-bench


def compute_metrics(results: ParsedResults, model_key: str) -> Metrics:
    """Compute key metrics for a single model section."""
    m = Metrics()
    m.model_label = model_key
    m.quant = extract_quant(model_key)
    m.model_size = extract_model_size(model_key)
    if results.build_hashes:
        m.build_hash = results.build_hashes[0]

    data = results.models[model_key]
    batched_rows: list[BenchRow] = data["batched"]
    lb_rows: list[LlamaBenchRow] = data["llama_bench"]

    # ---- Backend from llama-bench ----
    if lb_rows:
        m.backend = lb_rows[0].backend
        # Try to get ngl from llama-bench
        if lb_rows[0].ngl is not None:
            try:
                m.ngl_detected = int(lb_rows[0].ngl)
            except (ValueError, TypeError):
                pass

    # ---- Metrics from llama-batched-bench ----
    if batched_rows:
        # Best single-stream TG t/s: batch=1, any PP size → take max S_TG
        b1_rows = [r for r in batched_rows if r.batch == 1]
        if b1_rows:
            m.tg_single_stream = max(r.s_tg for r in b1_rows)
            m.pp_single_stream = max(r.s_pp for r in b1_rows)

            # TTFT estimate: 512 / PP_t/s * 1000 ms, for pp=512 B=1
            pp512_b1 = [r for r in b1_rows if r.pp == 512]
            if pp512_b1:
                pp_tps = pp512_b1[0].s_pp
                if pp_tps > 0:
                    m.ttft_p50_ms = 512.0 / pp_tps * 1000.0
            else:
                # Fall back to highest PP t/s among B=1 rows
                best_pp_row = max(b1_rows, key=lambda r: r.s_pp)
                if best_pp_row.s_pp > 0:
                    # Estimate TTFT as if pp=512
                    m.ttft_p50_ms = 512.0 / best_pp_row.s_pp * 1000.0

        # Best batched TG: find max batch with highest S_TG
        if batched_rows:
            max_batch = max(r.batch for r in batched_rows)
            max_batch_rows = [r for r in batched_rows if r.batch == max_batch]
            if max_batch_rows:
                m.tg_max_batch = max(r.s_tg for r in max_batch_rows)
                m.max_batch = max_batch

    # ---- Metrics from llama-bench ----
    if lb_rows:
        # tg32 test (no depth qualifier) → best single-stream TG
        tg_rows = [r for r in lb_rows if re.match(r"^tg\d+$", r.test.strip())]
        if tg_rows:
            m.tg_lb = max(r.tps for r in tg_rows)

        # pp* test (no depth qualifier) → best single-stream PP
        pp_rows = [r for r in lb_rows if re.match(r"^pp\d+$", r.test.strip())]
        if pp_rows:
            m.pp_lb = max(r.tps for r in pp_rows)

        # Refine TTFT using pp512 from llama-bench if not already set from batched
        if m.ttft_p50_ms is None:
            pp512_lb = [r for r in pp_rows if r.test.strip() == "pp512"]
            if pp512_lb:
                pp_tps = pp512_lb[0].tps
                if pp_tps > 0:
                    m.ttft_p50_ms = 512.0 / pp_tps * 1000.0
            elif pp_rows:
                # use whatever PP is available and scale
                pp_row = min(pp_rows, key=lambda r: int(re.search(r"\d+", r.test).group()))
                pp_val = int(re.search(r"\d+", pp_row.test).group())
                if pp_row.tps > 0:
                    m.ttft_p50_ms = 512.0 / pp_row.tps * 1000.0 * (512.0 / pp_val if pp_val != 512 else 1.0)

        # Prefer llama-bench single-stream TG if batched not available
        if m.tg_single_stream is None and m.tg_lb is not None:
            m.tg_single_stream = m.tg_lb
        if m.pp_single_stream is None and m.pp_lb is not None:
            m.pp_single_stream = m.pp_lb

    return m


# ---------------------------------------------------------------------------
# Markdown table editing helpers
# ---------------------------------------------------------------------------

def _find_section_line(md_lines: list[str], heading_pattern: str) -> int:
    """Return the line index of the first heading matching the regex pattern."""
    for i, line in enumerate(md_lines):
        if re.match(heading_pattern, line.strip()):
            return i
    return -1


def _find_table_in_section(md_lines: list[str], section_start: int,
                           next_section_start: int) -> tuple[int, int]:
    """
    Within md_lines[section_start:next_section_start], find the first markdown
    table.  Returns (header_line_idx, last_data_line_idx) as absolute indices
    into md_lines.  Returns (-1, -1) if not found.
    """
    header_idx = -1
    last_data_idx = -1
    in_table = False
    for i in range(section_start, next_section_start):
        line = md_lines[i]
        stripped = line.strip()
        if stripped.startswith("|"):
            if not in_table:
                # Check it looks like a header row
                if re.search(r"\w", stripped):
                    header_idx = i
                    in_table = True
            last_data_idx = i
        else:
            if in_table:
                # Table ended
                break
    return header_idx, last_data_idx


def _parse_md_table_headers(header_line: str) -> list[str]:
    """Return list of column header strings from a markdown table row."""
    cells = header_line.strip().split("|")
    return [c.strip() for c in cells if c.strip()]


def _col_indices(headers: list[str], *names) -> dict[str, int]:
    """
    Build a map of header-name -> column index (0-based among non-empty cells).
    Matching is case-insensitive, partial-match allowed.
    """
    result = {}
    for name in names:
        for i, h in enumerate(headers):
            if name.lower() in h.lower():
                result[name] = i
                break
    return result


def _table_rows(md_lines: list[str], header_line: int,
                last_line: int) -> list[tuple[int, list[str]]]:
    """
    Return list of (line_index, [cell, ...]) for all data rows in the table
    (skipping the header and separator lines).
    """
    rows = []
    for i in range(header_line + 1, last_line + 1):
        stripped = md_lines[i].strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.split("|")]
        cells = [c for c in cells if c != ""]
        # Skip separator rows
        if cells and re.match(r"^[-:]+$", cells[0]):
            continue
        if cells:
            rows.append((i, cells))
    return rows


def _update_cell(line: str, col_index: int, new_value: str) -> str:
    """
    Replace the value in the col_index-th non-empty pipe-delimited cell.
    Preserves surrounding whitespace padding.
    """
    # Split on pipes, keeping structure
    parts = line.split("|")
    # parts[0] is empty (leading |), parts[1..] are cells, parts[-1] may be empty
    non_empty_count = -1
    for i, part in enumerate(parts):
        if part.strip() == "" and i == 0:
            continue
        if part.strip() == "" and i == len(parts) - 1:
            continue
        non_empty_count += 1
        if non_empty_count == col_index:
            # Replace while preserving column width
            old_stripped = part.strip()
            leading_spaces = len(part) - len(part.lstrip())
            trailing_spaces = len(part) - len(part.rstrip())
            new_padded = " " * leading_spaces + new_value + " " * trailing_spaces
            # If new value is longer than column, just use with minimal padding
            if len(new_value) + 2 > len(part):
                new_padded = " " + new_value + " "
            parts[i] = new_padded
            break
    return "|".join(parts)


def _append_table_row(md_lines: list[str], last_data_line: int,
                      new_row_cells: list[str]) -> int:
    """
    Insert a new row after last_data_line.  Returns the new last_data_line.
    """
    # Build row matching rough column widths of the header row
    row_str = "| " + " | ".join(new_row_cells) + " |"
    md_lines.insert(last_data_line + 1, row_str)
    return last_data_line + 1


# ---------------------------------------------------------------------------
# Section 12 (Run Log) update
# ---------------------------------------------------------------------------

def update_run_log(md_lines: list[str], metrics: Metrics, args: argparse.Namespace,
                   dry_run: bool) -> list[str]:
    """
    Append a new row to the Section 12 Run Log table.
    Expected columns: Date | Hardware | Chip | Runtime | Model | Quant | PP t/s | TG t/s | Notes | Commit
    """
    sec12 = _find_section_line(md_lines, r"^##\s+12\.")
    if sec12 == -1:
        # Fallback: look for "Run Log" heading
        sec12 = _find_section_line(md_lines, r"^##\s+.*[Rr]un\s+[Ll]og")
    if sec12 == -1:
        print("WARNING: Could not find Section 12 (Run Log) heading.", file=sys.stderr)
        return md_lines

    # Next section starts at the next ##
    next_sec = len(md_lines)
    for i in range(sec12 + 1, len(md_lines)):
        if re.match(r"^##\s+", md_lines[i]):
            next_sec = i
            break

    header_line, last_data_line = _find_table_in_section(md_lines, sec12, next_sec)
    if header_line == -1:
        print("WARNING: Could not find Run Log table.", file=sys.stderr)
        return md_lines

    headers = _parse_md_table_headers(md_lines[header_line])

    # Build chip description from runtime / machine
    chip = _machine_to_chip(args.machine)

    # Format t/s values
    pp_val = f"{metrics.pp_single_stream:.0f}" if metrics.pp_single_stream else "—"
    tg_val = f"{metrics.tg_single_stream:.1f}" if metrics.tg_single_stream else "—"

    # Notes
    notes_parts = []
    if args.ngl is not None:
        notes_parts.append(f"ngl={args.ngl}")
    if metrics.ngl_detected is not None and args.ngl is None:
        notes_parts.append(f"ngl={metrics.ngl_detected}")
    if metrics.backend:
        notes_parts.append(f"backend={metrics.backend}")
    if metrics.build_hash:
        notes_parts.append(f"build={metrics.build_hash}")
    notes = ", ".join(notes_parts) if notes_parts else "—"

    runtime_display = _runtime_display(args.runtime, args.ngl)

    new_cells = [
        args.date,
        args.machine,
        chip,
        runtime_display,
        metrics.model_label,
        metrics.quant,
        pp_val,
        tg_val,
        notes,
        "—",   # Commit — filled in manually after git commit
    ]

    # Pad to number of header columns
    while len(new_cells) < len(headers):
        new_cells.append("—")

    if dry_run:
        print(f"[DRY RUN] Would append to Run Log:")
        print("  | " + " | ".join(new_cells) + " |")
        return md_lines

    new_lines = list(md_lines)
    _append_table_row(new_lines, last_data_line, new_cells)
    print(f"  + Run Log: appended row for {metrics.model_label} on {args.machine}")
    return new_lines


# ---------------------------------------------------------------------------
# Section 2.6 (Phase 1 results summary) update
# ---------------------------------------------------------------------------

def _runtime_display(runtime: str, ngl: Optional[int]) -> str:
    """Format runtime string as it appears in Section 2.6."""
    mapping = {
        "llamacpp": "llama.cpp",
        "ollama": "Ollama",
        "vllm-mlx": "vLLM-MLX",
    }
    base = mapping.get(runtime.lower(), runtime)
    if ngl is not None:
        return f"{base} (ngl={ngl})"
    return base


def _machine_to_chip(machine: str) -> str:
    chips = {
        "p1-m2-8g": "Apple M2",
        "p1-m3-16g": "Apple M3",
        "p1-i7-rtx3050": "Intel i7 / RTX 3050",
        "m2-ultra": "Apple M2 Ultra",
        "dgx-spark": "NVIDIA GB10",
    }
    return chips.get(machine, machine)


def _row_matches_key(cells: list[str], machine: str, runtime_display: str,
                     quant: str, model_size: str) -> bool:
    """
    Return True when a Section 2.6 row matches the current run's
    (machine, runtime, model-size+quant) key.

    The row format is:
      | `machine` | model_desc | runtime | TG t/s | TTFT p50 | ... |
    """
    if len(cells) < 3:
        return False

    machine_cell = cells[0].strip().strip("`")
    model_cell   = cells[1].strip()
    runtime_cell = cells[2].strip()

    # Machine must match
    if machine_cell.lower() != machine.lower():
        return False

    # Runtime must match (partial ok: "llama.cpp" matches "llama.cpp (ngl=0)")
    rt_base = runtime_display.split(" (")[0].lower()
    rc_base = runtime_cell.split(" (")[0].lower()
    if rt_base != rc_base:
        return False

    # Model: check if size and quant strings appear in the model cell
    if model_size and model_size.lower() not in model_cell.lower():
        return False
    if quant and quant.lower() not in model_cell.lower():
        return False

    return True


def update_phase1_summary(md_lines: list[str], metrics: Metrics,
                          args: argparse.Namespace, dry_run: bool) -> list[str]:
    """
    Update the matching row in Section 2.6 Phase 1 results summary.
    Fills in TG t/s and TTFT p50 columns.

    Table columns: Machine | Model | Runtime | TG t/s | TTFT p50 (B=1) | Max concurrent @ SLA | Decision signal
    """
    sec26 = _find_section_line(md_lines, r"^###\s+2\.6")
    if sec26 == -1:
        # Fallback
        sec26 = _find_section_line(md_lines, r"^###.*[Pp]hase\s+1\s+results\s+summary")
    if sec26 == -1:
        print("WARNING: Could not find Section 2.6 heading.", file=sys.stderr)
        return md_lines

    next_sec = len(md_lines)
    for i in range(sec26 + 1, len(md_lines)):
        if re.match(r"^##", md_lines[i]):
            next_sec = i
            break

    header_line, last_data_line = _find_table_in_section(md_lines, sec26, next_sec)
    if header_line == -1:
        print("WARNING: Could not find Section 2.6 table.", file=sys.stderr)
        return md_lines

    headers = _parse_md_table_headers(md_lines[header_line])
    col = _col_indices(headers, "TG", "TTFT", "Machine", "Runtime", "Model")

    runtime_display = _runtime_display(args.runtime, args.ngl)
    rows = _table_rows(md_lines, header_line, last_data_line)

    new_lines = list(md_lines)
    updated = False

    for line_idx, cells in rows:
        if _row_matches_key(cells, args.machine, runtime_display,
                            metrics.quant, metrics.model_size):
            # Fill in TG t/s
            if "TG" in col and metrics.tg_single_stream is not None:
                tg_str = f"{metrics.tg_single_stream:.1f}"
                new_lines[line_idx] = _update_cell(
                    new_lines[line_idx], col["TG"], tg_str)

            # Fill in TTFT p50
            if "TTFT" in col and metrics.ttft_p50_ms is not None:
                ttft_str = f"~{metrics.ttft_p50_ms:.0f} ms"
                new_lines[line_idx] = _update_cell(
                    new_lines[line_idx], col["TTFT"], ttft_str)

            if not dry_run:
                print(f"  + Section 2.6: updated row for {args.machine} / "
                      f"{metrics.model_label} / {runtime_display}")
            else:
                print(f"[DRY RUN] Would update Section 2.6 row:")
                print(f"  Machine={args.machine}, Model={metrics.model_label}, "
                      f"Runtime={runtime_display}")
                if metrics.tg_single_stream:
                    print(f"  TG t/s  -> {metrics.tg_single_stream:.1f}")
                if metrics.ttft_p50_ms:
                    print(f"  TTFT p50 -> ~{metrics.ttft_p50_ms:.0f} ms")
                # Restore original line in dry-run
                new_lines[line_idx] = md_lines[line_idx]
            updated = True
            break   # Only update the first matching row

    if not updated:
        print(f"  ~ Section 2.6: no matching row found for "
              f"machine={args.machine}, runtime={runtime_display}, "
              f"quant={metrics.quant}, size={metrics.model_size}")

    return new_lines


# ---------------------------------------------------------------------------
# Section 8.1 / 8.2 workload scenario update
# ---------------------------------------------------------------------------

def _choose_section8(metrics: Metrics) -> Optional[str]:
    """
    Decide whether this result belongs to 8.1 (Autocomplete/FastPool) or
    8.2 (Chat/ReasonPool) based on model size heuristics.

    Returns "8.1", "8.2", or None (skip).
    """
    size_str = metrics.model_size.upper()
    # Small models (≤8B) → FastPool (8.1); larger → ReasonPool (8.2)
    m = re.match(r"([\d.]+)B", size_str)
    if m:
        size_num = float(m.group(1))
        if size_num <= 8.0:
            return "8.1"
        else:
            return "8.2"
    return None


def update_section8(md_lines: list[str], metrics: Metrics,
                    args: argparse.Namespace, dry_run: bool) -> list[str]:
    """
    Update the matching row in Section 8.1 or 8.2 workload scenario table.
    Looks for a row matching (Hardware, Runtime) and fills in PP t/s, TG t/s,
    TTFT p50 if all columns exist.

    These tables have columns:
      Hardware | Runtime | Quant | PP t/s | TG t/s | TTFT p50 (est.) | Tested
    """
    target = _choose_section8(metrics)
    if target is None:
        return md_lines

    # Find the right sub-section
    sec_pattern = r"^###\s+8\." + re.escape(target.split(".")[1])
    sec_line = _find_section_line(md_lines, sec_pattern)
    if sec_line == -1:
        return md_lines

    next_sec = len(md_lines)
    for i in range(sec_line + 1, len(md_lines)):
        if re.match(r"^##", md_lines[i]):
            next_sec = i
            break

    # There may be multiple tables in section 8.x — scan for the first one
    # with a Hardware column
    all_header_lines = []
    for i in range(sec_line, next_sec):
        stripped = md_lines[i].strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")]
            cells = [c for c in cells if c]
            if cells and "hardware" in cells[0].lower():
                all_header_lines.append(i)

    if not all_header_lines:
        return md_lines

    header_line = all_header_lines[0]
    # Find last data line of this table
    last_data_line = header_line
    for i in range(header_line + 1, next_sec):
        if md_lines[i].strip().startswith("|"):
            last_data_line = i
        elif last_data_line > header_line:
            break

    headers = _parse_md_table_headers(md_lines[header_line])
    col = _col_indices(headers, "PP", "TG", "TTFT", "Hardware", "Runtime", "Quant", "Tested")

    runtime_display = _runtime_display(args.runtime, args.ngl)
    machine_display = f"`{args.machine}`"
    rows = _table_rows(md_lines, header_line, last_data_line)

    new_lines = list(md_lines)
    updated = False

    for line_idx, cells in rows:
        if len(cells) < 2:
            continue
        hw_cell = cells[0].strip().strip("`")
        rt_cell = cells[1].strip() if len(cells) > 1 else ""

        hw_match = hw_cell.lower() == args.machine.lower()
        rt_base = runtime_display.split(" (")[0].lower()
        rc_base = rt_cell.split(" (")[0].lower()
        rt_match = rt_base == rc_base

        if hw_match and rt_match:
            if "PP" in col and metrics.pp_single_stream is not None:
                pp_str = f"{metrics.pp_single_stream:.0f}"
                new_lines[line_idx] = _update_cell(new_lines[line_idx], col["PP"], pp_str)
            if "TG" in col and metrics.tg_single_stream is not None:
                tg_str = f"{metrics.tg_single_stream:.1f}"
                new_lines[line_idx] = _update_cell(new_lines[line_idx], col["TG"], tg_str)
            if "TTFT" in col and metrics.ttft_p50_ms is not None:
                ttft_str = f"~{metrics.ttft_p50_ms:.0f} ms"
                new_lines[line_idx] = _update_cell(new_lines[line_idx], col["TTFT"], ttft_str)
            if "Tested" in col:
                new_lines[line_idx] = _update_cell(
                    new_lines[line_idx], col["Tested"], args.date)

            if not dry_run:
                print(f"  + Section {target}: updated row for {args.machine} / "
                      f"{runtime_display}")
            else:
                print(f"[DRY RUN] Would update Section {target} row for "
                      f"{args.machine} / {runtime_display}")
                new_lines[line_idx] = md_lines[line_idx]
            updated = True
            break

    if not updated:
        print(f"  ~ Section {target}: no matching row for "
              f"machine={args.machine}, runtime={runtime_display}")

    return new_lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

VALID_MACHINES = {
    "p1-m2-8g", "p1-m3-16g", "p1-i7-rtx3050", "m2-ultra", "dgx-spark",
}
VALID_RUNTIMES = {"llamacpp", "ollama", "vllm-mlx"}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest llama-bench results and update benchmarkings.md",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "bench_file",
        nargs="?",
        default=None,
        help="Path to bench output file. Reads stdin if omitted.",
    )
    parser.add_argument(
        "--machine",
        required=True,
        choices=sorted(VALID_MACHINES),
        help="Machine ID",
    )
    parser.add_argument(
        "--runtime",
        required=True,
        choices=sorted(VALID_RUNTIMES),
        help="Inference runtime",
    )
    parser.add_argument(
        "--ngl",
        type=int,
        default=None,
        help="Number of GPU layers used (optional)",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Run date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be changed, do not write",
    )
    parser.add_argument(
        "--benchmarkings",
        default=None,
        help="Path to benchmarkings.md (default: auto-detect from script location)",
    )
    return parser


def resolve_benchmarkings_path(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    # Assume script lives at <project_root>/scripts/ingest_results.py
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    candidate = os.path.join(project_root, "benchmarkings.md")
    if os.path.isfile(candidate):
        return candidate
    # Fallback: current directory
    cwd_candidate = os.path.join(os.getcwd(), "benchmarkings.md")
    if os.path.isfile(cwd_candidate):
        return cwd_candidate
    return candidate  # Return even if missing; will error on open


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    # Set default date
    if args.date is None:
        args.date = date.today().isoformat()
    else:
        # Validate date format
        try:
            date.fromisoformat(args.date)
        except ValueError:
            print(f"ERROR: Invalid date format '{args.date}'. Use YYYY-MM-DD.",
                  file=sys.stderr)
            sys.exit(1)

    # Read bench file or stdin
    if args.bench_file:
        try:
            with open(args.bench_file, "r", encoding="utf-8") as fh:
                bench_text = fh.read()
        except OSError as e:
            print(f"ERROR: Cannot read bench file: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        bench_text = sys.stdin.read()

    if not bench_text.strip():
        print("ERROR: Empty bench input.", file=sys.stderr)
        sys.exit(1)

    # Parse bench output
    print("Parsing bench output...")
    try:
        results = parse_bench_file(bench_text)
    except Exception as e:
        print(f"ERROR: Failed to parse bench file: {e}", file=sys.stderr)
        sys.exit(1)

    if not results.models:
        print("ERROR: No model sections found in bench output.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(results.models)} model section(s): "
          f"{', '.join(results.model_order)}")

    # Compute metrics for each model
    all_metrics = []
    for model_key in results.model_order:
        m = compute_metrics(results, model_key)
        all_metrics.append(m)
        print(f"\nModel: {model_key}")
        print(f"  Quant:             {m.quant}")
        print(f"  Backend:           {m.backend}")
        if m.tg_single_stream:
            print(f"  TG t/s (B=1):      {m.tg_single_stream:.2f}")
        if m.pp_single_stream:
            print(f"  PP t/s (B=1):      {m.pp_single_stream:.2f}")
        if m.ttft_p50_ms:
            print(f"  TTFT p50 (est.):   {m.ttft_p50_ms:.1f} ms")
        if m.tg_max_batch:
            print(f"  TG t/s (B={m.max_batch}):    {m.tg_max_batch:.2f}")
        if m.build_hash:
            print(f"  Build hash:        {m.build_hash}")
        if results.system_info:
            snippet = results.system_info[:120].replace("\n", " | ")
            print(f"  System info:       {snippet}...")

    # Read benchmarkings.md
    bm_path = resolve_benchmarkings_path(args.benchmarkings)
    try:
        with open(bm_path, "r", encoding="utf-8") as fh:
            md_content = fh.read()
    except OSError as e:
        print(f"\nERROR: Cannot read benchmarkings.md at {bm_path}: {e}",
              file=sys.stderr)
        sys.exit(1)

    md_lines = md_content.splitlines()

    print(f"\nUpdating {bm_path} ...")
    print(f"  Machine: {args.machine}  Runtime: {args.runtime}  Date: {args.date}")

    # Apply updates for each model found in the bench file
    for metrics in all_metrics:
        print(f"\n--- {metrics.model_label} ---")
        md_lines = update_run_log(md_lines, metrics, args, args.dry_run)
        md_lines = update_phase1_summary(md_lines, metrics, args, args.dry_run)
        md_lines = update_section8(md_lines, metrics, args, args.dry_run)

    # Write updated file
    if not args.dry_run:
        new_content = "\n".join(md_lines)
        # Preserve trailing newline
        if md_content.endswith("\n"):
            new_content += "\n"
        try:
            with open(bm_path, "w", encoding="utf-8") as fh:
                fh.write(new_content)
            print(f"\nWrote updated benchmarkings.md ({len(new_content)} bytes)")
        except OSError as e:
            print(f"\nERROR: Cannot write benchmarkings.md: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("\n[DRY RUN] benchmarkings.md was NOT modified.")

    print("\nDone.")
    sys.exit(0)


if __name__ == "__main__":
    main()
