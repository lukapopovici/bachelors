"""
MSV-med GUI - DearPyGui interface for the PACS backend API.
Run: python gui.py
Requires: uv sync --extra gui
"""

import threading
import time
import httpx
import json
import os
from pathlib import Path
import dearpygui.dearpygui as dpg

# Configuration
API_URL   = os.getenv("API_URL",    "http://localhost:8000")
API_TOKEN = os.getenv("API_TOKEN")
API_USERNAME = os.getenv("API_USERNAME", os.getenv("ADMIN_USERNAME", "admin"))
API_PASSWORD = os.getenv("API_PASSWORD", os.getenv("ADMIN_PASSWORD", "admin123"))
ORTHANC   = os.getenv("ORTHANC_URL","http://localhost:8042")
UI_FONT   = os.getenv("UI_FONT", "/usr/share/fonts/abattis-cantarell-fonts/Cantarell-Regular.otf")

HEADERS = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}


def login_api():
    global API_TOKEN, HEADERS
    if not API_USERNAME or not API_PASSWORD:
        return False
    try:
        r = httpx.post(
            f"{API_URL}/auth/login",
            json={"username": API_USERNAME, "password": API_PASSWORD},
            timeout=15,
        )
        if r.status_code != 200:
            return False
        token = r.json().get("access_token")
        if not token:
            return False
        API_TOKEN = token
        HEADERS = {"Authorization": f"Bearer {token}"}
        return True
    except Exception:
        return False


def request_with_auth(method, path, **kwargs):
    request_kwargs = dict(kwargs)
    request_kwargs.setdefault("headers", HEADERS.copy())
    request_kwargs.setdefault("timeout", 10)
    response = method(f"{API_URL}{path}", **request_kwargs)
    if response.status_code == 401 and login_api():
        request_kwargs["headers"] = HEADERS.copy()
        response = method(f"{API_URL}{path}", **request_kwargs)
    return response

# Application state
state = {
    "studies":        [],
    "jobs":           [],
    "records":        [],
    "search_results": [],
    "pacs_configs":   [],
    "selected_study": None,
    "selected_job":   None,
    "stats":          {},
    "audit":          [],
    "workers":        [],
}

# HTTP helpers
def raise_for_api_error(response):
    if response.is_success:
        return
    try:
        payload = response.json()
        detail = payload.get("detail", payload)
        if isinstance(detail, list):
            detail = "; ".join(
                f"{'.'.join(str(part) for part in item.get('loc', []))}: {item.get('msg', 'Invalid value')}"
                for item in detail
                if isinstance(item, dict)
            )
        elif isinstance(detail, dict):
            detail = detail.get("message", str(detail))
    except ValueError:
        detail = response.text.strip() or response.reason_phrase
    raise RuntimeError(f"HTTP {response.status_code}: {detail}")


def api_get(path, params=None):
    try:
        r = request_with_auth(httpx.get, path, params=params)
        raise_for_api_error(r)
        return r.json(), None
    except Exception as e:
        return None, str(e)


def api_post(path, json_body=None, files=None, params=None):
    try:
        if files:
            r = request_with_auth(
                httpx.post,
                path,
                files=files,
                params=params,
                timeout=60,
            )
        else:
            r = request_with_auth(
                httpx.post,
                path,
                json=json_body,
                params=params,
                timeout=30,
            )
        raise_for_api_error(r)
        return r.json(), None
    except Exception as e:
        return None, str(e)


def api_delete(path):
    try:
        r = request_with_auth(httpx.delete, path, timeout=10)
        raise_for_api_error(r)
        return (r.json() if r.content else None), None
    except Exception as e:
        return None, str(e)


def normalize_study_ids(payload):
    if payload is None:
        return []
    if isinstance(payload, dict):
        items = payload.get("results") if isinstance(payload.get("results"), list) else []
    elif isinstance(payload, list):
        items = payload
    else:
        return []

    ids = []
    for item in items:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict):
            if "id" in item and item["id"]:
                ids.append(item["id"])
            elif "orthanc_study_id" in item and item["orthanc_study_id"]:
                ids.append(item["orthanc_study_id"])
    return ids


def set_status(msg, error=False):
    color = [220, 60, 60] if error else [60, 200, 100]
    if dpg.does_item_exist("status_bar"):
        dpg.set_value("status_bar", msg)
        dpg.configure_item("status_bar", color=color)

