
import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date, time, datetime, timedelta
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st
from streamlit_calendar import calendar


DB_NAME = os.environ.get("NOTARY_DB_PATH", "notary_assistant.db")
APP_VERSION = "3.4.0"
PORTAL_TOKEN_DAYS = 7  # Portal links expire after this many days


def _make_portal_token(client_id, day_bucket=None):
    """Generate a token valid for the current 7-day window."""
    import base64, hashlib, math
    try:
        secret = st.secrets.get("SUPABASE_KEY", "notary")[:16]
    except Exception:
        secret = "notary_default_key"
    if day_bucket is None:
        day_bucket = math.floor(datetime.now().timestamp() / 86400 / PORTAL_TOKEN_DAYS)
    payload = f"{client_id}:{secret}:{day_bucket}"
    return base64.urlsafe_b64encode(
        hashlib.sha256(payload.encode()).digest()[:12]
    ).decode().rstrip("=")


def _verify_portal_token(client_id, token):
    """Accept tokens from current or previous window (grace period)."""
    import math
    try:
        day_bucket = math.floor(datetime.now().timestamp() / 86400 / PORTAL_TOKEN_DAYS)
        for bucket in [day_bucket, day_bucket - 1]:
            if token == _make_portal_token(client_id, bucket):
                return True
        return False
    except Exception:
        return False
_SAFE_EXPORT_TABLES = frozenset(
    ["clients", "appointments", "payments", "checklist", "attachments", "followups", "settings"]
)

# ── Supabase connection ───────────────────────────────────────────────────────
def _get_supabase():
    """Return a Supabase client if credentials are available, else None."""
    try:
        from supabase import create_client
        try:
            url = st.secrets.get("SUPABASE_URL", "")
            key = st.secrets.get("SUPABASE_KEY", "")
        except Exception:
            return None
        if url and key:
            return create_client(url, key)
    except Exception:
        pass
    return None


try:
    @st.cache_resource
    def _supabase_client():
        """Cached Supabase client — created once per session."""
        return _get_supabase()
except Exception:
    def _supabase_client():
        return None


def using_supabase():
    """True if a working Supabase client is available."""
    return _supabase_client() is not None


def sb():
    """Shorthand for the cached Supabase client."""
    return _supabase_client()

UPLOAD_FOLDER = "uploads"
BACKUP_FOLDER = "backups"

SIGNING_TYPES = [
    "General Notary", "Loan Signing", "Refinance", "Purchase", "Seller Package",
    "HELOC", "Power of Attorney", "I-9 Verification", "Fingerprinting", "Other"
]

STATUSES = ["Scheduled", "Completed", "Canceled", "No Show", "Awaiting Payment", "Paid"]

# Default fees per signing type — editable in Settings / Business Profile
DEFAULT_SIGNING_TYPE_FEES = {
    "General Notary": 25.0,
    "Loan Signing": 125.0,
    "Refinance": 150.0,
    "Purchase": 150.0,
    "Seller Package": 100.0,
    "HELOC": 125.0,
    "Power of Attorney": 50.0,
    "I-9 Verification": 50.0,
    "Fingerprinting": 25.0,
    "Other": 0.0,
}

REFERRAL_SOURCES = [
    "Google Search",
    "Website",
    "Referral",
    "Facebook",
    "LinkedIn",
    "Yelp",
    "Notary Rotary",
    "Snapdocs",
    "SigningOrder",
    "123Notary",
    "Business Card",
    "Repeat Client",
    "Other"
]

PAYMENT_METHODS = ["Cash", "Check", "Credit/Debit", "Zelle", "Cash App", "Venmo", "PayPal", "ACH", "Other"]

CHECKLIST_ITEMS = [
    "Appointment confirmed",
    "ID requirement explained",
    "Documents printed",
    "Travel route checked",
    "Signing completed",
    "Scanbacks required",
    "Scanbacks sent",
    "Invoice sent",
    "Payment received",
    "Review requested"
]




def clear_all_caches():
    """Force refresh all cached data from Supabase/SQLite."""
    get_all_appointments_dataframe.clear()
    get_clients.clear()
    get_all_payment_totals.clear()
    get_settings.clear()

def get_signing_type_fees(settings):
    """Return dict of {signing_type: fee} from settings, falling back to defaults."""
    raw = settings.get("signing_type_fees") or ""
    if raw:
        try:
            stored = json.loads(raw)
            # Merge with defaults so new types always have a value
            merged = dict(DEFAULT_SIGNING_TYPE_FEES)
            merged.update(stored)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_SIGNING_TYPE_FEES)


def upload_to_supabase_storage(file_bytes, file_name, appointment_id):
    """Upload a file to Supabase Storage bucket 'attachments'.
    Returns the public URL on success, None on failure.
    """
    if not using_supabase():
        return None
    try:
        path = f"{appointment_id}/{file_name}"
        sb().storage.from_("attachments").upload(
            path, file_bytes,
            {"content-type": "application/octet-stream", "x-upsert": "true"}
        )
        url = sb().storage.from_("attachments").get_public_url(path)
        return url
    except Exception:
        return None


def get_supabase_attachment_url(file_path):
    """Return a fresh signed URL for a Supabase Storage file (1 hour expiry)."""
    if not using_supabase() or not file_path or not file_path.startswith("supabase://"):
        return None
    try:
        path = file_path.replace("supabase://attachments/", "")
        result = sb().storage.from_("attachments").create_signed_url(path, 3600)
        return result.get("signedURL") or result.get("signedUrl")
    except Exception:
        return None

def ensure_folders():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(BACKUP_FOLDER, exist_ok=True)


def responsive_columns(n_desktop, n_tablet=2, n_mobile=1):
    """Return st.columns() with a count appropriate for the screen.
    Streamlit can't detect screen width server-side, so we use CSS to collapse
    columns visually — this helper also applies a data attribute for targeting.
    Always returns n_desktop columns; the CSS media queries handle the collapse.
    """
    return st.columns(n_desktop)


@contextmanager
def db_conn():
    """Context manager for SQLite connections.
    Guarantees conn.close() even if an exception is raised mid-function.
    Usage:
        with db_conn() as conn:
            cursor = conn.cursor()
            ...
    """
    conn = sqlite3.connect(DB_NAME)
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def connect_db():
    """Legacy helper kept for any direct call sites not yet migrated."""
    return sqlite3.connect(DB_NAME)


