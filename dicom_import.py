"""
dicom_import.py — Recursively scan a folder for DICOM files and upload to Orthanc.
Usage:
    python dicom_import.py /path/to/folder
    python dicom_import.py /path/to/folder --orthanc http://localhost:8042
    python dicom_import.py /path/to/folder --dry-run
    python dicom_import.py /path/to/folder --workers 4
"""

import argparse
import os
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import pydicom
from pydicom.errors import InvalidDicomError

# Configuration
DEFAULT_ORTHANC  = os.getenv("ORTHANC_URL",  "http://localhost:8042")
DEFAULT_USER     = os.getenv("ORTHANC_USER", "orthanc")
DEFAULT_PASS     = os.getenv("ORTHANC_PASS", "orthanc")

# Terminal colors
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):    print(f"  {GREEN}✓{RESET} {msg}")
def err(msg):   print(f"  {RED}✗{RESET} {msg}")
def warn(msg):  print(f"  {YELLOW}!{RESET} {msg}")
def info(msg):  print(f"  {CYAN}→{RESET} {msg}")

# DICOM detection
DICOM_EXTENSIONS = {".dcm", ".dicom", ".dic"}

def is_dicom(path: Path) -> bool:
    """Return True if the file is a valid DICOM file."""
    # Fast check: known extension
    if path.suffix.lower() in DICOM_EXTENSIONS:
        return True
    # Slow check: try to read the file header (handles files without extension)
    try:
        pydicom.dcmread(str(path), stop_before_pixels=True, force=False)
        return True
    except (InvalidDicomError, Exception):
        return False

def scan_folder(folder: Path, check_content: bool = False) -> list[Path]:
    """
    Recursively find all DICOM files in folder.
    check_content=True: verify every file by reading header (slower, more accurate).
    check_content=False: trust extensions only (fast, misses extensionless files).
    """
    found = []
    all_files = list(folder.rglob("*"))
    total = len(all_files)

    print(f"\n{BOLD}Scanning:{RESET} {folder}")
    print(f"  Found {total} total files to check...\n")

    for i, f in enumerate(all_files):
        if not f.is_file():
            continue
        if check_content:
            if is_dicom(f):
                found.append(f)
        else:
            if f.suffix.lower() in DICOM_EXTENSIONS or f.suffix == "":
                if is_dicom(f):
                    found.append(f)
            elif f.suffix.lower() in DICOM_EXTENSIONS:
                found.append(f)

        # Progress indicator every 100 files
        if (i + 1) % 100 == 0:
            print(f"\r  Checked {i+1}/{total} files, found {len(found)} DICOM...", end="", flush=True)

    print(f"\r  Checked {total}/{total} files.{' ' * 20}")
    return found

# Orthanc upload
def test_orthanc(orthanc_url: str, user: str, password: str) -> bool:
    try:
        r = httpx.get(f"{orthanc_url}/system", auth=(user, password), timeout=5)
        return r.status_code == 200
    except Exception:
        return False