def log(msg):
    if dpg.does_item_exist("log_box"):
        ts = time.strftime("%H:%M:%S")
        current = dpg.get_value("log_box") or ""
        dpg.set_value("log_box", f"[{ts}] {msg}\n" + current)

# Health
def check_health():
    def _do():
        data, err = api_get("/health")
        if err:
            set_status(f"API unreachable: {err}", error=True)
            if dpg.does_item_exist("health_api"):
                dpg.set_value("health_api", "API: OFFLINE")
                dpg.configure_item("health_api", color=[220, 60, 60])
            return
        api_ok  = data.get("api") == "ok"
        pacs_ok = data.get("pacs_reachable", False)
        if dpg.does_item_exist("health_api"):
            dpg.set_value("health_api",  "API: OK"  if api_ok  else "API: OFFLINE")
            dpg.set_value("health_pacs", "PACS: OK" if pacs_ok else "PACS: OFFLINE")
            dpg.configure_item("health_api",  color=[60,200,100] if api_ok  else [220,60,60])
            dpg.configure_item("health_pacs", color=[60,200,100] if pacs_ok else [220,60,60])
    threading.Thread(target=_do, daemon=True).start()

# Studies
def refresh_studies():
    def _do():
        set_status("Fetching studies...")
        data, err = api_get("/studies")
        if err:
            set_status(f"Error: {err}", error=True)
            return
        state["studies"] = normalize_study_ids(data)
        _rebuild_studies_table()
        set_status(f"Loaded {len(state['studies'])} studies.")
        log(f"Loaded {len(state['studies'])} studies.")
    threading.Thread(target=_do, daemon=True).start()

def _rebuild_studies_table():
    if not dpg.does_item_exist("studies_table"):
        return
    dpg.delete_item("studies_table", children_only=True)
    for col in ["#", "Study ID", "Actions"]:
        dpg.add_table_column(label=col, parent="studies_table")
    for i, sid in enumerate(state["studies"]):
        short = sid[:22] + "..." if len(sid) > 22 else sid
        with dpg.table_row(parent="studies_table"):
            dpg.add_text(str(i + 1))
            dpg.add_text(short)
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Details", callback=lambda s, a, u: show_study_details(u),
                    user_data=sid, width=65,
                )
                dpg.add_button(
                    label="Ingest", callback=lambda s, a, u: ingest_study(u),
                    user_data=sid, width=55,
                )
                dpg.add_button(
                    label="Forward", callback=lambda s, a, u: open_forward_dialog(u),
                    user_data=sid, width=65,
                )

def show_study_details(study_id):
    if not study_id:
        set_status("Study ID is missing.", error=True)
        return
    def _do():
        data, err = api_get(f"/studies/{study_id}")
        if err:
            log(f"Study detail error: {err}")
            return
        tags    = data.get("MainDicomTags", {})
        patient = data.get("PatientMainDicomTags", {})
        text = (
            f"Study ID:     {study_id}\n"
            f"Patient:      {patient.get('PatientName','N/A')}\n"
            f"Patient ID:   {patient.get('PatientID','N/A')}\n"
            f"Modality:     {tags.get('Modality','N/A')}\n"
            f"Date:         {tags.get('StudyDate','N/A')}\n"
            f"Description:  {tags.get('StudyDescription','N/A')}\n"
            f"Series:       {len(data.get('Series', []))}\n"
            f"Instances:    {len(data.get('Instances', []))}\n"
        )
        if dpg.does_item_exist("study_detail_text"):
            dpg.set_value("study_detail_text", text)
    threading.Thread(target=_do, daemon=True).start()

def ingest_study(study_id):
    if not study_id:
        set_status("Study ID is missing.", error=True)
        return
    def _do():
        set_status(f"Ingesting {study_id[:16]}...")
        data, err = api_post(f"/query/ingest/{study_id}")
        if err:
            set_status(f"Ingest error: {err}", error=True)
            log(f"Ingest error: {err}")
        else:
            set_status("Study ingested.")
            log(f"Ingest job {data.get('job_id', '')} completed - DB id {data.get('id')}")
        refresh_jobs()
    threading.Thread(target=_do, daemon=True).start()