def create_tables():
    with db_conn() as conn:
        cursor = conn.cursor()

        # Settings table first, then migrations, then default row.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                business_name TEXT,
                business_phone TEXT,
                business_email TEXT,
                business_website TEXT,
                business_tagline TEXT,
                default_fee REAL,
                default_mileage_rate REAL,
                default_travel_buffer INTEGER,
                logo_path TEXT
            )
        """)

        cursor.execute("PRAGMA table_info(settings)")
        settings_columns = [col[1] for col in cursor.fetchall()]

        settings_needed = {
            "business_website": "TEXT",
            "business_tagline": "TEXT",
            "default_fee": "REAL",
            "default_mileage_rate": "REAL",
            "default_travel_buffer": "INTEGER",
            "logo_path": "TEXT",
            "auth_enabled": "INTEGER",
            "app_password": "TEXT",
            "cloud_database_url": "TEXT",
            "signing_type_fees": "TEXT",
            "revenue_goal": "REAL",
            "supabase_url": "TEXT",
            "supabase_key": "TEXT",
            "smtp_host": "TEXT",
            "smtp_port": "INTEGER",
            "smtp_user": "TEXT",
            "smtp_password": "TEXT",
            "smtp_from_name": "TEXT",
            "gcal_enabled": "INTEGER",
            "notification_email": "TEXT",
        }

        for column, column_type in settings_needed.items():
            if column not in settings_columns:
                cursor.execute(f"ALTER TABLE settings ADD COLUMN {column} {column_type}")

        cursor.execute("""
            INSERT OR IGNORE INTO settings (
                id, business_name, business_phone, business_email,
                business_website, business_tagline, default_fee,
                default_mileage_rate, default_travel_buffer, logo_path,
                auth_enabled, app_password, cloud_database_url
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            1, "Sean M. Keyser, Notary Signing Agent", "", "",
            "https://keysernotaryfl.com", "Sign with confidence. Seal with trust.",
            0, 0.67, 30, "", 0, "", ""
        ))

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                phone TEXT, email TEXT, address TEXT,
                notes TEXT, referral_source TEXT
            )
        """)

        cursor.execute("PRAGMA table_info(clients)")
        client_columns = [col[1] for col in cursor.fetchall()]
        if "referral_source" not in client_columns:
            cursor.execute("ALTER TABLE clients ADD COLUMN referral_source TEXT")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER, client_name TEXT, client_phone TEXT, client_email TEXT,
                appointment_date TEXT, appointment_time TEXT,
                duration_minutes INTEGER, end_time TEXT,
                signing_type TEXT, location TEXT,
                fee REAL, mileage REAL, status TEXT, notes TEXT,
                invoice_date TEXT, payment_due_date TEXT,
                client_notes TEXT, internal_notes TEXT,
                FOREIGN KEY (client_id) REFERENCES clients(id)
            )
        """)

        cursor.execute("PRAGMA table_info(appointments)")
        columns = [col[1] for col in cursor.fetchall()]
        needed_columns = {
            "client_id": "INTEGER", "appointment_time": "TEXT",
            "duration_minutes": "INTEGER", "end_time": "TEXT",
            "invoice_date": "TEXT", "payment_due_date": "TEXT",
            "client_notes": "TEXT", "internal_notes": "TEXT"
        }
        for column, column_type in needed_columns.items():
            if column not in columns:
                cursor.execute(f"ALTER TABLE appointments ADD COLUMN {column} {column_type}")

        for ddl in [
            """CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER, payment_date TEXT,
                amount_paid REAL, payment_method TEXT, notes TEXT,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id))""",
            """CREATE TABLE IF NOT EXISTS checklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER, item_name TEXT,
                completed INTEGER DEFAULT 0,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id))""",
            """CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER, file_name TEXT, file_path TEXT,
                uploaded_at TEXT, notes TEXT,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id))""",
            """CREATE TABLE IF NOT EXISTS followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER, followup_date TEXT, followup_type TEXT,
                outcome TEXT, notes TEXT, completed INTEGER DEFAULT 0,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id))""",
            """CREATE TABLE IF NOT EXISTS status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER,
                old_status TEXT,
                new_status TEXT,
                changed_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT,
                signing_type TEXT,
                fee REAL,
                created_date TEXT,
                status TEXT DEFAULT 'Sent',
                notes TEXT,
                quote_text TEXT)""",
            """CREATE TABLE IF NOT EXISTS client_comms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                client_name TEXT,
                comm_date TEXT,
                comm_type TEXT,
                direction TEXT,
                subject TEXT,
                notes TEXT)""",
            """CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expense_date TEXT,
                category TEXT,
                description TEXT,
                amount REAL,
                receipt_path TEXT,
                notes TEXT)""",
            """CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_name TEXT NOT NULL,
                signing_type TEXT,
                location TEXT,
                fee REAL,
                mileage REAL,
                duration_minutes INTEGER,
                notes TEXT,
                client_notes TEXT,
                internal_notes TEXT,
                created_at TEXT)""",
        ]:
            cursor.execute(ddl)

        conn.commit()


def create_supabase_tables():
    """Create all tables in Supabase using the REST API (run once on first deploy).
    Uses raw SQL via the Supabase SQL editor equivalent — calls rpc if available,
    otherwise provides the SQL for manual execution.
    """
    client = sb()
    if not client:
        return False, "No Supabase connection"

    # Test connection with a simple query
    try:
        client.table("settings").select("id").limit(1).execute()
        return True, "Tables already exist"
    except Exception:
        pass

    return False, "Tables need to be created — see Cloud Database Setup page for SQL"


def _clean_for_supabase(df):
    """Replace NaN/inf with None and convert float IDs (1.0, 2.0) to int
    so Supabase bigint columns don't reject them.
    """
    import math

    # Integer columns that Supabase expects as bigint
    int_cols = {"id", "client_id", "appointment_id", "duration_minutes",
                "smtp_port", "auth_enabled", "gcal_enabled", "completed"}

    df = df.where(pd.notnull(df), None)
    records = df.to_dict("records")
    cleaned = []
    for row in records:
        clean_row = {}
        for k, v in row.items():
            if v is None:
                clean_row[k] = None
            elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                clean_row[k] = None
            elif isinstance(v, float) and k in int_cols and v == int(v):
                clean_row[k] = int(v)
            elif isinstance(v, float) and v == int(v) and k.endswith("_id") or k == "id":
                clean_row[k] = int(v)
            else:
                clean_row[k] = v
        cleaned.append(clean_row)
    return cleaned


def migrate_sqlite_to_supabase():
    """One-time migration: copy all SQLite data into Supabase.
    Safe to run multiple times — uses upsert so existing records aren't duplicated.
    """
    client = sb()
    if not client:
        return False, "No Supabase connection"

    results = []

    try:
        # Settings
        with db_conn() as conn:
            settings_df = pd.read_sql_query("SELECT * FROM settings", conn)
        if not settings_df.empty:
            client.table("settings").upsert(_clean_for_supabase(settings_df)).execute()
            results.append(f"✅ Settings: {len(settings_df)} row(s)")

        # Clients FIRST — appointments depend on client_id
        with db_conn() as conn:
            clients_df = pd.read_sql_query("SELECT * FROM clients", conn)
        if not clients_df.empty:
            client.table("clients").upsert(_clean_for_supabase(clients_df)).execute()
            results.append(f"✅ Clients: {len(clients_df)} row(s)")

        # Appointments — strip client_id FK to avoid constraint errors,
        # Supabase will still store it but won't enforce the FK since we dropped it in SQL
        with db_conn() as conn:
            appts_df = pd.read_sql_query("SELECT * FROM appointments", conn)
        if not appts_df.empty:
            # Get valid client IDs from Supabase
            valid_clients = client.table("clients").select("id").execute()
            valid_ids = {r["id"] for r in valid_clients.data}
            # Null out any client_id not present in Supabase clients table
            appts_df["client_id"] = appts_df["client_id"].apply(
                lambda x: int(x) if pd.notna(x) and int(x) in valid_ids else None
            )
            client.table("appointments").upsert(_clean_for_supabase(appts_df)).execute()
            results.append(f"✅ Appointments: {len(appts_df)} row(s)")

        # Payments
        with db_conn() as conn:
            payments_df = pd.read_sql_query("SELECT * FROM payments", conn)
        if not payments_df.empty:
            client.table("payments").upsert(_clean_for_supabase(payments_df)).execute()
            results.append(f"✅ Payments: {len(payments_df)} row(s)")

        # Checklist
        with db_conn() as conn:
            checklist_df = pd.read_sql_query("SELECT * FROM checklist", conn)
        if not checklist_df.empty:
            client.table("checklist").upsert(_clean_for_supabase(checklist_df)).execute()
            results.append(f"✅ Checklist: {len(checklist_df)} row(s)")

        # Followups
        with db_conn() as conn:
            followups_df = pd.read_sql_query("SELECT * FROM followups", conn)
        if not followups_df.empty:
            client.table("followups").upsert(_clean_for_supabase(followups_df)).execute()
            results.append(f"✅ Follow-ups: {len(followups_df)} row(s)")

        # Templates
        with db_conn() as conn:
            templates_df = pd.read_sql_query("SELECT * FROM templates", conn)
        if not templates_df.empty:
            client.table("templates").upsert(_clean_for_supabase(templates_df)).execute()
            results.append(f"✅ Templates: {len(templates_df)} row(s)")

        return True, "\n".join(results)

    except Exception as e:
        return False, f"Migration error: {e}"


@st.cache_data(ttl=60)
def get_settings():
    if using_supabase():
        try:
            resp = sb().table("settings").select("*").eq("id", 1).execute()
            if resp.data:
                return resp.data[0]
        except Exception:
            pass
    with db_conn() as conn:
        row = pd.read_sql_query("SELECT * FROM settings WHERE id = 1", conn)
    if row.empty:
        return {
            "business_name": "Sean M. Keyser, Notary Signing Agent",
            "business_phone": "",
            "business_email": "",
            "business_website": "https://keysernotaryfl.com",
            "business_tagline": "Sign with confidence. Seal with trust.",
            "default_fee": 0,
            "default_mileage_rate": 0.67,
            "default_travel_buffer": 30,
            "logo_path": "",
            "auth_enabled": 0,
            "app_password": "",
            "cloud_database_url": "",
            "signing_type_fees": "",
            "revenue_goal": 0.0,
            "supabase_url": "",
            "supabase_key": "",
            "smtp_host": "",
            "smtp_port": 587,
            "smtp_user": "",
            "smtp_password": "",
            "smtp_from_name": "",
            "gcal_enabled": 0,
            "notification_email": "",
        }

    return row.iloc[0].to_dict()


