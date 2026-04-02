from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from psycopg2.extras import execute_values
import psycopg2
import json
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv(override=True)

REQUIRED_KEYS = [
    "Case Number", "Service Center", "Case Type", "Case Status",
    "Title", "Application Filed Date", "Last Received Date",
]

app = FastAPI(title="Caseflow API — Architecture A (Monolithic)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["URL_TO_FRONTEND"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import psycopg2


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "casesdb"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )


def ensure_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.case_latest (
                case_number            TEXT PRIMARY KEY,
                service_center         TEXT,
                case_type              TEXT,
                case_status            TEXT,
                title                  TEXT,
                application_filed_date TIMESTAMP,
                last_received_date     TIMESTAMP,
                updated_at             TIMESTAMP DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.import_runs (
                id           SERIAL PRIMARY KEY,
                filename     TEXT,
                imported_at  TIMESTAMP DEFAULT NOW(),
                rows_read    INTEGER,
                rows_kept    INTEGER,
                rows_skipped INTEGER,
                total_in_db  INTEGER
            );
        """)
        conn.commit()
        
@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    try:
        conn = get_conn()
        conn.close()
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        return {"status": "error", "db": str(e)}
        
def parse_dt(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return None
        
# ── HTML pages ────────────────────────────────────────────────────────────────

UPLOAD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Caseflow – Import</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --ink: #111827; --muted: #6b7280; --sand: #f7f3ea;
    --teal: #0f766e; --teal-dark: #0b5f58;
    --card: #fffdf8; --line: rgba(17,24,39,0.12);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Space Grotesk', sans-serif;
    background: radial-gradient(1200px 800px at 10% -20%, #fff2c9 0%, transparent 60%),
                radial-gradient(900px 600px at 95% 0%, #cfeee8 0%, transparent 55%),
                var(--sand);
    min-height: 100vh; color: var(--ink);
  }
  header {
    background: rgba(255,253,248,0.9); border-bottom: 1px solid var(--line);
    padding: 0 32px; height: 62px; display: flex; align-items: center;
    justify-content: space-between; backdrop-filter: blur(8px);
  }
  .brand-name { font-size: 16px; font-weight: 600; }
  .logout {
    font-size: 12px; color: var(--teal); text-decoration: none;
    padding: 7px 14px; border: 1px solid rgba(15,118,110,0.3); border-radius: 999px;
    font-family: 'IBM Plex Mono', monospace; letter-spacing: 0.06em; transition: all 0.15s;
  }
  .logout:hover { border-color: var(--teal); color: var(--teal-dark); }
  .page { max-width: 720px; margin: 48px auto; padding: 0 24px; }
  h2 { font-size: 22px; font-weight: 600; margin-bottom: 6px; }
  .sub { font-size: 14px; color: var(--muted); margin-bottom: 28px; }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 18px;
    padding: 32px; box-shadow: 0 16px 28px rgba(17,24,39,0.12);
    animation: rise 0.45s ease-out; margin-bottom: 24px;
  }
  .card-title { font-size: 15px; font-weight: 600; margin-bottom: 16px; }
  .drop-zone {
    border: 2px dashed rgba(17,24,39,0.22); border-radius: 14px;
    padding: 52px 24px; text-align: center; cursor: pointer; transition: all 0.2s;
    background: linear-gradient(135deg, #fff7e8 0%, #fef3e2 45%, #e7f6f2 100%);
  }
  .drop-zone:hover, .drop-zone.drag-over {
    border-color: var(--teal); box-shadow: 0 12px 20px rgba(15,118,110,0.2);
    transform: translateY(-1px);
  }
  .upload-icon {
    width: 46px; height: 46px; background: rgba(15,118,110,0.12);
    border-radius: 12px; display: flex; align-items: center; justify-content: center;
    margin: 0 auto 16px;
  }
  .upload-icon svg { width: 22px; height: 22px; color: var(--teal); }
  .drop-zone h3 { font-size: 16px; font-weight: 600; margin-bottom: 6px; }
  .drop-zone p  { font-size: 13px; color: var(--muted); }
  .drop-zone p span { color: var(--teal); cursor: pointer; }
  #fileInput { display: none; }
  .selected-file {
    display: none; align-items: center; gap: 10px; margin-top: 16px;
    background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px;
  }
  .selected-file.visible { display: flex; }
  .file-name { font-size: 13px; font-weight: 600; flex: 1; }
  .file-size { font-size: 12px; color: var(--muted); }
  .remove { background: none; border: none; cursor: pointer; color: var(--muted); font-size: 18px; padding: 0 4px; }
  .remove:hover { color: #ef4444; }
  .import-btn {
    width: 100%; margin-top: 16px; padding: 12px;
    background: linear-gradient(135deg, var(--teal) 0%, #14b8a6 100%);
    color: white; border: none; border-radius: 12px;
    font-size: 14px; font-weight: 600; font-family: 'Space Grotesk', sans-serif;
    cursor: pointer; transition: transform 0.12s, box-shadow 0.2s;
    display: flex; align-items: center; justify-content: center; gap: 8px;
  }
  .import-btn:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 12px 20px rgba(15,118,110,0.25); }
  .import-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .result { display: none; margin-top: 20px; padding: 16px 20px; border-radius: 12px; font-size: 14px; }
  .result.success { display: block; background: #ecfdf5; border: 1px solid #a7f3d0; color: #065f46; }
  .result.error   { display: block; background: #fff1f2; border: 1px solid #fecdd3; color: #9f1239; }
  .result strong  { display: block; font-weight: 600; margin-bottom: 6px; }
  .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
  .stat { background: #fff; border: 1px solid rgba(15,118,110,0.15); border-radius: 10px; padding: 12px 14px; }
  .stat-val { font-size: 22px; font-weight: 700; }
  .stat-lbl { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .runs-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .runs-table th {
    text-align: left; padding: 8px 12px; font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted);
    border-bottom: 1px solid var(--line);
  }
  .runs-table td { padding: 10px 12px; border-bottom: 1px solid rgba(17,24,39,0.06); }
  .runs-table tr:last-child td { border-bottom: none; }
  .spinner {
    width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.3);
    border-top-color: white; border-radius: 50%;
    animation: spin 0.7s linear infinite; display: none;
  }
  .spinner.visible { display: block; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
  @media (max-width: 640px) { .stats { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header>
  <span class="brand-name">Caseflow</span>
  <a href="/logout" class="logout">Sign out</a>
</header>
<div class="page">
  <h2>Import Cases</h2>
  <p class="sub">Upload a JSON file to import cases into the database.</p>
  <div class="card">
    <div class="card-title">Upload JSON Dataset</div>
    <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
      <div class="upload-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round"
                d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"/>
        </svg>
      </div>
      <h3>Drop your JSON file here</h3>
      <p>or <span>browse to upload</span></p>
    </div>
    <input type="file" id="fileInput" accept=".json" onchange="handleFile(this.files[0])">
    <div class="selected-file" id="selectedFile">
      <span class="file-name" id="fileName">–</span>
      <span class="file-size" id="fileSize">–</span>
      <button class="remove" onclick="clearFile()">×</button>
    </div>
    <button class="import-btn" id="importBtn" disabled onclick="runImport()">
      <div class="spinner" id="spinner"></div>
      <span id="btnLabel">Import to Database</span>
    </button>
    <div class="result" id="result"></div>
  </div>
  <div class="card">
    <div class="card-title">Import History</div>
    <div id="runsContainer">
      <p style="font-size:13px;color:var(--muted)">Loading…</p>
    </div>
  </div>
</div>
<script>
  let selectedFile = null;

  function handleFile(f) {
    if (!f) return;
    selectedFile = f;
    document.getElementById('fileName').textContent = f.name;
    document.getElementById('fileSize').textContent = (f.size/1024).toFixed(1) + ' KB';
    document.getElementById('selectedFile').classList.add('visible');
    document.getElementById('importBtn').disabled = false;
    document.getElementById('result').className = 'result';
  }

  function clearFile() {
    selectedFile = null;
    document.getElementById('fileInput').value = '';
    document.getElementById('selectedFile').classList.remove('visible');
    document.getElementById('importBtn').disabled = true;
  }

  const dz = document.getElementById('dropZone');
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag-over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag-over'));
  dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('drag-over'); handleFile(e.dataTransfer.files[0]); });

  async function runImport() {
    if (!selectedFile) return;
    const btn = document.getElementById('importBtn');
    const spinner = document.getElementById('spinner');
    const label = document.getElementById('btnLabel');
    const result = document.getElementById('result');
    btn.disabled = true; spinner.classList.add('visible'); label.textContent = 'Importing…';
    result.className = 'result';
    const form = new FormData();
    form.append('file', selectedFile);
    try {
      const res = await fetch('/import-json', { method: 'POST', body: form });
      const d = await res.json();
      if (res.ok) {
        result.className = 'result success';
        result.innerHTML = `
          <strong>✓ Import successful</strong>
          <div class="stats">
            <div class="stat"><div class="stat-val">${d.rows_read.toLocaleString()}</div><div class="stat-lbl">Rows read</div></div>
            <div class="stat"><div class="stat-val">${d.rows_kept.toLocaleString()}</div><div class="stat-lbl">Rows imported</div></div>
            <div class="stat"><div class="stat-val">${d.rows_skipped.toLocaleString()}</div><div class="stat-lbl">Rows skipped</div></div>
            <div class="stat"><div class="stat-val">${d.total_in_db.toLocaleString()}</div><div class="stat-lbl">Total in database</div></div>
          </div>`;
        loadRuns();
      } else {
        result.className = 'result error';
        result.innerHTML = `<strong>Import failed</strong>${d.detail || 'Unknown error'}`;
      }
    } catch(e) {
      result.className = 'result error';
      result.innerHTML = `<strong>Error</strong>Could not reach the server.`;
    } finally {
      btn.disabled = false; spinner.classList.remove('visible'); label.textContent = 'Import to Database';
    }
  }

  async function loadRuns() {
    try {
      const res = await fetch('/api/import-runs');
      const d = await res.json();
      const el = document.getElementById('runsContainer');
      if (!d.runs.length) { el.innerHTML = '<p style="font-size:13px;color:var(--muted)">No imports yet.</p>'; return; }
      el.innerHTML = `
        <table class="runs-table">
          <thead><tr>
            <th>#</th><th>Filename</th><th>Imported At</th>
            <th>Read</th><th>Kept</th><th>Skipped</th><th>Total in DB</th>
          </tr></thead>
          <tbody>${d.runs.map(r => `
            <tr>
              <td>${r.id}</td><td>${r.filename}</td>
              <td>${new Date(r.imported_at).toLocaleString()}</td>
              <td>${r.rows_read}</td><td>${r.rows_kept}</td>
              <td>${r.rows_skipped}</td><td>${r.total_in_db}</td>
            </tr>`).join('')}
          </tbody>
        </table>`;
    } catch {}
  }

  loadRuns();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url="/upload")


@app.get("/upload", response_class=HTMLResponse)
def upload_page():
    return HTMLResponse(UPLOAD_HTML)


@app.post("/import-json")
async def import_json(file: UploadFile = File(...)):
    content  = await file.read()
    filename = file.filename or "upload.json"

    # Parse JSON
    try:
        records = json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    if not isinstance(records, list):
        raise HTTPException(status_code=400, detail="JSON must be an array of objects")

    # Validate and normalise
    rows_read = len(records)
    cleaned   = {}
    for rec in records:
        missing = [k for k in REQUIRED_KEYS if k not in rec]
        if missing:
            raise HTTPException(status_code=400, detail=f"Missing keys: {missing}")

        last_received = parse_dt(rec.get("Last Received Date"))
        case_number   = str(rec.get("Case Number", "")).strip()

        if not case_number or last_received is None:
            continue

        row = (
            case_number,
            str(rec.get("Service Center", "")).strip(),
            str(rec.get("Case Type", "")).strip(),
            str(rec.get("Case Status", "")).strip().lower(),
            str(rec.get("Title", "")).strip(),
            parse_dt(rec.get("Application Filed Date")),
            last_received,
            datetime.utcnow(),
        )

        # Deduplicate: keep latest last_received_date per case number
        if case_number not in cleaned or last_received > cleaned[case_number][6]:
            cleaned[case_number] = row

    rows      = list(cleaned.values())
    rows_kept = len(rows)

    # DB operations
    try:
        conn = get_conn()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database connection failed: {e}")

    try:
        ensure_tables(conn)

        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO public.case_latest
                    (case_number, service_center, case_type, case_status,
                     title, application_filed_date, last_received_date, updated_at)
                VALUES %s
                ON CONFLICT (case_number) DO UPDATE SET
                    service_center         = EXCLUDED.service_center,
                    case_type              = EXCLUDED.case_type,
                    case_status            = EXCLUDED.case_status,
                    title                  = EXCLUDED.title,
                    application_filed_date = EXCLUDED.application_filed_date,
                    last_received_date     = EXCLUDED.last_received_date,
                    updated_at             = EXCLUDED.updated_at;
            """, rows)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM public.case_latest")
            total_in_db = cur.fetchone()[0]

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.import_runs
                    (filename, rows_read, rows_kept, rows_skipped, total_in_db)
                VALUES (%s, %s, %s, %s, %s)
            """, (filename, rows_read, rows_kept, rows_read - rows_kept, total_in_db))
        conn.commit()

        return {
            "status":       "success",
            "filename":     filename,
            "rows_read":    rows_read,
            "rows_kept":    rows_kept,
            "rows_skipped": rows_read - rows_kept,
            "total_in_db":  total_in_db,
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        conn.close()
        
# ── Dashboard API ─────────────────────────────────────────────────────────────

@app.get("/api/stats")
def api_stats():
    try:
        conn = get_conn()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM public.case_latest")
            total = cur.fetchone()[0]

            cur.execute("""
                SELECT case_status FROM public.case_latest
                GROUP BY case_status ORDER BY COUNT(*) DESC LIMIT 1
            """)
            row = cur.fetchone()
            top_status = row[0] if row else "N/A"

            cur.execute("SELECT MAX(updated_at) FROM public.case_latest")
            last = cur.fetchone()[0]

            cur.execute("""
                SELECT AVG(EXTRACT(EPOCH FROM (last_received_date - application_filed_date)) / 86400)
                FROM public.case_latest
                WHERE application_filed_date IS NOT NULL
                AND last_received_date IS NOT NULL
            """)
            avg = cur.fetchone()[0]

        return {
            "total_cases":        total,
            "top_status":         top_status,
            "last_import":        last.isoformat() if last else None,
            "avg_processing_days": round(float(avg), 1) if avg else None,
        }
    finally:
        conn.close()


@app.get("/api/charts/by-month")
def api_by_month():
    try:
        conn = get_conn()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT TO_CHAR(application_filed_date, 'YYYY-MM') AS month,
                       case_status, COUNT(*) AS cnt
                FROM public.case_latest
                WHERE application_filed_date IS NOT NULL
                GROUP BY month, case_status
                ORDER BY month ASC, cnt DESC
            """)
            rows = cur.fetchall()
        data = {}
        for month, status, cnt in rows:
            data.setdefault(month, {})[status] = cnt
        return {"data": data}
    finally:
        conn.close()