def ingest_all():
    def _do():
        set_status("Ingesting all studies...")
        data, err = api_post("/query/ingest/all")
        if err:
            set_status(f"Bulk ingest error: {err}", error=True)
        else:
            i = len(data.get("ingested",[])); s = len(data.get("skipped",[])); f = len(data.get("failed",[]))
            set_status(f"Done. Ingested:{i} Skipped:{s} Failed:{f}")
            log(f"Bulk ingest job {data.get('job_id', '')} - ingested:{i} skipped:{s} failed:{f}")
        refresh_jobs()
    threading.Thread(target=_do, daemon=True).start()

# Forward dialog
def open_forward_dialog(study_id):
    if not study_id:
        set_status("Study ID is missing.", error=True)
        return
    if dpg.does_item_exist("forward_dialog"):
        dpg.delete_item("forward_dialog")
    with dpg.window(label=f"Forward - {study_id[:18]}...", tag="forward_dialog",
                    modal=True, width=500, height=360, pos=[200,160]):
        dpg.add_text("Target PACS URL:")
        dpg.add_input_text(tag="fwd_pacs_url",  default_value="http://localhost:8042", width=440)
        dpg.add_text("User:"); dpg.add_input_text(tag="fwd_pacs_user", default_value="orthanc", width=440)
        dpg.add_text("Password:"); dpg.add_input_text(tag="fwd_pacs_pass", default_value="orthanc", password=True, width=440)
        dpg.add_checkbox(label="Anonymize", tag="fwd_anon")
        dpg.add_text("Examination result (optional):")
        dpg.add_input_text(tag="fwd_result", multiline=True, width=440, height=70)
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True):
            dpg.add_button(label="Forward", width=100, callback=lambda: _submit_forward(study_id))
            dpg.add_button(label="Cancel",  width=80,  callback=lambda: dpg.delete_item("forward_dialog"))

def _submit_forward(study_id):
    body = {
        "source_study_id":   study_id,
        "target_pacs_url":   dpg.get_value("fwd_pacs_url"),
        "target_pacs_user":  dpg.get_value("fwd_pacs_user"),
        "target_pacs_pass":  dpg.get_value("fwd_pacs_pass"),
        "anonymize":         dpg.get_value("fwd_anon"),
        "examination_result":dpg.get_value("fwd_result") or None,
    }
    if dpg.does_item_exist("forward_dialog"):
        dpg.delete_item("forward_dialog")
    def _do():
        data, err = api_post(f"/studies/{study_id}/forward", json_body=body)
        if err:
            set_status(f"Forward error: {err}", error=True)
            log(f"Forward error: {err}")
        else:
            set_status(f"Forward queued: {data.get('job_id','')[:16]}...")
            log(f"Forward job: {data.get('job_id')}")
    threading.Thread(target=_do, daemon=True).start()

# Jobs
def refresh_jobs():
    def _do():
        data, err = api_get("/jobs")
        if err:
            set_status(f"Jobs error: {err}", error=True)
            return
        state["jobs"] = (data or {}).get("results", [])
        _rebuild_jobs_table()
        set_status(f"Loaded {len(state['jobs'])} jobs.")
    threading.Thread(target=_do, daemon=True).start()


def create_demo_job():
    def _do():
        data, err = api_post("/jobs/demo", params={"duration_seconds": 30})
        if err:
            set_status(f"Demo job error: {err}", error=True)
            log(f"Demo job error: {err}")
            return
        set_status(f"30-second demo job queued: {data.get('job_id', '')[:16]}...")
        log(f"Queued 30-second demo job: {data.get('job_id')}")
        refresh_jobs()
    threading.Thread(target=_do, daemon=True).start()

def _rebuild_jobs_table():
    if not dpg.does_item_exist("jobs_table"):
        return
    dpg.delete_item("jobs_table", children_only=True)
    for col in ["Type", "Status", "Progress", "Created", "Actions"]:
        dpg.add_table_column(label=col, parent="jobs_table")
    for job in state["jobs"]:
        prog = job.get("progress", {})
        prog_str = f"{prog.get('done',0)}/{prog.get('total',0)}"
        created  = job.get("created_at","")[:19].replace("T"," ")
        status   = job.get("status","")
        color    = [60,200,100] if status=="completed" else [220,60,60] if "failed" in status else [220,180,60]
        with dpg.table_row(parent="jobs_table"):
            dpg.add_text(job.get("type","-"))
            dpg.add_text(status, color=color)
            dpg.add_text(prog_str)
            dpg.add_text(created)
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Details", callback=lambda s, a, u: _show_job_detail(u),
                    user_data=job, width=65,
                )