def update_settings(settings):
    if using_supabase():
        try:
            sb().table("settings").upsert({**settings, "id": 1}).execute()
            get_settings.clear()
            return
        except Exception:
            pass
    with db_conn() as conn:
        conn.execute("""
            UPDATE settings
            SET business_name = ?,
                business_phone = ?,
                business_email = ?,
                business_website = ?,
                business_tagline = ?,
                default_fee = ?,
                default_mileage_rate = ?,
                default_travel_buffer = ?,
                logo_path = ?,
                auth_enabled = ?,
                app_password = ?,
                cloud_database_url = ?,
                signing_type_fees = ?,
                revenue_goal = ?,
                supabase_url = ?,
                supabase_key = ?
            WHERE id = 1
        """, (
            settings["business_name"],
            settings["business_phone"],
            settings["business_email"],
            settings["business_website"],
            settings["business_tagline"],
            settings["default_fee"],
            settings["default_mileage_rate"],
            settings["default_travel_buffer"],
            settings["logo_path"],
            int(bool(settings.get("auth_enabled", 0))),
            settings.get("app_password", ""),
            settings.get("cloud_database_url", ""),
            settings.get("signing_type_fees", ""),
            float(settings.get("revenue_goal") or 0),
            settings.get("supabase_url", ""),
            settings.get("supabase_key", ""),
        ))
        conn.commit()
    get_settings.clear()



def send_email(to_email, subject, body_text, pdf_bytes=None, pdf_filename=None, settings=None):
    """Send an email via SMTP. Returns (success, error_message).
    Automatically uses SSL (SMTP_SSL) for port 465, TLS (STARTTLS) for all others.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    if settings is None:
        settings = {}

    host = settings.get("smtp_host", "")
    port = int(settings.get("smtp_port") or 587)
    user = settings.get("smtp_user", "")
    password = settings.get("smtp_password", "")
    from_name = settings.get("smtp_from_name") or settings.get("business_name", "Notary Assistant")
    use_ssl = port == 465  # SSL for 465, STARTTLS for 587 and others

    if not host or not user or not password:
        return False, "SMTP not configured. Add SMTP settings in Settings / Business Profile."

    try:
        msg = MIMEMultipart()
        msg["From"] = f"{from_name} <{user}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body_text, "plain"))

        if pdf_bytes and pdf_filename:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(pdf_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={pdf_filename}")
            msg.attach(part)

        if use_ssl:
            # Port 465 — SSL from the start
            with smtplib.SMTP_SSL(host, port) as server:
                server.login(user, password)
                server.sendmail(user, to_email, msg.as_string())
        else:
            # Port 587 (or other) — STARTTLS
            with smtplib.SMTP(host, port) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(user, password)
                server.sendmail(user, to_email, msg.as_string())

        return True, None
    except Exception as e:
        return False, str(e)


def send_daily_digest(settings):
    """Send a daily digest email with tomorrow's appointments and overdue invoices."""
    notify_email = settings.get("notification_email", "")
    if not notify_email:
        return False, "No notification email set in Settings."

    df = get_all_appointments_dataframe()
    if df.empty:
        return False, "No appointments to report."

    df["appointment_date"] = pd.to_datetime(df["appointment_date"], errors="coerce")
    tomorrow = pd.Timestamp(date.today() + timedelta(days=1))
    tomorrow_appts = df[df["appointment_date"].dt.date == tomorrow.date()].sort_values("appointment_time")

    invoice_df = get_invoice_status_dataframe(df)
    overdue = invoice_df[invoice_df["invoice_status"] == "Overdue"] if not invoice_df.empty else pd.DataFrame()

    lines = [
        f"Good morning! Here's your notary day ahead — {tomorrow.strftime('%A, %B %d, %Y')}",
        "",
        f"TOMORROW'S APPOINTMENTS ({len(tomorrow_appts)})",
        "=" * 45,
    ]
    if tomorrow_appts.empty:
        lines.append("No appointments scheduled.")
    else:
        for _, r in tomorrow_appts.iterrows():
            lines.append(f"• {r['appointment_time']} — {r['client_name']} | {r['signing_type']} | {r['location'] or 'TBD'} | ${float(r['fee'] or 0):,.2f}")

    lines += ["", f"OVERDUE INVOICES ({len(overdue)})", "=" * 45]
    if overdue.empty:
        lines.append("No overdue invoices. 🎉")
    else:
        for _, r in overdue.iterrows():
            lines.append(f"• {r['client_name']} — ${float(r['balance_due'] or 0):,.2f} overdue")

    lines += ["", f"— {settings.get('business_name', 'Notary Assistant')}"]

    return send_email(
        notify_email,
        f"Notary Day Ahead — {tomorrow.strftime('%b %d')}",
        "\n".join(lines),
        settings=settings
    )


def get_gcal_add_url(row):
    """Return a Google Calendar event creation URL for an appointment."""
    from urllib.parse import urlencode
    try:
        appt_date = str(row.get("appointment_date", ""))[:10].replace("-", "")
        start_time = str(row.get("appointment_time", "09:00")).replace(":", "")
        end_time = str(row.get("end_time", "10:00")).replace(":", "")
        if len(start_time) == 4: start_time += "00"
        if len(end_time) == 4: end_time += "00"
        start = f"{appt_date}T{start_time}"
        end = f"{appt_date}T{end_time}"
        title = f"{row.get('signing_type','Signing')} — {row.get('client_name','')}"
        details = f"Fee: ${float(row.get('fee') or 0):,.2f}\nPhone: {row.get('client_phone','')}"
        location = row.get("location", "")
        params = {
            "action": "TEMPLATE",
            "text": title,
            "dates": f"{start}/{end}",
            "details": details,
            "location": location,
        }
        return "https://calendar.google.com/calendar/render?" + urlencode(params)
    except Exception:
        return None


def get_sms_link(row, settings):
    """Return a pre-filled SMS link for appointment confirmation."""
    from urllib.parse import quote
    phone = str(row.get("client_phone") or "").strip().replace("-","").replace("(","").replace(")","").replace(" ","")
    if not phone:
        return None, "No phone number on file"
    try:
        appt_date = pd.to_datetime(str(row.get("appointment_date",""))).strftime("%A, %B %d at")
    except Exception:
        appt_date = str(row.get("appointment_date",""))
    appt_time = str(row.get("appointment_time","")).strip()
    signing_type = str(row.get("signing_type","")).strip()
    location = str(row.get("location","")).strip()
    fee = float(row.get("fee") or 0)
    biz = settings.get("business_name","Your Notary")

    body = (
        f"Hi {row.get('client_name','')}, this is {biz} confirming your "
        f"{signing_type} appointment for {appt_date} {appt_time}"
    )
    if location:
        body += f" at {location}"
    body += f". Fee: ${fee:,.2f}. Reply to confirm or call with questions."

    sms_url = f"sms:{phone}?body={quote(body)}"
    return sms_url, None

def calculate_end_time(appointment_date, appointment_time, duration_minutes):
    start_dt = datetime.combine(appointment_date, appointment_time)
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    return end_dt.strftime("%H:%M")