@app.get("/api/charts/by-day")
def api_by_day(month: str):
    try:
        conn = get_conn()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT TO_CHAR(application_filed_date, 'YYYY-MM-DD') AS day,
                       case_status, COUNT(*) AS cnt
                FROM public.case_latest
                WHERE TO_CHAR(application_filed_date, 'YYYY-MM') = %s
                GROUP BY day, case_status
                ORDER BY day ASC
            """, (month,))
            rows = cur.fetchall()
        data = {}
        for day, status, cnt in rows:
            data.setdefault(day, {})[status] = cnt
        return {"month": month, "data": data}
    finally:
        conn.close()


@app.get("/api/charts/pie-month")
def api_pie_month(month: str):
    try:
        conn = get_conn()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT case_status, COUNT(*) AS cnt
                FROM public.case_latest
                WHERE TO_CHAR(application_filed_date, 'YYYY-MM') = %s
                GROUP BY case_status ORDER BY cnt DESC
            """, (month,))
            rows = cur.fetchall()
        return {"month": month, "data": [{"status": r[0], "count": r[1]} for r in rows]}
    finally:
        conn.close()


@app.get("/api/charts/pie-day-filed")
def api_pie_day_filed(date: str):
    try:
        conn = get_conn()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT case_status, COUNT(*) AS cnt
                FROM public.case_latest
                WHERE DATE(application_filed_date) = %s
                GROUP BY case_status ORDER BY cnt DESC
            """, (date,))
            rows = cur.fetchall()
        return {"date": date, "data": [{"status": r[0], "count": r[1]} for r in rows]}
    finally:
        conn.close()


@app.get("/api/search")
def api_search(case_number: str = None, date: str = None):
    if not case_number and not date:
        raise HTTPException(status_code=400, detail="Provide case_number or date")
    try:
        conn = get_conn()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    try:
        with conn.cursor() as cur:
            if case_number:
                cur.execute("""
                    SELECT case_number, service_center, case_type, case_status,
                           title, application_filed_date, last_received_date, updated_at
                    FROM public.case_latest
                    WHERE UPPER(case_number) = UPPER(%s)
                """, (case_number.strip(),))
            else:
                cur.execute("""
                    SELECT case_number, service_center, case_type, case_status,
                           title, application_filed_date, last_received_date, updated_at
                    FROM public.case_latest
                    WHERE DATE(application_filed_date) = %s
                    ORDER BY application_filed_date DESC
                """, (date,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        for row in rows:
            for k, v in row.items():
                if hasattr(v, 'isoformat'):
                    row[k] = v.isoformat()

        return {"results": rows, "count": len(rows)}
    finally:
        conn.close()


@app.get("/api/import-runs")
def api_import_runs():
    try:
        conn = get_conn()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, filename, imported_at, rows_read, rows_kept,
                       rows_skipped, total_in_db
                FROM public.import_runs
                ORDER BY imported_at DESC
                LIMIT 50
            """)
            cols = [d[0] for d in cur.description]
            runs = [dict(zip(cols, r)) for r in cur.fetchall()]
        for run in runs:
            if run.get("imported_at"):
                run["imported_at"] = run["imported_at"].isoformat()
        return {"runs": runs, "count": len(runs)}
    finally:
        conn.close()