def _show_job_detail(job):
    if not dpg.does_item_exist("job_detail_text"):
        return
    errors    = job.get("errors",[])
    instances = job.get("instances",[])
    ok_count  = sum(1 for i in instances if i.get("ok") or i.get("status")=="ok")
    text = (
        f"Job ID:    {job.get('id','')}\n"
        f"Type:      {job.get('type','')}\n"
        f"Status:    {job.get('status','')}\n"
        f"Progress:  {job.get('progress',{}).get('done',0)}/{job.get('progress',{}).get('total',0)}\n"
        f"Instances: {len(instances)} total, {ok_count} ok\n"
        f"Errors:    {len(errors)}\n"
        f"Created:   {job.get('created_at','')[:19]}\n"
        f"Updated:   {job.get('updated_at','')[:19]}\n"
    )
    if errors:
        text += "\nErrors:\n"
        for e in errors[:5]:
            text += f"  - {json.dumps(e)}\n"
    dpg.set_value("job_detail_text", text)

# Search
def normalize_search_modality(value):
    modality = (value or "").strip()
    if modality.upper() in {"N/A", "NA", "NONE", "ALL", "-"}:
        return None
    return modality or None


def do_search():
    query    = dpg.get_value("search_query")
    modality = normalize_search_modality(dpg.get_value("search_modality"))
    if not query:
        set_status("Enter a search query.", error=True)
        return
    def _do():
        set_status("Searching...")
        params = {"q": query}
        if modality:
            params["modality"] = modality
        data, err = api_get("/query/search", params=params)
        if err:
            set_status(f"Search error: {err}", error=True)
            return
        state["search_results"] = (data or {}).get("results", [])
        _rebuild_search_table()
        set_status(f"Found {len(state['search_results'])} results.")
    threading.Thread(target=_do, daemon=True).start()

def _rebuild_search_table():
    if not dpg.does_item_exist("search_table"):
        return
    dpg.delete_item("search_table", children_only=True)
    for col in ["Modality","Date","Description","Comments","Instances"]:
        dpg.add_table_column(label=col, parent="search_table")
    for r in state["search_results"]:
        with dpg.table_row(parent="search_table"):
            dpg.add_text(r.get("modality") or "-")
            dpg.add_text(r.get("study_date") or "-")
            dpg.add_text((r.get("study_description") or "")[:40])
            dpg.add_text((r.get("image_comments")    or "")[:50])
            dpg.add_text(str(r.get("instance_count") or "-"))

# Administration
def refresh_admin():
    """Refresh all admin sections in parallel."""
    threading.Thread(target=_fetch_stats,   daemon=True).start()
    threading.Thread(target=_fetch_pacs,    daemon=True).start()
    threading.Thread(target=_fetch_audit,   daemon=True).start()
    threading.Thread(target=_fetch_workers, daemon=True).start()

def _fetch_stats():
    data, err = api_get("/admin/stats")
    if err:
        log(f"Stats error: {err}")
        return
    state["stats"] = data
    _render_stats(data)

def _render_stats(d):
    if not dpg.does_item_exist("stats_text"):
        return
    jobs  = d.get("jobs", {})
    orth  = d.get("orthanc", {})
    redis = d.get("redis", {})
    sr    = jobs.get("success_rate_pct")
    sr_str = f"{sr}%" if sr is not None else "N/A"
    text = (
        f"Jobs\n"
        f"  Total       : {jobs.get('total',0)}\n"
        f"  Completed   : {jobs.get('completed',0)}\n"
        f"  With errors : {jobs.get('completed_with_errors',0)}\n"
        f"  Failed      : {jobs.get('failed',0)}\n"
        f"  Queued      : {jobs.get('queued',0)}\n"
        f"  Processing  : {jobs.get('processing',0)}\n"
        f"  Success rate: {sr_str}\n"
        f"  Instances   : {jobs.get('total_instances',0)}\n"
        f"  Last 24h    : {jobs.get('last_24h',0)}\n\n"
        f"Orthanc\n"
        f"  Reachable   : {orth.get('reachable','?')}\n"
        f"  Version     : {orth.get('version','?')}\n"
        f"  Studies     : {orth.get('studies','?')}\n"
        f"  Instances   : {orth.get('instances','?')}\n\n"
        f"Redis\n"
        f"  Reachable   : {redis.get('reachable','?')}\n\n"
        f"PACS Configs\n"
        f"  Configured  : {d.get('pacs_configs',0)}\n"
    )
    dpg.set_value("stats_text", text)