def check_schedule_issues(appointment_date, appointment_time, duration_minutes, travel_buffer_minutes, exclude_appointment_id=None):
    query = """
        SELECT id, client_name, appointment_date, appointment_time, end_time, signing_type, status, location
        FROM appointments
        WHERE appointment_date = ?
        AND status NOT IN ('Canceled', 'No Show')
    """
    params = [str(appointment_date)]

    if exclude_appointment_id is not None:
        query += " AND id != ?"
        params.append(int(exclude_appointment_id))

    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()

    new_start = datetime.combine(appointment_date, appointment_time)
    new_end = new_start + timedelta(minutes=duration_minutes)

    conflicts = []
    buffer_warnings = []

    for row in rows:
        existing_id = row[0]
        existing_client = row[1]
        existing_date = row[2]
        existing_start_text = row[3] or "09:00"
        existing_end_text = row[4] or "10:00"
        existing_type = row[5]
        existing_status = row[6]
        existing_location = row[7] or ""

        try:
            existing_start = datetime.strptime(f"{existing_date} {existing_start_text}", "%Y-%m-%d %H:%M")
            existing_end = datetime.strptime(f"{existing_date} {existing_end_text}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue

        if new_start < existing_end and new_end > existing_start:
            conflicts.append({
                "id": existing_id,
                "client": existing_client,
                "start": existing_start_text,
                "end": existing_end_text,
                "type": existing_type,
                "status": existing_status,
                "location": existing_location
            })
        else:
            gap_before = (new_start - existing_end).total_seconds() / 60
            gap_after = (existing_start - new_end).total_seconds() / 60

            if 0 <= gap_before < travel_buffer_minutes:
                buffer_warnings.append({
                    "client": existing_client,
                    "start": existing_start_text,
                    "end": existing_end_text,
                    "gap": int(gap_before),
                    "type": existing_type,
                    "status": existing_status,
                    "location": existing_location,
                    "message": "New appointment starts too soon after this appointment."
                })

            if 0 <= gap_after < travel_buffer_minutes:
                buffer_warnings.append({
                    "client": existing_client,
                    "start": existing_start_text,
                    "end": existing_end_text,
                    "gap": int(gap_after),
                    "type": existing_type,
                    "status": existing_status,
                    "location": existing_location,
                    "message": "This appointment starts too soon after the new appointment."
                })

    return conflicts, buffer_warnings


def add_client(client_name, phone, email, address, referral_source, notes):
    if using_supabase():
        try:
            sb().table("clients").insert({
                "client_name": client_name, "phone": phone, "email": email,
                "address": address, "referral_source": referral_source, "notes": notes
            }).execute()
            get_clients.clear()
            return
        except Exception:
            pass
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO clients (client_name, phone, email, address, referral_source, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (client_name, phone, email, address, referral_source, notes)
        )
        conn.commit()
    get_clients.clear()


def update_client(client_id, client_name, phone, email, address, referral_source, notes):
    if using_supabase():
        try:
            sb().table("clients").update({
                "client_name": client_name, "phone": phone, "email": email,
                "address": address, "referral_source": referral_source, "notes": notes
            }).eq("id", client_id).execute()
            sb().table("appointments").update({
                "client_name": client_name, "client_phone": phone, "client_email": email
            }).eq("client_id", client_id).execute()
            get_clients.clear()
            get_all_appointments_dataframe.clear()
            return
        except Exception:
            pass
    with db_conn() as conn:
        conn.execute(
            "UPDATE clients SET client_name=?, phone=?, email=?, address=?, referral_source=?, notes=? WHERE id=?",
            (client_name, phone, email, address, referral_source, notes, client_id)
        )
        conn.execute(
            "UPDATE appointments SET client_name=?, client_phone=?, client_email=? WHERE client_id=?",
            (client_name, phone, email, client_id)
        )
        conn.commit()
    get_clients.clear()
    get_all_appointments_dataframe.clear()


def delete_client(client_id):
    if using_supabase():
        try:
            sb().table("clients").delete().eq("id", client_id).execute()
            get_clients.clear()
            return
        except Exception:
            pass
    with db_conn() as conn:
        conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        conn.commit()
    get_clients.clear()


@st.cache_data(ttl=30)
def get_clients():
    if using_supabase():
        try:
            resp = sb().table("clients").select("id,client_name,phone,email,address,referral_source,notes").order("client_name").execute()
            return [(r["id"], r["client_name"], r["phone"], r["email"], r["address"], r["referral_source"], r["notes"]) for r in resp.data]
        except Exception:
            pass
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, client_name, phone, email, address, referral_source, notes FROM clients ORDER BY client_name"
        )
        return cursor.fetchall()


def get_client_by_id(client_id):
    if client_id is None:
        return None
    try:
        client_id = int(client_id)
    except (TypeError, ValueError):
        return None
    if using_supabase():
        try:
            resp = sb().table("clients").select(
                "id,client_name,phone,email,address,referral_source,notes"
            ).eq("id", client_id).execute()
            if resp.data:
                r = resp.data[0]
                return (r["id"], r["client_name"], r["phone"], r["email"],
                        r["address"], r["referral_source"], r["notes"])
        except Exception:
            pass
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, client_name, phone, email, address, referral_source, notes FROM clients WHERE id = ?",
            (client_id,)
        )
        return cursor.fetchone()


def add_appointment(client_id, client_name, client_phone, client_email,
                    appointment_date, appointment_time, duration_minutes, end_time,
                    signing_type, location, fee, mileage, status, notes,
                    invoice_date=None, payment_due_date=None):
    if invoice_date is None:
        invoice_date = appointment_date
    if payment_due_date is None:
        payment_due_date = (date.fromisoformat(str(appointment_date)) + timedelta(days=7)).isoformat()

    if using_supabase():
        try:
            resp = sb().table("appointments").insert({
                "client_id": client_id, "client_name": client_name,
                "client_phone": client_phone, "client_email": client_email,
                "appointment_date": str(appointment_date), "appointment_time": appointment_time,
                "duration_minutes": duration_minutes, "end_time": end_time,
                "signing_type": signing_type, "location": location,
                "fee": fee, "mileage": mileage, "status": status, "notes": notes,
                "invoice_date": str(invoice_date), "payment_due_date": str(payment_due_date)
            }).execute()
            appointment_id = resp.data[0]["id"]
            # Add checklist items
            sb().table("checklist").insert([
                {"appointment_id": appointment_id, "item_name": item, "completed": False}
                for item in CHECKLIST_ITEMS
            ]).execute()
            get_all_appointments_dataframe.clear()
            return appointment_id
        except Exception:
            pass

    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO appointments (
                client_id, client_name, client_phone, client_email,
                appointment_date, appointment_time, duration_minutes, end_time,
                signing_type, location, fee, mileage, status, notes, invoice_date, payment_due_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            client_id, client_name, client_phone, client_email,
            appointment_date, appointment_time, duration_minutes, end_time,
            signing_type, location, fee, mileage, status, notes, invoice_date, payment_due_date
        ))
        appointment_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO checklist (appointment_id, item_name, completed) VALUES (?, ?, 0)",
            [(appointment_id, item) for item in CHECKLIST_ITEMS]
        )
        conn.commit()
    get_all_appointments_dataframe.clear()
    return appointment_id


def get_appointments(search_text="", status_filter="All"):
    query = """
        SELECT 
            id, client_name, appointment_date, appointment_time,
            signing_type, location, fee, mileage, status
        FROM appointments
        WHERE 1=1
    """
    params = []
    if search_text:
        search_pattern = f"%{search_text}%"
        query += " AND (client_name LIKE ? OR signing_type LIKE ? OR location LIKE ?)"
        params.extend([search_pattern, search_pattern, search_pattern])
    if status_filter != "All":
        query += " AND status = ?"
        params.append(status_filter)
    query += " ORDER BY appointment_date DESC, appointment_time DESC"
    with db_conn() as conn:
        return conn.execute(query, params).fetchall()


@st.cache_data(ttl=30)
def get_all_appointments_dataframe():
    if using_supabase():
        try:
            resp = sb().table("appointments").select("*").order("appointment_date", desc=True).execute()
            if resp.data:
                return pd.DataFrame(resp.data)
        except Exception:
            pass
    with db_conn() as conn:
        df = pd.read_sql_query("""
        SELECT 
            id,
            client_id,
            client_name,
            client_phone,
            client_email,
            appointment_date,
            appointment_time,
            duration_minutes,
            end_time,
            signing_type,
            location,
            fee,
            mileage,
            status,
            notes,
            invoice_date,
            payment_due_date,
            client_notes,
            internal_notes
        FROM appointments
        ORDER BY appointment_date DESC, appointment_time DESC
    """, conn)
    return df