def upload_file(path: Path, orthanc_url: str, user: str, password: str) -> dict:
    """Upload a single DICOM file to Orthanc. Returns result dict."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        r = httpx.post(
            f"{orthanc_url}/instances",
            content=data,
            headers={"Content-Type": "application/dicom"},
            auth=(user, password),
            timeout=30,
        )
        if r.status_code == 200:
            resp = r.json()
            return {
                "file": str(path),
                "status": "ok",
                "orthanc_id": resp.get("ID", ""),
                "duplicate": resp.get("Status") == "AlreadyStored",
            }
        else:
            return {"file": str(path), "status": "failed", "code": r.status_code, "body": r.text[:200]}
    except Exception as e:
        return {"file": str(path), "status": "error", "error": str(e)}

# Upload confirmation
def confirm_upload(files: list[Path], orthanc_url: str) -> bool:
    print(f"\n{BOLD}Summary:{RESET}")
    print(f"  DICOM files found : {CYAN}{len(files)}{RESET}")
    print(f"  Target Orthanc    : {CYAN}{orthanc_url}{RESET}")
    print()

    # Show first 10 files as preview
    preview = files[:10]
    for f in preview:
        print(f"    {f}")
    if len(files) > 10:
        print(f"    ... and {len(files) - 10} more")

    print()
    answer = input(f"  Upload all {len(files)} files to Orthanc? [{GREEN}y{RESET}/{RED}n{RESET}]: ").strip().lower()
    return answer in ("y", "yes")

# Main
def main():
    parser = argparse.ArgumentParser(
        description="Recursively scan a folder for DICOM files and upload to Orthanc."
    )
    parser.add_argument("folder", help="Folder to scan recursively")
    parser.add_argument("--orthanc",  default=DEFAULT_ORTHANC, help="Orthanc URL (default: %(default)s)")
    parser.add_argument("--user",     default=DEFAULT_USER,    help="Orthanc username")
    parser.add_argument("--password", default=DEFAULT_PASS,    help="Orthanc password")
    parser.add_argument("--dry-run",  action="store_true",     help="Scan only, do not upload")
    parser.add_argument("--workers",  type=int, default=2,     help="Parallel upload threads (default: 2)")
    parser.add_argument("--deep-scan",action="store_true",     help="Check file content, not just extension (slower)")
    parser.add_argument("--yes",      action="store_true",     help="Skip confirmation prompt")
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists() or not folder.is_dir():
        print(f"{RED}Error:{RESET} '{folder}' is not a valid directory.")
        sys.exit(1)

    print(f"\n{BOLD}{CYAN}MSV-med DICOM Importer{RESET}")
    print("─" * 40)

    # Test Orthanc connectivity (skip in dry-run)
    if not args.dry_run:
        info(f"Testing Orthanc at {args.orthanc}...")
        if test_orthanc(args.orthanc, args.user, args.password):
            ok("Orthanc is reachable.")
        else:
            err(f"Cannot reach Orthanc at {args.orthanc}. Check URL and credentials.")
            sys.exit(1)

    # Scan
    files = scan_folder(folder, check_content=args.deep_scan)

    if not files:
        warn("No DICOM files found in the specified folder.")
        sys.exit(0)

    print(f"\n  {GREEN}{BOLD}Found {len(files)} DICOM file(s).{RESET}")

    if args.dry_run:
        print(f"\n{YELLOW}Dry run mode — no files will be uploaded.{RESET}")
        for f in files:
            print(f"  {f}")
        sys.exit(0)

    # Confirm
    if not args.yes:
        if not confirm_upload(files, args.orthanc):
            print("  Aborted.")
            sys.exit(0)

    # Upload
    print(f"\n{BOLD}Uploading with {args.workers} worker(s)...{RESET}\n")
    results = {"ok": 0, "duplicate": 0, "failed": 0, "error": 0}
    start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(upload_file, f, args.orthanc, args.user, args.password): f
            for f in files
        }
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            status = result["status"]

            if status == "ok":
                if result.get("duplicate"):
                    results["duplicate"] += 1
                    warn(f"[{i}/{len(files)}] Already stored: {Path(result['file']).name}")
                else:
                    results["ok"] += 1
                    ok(f"[{i}/{len(files)}] {Path(result['file']).name} → {result.get('orthanc_id', '')[:16]}")
            else:
                results["failed" if status == "failed" else "error"] += 1
                err(f"[{i}/{len(files)}] {Path(result['file']).name} — {result.get('error') or result.get('body', '')[:80]}")

    elapsed = time.time() - start

    # Summary
    print(f"\n{'─' * 40}")
    print(f"{BOLD}Done in {elapsed:.1f}s{RESET}")
    print(f"  {GREEN}Uploaded : {results['ok']}{RESET}")
    print(f"  {YELLOW}Duplicate: {results['duplicate']}{RESET}")
    print(f"  {RED}Failed   : {results['failed'] + results['error']}{RESET}")
    print()

if __name__ == "__main__":
    main()