def _fetch_pacs():
    data, err = api_get("/admin/pacs")
    if err:
        log(f"PACS list error: {err}")
        return
    state["pacs_configs"] = data or []
    _rebuild_pacs_table()

def _rebuild_pacs_table():
    if not dpg.does_item_exist("pacs_table"):
        return
    dpg.delete_item("pacs_table", children_only=True)
    for col in ["Name", "URL", "Actions"]:
        dpg.add_table_column(label=col, parent="pacs_table")
    for cfg in state["pacs_configs"]:
        with dpg.table_row(parent="pacs_table"):
            dpg.add_text(cfg.get("name",""))
            dpg.add_text(cfg.get("url",""))
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Test", width=50, user_data=cfg["id"],
                    callback=lambda s, a, u: _test_pacs(u),
                )
                dpg.add_button(
                    label="Delete", width=60, user_data=cfg["id"],
                    callback=lambda s, a, u: _delete_pacs(u),
                )

def _test_pacs(pacs_id):
    def _do():
        set_status("Testing PACS...")
        data, err = api_get(f"/admin/pacs/{pacs_id}/connectivity")
        if err:
            set_status(f"Test error: {err}", error=True)
            return
        if data.get("reachable"):
            msg = f"PACS OK - v{data.get('orthanc_version','?')} - {data.get('latency_ms','?')}ms"
            set_status(msg)
            log(msg)
        else:
            msg = f"PACS unreachable: {data.get('error', data.get('status_code','?'))}"
            set_status(msg, error=True)
            log(msg)
    threading.Thread(target=_do, daemon=True).start()

def _delete_pacs(pacs_id):
    def _do():
        _, err = api_delete(f"/admin/pacs/{pacs_id}")
        if err:
            set_status(f"Delete error: {err}", error=True)
        else:
            set_status("PACS config deleted.")
            log(f"Deleted PACS config {pacs_id}")
            _fetch_pacs()
    threading.Thread(target=_do, daemon=True).start()

def _open_add_pacs_dialog():
    if dpg.does_item_exist("add_pacs_dialog"):
        dpg.delete_item("add_pacs_dialog")
    with dpg.window(label="Add PACS Config", tag="add_pacs_dialog",
                    modal=True, width=440, height=280, pos=[220, 180]):
        dpg.add_text("Name:");     dpg.add_input_text(tag="new_pacs_name", width=400)
        dpg.add_text("URL:");      dpg.add_input_text(tag="new_pacs_url",  default_value="http://", width=400)
        dpg.add_text("Username:"); dpg.add_input_text(tag="new_pacs_user", default_value="orthanc", width=400)
        dpg.add_text("Password:"); dpg.add_input_text(tag="new_pacs_pass", default_value="orthanc", password=True, width=400)
        dpg.add_spacer(height=8)
        with dpg.group(horizontal=True):
            dpg.add_button(label="Add", width=80, callback=_submit_add_pacs)
            dpg.add_button(label="Cancel", width=80, callback=lambda: dpg.delete_item("add_pacs_dialog"))

def _submit_add_pacs():
    body = {
        "name":     dpg.get_value("new_pacs_name"),
        "url":      dpg.get_value("new_pacs_url"),
        "username": dpg.get_value("new_pacs_user"),
        "password": dpg.get_value("new_pacs_pass"),
    }
    if dpg.does_item_exist("add_pacs_dialog"):
        dpg.delete_item("add_pacs_dialog")
    def _do():
        data, err = api_post("/admin/pacs", json_body=body)
        if err:
            set_status(f"Add PACS error: {err}", error=True)
        else:
            set_status(f"PACS added: {body['name']}")
            log(f"Added PACS config: {body['name']} - {data.get('id')}")
            _fetch_pacs()
    threading.Thread(target=_do, daemon=True).start()

def _fetch_audit():
    data, err = api_get("/admin/audit?limit=30")
    if err:
        return
    state["audit"] = data or []
    _rebuild_audit_table()