def get_appointment_by_id(appointment_id):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT 
            id,
            client_id,
            client_name,
            client_phone,
            client_email,
            appointment_date,
            appointment_time,
            duration_minutes,
            end_time,
            signing_type,
            location,
            fee,
            mileage,
            status,
            notes,
            invoice_date,
            payment_due_date,
            client_notes,
            internal_notes
        FROM appointments
        WHERE id = ?
    """, (appointment_id,))
        return cursor.fetchone()


def update_appointment(appointment_id, client_name, client_phone, client_email,
                       appointment_date, appointment_time, duration_minutes, end_time,
                       signing_type, location, fee, mileage, status, notes,
                       invoice_date=None, payment_due_date=None,
                       client_notes=None, internal_notes=None):
    if invoice_date is None:
        invoice_date = appointment_date
    if payment_due_date is None:
        payment_due_date = (date.fromisoformat(str(appointment_date)) + timedelta(days=7)).isoformat()

    with db_conn() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE appointments
            SET
                client_name = ?,
                client_phone = ?,
                client_email = ?,
                appointment_date = ?,
                appointment_time = ?,
                duration_minutes = ?,
                end_time = ?,
                signing_type = ?,
                location = ?,
                fee = ?,
                mileage = ?,
                status = ?,
                notes = ?,
                invoice_date = ?,
                payment_due_date = ?,
                client_notes = ?,
                internal_notes = ?
            WHERE id = ?
        """, (
            client_name, client_phone, client_email,
            appointment_date, appointment_time, duration_minutes, end_time,
            signing_type, location, fee, mileage, status, notes,
            invoice_date, payment_due_date,
            client_notes, internal_notes,
            appointment_id
        ))
        conn.commit()
    get_all_appointments_dataframe.clear()


def _record_status_history(appointment_id, old_status, new_status):
    """Record a status change in history log."""
    changed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        if using_supabase():
            sb().table("status_history").insert({
                "appointment_id": appointment_id, "old_status": old_status,
                "new_status": new_status, "changed_at": changed_at
            }).execute()
        else:
            with db_conn() as conn:
                conn.execute(
                    "INSERT INTO status_history (appointment_id, old_status, new_status, changed_at) VALUES (?,?,?,?)",
                    (appointment_id, old_status, new_status, changed_at)
                )
                conn.commit()
    except Exception:
        pass  # Never crash status updates due to history logging


def get_status_history(appointment_id):
    try:
        if using_supabase():
            resp = sb().table("status_history").select("*").eq("appointment_id", appointment_id).order("changed_at").execute()
            if resp.data:
                return pd.DataFrame(resp.data)
        else:
            with db_conn() as conn:
                return pd.read_sql_query(
                    "SELECT * FROM status_history WHERE appointment_id=? ORDER BY changed_at",
                    conn, params=(appointment_id,)
                )
    except Exception:
        pass
    return pd.DataFrame()


def update_status(appointment_id, new_status):
    # Get current status for history
    try:
        df_cur = get_all_appointments_dataframe()
        if not df_cur.empty:
            cur_rows = df_cur[df_cur["id"] == appointment_id]
            old_status = cur_rows.iloc[0]["status"] if not cur_rows.empty else "Unknown"
        else:
            old_status = "Unknown"
        _record_status_history(appointment_id, old_status, new_status)
    except Exception:
        pass

    if using_supabase():
        try:
            sb().table("appointments").update({"status": new_status}).eq("id", appointment_id).execute()
            get_all_appointments_dataframe.clear()
            return
        except Exception:
            pass
    with db_conn() as conn:
        conn.execute("UPDATE appointments SET status = ? WHERE id = ?", (new_status, appointment_id))
        conn.commit()
    get_all_appointments_dataframe.clear()


def delete_appointment(appointment_id):
    if using_supabase():
        try:
            for tbl in ["payments", "checklist", "attachments"]:
                sb().table(tbl).delete().eq("appointment_id", appointment_id).execute()
            sb().table("appointments").delete().eq("id", appointment_id).execute()
            get_all_appointments_dataframe.clear()
            get_all_payment_totals.clear()
            return
        except Exception:
            pass
    with db_conn() as conn:
        conn.execute("DELETE FROM payments WHERE appointment_id = ?", (appointment_id,))
        conn.execute("DELETE FROM checklist WHERE appointment_id = ?", (appointment_id,))
        conn.execute("DELETE FROM attachments WHERE appointment_id = ?", (appointment_id,))
        conn.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))
        conn.commit()
    get_all_appointments_dataframe.clear()
    get_all_payment_totals.clear()


def get_payments_dataframe():
    with db_conn() as conn:
        df = pd.read_sql_query("""
        SELECT 
            payments.id,
            payments.appointment_id,
            payments.payment_date,
            payments.amount_paid,
            payments.payment_method,
            payments.notes,
            appointments.client_name,
            appointments.signing_type,
            appointments.fee,
            appointments.status
        FROM payments
        LEFT JOIN appointments ON payments.appointment_id = appointments.id
        ORDER BY payments.payment_date DESC
    """, conn)
    return df


def add_payment(appointment_id, payment_date, amount_paid, payment_method, notes):
    if using_supabase():
        try:
            sb().table("payments").insert({
                "appointment_id": appointment_id, "payment_date": str(payment_date),
                "amount_paid": float(amount_paid), "payment_method": payment_method, "notes": notes
            }).execute()
            # Update appointment status
            resp = sb().table("appointments").select("fee").eq("id", appointment_id).execute()
            fee = float(resp.data[0]["fee"] or 0) if resp.data else 0
            totals = get_all_payment_totals.__wrapped__() if hasattr(get_all_payment_totals, "__wrapped__") else None
            if totals is None:
                paid_resp = sb().table("payments").select("amount_paid").eq("appointment_id", appointment_id).execute()
                total_paid = sum(float(r["amount_paid"] or 0) for r in paid_resp.data)
            else:
                total_paid = totals.get(appointment_id, 0)
            new_status = "Paid" if fee > 0 and total_paid >= fee else "Awaiting Payment"
            sb().table("appointments").update({"status": new_status}).eq("id", appointment_id).execute()
            get_all_payment_totals.clear()
            get_all_appointments_dataframe.clear()
            return
        except Exception:
            pass
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO payments (appointment_id, payment_date, amount_paid, payment_method, notes) VALUES (?, ?, ?, ?, ?)",
            (appointment_id, payment_date, amount_paid, payment_method, notes)
        )
        fee_row = cursor.execute("SELECT fee FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
        paid_row = cursor.execute("SELECT SUM(amount_paid) FROM payments WHERE appointment_id = ?", (appointment_id,)).fetchone()
        fee = float(fee_row[0] or 0) if fee_row else 0
        total_paid = float(paid_row[0] or 0) if paid_row and paid_row[0] is not None else 0
        if fee > 0 and total_paid >= fee:
            cursor.execute("UPDATE appointments SET status = 'Paid' WHERE id = ?", (appointment_id,))
        elif total_paid > 0:
            cursor.execute("UPDATE appointments SET status = 'Awaiting Payment' WHERE id = ?", (appointment_id,))
        conn.commit()
    get_all_payment_totals.clear()
    get_all_appointments_dataframe.clear()


def get_payment_total_for_appointment(appointment_id):
    with db_conn() as conn:
        row = conn.execute("SELECT SUM(amount_paid) FROM payments WHERE appointment_id = ?", (appointment_id,)).fetchone()
    return float(row[0] or 0)


@st.cache_data(ttl=30)
def get_all_payment_totals():
    """Return {appointment_id: total_paid} in a single DB query."""
    if using_supabase():
        try:
            resp = sb().table("payments").select("appointment_id,amount_paid").execute()
            totals = {}
            for r in resp.data:
                aid = r["appointment_id"]
                totals[aid] = totals.get(aid, 0.0) + float(r["amount_paid"] or 0)
            return totals
        except Exception:
            pass
    with db_conn() as conn:
        rows = conn.execute("SELECT appointment_id, SUM(amount_paid) FROM payments GROUP BY appointment_id").fetchall()
    return {row[0]: float(row[1] or 0) for row in rows}



def get_dashboard_payment_summary(df):
    """Return paid and balance totals.

    Payments table is the source of truth when payments are recorded.
    If an appointment is manually marked Paid but has no payment records,
    count its fee as paid so paid invoices still show on the dashboard.
    Uses a single bulk query instead of per-row DB calls.
    """
    if df.empty:
        return 0.0, 0.0, df

    working_df = df.copy()
    working_df["fee"] = pd.to_numeric(working_df["fee"], errors="coerce").fillna(0)

    payment_totals = get_all_payment_totals()

    def calc_paid(row):
        recorded = payment_totals.get(int(row["id"]), 0.0)
        return float(row["fee"]) if recorded == 0 and row.get("status") == "Paid" else recorded

    working_df["paid_amount"] = working_df.apply(calc_paid, axis=1)
    working_df["balance_due"] = (working_df["fee"] - working_df["paid_amount"]).clip(lower=0)

    return working_df["paid_amount"].sum(), working_df["balance_due"].sum(), working_df



def get_invoice_status_dataframe(df):
    if df.empty:
        return df

    working_df = df.copy()
    working_df["fee"] = pd.to_numeric(working_df["fee"], errors="coerce").fillna(0)
    working_df["invoice_date"] = working_df.get("invoice_date", working_df["appointment_date"])
    working_df["payment_due_date"] = working_df.get("payment_due_date", working_df["appointment_date"])
    working_df["invoice_date"] = working_df["invoice_date"].fillna(working_df["appointment_date"])
    working_df["payment_due_date"] = working_df["payment_due_date"].fillna(working_df["appointment_date"])

    payment_totals = get_all_payment_totals()
    today_value = date.today()

    def row_invoice_status(row):
        fee = float(row.get("fee") or 0)
        recorded_paid = payment_totals.get(int(row["id"]), 0.0)
        paid = float(row["fee"]) if recorded_paid == 0 and row.get("status") == "Paid" else recorded_paid
        balance = max(fee - paid, 0)
        try:
            due_date = date.fromisoformat(str(row.get("payment_due_date"))[:10])
        except Exception:
            due_date = today_value

        if fee <= 0:
            status = "No Charge"
        elif paid >= fee:
            status = "Paid"
        elif paid > 0:
            status = "Partially Paid"
        elif balance > 0 and due_date < today_value:
            status = "Overdue"
        else:
            status = "Unpaid"
        return paid, balance, status

    results = working_df.apply(row_invoice_status, axis=1, result_type="expand")
    working_df["paid_amount"] = results[0]
    working_df["balance_due"] = results[1]
    working_df["invoice_status"] = results[2]

    return working_df


def add_followup(appointment_id, followup_date, followup_type, outcome, notes, completed):
    if using_supabase():
        try:
            sb().table("followups").insert({
                "appointment_id": appointment_id, "followup_date": str(followup_date),
                "followup_type": followup_type, "outcome": outcome,
                "notes": notes, "completed": bool(completed)
            }).execute()
            return
        except Exception:
            pass
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO followups (appointment_id, followup_date, followup_type, outcome, notes, completed) VALUES (?, ?, ?, ?, ?, ?)",
            (appointment_id, followup_date, followup_type, outcome, notes, 1 if completed else 0)
        )
        conn.commit()


