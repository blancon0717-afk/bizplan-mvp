from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

from claude_structurer import Structurer
from notion_client import (
    NotionClient,
    extract_row_fields,
    find_plan_pdf,
    should_exclude_row,
)
from notion_meta_features import compute_meta_features
from pdf_extractor import download_pdf, extract_text

ROOT = Path(__file__).parent
LOG_DIR = ROOT / "logs"
OUT_DIR = ROOT / "data"
LOG_DIR.mkdir(exist_ok=True)
OUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("pipeline")
fail_log = logging.getLogger("failures")
_fh = logging.FileHandler(LOG_DIR / "failures.log", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
fail_log.addHandler(_fh)
fail_log.setLevel(logging.INFO)
fail_log.propagate = False


def require(var: str) -> str:
    v = os.getenv(var)
    if not v or v.startswith("sk-ant-xxx") or v.startswith("xxxxxx"):
        log.error("missing or placeholder env: %s", var)
        sys.exit(1)
    return v


def process_page(page: dict, notion: NotionClient, structurer: Structurer) -> dict:
    row = extract_row_fields(page)
    excluded, why = should_exclude_row(row)
    if excluded:
        return {**row, "skip_reason": why, "status": "skipped"}

    url, fname = find_plan_pdf(notion, page)
    if not url:
        return {**row, "skip_reason": "no plan pdf/hwp", "status": "skipped"}

    ext = (fname or "").lower().rsplit(".", 1)[-1]
    if ext not in ("pdf",):
        return {**row, "pdf_url": url, "pdf_name": fname, "skip_reason": f"non-pdf ext={ext}", "status": "skipped"}

    pdf_bytes = download_pdf(url)
    text = extract_text(pdf_bytes)
    if not text.strip():
        raise RuntimeError("empty text from pdf")

    features = structurer.structure(text, 주관기관=row.get("주관기관"))
    meta_feats = compute_meta_features(row)
    return {
        **row,
        "pdf_url": url,
        "pdf_name": fname,
        "text_chars": len(text),
        "status": "ok",
        **meta_feats,
        **features,
    }


def run(sample_size: int, concurrency: int, dry_run: bool = False) -> None:
    load_dotenv()
    notion_token = require("NOTION_TOKEN")
    db_id = require("NOTION_DB_ID")
    anthropic_key = require("ANTHROPIC_API_KEY")

    notion = NotionClient(notion_token, db_id)
    structurer = Structurer(anthropic_key)

    log.info("fetching plan pages (limit=%s)", sample_size if sample_size > 0 else "∞")
    pages: list[dict] = []
    for p in notion.iter_plan_pages():
        pages.append(p)
        if sample_size and len(pages) >= sample_size:
            break
    log.info("fetched %d pages", len(pages))

    if dry_run:
        log.info("DRY-RUN: listing first %d page metadata only (no PDF download / no Haiku call)", min(10, len(pages)))
        for p in pages[:10]:
            meta = extract_row_fields(p)
            url, fname = find_plan_pdf(notion, p)
            log.info(" - %s | %s | %s | doc=%s fin=%s | file=%s", meta.get("사업분야"), meta.get("업체명"), meta.get("아이템명"), meta.get("서류합격"), meta.get("최종합격"), fname)
        notion.close()
        return

    results: list[dict] = []
    started = time.time()

    def task(p: dict) -> dict:
        try:
            return process_page(p, notion, structurer)
        except Exception as e:
            meta = extract_row_fields(p)
            err = f"{type(e).__name__}: {e}"
            fail_log.info("page=%s err=%s", meta.get("page_id"), err)
            return {**meta, "status": "fail", "error": err}

    total = len(pages)
    done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(task, p) for p in pages]
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            done += 1
            pct = done * 100 // total if total else 100
            elapsed = time.time() - started
            log.info("progress %d/%d (%d%%) elapsed=%.1fs | %s", done, total, pct, elapsed, structurer.usage_str())

    by_status = {}
    for r in results:
        s = r.get("status", "?")
        by_status[s] = by_status.get(s, 0) + 1
    log.info("done by status: %s", by_status)

    out_json = OUT_DIR / f"rubric_results_{int(time.time())}.jsonl"
    with open(out_json, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    log.info("wrote %s (%d rows)", out_json, len(results))

    all_keys: list[str] = []
    seen = set()
    for r in results:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                all_keys.append(k)
    out_csv = OUT_DIR / f"rubric_results_{int(time.time())}.csv"
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_keys)
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in all_keys})
    log.info("wrote %s", out_csv)

    notion.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", type=int, default=None, help="max pages to process; 0 = all")
    p.add_argument("--concurrency", type=int, default=None)
    p.add_argument("--dry-run", action="store_true", help="list sample page metadata, no Haiku / PDF download")
    return p.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    sample = args.sample if args.sample is not None else int(os.getenv("SAMPLE_SIZE", "50"))
    conc = args.concurrency if args.concurrency is not None else int(os.getenv("CONCURRENCY", "10"))
    run(sample_size=sample, concurrency=conc, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