def _rebuild_audit_table():
    if not dpg.does_item_exist("audit_table"):
        return
    dpg.delete_item("audit_table", children_only=True)
    for col in ["Type","Status","Instances","Errors","Anonymized","Created","Target PACS"]:
        dpg.add_table_column(label=col, parent="audit_table")
    for entry in state["audit"]:
        status = entry.get("status","")
        color  = [60,200,100] if status=="completed" else [220,60,60] if "failed" in status else [220,180,60]
        with dpg.table_row(parent="audit_table"):
            dpg.add_text(entry.get("type","-"))
            dpg.add_text(status, color=color)
            dpg.add_text(str(entry.get("instances",0)))
            dpg.add_text(str(entry.get("errors",0)))
            dpg.add_text("Da" if entry.get("anonymized") else "Nu")
            dpg.add_text(entry.get("created_at","")[:19].replace("T"," "))
            dpg.add_text((entry.get("target_pacs") or "-")[:30])

def _fetch_workers():
    data, err = api_get("/admin/workers")
    if err:
        return
    state["workers"] = data.get("workers", [])
    _render_workers(data)

def _render_workers(d):
    if not dpg.does_item_exist("workers_text"):
        return
    workers = d.get("workers", [])
    if not workers:
        note = d.get("note") or d.get("error") or "No workers online."
        dpg.set_value("workers_text", note)
        return
    text = f"Online workers: {d.get('total_online', len(workers))}\n\n"
    for w in workers:
        text += f"  {w['name']}\n"
        text += f"    Status       : {w['status']}\n"
        text += f"    Active tasks : {w['active_tasks']}\n"
        if w.get("tasks"):
            text += f"    Tasks        : {', '.join(w['tasks'])}\n"
        text += "\n"
    dpg.set_value("workers_text", text)

def _purge_jobs(status_filter):
    def _do():
        path = f"/admin/jobs?status={status_filter}" if status_filter else "/admin/jobs"
        data, err = api_delete(path)
        if err:
            set_status(f"Purge error: {err}", error=True)
        else:
            n = data.get("deleted_count", 0)
            set_status(f"Purged {n} jobs.")
            log(f"Purged {n} jobs (filter: {status_filter or 'all'})")
            refresh_jobs()
            _fetch_stats()
    threading.Thread(target=_do, daemon=True).start()


def _open_clear_database_dialog():
    if dpg.does_item_exist("clear_database_dialog"):
        dpg.delete_item("clear_database_dialog")
    with dpg.window(label="Clear PostgreSQL Data", tag="clear_database_dialog",
                    modal=True, width=460, height=170, pos=[300, 250]):
        dpg.add_text("This deletes all indexed study records from PostgreSQL.")
        dpg.add_text("PACS files and in-memory jobs are not affected.", color=[220, 180, 80])
        dpg.add_spacer(height=10)
        with dpg.group(horizontal=True):
            dpg.add_button(label="Delete PostgreSQL Data", width=180,
                           callback=_clear_database)
            dpg.add_button(label="Cancel", width=90,
                           callback=lambda: dpg.delete_item("clear_database_dialog"))


def _clear_database():
    dpg.delete_item("clear_database_dialog")

    def _do():
        data, err = api_delete("/admin/database")
        if err:
            set_status(f"Database clear error: {err}", error=True)
            log(f"Database clear error: {err}")
            return
        deleted_count = data.get("deleted_count", 0)
        set_status(f"Deleted {deleted_count} PostgreSQL study records.")
        log(f"Deleted {deleted_count} indexed study records from PostgreSQL.")
        refresh_admin()

    threading.Thread(target=_do, daemon=True).start()

# Auto-refresh
def _auto_refresh():
    while True:
        time.sleep(10)
        check_health()


GUI_PALETTE = {
    "background": [15, 21, 28, 255],
    "surface": [24, 33, 43, 255],
    "surface_alt": [30, 42, 53, 255],
    "teal": [38, 157, 148, 255],
    "teal_hover": [57, 187, 175, 255],
    "teal_active": [27, 119, 115, 255],
    "amber": [226, 169, 74, 255],
    "coral": [241, 126, 106, 255],
    "text": [232, 239, 241, 255],
    "muted": [149, 164, 175, 255],
}