def get_followups_dataframe():
    if using_supabase():
        try:
            resp = sb().table("followups").select("*, appointments(client_name, appointment_date, signing_type)").order("followup_date", desc=True).execute()
            if resp.data:
                rows = []
                for r in resp.data:
                    appt = r.pop("appointments", {}) or {}
                    r["client_name"] = appt.get("client_name", "")
                    r["appointment_date"] = appt.get("appointment_date", "")
                    r["signing_type"] = appt.get("signing_type", "")
                    rows.append(r)
                return pd.DataFrame(rows)
        except Exception:
            pass
    with db_conn() as conn:
        df = pd.read_sql_query("""
        SELECT 
            followups.id,
            followups.appointment_id,
            followups.followup_date,
            followups.followup_type,
            followups.outcome,
            followups.notes,
            followups.completed,
            appointments.client_name,
            appointments.signing_type,
            appointments.appointment_date
        FROM followups
        LEFT JOIN appointments ON followups.appointment_id = appointments.id
        ORDER BY followups.followup_date DESC
    """, conn)
    return df


def quote_text(settings, client_name, signing_type, base_fee, travel_fee, after_hours_fee, extra_fee, notes):
    total = float(base_fee or 0) + float(travel_fee or 0) + float(after_hours_fee or 0) + float(extra_fee or 0)
    return f"""QUOTE / ESTIMATE

{settings.get('business_name', '')}
{settings.get('business_tagline', '')}
{settings.get('business_phone', '')}
{settings.get('business_email', '')}
{settings.get('business_website', '')}

Client: {client_name}
Service: {signing_type}

Base Fee: ${float(base_fee or 0):,.2f}
Travel Fee: ${float(travel_fee or 0):,.2f}
After-Hours Fee: ${float(after_hours_fee or 0):,.2f}
Additional Fee: ${float(extra_fee or 0):,.2f}

Estimated Total: ${total:,.2f}

Notes:
{notes}

This is an estimate only. Final pricing may change based on document type, travel distance, waiting time, witnesses, scanbacks, printing, or other special requirements.
"""




EXPENSE_CATEGORIES = [
    "Notary Supplies", "E&O Insurance", "Background Check",
    "Printing / Paper", "Mileage / Gas", "Software / Subscriptions",
    "Training / Education", "Marketing", "Equipment", "Professional Fees", "Other"
]


def add_expense(expense_date, category, description, amount, notes):
    if using_supabase():
        try:
            sb().table("expenses").insert({
                "expense_date": str(expense_date), "category": category,
                "description": description, "amount": float(amount), "notes": notes
            }).execute()
            return
        except Exception:
            pass
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO expenses (expense_date, category, description, amount, notes) VALUES (?, ?, ?, ?, ?)",
            (str(expense_date), category, description, float(amount), notes)
        )
        conn.commit()


def get_expenses_dataframe():
    if using_supabase():
        try:
            resp = sb().table("expenses").select("*").order("expense_date", desc=True).execute()
            if resp.data:
                return pd.DataFrame(resp.data)
        except Exception:
            pass
    with db_conn() as conn:
        return pd.read_sql_query("SELECT * FROM expenses ORDER BY expense_date DESC", conn)


def delete_expense(expense_id):
    if using_supabase():
        try:
            sb().table("expenses").delete().eq("id", expense_id).execute()
            return
        except Exception:
            pass
    with db_conn() as conn:
        conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        conn.commit()


QUOTE_STATUSES = ["Sent", "Accepted", "Declined", "Expired", "Converted"]
COMM_TYPES = ["Email", "Phone Call", "Text / SMS", "In Person", "Other"]
COMM_DIRECTIONS = ["Outbound", "Inbound"]


def save_quote_record(client_name, signing_type, fee, quote_text_val, notes=""):
    created = str(date.today())
    if using_supabase():
        try:
            sb().table("quotes").insert({
                "client_name": client_name, "signing_type": signing_type,
                "fee": float(fee), "created_date": created,
                "status": "Sent", "notes": notes, "quote_text": quote_text_val
            }).execute()
            return
        except Exception:
            pass
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO quotes (client_name, signing_type, fee, created_date, status, notes, quote_text) VALUES (?,?,?,?,?,?,?)",
            (client_name, signing_type, float(fee), created, "Sent", notes, quote_text_val)
        )
        conn.commit()


def get_quotes_dataframe():
    if using_supabase():
        try:
            resp = sb().table("quotes").select("*").order("created_date", desc=True).execute()
            if resp.data:
                return pd.DataFrame(resp.data)
        except Exception:
            pass
    with db_conn() as conn:
        return pd.read_sql_query("SELECT * FROM quotes ORDER BY created_date DESC", conn)


def update_quote_status(quote_id, new_status):
    if using_supabase():
        try:
            sb().table("quotes").update({"status": new_status}).eq("id", quote_id).execute()
            return
        except Exception:
            pass
    with db_conn() as conn:
        conn.execute("UPDATE quotes SET status=? WHERE id=?", (new_status, quote_id))
        conn.commit()


def add_client_comm(client_id, client_name, comm_type, direction, subject, notes):
    comm_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    if using_supabase():
        try:
            sb().table("client_comms").insert({
                "client_id": client_id, "client_name": client_name,
                "comm_date": comm_date, "comm_type": comm_type,
                "direction": direction, "subject": subject, "notes": notes
            }).execute()
            return
        except Exception:
            pass
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO client_comms (client_id, client_name, comm_date, comm_type, direction, subject, notes) VALUES (?,?,?,?,?,?,?)",
            (client_id, client_name, comm_date, comm_type, direction, subject, notes)
        )
        conn.commit()