def apply_theme():
    palette = GUI_PALETTE
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, palette["background"])
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, palette["surface"])
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, [24, 34, 49, 255])
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, palette["surface_alt"])
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, [42, 70, 75, 255])
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, [45, 91, 91, 255])
            dpg.add_theme_color(dpg.mvThemeCol_Button, palette["teal"])
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, palette["teal_hover"])
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, palette["teal_active"])
            dpg.add_theme_color(dpg.mvThemeCol_Header, [35, 74, 76, 255])
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, [49, 105, 101, 255])
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, [58, 124, 114, 255])
            dpg.add_theme_color(dpg.mvThemeCol_Tab, [29, 43, 51, 255])
            dpg.add_theme_color(dpg.mvThemeCol_TabActive, palette["teal"])
            dpg.add_theme_color(dpg.mvThemeCol_TabHovered, palette["teal_hover"])
            dpg.add_theme_color(dpg.mvThemeCol_TabUnfocused, [29, 43, 51, 255])
            dpg.add_theme_color(dpg.mvThemeCol_TabUnfocusedActive, [35, 84, 82, 255])
            dpg.add_theme_color(dpg.mvThemeCol_Border, [62, 82, 91, 255])
            dpg.add_theme_color(dpg.mvThemeCol_Text, palette["text"])
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, palette["muted"])
            dpg.add_theme_color(dpg.mvThemeCol_Separator, [65, 91, 96, 255])
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, [15, 22, 33, 255])
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, [74, 95, 119, 255])
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered, [90, 115, 141, 255])
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabActive, [112, 142, 176, 255])
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Text, [248, 252, 249, 255])
        with dpg.theme_component(dpg.mvTab):
            dpg.add_theme_color(dpg.mvThemeCol_Text, [241, 247, 246, 255])
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 10)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 10, 8)
            dpg.add_theme_style(dpg.mvStyleVar_ItemInnerSpacing, 8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 14, 12)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 10, 6)
            dpg.add_theme_style(dpg.mvStyleVar_IndentSpacing, 18)
    dpg.bind_theme(theme)


def apply_font():
    if not Path(UI_FONT).is_file():
        return
    with dpg.font_registry():
        default_font = dpg.add_font(UI_FONT, 16)
    dpg.bind_font(default_font)