def get_client_comms(client_id=None):
    if using_supabase():
        try:
            q = sb().table("client_comms").select("*").order("comm_date", desc=True)
            if client_id:
                q = q.eq("client_id", client_id)
            resp = q.execute()
            if resp.data:
                return pd.DataFrame(resp.data)
        except Exception:
            pass
    with db_conn() as conn:
        if client_id:
            return pd.read_sql_query(
                "SELECT * FROM client_comms WHERE client_id=? ORDER BY comm_date DESC",
                conn, params=(client_id,)
            )
        return pd.read_sql_query("SELECT * FROM client_comms ORDER BY comm_date DESC", conn)

def save_template(template_name, signing_type, location, fee, mileage, duration_minutes, notes, client_notes, internal_notes):
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO templates (template_name, signing_type, location, fee, mileage,
               duration_minutes, notes, client_notes, internal_notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (template_name, signing_type, location, fee, mileage, duration_minutes,
             notes, client_notes, internal_notes, __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        conn.commit()


def get_templates():
    with db_conn() as conn:
        import pandas as pd
        return pd.read_sql_query("SELECT * FROM templates ORDER BY template_name", conn)


def delete_template(template_id):
    with db_conn() as conn:
        conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        conn.commit()

def ensure_checklist_for_appointment(appointment_id):
    with db_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM checklist WHERE appointment_id = ?", (appointment_id,)).fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO checklist (appointment_id, item_name, completed) VALUES (?, ?, 0)",
                [(appointment_id, item) for item in CHECKLIST_ITEMS]
            )
        conn.commit()


def get_checklist(appointment_id):
    ensure_checklist_for_appointment(appointment_id)
    if using_supabase():
        try:
            resp = sb().table("checklist").select("id,item_name,completed").eq("appointment_id", appointment_id).order("id").execute()
            return [(r["id"], r["item_name"], 1 if r["completed"] else 0) for r in resp.data]
        except Exception:
            pass
    with db_conn() as conn:
        return conn.execute(
            "SELECT id, item_name, completed FROM checklist WHERE appointment_id = ? ORDER BY id",
            (appointment_id,)
        ).fetchall()


def update_checklist_item(checklist_id, completed):
    if using_supabase():
        try:
            sb().table("checklist").update({"completed": bool(completed)}).eq("id", checklist_id).execute()
            return
        except Exception:
            pass
    with db_conn() as conn:
        conn.execute("UPDATE checklist SET completed = ? WHERE id = ?", (1 if completed else 0, checklist_id))
        conn.commit()


def add_attachment(appointment_id, file_name, file_path, notes):
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO attachments (appointment_id, file_name, file_path, uploaded_at, notes) VALUES (?, ?, ?, ?, ?)",
            (appointment_id, file_name, file_path, datetime.now().strftime("%Y-%m-%d %H:%M"), notes)
        )
        conn.commit()


def get_attachments(appointment_id=None):
    with db_conn() as conn:
        if appointment_id:
            return pd.read_sql_query(
                "SELECT * FROM attachments WHERE appointment_id = ? ORDER BY uploaded_at DESC",
                conn, params=(appointment_id,)
            )
        return pd.read_sql_query(
            """SELECT attachments.*, appointments.client_name, appointments.appointment_date
               FROM attachments LEFT JOIN appointments ON attachments.appointment_id = appointments.id
               ORDER BY uploaded_at DESC""",
            conn
        )


def status_color(status):
    colors = {
        "Scheduled": "#2563eb",
        "Completed": "#16a34a",
        "Canceled": "#dc2626",
        "No Show": "#7f1d1d",
        "Awaiting Payment": "#f59e0b",
        "Paid": "#15803d"
    }
    return colors.get(status, "#6b7280")


def appointment_subject(row):
    return f"Appointment Confirmation - {row['signing_type']}"


def appointment_email_body(row, settings):
    return f"""Hello {row['client_name']},

This is a confirmation for your upcoming appointment.

Appointment Details:
Date: {row['appointment_date']}
Time: {row['appointment_time']} - {row['end_time']}
Service: {row['signing_type']}
Location: {row['location']}

Please have a valid, unexpired government-issued photo ID available at the appointment.

Fee: ${float(row['fee'] or 0):,.2f}

Thank you,

{settings['business_name']}
{settings['business_phone']}
{settings['business_email']}
{settings['business_website']}
"""


def payment_reminder_body(row, settings):
    paid = get_payment_total_for_appointment(int(row["id"]))
    balance = float(row["fee"] or 0) - paid

    return f"""Hello {row['client_name']},

This is a friendly reminder that payment is still pending for the following appointment:

Date: {row['appointment_date']}
Service: {row['signing_type']}
Amount Billed: ${float(row['fee'] or 0):,.2f}
Amount Paid: ${paid:,.2f}
Balance Due: ${balance:,.2f}

Please let me know if you have any questions.

Thank you,

{settings['business_name']}
{settings['business_phone']}
{settings['business_email']}
{settings['business_website']}
"""


def invoice_text(row, settings):
    invoice_number = f"INV-{int(row['id']):05d}"
    paid = get_payment_total_for_appointment(int(row["id"]))
    balance = float(row["fee"] or 0) - paid

    return f"""
{settings['business_name']}
{settings['business_tagline']}
{settings['business_phone']}
{settings['business_email']}
{settings['business_website']}

INVOICE
Invoice Number: {invoice_number}
Invoice Date: {date.today()}

Bill To:
{row['client_name']}
{row['client_email'] or ''}
{row['client_phone'] or ''}

Service Date: {row['appointment_date']}
Service Time: {row['appointment_time']} - {row['end_time']}
Service Type: {row['signing_type']}
Location: {row['location']}

Description:
{row['signing_type']} service

Amount Billed: ${float(row['fee'] or 0):,.2f}
Amount Paid: ${paid:,.2f}
Balance Due: ${balance:,.2f}

Mileage Logged: {float(row['mileage'] or 0):,.1f} miles
Status: {row['status']}

Notes:
{row['notes'] or ''}

Thank you for your business.
"""