# Main window
def main():
    dpg.create_context()
    apply_font()
    apply_theme()
    palette = GUI_PALETTE

    with dpg.window(tag="main_window", label="MSV-med - PACS Manager"):

        with dpg.group(horizontal=True):
            dpg.add_text("MSV-med PACS Manager", color=palette["teal_hover"])
            dpg.add_spacer(width=18)
            dpg.add_text("API: UNKNOWN", tag="health_api", color=[150, 150, 150])
            dpg.add_text("PACS: UNKNOWN", tag="health_pacs", color=[150, 150, 150])
            dpg.add_spacer(width=18)
            dpg.add_button(label="Check Health", callback=check_health, width=120, height=32)
        dpg.add_spacer(height=6)
        dpg.add_separator()

        with dpg.tab_bar():

            with dpg.tab(label="Studies"):
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Refresh", callback=refresh_studies, width=100)
                    dpg.add_button(label="Ingest All", callback=ingest_all, width=110)
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    with dpg.child_window(width=600, height=480, border=True):
                        dpg.add_text("Studies in PACS", color=[91, 211, 196])
                        dpg.add_separator()
                        with dpg.table(tag="studies_table", header_row=True,
                                       borders_innerH=True, borders_outerH=True,
                                       borders_outerV=True, scrollY=True, height=430,
                                       policy=dpg.mvTable_SizingFixedFit):
                            pass
                    dpg.add_spacer(width=8)
                    with dpg.child_window(width=430, height=480, border=True):
                        dpg.add_text("Study Details", color=[91, 211, 196])
                        dpg.add_separator()
                        dpg.add_input_text(tag="study_detail_text", multiline=True,
                                           readonly=True, width=410, height=450,
                                           default_value="Select a study.")

            with dpg.tab(label="Jobs"):
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Refresh", callback=refresh_jobs, width=100)
                    dpg.add_button(label="Run 30s Demo Job", callback=create_demo_job, width=150)
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    with dpg.child_window(width=600, height=480, border=True):
                        dpg.add_text("Job Queue", color=[241, 190, 95])
                        dpg.add_separator()
                        with dpg.table(tag="jobs_table", header_row=True,
                                       borders_innerH=True, borders_outerH=True,
                                       borders_outerV=True, scrollY=True, height=430,
                                       policy=dpg.mvTable_SizingFixedFit):
                            pass
                    dpg.add_spacer(width=8)
                    with dpg.child_window(width=430, height=480, border=True):
                        dpg.add_text("Job Details", color=[241, 190, 95])
                        dpg.add_separator()
                        dpg.add_input_text(tag="job_detail_text", multiline=True,
                                           readonly=True, width=410, height=450,
                                           default_value="Click Details on a job.")

            with dpg.tab(label="AI Search"):
                dpg.add_text("Semantic search over indexed studies", color=[241, 143, 121])
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_text("Query:")
                    dpg.add_input_text(tag="search_query", width=380, hint="CT torace cu noduli")
                    dpg.add_text("Modality:")
                    dpg.add_input_text(tag="search_modality", width=70, hint="CT")
                    dpg.add_button(label="Search", callback=do_search, width=80)
                dpg.add_spacer(height=8)
                with dpg.child_window(height=430, border=True):
                    with dpg.table(tag="search_table", header_row=True,
                                   borders_innerH=True, borders_outerH=True,
                                   borders_outerV=True, scrollY=True, height=420,
                                   policy=dpg.mvTable_SizingStretchProp):
                        pass

            with dpg.tab(label="Admin"):
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Refresh All", callback=refresh_admin, width=110)
                dpg.add_spacer(height=8)

                with dpg.tab_bar(tag="admin_tabs"):

                    with dpg.tab(label="Overview"):
                        with dpg.group(horizontal=True):
                            with dpg.child_window(width=340, height=420, border=True):
                                dpg.add_text("System Stats", color=[123, 198, 255])
                                dpg.add_separator()
                                dpg.add_input_text(tag="stats_text", multiline=True,
                                                   readonly=True, width=320, height=380,
                                                   default_value="Click Refresh All.")
                            dpg.add_spacer(width=8)
                            with dpg.child_window(width=680, height=420, border=True):
                                dpg.add_text("Celery Workers", color=[123, 198, 255])
                                dpg.add_separator()
                                dpg.add_input_text(tag="workers_text", multiline=True,
                                                   readonly=True, width=660, height=380,
                                                   default_value="Click Refresh All.")

                    with dpg.tab(label="PACS Config"):
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Add PACS", callback=_open_add_pacs_dialog, width=100)
                            dpg.add_button(label="Refresh",  callback=_fetch_pacs,            width=90)
                        dpg.add_spacer(height=6)
                        with dpg.child_window(height=400, border=True):
                            with dpg.table(tag="pacs_table", header_row=True,
                                           borders_innerH=True, borders_outerH=True,
                                           borders_outerV=True, scrollY=True, height=390,
                                           policy=dpg.mvTable_SizingFixedFit):
                                pass

                    with dpg.tab(label="Audit Log"):
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Refresh",             callback=_fetch_audit,                    width=90)
                            dpg.add_button(label="Purge failed",        callback=lambda: _purge_jobs("failed"),   width=110)
                            dpg.add_button(label="Purge completed",     callback=lambda: _purge_jobs("completed"),width=130)
                            dpg.add_button(label="Purge ALL",           callback=lambda: _purge_jobs(None),       width=90)
                            dpg.add_button(label="Clear PostgreSQL Data", callback=_open_clear_database_dialog,      width=170)
                        dpg.add_spacer(height=6)
                        with dpg.child_window(height=400, border=True):
                            with dpg.table(tag="audit_table", header_row=True,
                                           borders_innerH=True, borders_outerH=True,
                                           borders_outerV=True, scrollY=True, height=390,
                                           policy=dpg.mvTable_SizingStretchProp):
                                pass

            with dpg.tab(label="Activity Log"):
                dpg.add_text("Activity Log", color=[173, 217, 161])
                dpg.add_separator()
                dpg.add_input_text(tag="log_box", multiline=True, readonly=True,
                                   width=-1, height=490, default_value="Waiting...\n")
                dpg.add_button(label="Clear", callback=lambda: dpg.set_value("log_box",""), width=80)

        dpg.add_separator()
        dpg.add_text("Ready.", tag="status_bar", color=[88, 214, 138])

    dpg.create_viewport(title="MSV-med PACS Manager", width=1180, height=760, resizable=True)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("main_window", True)

    check_health()
    refresh_studies()
    refresh_admin()

    threading.Thread(target=_auto_refresh, daemon=True).start()

    dpg.start_dearpygui()
    dpg.destroy_context()

if __name__ == "__main__":
    main()