def create_pdf_invoice(row, settings):
    """Generate a PDF invoice and return (bytes, error_string).
    Returns bytes in memory — no temp file written to disk, safe for Streamlit Cloud.
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import inch
        import io
    except ImportError:
        return None, "ReportLab is not installed. Run: pip install reportlab"

    invoice_number = f"INV-{int(row['id']):05d}"
    paid = get_payment_total_for_appointment(int(row["id"]))
    balance = float(row["fee"] or 0) - paid

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    LOGO_SIZE = 2.0 * inch   # logo width & max height on the PDF
    LOGO_X    = 0.75 * inch
    TEXT_X_WITH_LOGO    = LOGO_X + LOGO_SIZE + 0.2 * inch  # text starts right of logo
    TEXT_X_WITHOUT_LOGO = LOGO_X

    # Start y high enough that a 2-inch logo fits before the divider
    y = height - 0.85 * inch

    logo_path = settings.get("logo_path", "")
    has_logo = bool(logo_path and os.path.exists(logo_path))
    if has_logo:
        try:
            # Anchor the logo so its TOP edge aligns with y
            c.drawImage(
                logo_path,
                LOGO_X,
                y - LOGO_SIZE,          # bottom-left corner of image
                width=LOGO_SIZE,
                height=LOGO_SIZE,
                preserveAspectRatio=True,
                mask="auto",
            )
        except Exception:
            has_logo = False

    text_x = TEXT_X_WITH_LOGO if has_logo else TEXT_X_WITHOUT_LOGO

    c.setFont("Helvetica-Bold", 18)
    c.drawString(text_x, y, settings["business_name"])
    c.setFont("Helvetica", 10)
    y -= 0.28 * inch
    c.drawString(text_x, y, settings["business_tagline"] or "")
    y -= 0.20 * inch
    c.drawString(text_x, y, settings["business_phone"] or "")
    y -= 0.20 * inch
    c.drawString(text_x, y, settings["business_email"] or "")
    y -= 0.20 * inch
    c.drawString(text_x, y, settings["business_website"] or "")

    # Move y below the logo (whichever is taller — logo or text block)
    logo_bottom = (height - 0.85 * inch) - LOGO_SIZE if has_logo else y
    y = min(y, logo_bottom) - 0.4 * inch
    c.setFont("Helvetica-Bold", 20)
    c.drawString(0.75 * inch, y, "INVOICE")
    c.setFont("Helvetica", 11)
    y -= 0.35 * inch
    c.drawString(0.75 * inch, y, f"Invoice Number: {invoice_number}")
    y -= 0.22 * inch
    c.drawString(0.75 * inch, y, f"Invoice Date: {date.today()}")

    y -= 0.4 * inch
    c.setFont("Helvetica-Bold", 12)
    c.drawString(0.75 * inch, y, "Bill To:")
    c.setFont("Helvetica", 11)
    y -= 0.22 * inch
    c.drawString(0.75 * inch, y, str(row["client_name"]))
    y -= 0.2 * inch
    c.drawString(0.75 * inch, y, str(row["client_email"] or ""))
    y -= 0.2 * inch
    c.drawString(0.75 * inch, y, str(row["client_phone"] or ""))

    y -= 0.45 * inch
    c.setFont("Helvetica-Bold", 12)
    c.drawString(0.75 * inch, y, "Service Details")
    c.setFont("Helvetica", 11)

    details = [
        ("Service Date", row["appointment_date"]),
        ("Service Time", f"{row['appointment_time']} - {row['end_time']}"),
        ("Service Type", row["signing_type"]),
        ("Location", row["location"]),
        ("Mileage", f"{float(row['mileage'] or 0):,.1f} miles"),
        ("Status", row["status"]),
    ]

    for label, value in details:
        y -= 0.24 * inch
        c.drawString(0.75 * inch, y, f"{label}: {value}")

    y -= 0.45 * inch
    c.setFont("Helvetica-Bold", 12)
    c.drawString(0.75 * inch, y, "Charges")
    c.setFont("Helvetica", 11)
    y -= 0.25 * inch
    c.drawString(0.75 * inch, y, f"Amount Billed: ${float(row['fee'] or 0):,.2f}")
    y -= 0.25 * inch
    c.drawString(0.75 * inch, y, f"Amount Paid: ${paid:,.2f}")
    y -= 0.25 * inch
    c.setFont("Helvetica-Bold", 12)
    c.drawString(0.75 * inch, y, f"Balance Due: ${balance:,.2f}")

    y -= 0.5 * inch
    c.setFont("Helvetica", 10)
    c.drawString(0.75 * inch, y, "Thank you for your business.")

    c.save()
    buffer.seek(0)
    return buffer.getvalue(), None



def auto_backup_to_supabase():
    """Upload a SQLite backup snapshot to Supabase Storage (bucket: backups).
    Called once per session. Returns (success, message).
    """
    if not using_supabase():
        return False, "Supabase not connected"
    if not os.path.exists(DB_NAME):
        return False, "No local DB to back up"
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(DB_NAME, "rb") as f:
            db_bytes = f.read()
        path = f"auto/{timestamp}_notary_assistant.db"
        sb().storage.from_("backups").upload(
            path, db_bytes,
            {"content-type": "application/octet-stream", "x-upsert": "true"}
        )
        return True, f"Backup saved to Supabase Storage: {path}"
    except Exception as e:
        return False, str(e)

def create_backup():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_FOLDER, f"notary_assistant_backup_{timestamp}.db")
    shutil.copy2(DB_NAME, backup_path)
    return backup_path



def safe_table_count(table_name):
    try:
        with db_conn() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    except Exception:
        return None


def database_health_snapshot():
    exists = os.path.exists(DB_NAME)
    size_mb = os.path.getsize(DB_NAME) / (1024 * 1024) if exists else 0
    modified = datetime.fromtimestamp(os.path.getmtime(DB_NAME)).strftime("%Y-%m-%d %H:%M:%S") if exists else "Missing"
    tables = ["settings", "clients", "appointments", "payments", "checklist", "attachments"]
    counts = {table: safe_table_count(table) for table in tables}
    return exists, size_mb, modified, counts


def export_table_csv(table_name):
    if table_name not in _SAFE_EXPORT_TABLES:
        raise ValueError(f"export_table_csv: '{table_name}' is not an allowed table.")
    with db_conn() as conn:
        return pd.read_sql_query(f"SELECT * FROM {table_name}", conn)


def is_valid_email(email):
    if not email:
        return True
    return "@" in email and "." in email.split("@")[-1]


def has_duplicate_appointment(client_id, appointment_date, appointment_time, exclude_appointment_id=None):
    params = [client_id, str(appointment_date),
              appointment_time.strftime("%H:%M") if hasattr(appointment_time, "strftime") else appointment_time]
    query = "SELECT id, client_name, signing_type FROM appointments WHERE client_id=? AND appointment_date=? AND appointment_time=?"
    if exclude_appointment_id is not None:
        query += " AND id != ?"
        params.append(exclude_appointment_id)
    with db_conn() as conn:
        return conn.execute(query, params).fetchall()


def recalculate_payment_statuses():
    with db_conn() as conn:
        appointments = conn.execute("SELECT id, fee, status FROM appointments").fetchall()
        for appointment_id, fee, status in appointments:
            if status in ["Canceled", "No Show"]:
                continue
            paid = conn.execute("SELECT SUM(amount_paid) FROM payments WHERE appointment_id = ?", (appointment_id,)).fetchone()[0] or 0
            fee_value = fee or 0
            if fee_value > 0 and paid >= fee_value:
                conn.execute("UPDATE appointments SET status = 'Paid' WHERE id = ?", (appointment_id,))
            elif paid > 0 or fee_value > 0:
                conn.execute("UPDATE appointments SET status = 'Awaiting Payment' WHERE id = ?", (appointment_id,))
        conn.commit()
    get_all_appointments_dataframe.clear()
    get_all_payment_totals.clear()


def reset_demo_data(preserve_clients=True):
    backup_path = create_backup()
    with db_conn() as conn:
        for tbl in ["payments", "checklist", "attachments", "followups", "appointments"]:
            conn.execute(f"DELETE FROM {tbl}")
        if not preserve_clients:
            conn.execute("DELETE FROM clients")
        conn.commit()
    get_all_appointments_dataframe.clear()
    get_clients.clear()
    get_all_payment_totals.clear()
    return backup_path


def validate_client_inputs(client_name, email):
    errors = []
    if not client_name or not client_name.strip():
        errors.append("Client name is required.")
    if not is_valid_email(email):
        errors.append("Client email does not look valid.")
    return errors


def validate_appointment_inputs(client_name, client_email, duration_minutes, fee, mileage):
    errors = []
    if not client_name or not str(client_name).strip():
        errors.append("Client name is required.")
    if not is_valid_email(client_email):
        errors.append("Client email does not look valid.")
    if duration_minutes < 15:
        errors.append("Duration must be at least 15 minutes.")
    if fee < 0:
        errors.append("Fee cannot be negative.")
    if mileage < 0:
        errors.append("Mileage cannot be negative.")
    return errors



def global_search(query):
    """Search across clients, appointments, and payments. Returns dict of results."""
    if not query or len(query.strip()) < 2:
        return {}
    q = query.strip().lower()
    results = {}

    # Search clients
    clients = get_clients()
    matched_clients = [
        c for c in clients
        if q in (c[1] or "").lower()
        or q in (c[2] or "").lower()
        or q in (c[3] or "").lower()
        or q in (c[4] or "").lower()
    ]
    if matched_clients:
        results["clients"] = matched_clients

    # Search appointments
    df = get_all_appointments_dataframe()
    if not df.empty:
        mask = (
            df["client_name"].str.lower().str.contains(q, na=False) |
            df["signing_type"].str.lower().str.contains(q, na=False) |
            df["location"].str.lower().str.contains(q, na=False) |
            df["status"].str.lower().str.contains(q, na=False) |
            df["notes"].fillna("").str.lower().str.contains(q, na=False) |
            df["appointment_date"].astype(str).str.contains(q, na=False)
        )
        matched_appts = df[mask]
        if not matched_appts.empty:
            results["appointments"] = matched_appts

    return results

def appointment_selector(df, label="Select Appointment"):
    if df.empty:
        return None, None

    choices = {
        f"{row['id']} - {row['client_name']} - {row['appointment_date']} {row['appointment_time'] or ''} - {row['status']}": row["id"]
        for _, row in df.iterrows()
    }

    selected = st.selectbox(label, list(choices.keys()))
    appointment_id = choices[selected]
    row = df[df["id"] == appointment_id].iloc[0]
    return appointment_id, row


