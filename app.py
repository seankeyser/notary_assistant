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
APP_VERSION = "3.3.0"
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
    with db_conn() as conn:
        cursor = conn.cursor()

    query = """
        SELECT id, client_name, appointment_date, appointment_time, end_time, signing_type, status, location
        FROM appointments
        WHERE appointment_date = ?
        AND status NOT IN ('Canceled', 'No Show')
    """

    params = [str(appointment_date)]

    if exclude_appointment_id is not None:
        query += " AND id != ?"
        params.append(exclude_appointment_id)

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

        existing_start = datetime.strptime(f"{existing_date} {existing_start_text}", "%Y-%m-%d %H:%M")
        existing_end = datetime.strptime(f"{existing_date} {existing_end_text}", "%Y-%m-%d %H:%M")

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


ensure_folders()
create_tables()
settings = get_settings()

st.set_page_config(page_title="Notary Digital Assistant", layout="centered")

# ── Auto-backup to Supabase Storage (once per session) ───────────────────────
if "auto_backup_done" not in st.session_state:
    st.session_state.auto_backup_done = True
    try:
        _ok, _msg = auto_backup_to_supabase()
    except Exception:
        pass  # Never crash startup due to backup failure

# ── Client Portal intercept ───────────────────────────────────────────────────
# Must run before anything else renders. If ?portal= is in the URL,
# show a locked-down client view and stop — never load the main app.
PORTAL_TOKEN_DAYS = 7  # Portal links expire after this many days

def _make_portal_token(client_id, day_bucket=None):
    """Generate a token valid for the current 7-day window."""
    import base64, hashlib, math
    secret = st.secrets.get("SUPABASE_KEY", "notary")[:16]
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

_qp = st.query_params
if "portal" in _qp:
    _portal_client_id = _qp.get("portal")
    _portal_token = _qp.get("token", "")

    # Hide sidebar entirely for portal view
    st.markdown("<style>[data-testid='stSidebar']{display:none!important;}</style>", unsafe_allow_html=True)

    try:
        _client_id_int = int(_portal_client_id)
    except (ValueError, TypeError):
        st.error("Invalid portal link.")
        st.stop()

    if not _verify_portal_token(_portal_client_id, _portal_token):
        st.error("⛔ This link is invalid or has expired.")
        st.stop()

    # Valid token — show read-only client view
    _clients = get_clients()
    _client_info = next((c for c in _clients if c[0] == _client_id_int), None)

    if not _client_info:
        st.error("Client not found.")
        st.stop()

    biz_name = settings.get("business_name", "Notary Assistant")
    biz_phone = settings.get("business_phone", "")
    biz_email = settings.get("business_email", "")

    st.title(f"📋 {biz_name}")
    if biz_phone: st.write(f"📞 {biz_phone}")
    if biz_email: st.write(f"📧 {biz_email}")
    st.divider()

    st.subheader(f"Account Summary — {_client_info[1]}")

    _df = get_all_appointments_dataframe()
    if _df.empty:
        st.info("No appointments on record.")
        st.stop()

    _client_df = _df[_df["client_id"] == _client_id_int].copy()
    if _client_df.empty:
        st.info("No appointments on record.")
        st.stop()

    _all_paid = get_all_payment_totals()
    _client_df["fee"] = pd.to_numeric(_client_df["fee"], errors="coerce").fillna(0)
    _client_df["paid"] = _client_df["id"].apply(lambda x: _all_paid.get(int(x), 0.0))
    _client_df["balance"] = (_client_df["fee"] - _client_df["paid"]).clip(lower=0)
    _client_df["appointment_date"] = pd.to_datetime(_client_df["appointment_date"], errors="coerce")

    _total_balance = _client_df["balance"].sum()
    _total_billed = _client_df["fee"].sum()
    _total_paid = _client_df["paid"].sum()

    # Summary cards
    st.markdown(f"""
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:0.5rem;margin-bottom:1rem;">
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:0.6rem 0.75rem;">
            <div style="font-size:0.75rem;color:#64748b;font-weight:500;">Total Billed</div>
            <div style="font-size:1.3rem;font-weight:700;color:#0f172a;">${_total_billed:,.2f}</div>
        </div>
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:0.6rem 0.75rem;">
            <div style="font-size:0.75rem;color:#64748b;font-weight:500;">Total Paid</div>
            <div style="font-size:1.3rem;font-weight:700;color:#22c55e;">${_total_paid:,.2f}</div>
        </div>
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:0.6rem 0.75rem;">
            <div style="font-size:0.75rem;color:#64748b;font-weight:500;">Balance Due</div>
            <div style="font-size:1.3rem;font-weight:700;color:{'#ef4444' if _total_balance > 0 else '#22c55e'};">${_total_balance:,.2f}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if _total_balance > 0:
        st.warning(f"Outstanding balance: **${_total_balance:,.2f}**. Please contact us to arrange payment.")
    else:
        st.success("✅ Your account is paid in full. Thank you!")

    st.divider()
    st.subheader("Appointment History")

    for _, _r in _client_df.sort_values("appointment_date", ascending=False).iterrows():
        with st.expander(
            f"{_r['appointment_date'].strftime('%b %d, %Y') if pd.notna(_r['appointment_date']) else '—'}"
            f" — {_r['signing_type']} — ${float(_r['fee']):,.2f}"
        ):
            st.write(f"**Time:** {_r.get('appointment_time', '—')}")
            st.write(f"**Location:** {_r.get('location', '—')}")
            st.write(f"**Status:** {_r.get('status', '—')}")
            st.write(f"**Fee:** ${float(_r['fee']):,.2f}")
            st.write(f"**Paid:** ${float(_r['paid']):,.2f}")
            bal = float(_r['balance'])
            if bal > 0:
                st.write(f"**Balance Due:** :red[${bal:,.2f}]")
            else:
                st.write("**Balance Due:** :green[Paid in full]")

    st.divider()
    st.caption(f"Questions? Contact {biz_name}")
    if biz_phone: st.caption(f"📞 {biz_phone}")
    if biz_email: st.caption(f"📧 {biz_email}")

    st.stop()  # ← Never loads the main app for portal visitors


# ── Responsive / mobile CSS ───────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Base: full-width content area on all screens ── */
.block-container {
    max-width: 100% !important;
    padding-left: 1rem !important;
    padding-right: 1rem !important;
    padding-top: 0.75rem !important;
}

/* ── Disable Android Chrome text inflation which breaks flex layouts ── */
* {
    -webkit-text-size-adjust: 100% !important;
    text-size-adjust: 100% !important;
}

/* ── Consistent box sizing everywhere ── */
*, *::before, *::after {
    box-sizing: border-box !important;
}

/* ── Touch-friendly tap targets ── */
.stButton > button,
[data-testid="baseButton-secondary"],
[data-testid="baseButton-primary"],
[data-testid="stDownloadButton"] > button {
    min-height: 44px !important;
    font-size: 0.95rem !important;
}

/* ── Input fields: comfortable touch size ── */
.stTextInput > div > div > input,
.stNumberInput > div > div > input,
.stTextArea > div > div > textarea,
.stSelectbox > div > div,
.stDateInput > div > div > input,
.stTimeInput > div > div > input {
    min-height: 44px !important;
    font-size: 1rem !important;
}

/* ── Metric cards: tighten spacing ── */
[data-testid="metric-container"] {
    padding: 0.5rem 0.6rem !important;
    margin-bottom: 0.25rem !important;
}
[data-testid="stMetricValue"] {
    font-size: 1.3rem !important;
    line-height: 1.2 !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.78rem !important;
    line-height: 1.2 !important;
}

/* ══════════════════════════════════════════════════════════
   TABLET  (≤ 1024px) — 5-col becomes 3-col
   ══════════════════════════════════════════════════════════ */
@media (max-width: 1024px) {
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        gap: 0.4rem !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        min-width: 30% !important;
        flex: 1 1 30% !important;
    }
    h1 { font-size: 1.5rem !important; }
    h2 { font-size: 1.25rem !important; }
}

/* ══════════════════════════════════════════════════════════
   PHONE  (≤ 640px) — 2-column grid for metrics & buttons
   Pixel 9 Pro XL logical viewport = 412px
   ══════════════════════════════════════════════════════════ */
@media (max-width: 640px) {
    .block-container {
        padding-left: 0.4rem !important;
        padding-right: 0.4rem !important;
    }

    /* 2-column grid — sized for 412px viewport */
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        gap: 0.3rem !important;
        width: 100% !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        min-width: 0 !important;
        flex: 1 1 calc(50% - 0.3rem) !important;
        max-width: calc(50% - 0.3rem) !important;
        overflow: hidden !important;
    }

    /* Metric cards: compact 2-up grid */
    [data-testid="metric-container"] {
        padding: 0.4rem 0.5rem !important;
        margin: 0 !important;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.1rem !important;
        line-height: 1.15 !important;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.72rem !important;
        line-height: 1.1 !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
    }

    /* Sidebar: sensible width on phone */
    section[data-testid="stSidebar"] {
        min-width: 260px !important;
        max-width: 82vw !important;
    }

    h1 { font-size: 1.3rem !important; }
    h2 { font-size: 1.15rem !important; }
    h3 { font-size: 1.0rem !important; }

    /* Dataframes: horizontal scroll */
    [data-testid="stDataFrame"] {
        overflow-x: auto !important;
        -webkit-overflow-scrolling: touch !important;
    }

    /* Buttons: full width when alone, 2-up in column grids */
    .stButton > button {
        width: 100% !important;
    }
    [data-testid="stDownloadButton"],
    [data-testid="stDownloadButton"] > button {
        width: 100% !important;
    }

    /* Expanders: larger tap area */
    [data-testid="stExpander"] summary {
        padding: 0.65rem 0.75rem !important;
        font-size: 0.95rem !important;
    }

    /* Reduce logo size on phone */
    [data-testid="stImage"] img {
        max-width: 140px !important;
    }

    /* Date picker popover stays in viewport */
    [data-testid="stDateInputPopover"] {
        width: 94vw !important;
        left: 3vw !important;
    }
}

/* ══════════════════════════════════════════════════════════
   SMALL PHONE  (≤ 380px) — single column fallback
   ══════════════════════════════════════════════════════════ */
@media (max-width: 380px) {
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        min-width: 100% !important;
        flex: 1 1 100% !important;
    }
    [data-testid="stMetricValue"] { font-size: 1rem !important; }
}
</style>
""", unsafe_allow_html=True)



if settings.get("auth_enabled") in [1, "1", True] and settings.get("app_password"):
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.title("🔐 Notary Digital Assistant")
        password_attempt = st.text_input("Enter App Password", type="password")

        if st.button("Login"):
            if password_attempt == settings.get("app_password"):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password.")

        st.stop()


# -----------------------------
# SIDEBAR NAVIGATION + THEME
# -----------------------------

MENU_OPTIONS = [
    "Dashboard",
    "Calendar View",
    "Global Search",
    "Add Client",
    "View Clients",
    "Edit Client",
    "Client History",
    "Add Appointment",
    "View Appointments",
    "Edit Appointment",
    "Delete Appointment",
    "Job Checklist",
    "Payment Tracking",
    "Invoice Status",
    "Follow-Up Tracker",
    "Referral Analytics",
    "Quote Generator",
    "Document Attachments",
    "Reports / Export",
    "Mileage / Tax Report",
    "Invoice Generator",
    "Email Templates",
    "Map / Route Tools",
    "Admin / System Health",
    "Cloud Database Setup",
    "Settings / Business Profile",
    "Backup / Restore"
]

MENU_GROUPS = {
    "📊 Overview": ["Dashboard", "Calendar View", "Global Search"],
    "👤 Clients": ["Add Client", "View Clients", "Edit Client", "Client History", "Client Portal", "Retention Report", "Communication Log"],
    "📅 Appointments": ["Add Appointment", "View Appointments", "Edit Appointment", "Delete Appointment", "Job Checklist", "Appointment Templates", "Recurring Scheduler"],
    "💰 Finance": ["Payment Tracking", "Invoice Status", "Invoice Generator", "Quote Generator", "Quote Tracking", "Reports / Export", "Mileage / Tax Report", "Expense Tracker", "Profit & Loss"],
    "📬 Tools": ["Follow-Up Tracker", "Email Templates", "Map / Route Tools", "Document Attachments", "Referral Analytics", "Signing Day Sheet", "Service Area Map", "Notary Journal"],
    "⚙️ Admin": ["Admin / System Health", "Cloud Database Setup", "Settings / Business Profile", "Backup / Restore"],
}

if "selected_menu" not in st.session_state:
    st.session_state.selected_menu = "Dashboard"

st.sidebar.title("Navigation")

dark_mode = st.sidebar.toggle("Dark Mode", value=False)

if dark_mode:
    st.markdown(
        """
        <style>
        /* ── Base app & sidebar ─────────────────────────────────────────── */
        .stApp, .stApp > * {
            background-color: #0f172a !important;
            color: #e2e8f0 !important;
        }
        [data-testid="stSidebar"],
        [data-testid="stSidebar"] > div {
            background-color: #020617 !important;
        }
        [data-testid="stSidebar"] * {
            color: #e2e8f0 !important;
        }

        /* ── Typography ─────────────────────────────────────────────────── */
        h1, h2, h3, h4, h5, h6, p, label, span, div,
        .stMarkdown, .stMarkdown p,
        [data-testid="stText"] {
            color: #e2e8f0 !important;
        }

        /* ── Forms & containers ─────────────────────────────────────────── */
        [data-testid="stForm"],
        section[data-testid="stForm"],
        div[data-testid="stFormSubmitButton"] {
            background-color: #1e293b !important;
            border: 1px solid #334155 !important;
            border-radius: 8px !important;
        }
        .stExpander,
        [data-testid="stExpander"] {
            background-color: #1e293b !important;
            border: 1px solid #334155 !important;
            border-radius: 8px !important;
        }
        [data-testid="stExpander"] summary,
        [data-testid="stExpander"] summary:hover,
        [data-testid="stExpander"] summary p {
            color: #e2e8f0 !important;
            background-color: #1e293b !important;
        }
        .stExpander > div > div {
            background-color: #1e293b !important;
        }

        /* ── Metrics ────────────────────────────────────────────────────── */
        [data-testid="stMetric"],
        [data-testid="metric-container"] {
            background-color: #1e293b !important;
            border: 1px solid #334155 !important;
            border-radius: 12px !important;
            padding: 12px !important;
        }
        [data-testid="stMetricValue"],
        [data-testid="stMetricLabel"],
        [data-testid="stMetricDelta"] {
            color: #e2e8f0 !important;
        }

        /* ── Text inputs, number inputs, text areas ─────────────────────── */
        .stTextInput > div > div > input,
        .stNumberInput > div > div > input,
        .stTextArea > div > div > textarea,
        input[type="text"], input[type="number"],
        input[type="password"], textarea {
            background-color: #1e293b !important;
            color: #f1f5f9 !important;
            border-color: #475569 !important;
            border-radius: 6px !important;
        }
        .stTextInput > label,
        .stNumberInput > label,
        .stTextArea > label {
            color: #cbd5e1 !important;
        }

        /* ── Date & time pickers ────────────────────────────────────────── */
        .stDateInput > div > div > input,
        .stTimeInput > div > div > input,
        [data-baseweb="input"] input {
            background-color: #1e293b !important;
            color: #f1f5f9 !important;
            border-color: #475569 !important;
        }
        [data-baseweb="calendar"],
        [data-baseweb="calendar"] *,
        [data-testid="stDateInputPopover"],
        [data-testid="stDateInputPopover"] * {
            background-color: #1e293b !important;
            color: #f1f5f9 !important;
        }

        /* ── Select boxes & dropdowns ───────────────────────────────────── */
        div[data-baseweb="select"] > div,
        div[data-baseweb="select"] > div > div {
            background-color: #1e293b !important;
            color: #f1f5f9 !important;
            border-color: #475569 !important;
        }
        div[data-baseweb="select"] span,
        div[data-baseweb="select"] input,
        div[data-baseweb="select"] svg {
            color: #f1f5f9 !important;
            fill: #f1f5f9 !important;
        }
        div[data-baseweb="popover"],
        div[data-baseweb="popover"] *,
        div[data-baseweb="menu"],
        div[data-baseweb="menu"] * {
            background-color: #1e293b !important;
            color: #f1f5f9 !important;
        }
        div[role="listbox"],
        ul[role="listbox"],
        li[role="option"],
        div[role="option"] {
            background-color: #1e293b !important;
            color: #f1f5f9 !important;
        }
        div[role="option"]:hover,
        li[role="option"]:hover {
            background-color: #334155 !important;
            color: #ffffff !important;
        }

        /* ── Multiselect ────────────────────────────────────────────────── */
        [data-baseweb="multi-select"],
        [data-baseweb="tag"] {
            background-color: #334155 !important;
            color: #f1f5f9 !important;
        }

        /* ── Checkboxes ─────────────────────────────────────────────────── */
        .stCheckbox label,
        .stCheckbox span {
            color: #e2e8f0 !important;
        }
        [data-baseweb="checkbox"] {
            background-color: transparent !important;
        }

        /* ── Radio buttons ──────────────────────────────────────────────── */
        .stRadio label, .stRadio span {
            color: #e2e8f0 !important;
        }

        /* ── Sliders & toggles ──────────────────────────────────────────── */
        .stSlider [data-testid="stTickBarMin"],
        .stSlider [data-testid="stTickBarMax"] {
            color: #94a3b8 !important;
        }
        .stToggle label, .stToggle span {
            color: #e2e8f0 !important;
        }

        /* ── Buttons ────────────────────────────────────────────────────── */
        .stButton > button,
        [data-testid="baseButton-secondary"],
        [data-testid="baseButton-primary"] {
            background-color: #1e293b !important;
            color: #f1f5f9 !important;
            border: 1px solid #475569 !important;
            border-radius: 8px !important;
        }
        .stButton > button:hover,
        [data-testid="baseButton-secondary"]:hover {
            background-color: #334155 !important;
            color: #ffffff !important;
            border-color: #64748b !important;
        }
        [data-testid="baseButton-primary"] {
            background-color: #3b82f6 !important;
            border-color: #3b82f6 !important;
        }

        /* ── File uploader ──────────────────────────────────────────────── */
        [data-testid="stFileUploader"],
        [data-testid="stFileUploader"] > div,
        [data-testid="stFileUploaderDropzone"] {
            background-color: #1e293b !important;
            border-color: #475569 !important;
            color: #e2e8f0 !important;
        }
        [data-testid="stFileUploaderDropzone"] span,
        [data-testid="stFileUploaderDropzone"] p {
            color: #94a3b8 !important;
        }

        /* ── Alerts: info / warning / success / error ───────────────────── */
        [data-testid="stAlert"],
        div[data-testid="stAlert"] > div {
            color: #e2e8f0 !important;
        }
        [data-testid="stAlert"][kind="info"],
        div[class*="stAlert"][class*="info"] {
            background-color: #1e3a5f !important;
            border-color: #3b82f6 !important;
        }
        [data-testid="stAlert"][kind="warning"],
        div[class*="stAlert"][class*="warning"] {
            background-color: #3d2a00 !important;
            border-color: #f59e0b !important;
        }
        [data-testid="stAlert"][kind="success"],
        div[class*="stAlert"][class*="success"] {
            background-color: #0f3626 !important;
            border-color: #22c55e !important;
        }
        [data-testid="stAlert"][kind="error"],
        div[class*="stAlert"][class*="error"] {
            background-color: #3b1219 !important;
            border-color: #ef4444 !important;
        }
        /* Fallback: any alert box background */
        div[class*="alert"], div[class*="Alert"] {
            background-color: #1e293b !important;
            color: #e2e8f0 !important;
        }

        /* ── DataFrames & tables ────────────────────────────────────────── */
        [data-testid="stDataFrame"],
        [data-testid="stDataFrame"] *,
        .stDataFrame iframe,
        iframe {
            background-color: #1e293b !important;
            color: #e2e8f0 !important;
        }
        [data-testid="stDataFrameResizable"] {
            background-color: #1e293b !important;
        }

        /* ── Progress bar ───────────────────────────────────────────────── */
        [data-testid="stProgress"] > div {
            background-color: #334155 !important;
        }
        [data-testid="stProgress"] > div > div {
            background-color: #3b82f6 !important;
        }

        /* ── Download / upload buttons ──────────────────────────────────── */
        [data-testid="stDownloadButton"] > button {
            background-color: #1e293b !important;
            color: #f1f5f9 !important;
            border: 1px solid #475569 !important;
        }

        /* ── Tabs ───────────────────────────────────────────────────────── */
        [data-testid="stTabs"] [role="tab"] {
            color: #94a3b8 !important;
            background-color: transparent !important;
        }
        [data-testid="stTabs"] [role="tab"][aria-selected="true"] {
            color: #f1f5f9 !important;
            border-bottom-color: #3b82f6 !important;
        }
        [data-testid="stTabsContent"] {
            background-color: #0f172a !important;
        }

        /* ── Divider ────────────────────────────────────────────────────── */
        hr {
            border-color: #334155 !important;
        }

        /* ── Caption / small text ───────────────────────────────────────── */
        .stCaption, [data-testid="stCaptionContainer"] {
            color: #94a3b8 !important;
        }

        /* ── Code blocks ────────────────────────────────────────────────── */
        [data-testid="stCode"],
        .stCode, code, pre {
            background-color: #1e293b !important;
            color: #e2e8f0 !important;
            border: 1px solid #334155 !important;
        }

        /* ── Mobile dark mode overrides ─────────────────────────────────── */
        @media (max-width: 640px) {
            .stApp, .stApp > * {
                background-color: #0f172a !important;
            }
            [data-testid="stAlert"] {
                border-radius: 6px !important;
                padding: 0.5rem 0.6rem !important;
            }
            [data-testid="stExpander"] {
                margin-bottom: 0.4rem !important;
            }
            /* Keep metric card dark on 2-col grid */
            [data-testid="metric-container"] {
                background-color: #1e293b !important;
                border: 1px solid #334155 !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True
    )
else:
    st.markdown(
        """
        <style>
        .stButton > button {
            border-radius: 10px;
            width: 100%;
            text-align: left;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

st.sidebar.divider()

# Determine which group contains the active menu item so it opens by default
active_group = next(
    (group for group, items in MENU_GROUPS.items() if st.session_state.selected_menu in items),
    list(MENU_GROUPS.keys())[0]
)

for group_label, group_items in MENU_GROUPS.items():
    expanded = (group_label == active_group)
    with st.sidebar.expander(group_label, expanded=expanded):
        for item in group_items:
            is_active = (item == st.session_state.selected_menu)
            label = f"**{item}**" if is_active else item
            if st.button(label, key=f"nav_{item}", use_container_width=True):
                st.session_state.selected_menu = item

menu = st.session_state.selected_menu

st.sidebar.divider()
if st.sidebar.button("🔄 Refresh", use_container_width=True, help="Pull latest data from Supabase"):
    clear_all_caches()
    st.rerun()

# Offline / connection indicator
if using_supabase():
    st.sidebar.caption("☁️ Supabase connected")
else:
    st.sidebar.warning("⚠️ Offline — using local SQLite. Data may not persist.")

st.sidebar.caption(f"Version {APP_VERSION}")

if settings.get("logo_path") and os.path.exists(settings["logo_path"]):
    st.image(settings["logo_path"], width=220)

st.title("📋 Notary Digital Assistant")
st.subheader(settings.get("business_tagline") or "Client + Appointment CRM")
st.caption(f"App Version: {APP_VERSION}")


if menu == "Dashboard":
    st.header("Dashboard")

    df = get_all_appointments_dataframe()

    if df.empty:
        st.info("No appointments yet.")
    else:
        df["fee"] = pd.to_numeric(df["fee"], errors="coerce").fillna(0)
        df["mileage"] = pd.to_numeric(df["mileage"], errors="coerce").fillna(0)
        df["duration_minutes"] = pd.to_numeric(df["duration_minutes"], errors="coerce").fillna(60)
        df["appointment_date"] = pd.to_datetime(df["appointment_date"], errors="coerce")
        df["month"] = df["appointment_date"].dt.strftime("%Y-%m")

        today = pd.Timestamp(date.today())
        upcoming_df = df[
            (df["appointment_date"] >= today)
            & (df["status"].isin(["Scheduled", "Awaiting Payment"]))
        ].sort_values(["appointment_date", "appointment_time"]).head(10)

        total_paid_actual, total_unpaid, df = get_dashboard_payment_summary(df)

        total_appointments = len(df)
        total_fees = df["fee"].sum()
        total_mileage = df["mileage"].sum()

        # HTML metric grid — immune to Streamlit's column layout on mobile
        is_dark = dark_mode
        card_bg = "#1e293b" if is_dark else "#f8fafc"
        card_border = "#334155" if is_dark else "#e2e8f0"
        label_color = "#94a3b8" if is_dark else "#64748b"
        value_color = "#f1f5f9" if is_dark else "#0f172a"

        st.markdown(f"""
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:0.5rem;margin-bottom:0.75rem;">
            <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.75rem;color:{label_color};font-weight:500;">Appointments</div>
                <div style="font-size:1.4rem;font-weight:700;color:{value_color};">{total_appointments}</div>
            </div>
            <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.75rem;color:{label_color};font-weight:500;">Total Billed</div>
                <div style="font-size:1.4rem;font-weight:700;color:{value_color};">${total_fees:,.0f}</div>
            </div>
            <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.75rem;color:{label_color};font-weight:500;">Paid Invoices</div>
                <div style="font-size:1.4rem;font-weight:700;color:#22c55e;">${total_paid_actual:,.0f}</div>
            </div>
            <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.75rem;color:{label_color};font-weight:500;">Balance Due</div>
                <div style="font-size:1.4rem;font-weight:700;color:#f59e0b;">${total_unpaid:,.0f}</div>
            </div>
            <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.75rem;color:{label_color};font-weight:500;">Mileage</div>
                <div style="font-size:1.4rem;font-weight:700;color:{value_color};">{total_mileage:,.1f} mi</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Revenue goal tracker
        revenue_goal = float(settings.get("revenue_goal") or 0)
        if revenue_goal > 0:
            current_month = pd.Timestamp(date.today()).strftime("%Y-%m")
            month_revenue = df[df["month"] == current_month]["fee"].sum()
            pct = min(month_revenue / revenue_goal, 1.0)
            st.divider()
            st.subheader(f"Monthly Revenue Goal — {pd.Timestamp(date.today()).strftime('%B %Y')}")
            goal_col1, goal_col2 = st.columns([3, 1])
            with goal_col1:
                st.progress(pct)
            with goal_col2:
                st.write(f"**${month_revenue:,.0f}** / ${revenue_goal:,.0f}")
            if pct >= 1.0:
                st.success("🎉 Monthly goal reached!")
            else:
                remaining = revenue_goal - month_revenue
                st.caption(f"${remaining:,.0f} remaining to hit your goal")

        invoice_df = get_invoice_status_dataframe(df)
        paid_count = len(invoice_df[invoice_df["invoice_status"] == "Paid"])
        unpaid_count = len(invoice_df[invoice_df["invoice_status"] == "Unpaid"])
        partial_count = len(invoice_df[invoice_df["invoice_status"] == "Partially Paid"])
        overdue_count = len(invoice_df[invoice_df["invoice_status"] == "Overdue"])

        st.subheader("Invoice Status")
        st.markdown(f"""
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:0.5rem;margin-bottom:0.75rem;">
            <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.75rem;color:{label_color};font-weight:500;">✅ Paid</div>
                <div style="font-size:1.4rem;font-weight:700;color:#22c55e;">{paid_count}</div>
            </div>
            <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.75rem;color:{label_color};font-weight:500;">🔲 Unpaid</div>
                <div style="font-size:1.4rem;font-weight:700;color:{value_color};">{unpaid_count}</div>
            </div>
            <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.75rem;color:{label_color};font-weight:500;">⏳ Partial</div>
                <div style="font-size:1.4rem;font-weight:700;color:#f59e0b;">{partial_count}</div>
            </div>
            <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.75rem;color:{label_color};font-weight:500;">🔴 Overdue</div>
                <div style="font-size:1.4rem;font-weight:700;color:#ef4444;">{overdue_count}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        if overdue_count > 0:
            st.warning(f"⚠️ {overdue_count} invoice(s) are overdue.")

        # Pending follow-up alerts
        followups_df = get_followups_dataframe()
        if not followups_df.empty:
            today_str = str(date.today())
            pending_followups = followups_df[
                (followups_df["completed"] == 0) &
                (followups_df["followup_date"] <= today_str)
            ]
            if not pending_followups.empty:
                st.warning(f"📋 {len(pending_followups)} follow-up(s) are due or overdue.")
                with st.expander("View Pending Follow-Ups"):
                    st.dataframe(
                        pending_followups[["followup_date", "client_name", "followup_type", "outcome", "notes"]],
                        use_container_width=True
                    )

        st.divider()

        st.subheader("Upcoming Appointments")

        if upcoming_df.empty:
            st.success("No upcoming appointments.")
        else:
            st.dataframe(
                upcoming_df[
                    [
                        "appointment_date",
                        "appointment_time",
                        "end_time",
                        "client_name",
                        "signing_type",
                        "location",
                        "status"
                    ]
                ],
                use_container_width=True
            )

        st.divider()

        st.subheader("Paid Invoices")
        paid_invoice_df = df[(df["paid_amount"] > 0) | (df["status"] == "Paid")].copy()

        if paid_invoice_df.empty:
            st.info("No paid invoices yet.")
        else:
            st.dataframe(
                paid_invoice_df[
                    [
                        "appointment_date",
                        "appointment_time",
                        "client_name",
                        "signing_type",
                        "fee",
                        "paid_amount",
                        "balance_due",
                        "status"
                    ]
                ].sort_values(["appointment_date", "appointment_time"], ascending=False),
                use_container_width=True
            )

        st.divider()

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Revenue by Month")
            st.bar_chart(df.groupby("month")["fee"].sum())

        with col2:
            st.subheader("Appointments by Status")
            st.bar_chart(df["status"].value_counts())

        # Quarterly income summary
        st.divider()
        st.subheader("Quarterly Income Summary")
        df["quarter"] = df["appointment_date"].dt.to_period("Q").astype(str)
        quarterly = df.groupby("quarter").agg(
            appointments=("id", "count"),
            billed=("fee", "sum"),
            mileage=("mileage", "sum")
        ).reset_index()
        quarterly["mileage_deduction"] = quarterly["mileage"] * float(settings.get("default_mileage_rate") or 0.67)
        quarterly.columns = ["Quarter", "Appointments", "Billed ($)", "Miles", "Mileage Deduction ($)"]
        quarterly["Billed ($)"] = quarterly["Billed ($)"].map("${:,.2f}".format)
        quarterly["Mileage Deduction ($)"] = quarterly["Mileage Deduction ($)"].map("${:,.2f}".format)
        quarterly["Miles"] = quarterly["Miles"].map("{:,.1f}".format)
        st.dataframe(quarterly, use_container_width=True, hide_index=True)

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Revenue by Signing Type")
            st.bar_chart(df.groupby("signing_type")["fee"].sum().sort_values(ascending=False))

        with col2:
            st.subheader("Mileage by Signing Type")
            st.bar_chart(df.groupby("signing_type")["mileage"].sum().sort_values(ascending=False))

        # ── Year-over-Year Revenue Comparison ──────────────────────────────
        st.divider()
        st.subheader("Year-over-Year Revenue")
        df["year"] = df["appointment_date"].dt.year
        df["month_num"] = df["appointment_date"].dt.month
        years = sorted(df["year"].dropna().unique().astype(int), reverse=True)

        if len(years) >= 2:
            yoy_data = {}
            month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
            for yr in years[:3]:  # Show up to 3 years
                monthly = df[df["year"] == yr].groupby("month_num")["fee"].sum()
                yoy_data[str(yr)] = [monthly.get(m, 0) for m in range(1, 13)]

            yoy_df = pd.DataFrame(yoy_data, index=month_names)
            st.bar_chart(yoy_df)

            # Running annual totals
            st.caption("Annual totals:")
            ann_cols = st.columns(min(len(years[:3]), 3))
            for i, yr in enumerate(years[:3]):
                annual_total = df[df["year"] == yr]["fee"].sum()
                ann_cols[i].metric(str(yr), f"${annual_total:,.0f}")
        else:
            st.info("Add appointments across multiple years to see year-over-year comparison.")

        # ── Busiest Days / Times Heatmap ───────────────────────────────────
        st.divider()
        st.subheader("Busiest Days & Times")
        df["day_of_week"] = df["appointment_date"].dt.day_name()
        df["hour"] = pd.to_datetime(df["appointment_time"], format="%H:%M", errors="coerce").dt.hour

        day_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
        day_counts = df["day_of_week"].value_counts().reindex(day_order, fill_value=0)

        heat_col1, heat_col2 = st.columns(2)
        with heat_col1:
            st.caption("Appointments by day of week")
            st.bar_chart(day_counts)

        with heat_col2:
            st.caption("Appointments by hour of day")
            hour_counts = df["hour"].value_counts().sort_index()
            if not hour_counts.empty:
                hour_counts.index = [f"{h:02d}:00" for h in hour_counts.index]
                st.bar_chart(hour_counts)

        # ── Revenue vs Expenses ────────────────────────────────────────────
        st.divider()
        st.subheader("Revenue vs Expenses")
        exp_df_dash = get_expenses_dataframe()
        if not exp_df_dash.empty:
            exp_df_dash["expense_date"] = pd.to_datetime(exp_df_dash["expense_date"], errors="coerce")
            exp_df_dash["amount"] = pd.to_numeric(exp_df_dash["amount"], errors="coerce").fillna(0)
            exp_df_dash["month"] = exp_df_dash["expense_date"].dt.strftime("%Y-%m")
            monthly_expenses = exp_df_dash.groupby("month")["amount"].sum()
            monthly_revenue = df.groupby("month")["fee"].sum()
            rev_exp_df = pd.DataFrame({
                "Revenue": monthly_revenue,
                "Expenses": monthly_expenses
            }).fillna(0).sort_index()
            if not rev_exp_df.empty:
                st.bar_chart(rev_exp_df)
        else:
            st.caption("Add expenses in the Expense Tracker to see revenue vs expenses here.")

        # ── Notification digest button ──────────────────────────────────────
        st.divider()
        notify_col1, notify_col2 = st.columns([3, 1])
        with notify_col1:
            st.caption("Send yourself a daily digest with tomorrow's appointments and overdue invoices.")
        with notify_col2:
            if st.button("📬 Send Daily Digest"):
                with st.spinner("Sending..."):
                    ok, err = send_daily_digest(settings)
                if ok:
                    st.success("Digest sent!")
                else:
                    st.warning(err)

        # ── Real-time refresh button ────────────────────────────────────────
        rt_col1, rt_col2 = st.columns([3, 1])
        with rt_col1:
            st.caption("Force a fresh data pull from Supabase.")
        with rt_col2:
            if st.button("🔄 Refresh Data"):
                clear_all_caches()
                st.rerun()


elif menu == "Global Search":
    st.header("🔍 Global Search")
    st.caption("Search across all clients, appointments, notes, and locations.")

    query = st.text_input("Search", placeholder="Client name, location, signing type, date...",
                          label_visibility="collapsed")

    if query:
        with st.spinner("Searching..."):
            results = global_search(query)

        if not results:
            st.info(f"No results found for **{query}**")
        else:
            total = sum(
                len(v) if not hasattr(v, '__len__') else
                len(v) if isinstance(v, list) else len(v)
                for v in results.values()
            )
            st.success(f"Found results across {len(results)} category(ies)")

            if "clients" in results:
                st.subheader(f"👤 Clients ({len(results['clients'])})")
                for c in results["clients"]:
                    with st.expander(f"{c[1]} — {c[3] or 'No email'}"):
                        st.write(f"📞 {c[2] or '—'}")
                        st.write(f"📍 {c[4] or '—'}")
                        if st.button("View History", key=f"gs_client_{c[0]}"):
                            st.session_state.selected_menu = "Client History"
                            st.session_state["history_client_id"] = c[0]
                            st.rerun()

            if "appointments" in results:
                appts = results["appointments"]
                st.subheader(f"📅 Appointments ({len(appts)})")
                all_paid = get_all_payment_totals()
                for _, row in appts.sort_values("appointment_date", ascending=False).iterrows():
                    paid = all_paid.get(int(row["id"]), 0.0)
                    balance = max(float(row.get("fee") or 0) - paid, 0)
                    with st.expander(
                        f"{row['appointment_date']} — {row['client_name']} — "
                        f"{row['signing_type']} — {row['status']}"
                    ):
                        st.write(f"📍 {row.get('location') or '—'}")
                        st.write(f"💵 Fee: ${float(row.get('fee') or 0):,.2f}  "
                                f"Paid: ${paid:,.2f}  Balance: ${balance:,.2f}")
                        if row.get("notes"):
                            st.caption(f"Notes: {row['notes']}")
    else:
        st.caption("Start typing to search across your entire database.")


elif menu == "Calendar View":
    st.header("Calendar View")

    df = get_all_appointments_dataframe()

    if df.empty:
        st.info("No appointments to display on the calendar.")
    else:
        df["appointment_date"] = pd.to_datetime(df["appointment_date"], errors="coerce")
        df = df.dropna(subset=["appointment_date"])

        calendar_events = []

        for _, row in df.iterrows():
            appointment_time = row["appointment_time"] if row["appointment_time"] else "09:00"
            end_time = row["end_time"] if row["end_time"] else "10:00"

            start_datetime = f"{row['appointment_date'].strftime('%Y-%m-%d')}T{appointment_time}"
            end_datetime = f"{row['appointment_date'].strftime('%Y-%m-%d')}T{end_time}"

            calendar_events.append({
                "title": f"{row['client_name']} - {row['signing_type']}",
                "start": start_datetime,
                "end": end_datetime,
                "backgroundColor": status_color(row["status"]),
                "borderColor": status_color(row["status"])
            })

        calendar_options = {
            "initialView": "dayGridMonth",
            "height": 650,
            "headerToolbar": {
                "left": "prev,next today",
                "center": "title",
                "right": "dayGridMonth,timeGridWeek,timeGridDay,listWeek"
            },
            "eventDisplay": "block"
        }

        calendar(events=calendar_events, options=calendar_options, key="notary_calendar")

        st.divider()
        st.subheader("Appointment Agenda")

        st.dataframe(
            df[
                [
                    "appointment_date",
                    "appointment_time",
                    "end_time",
                    "client_name",
                    "signing_type",
                    "location",
                    "fee",
                    "mileage",
                    "status"
                ]
            ].sort_values(["appointment_date", "appointment_time"]),
            use_container_width=True
        )


elif menu == "Add Client":
    st.header("Add Client")

    with st.form("client_form"):
        client_name = st.text_input("Client Name")
        phone = st.text_input("Phone")
        email = st.text_input("Email")
        address = st.text_input("Address")
        referral_source = st.selectbox("How did they hear about you?", REFERRAL_SOURCES)
        notes = st.text_area("Notes")

        submitted = st.form_submit_button("Save Client")

        if submitted:
            errors = validate_client_inputs(client_name, email)
            if errors:
                for error in errors:
                    st.error(error)
            else:
                # Fuzzy duplicate check
                existing_clients = get_clients()
                name_lower = client_name.strip().lower()
                duplicates = [
                    c for c in existing_clients
                    if c[1].strip().lower() == name_lower
                    or (email and c[3] and c[3].strip().lower() == email.strip().lower())
                ]
                if duplicates and not st.session_state.get("confirm_add_client"):
                    st.warning(
                        f"A client named **{duplicates[0][1]}** already exists "
                        f"(ID {duplicates[0][0]}). Save anyway?"
                    )
                    if st.form_submit_button("Yes, save as new client"):
                        st.session_state["confirm_add_client"] = True
                        st.rerun()
                else:
                    st.session_state.pop("confirm_add_client", None)
                    add_client(client_name, phone, email, address, referral_source, notes)
                    st.success("Client saved successfully!")


elif menu == "Communication Log":
    st.header("Communication Log")
    st.caption("Track every email, call, and text with each client in one timeline.")

    clients = get_clients()
    if not clients:
        st.info("Add clients first.")
    else:
        comm_tab1, comm_tab2 = st.tabs(["📝 Log Communication", "📋 View History"])

        with comm_tab1:
            client_choices = {f"{c[1]} — {c[3] or 'No email'}": c[0] for c in clients}
            selected_comm_client = st.selectbox("Client", list(client_choices.keys()), key="comm_client")
            comm_client_id = client_choices[selected_comm_client]
            comm_client_name = selected_comm_client.split(" — ")[0]

            with st.form("comm_form"):
                comm_type = st.selectbox("Type", COMM_TYPES)
                comm_direction = st.selectbox("Direction", COMM_DIRECTIONS)
                comm_subject = st.text_input("Subject / Topic", placeholder="e.g. Appointment confirmation, Payment follow-up")
                comm_notes = st.text_area("Notes", height=100)

                if st.form_submit_button("💾 Log Communication", type="primary"):
                    if not comm_subject.strip():
                        st.error("Subject is required.")
                    else:
                        add_client_comm(comm_client_id, comm_client_name, comm_type,
                                       comm_direction, comm_subject, comm_notes)
                        st.success(f"Logged {comm_type} with {comm_client_name}")
                        st.rerun()

        with comm_tab2:
            # Filter options
            filter_col1, filter_col2 = st.columns(2)
            with filter_col1:
                view_client = st.selectbox("View Client", ["All Clients"] + [c[1] for c in clients], key="comm_view")
            with filter_col2:
                view_type = st.selectbox("Filter Type", ["All"] + COMM_TYPES, key="comm_type_filter")

            if view_client == "All Clients":
                comms_df = get_client_comms()
            else:
                client_id_view = next(c[0] for c in clients if c[1] == view_client)
                comms_df = get_client_comms(client_id_view)

            if view_type != "All" and not comms_df.empty:
                comms_df = comms_df[comms_df["comm_type"] == view_type]

            if comms_df.empty:
                st.info("No communications logged yet.")
            else:
                for _, comm_row in comms_df.iterrows():
                    direction_icon = "📤" if comm_row.get("direction") == "Outbound" else "📥"
                    type_icon = {"Email":"📧","Phone Call":"📞","Text / SMS":"💬",
                                "In Person":"🤝","Other":"📌"}.get(comm_row.get("comm_type",""), "📌")
                    with st.expander(
                        f"{direction_icon} {type_icon} {comm_row['comm_date']} — "
                        f"{comm_row['client_name']} — {comm_row['subject']}"
                    ):
                        st.write(f"**Type:** {comm_row['comm_type']} ({comm_row['direction']})")
                        st.write(f"**Date:** {comm_row['comm_date']}")
                        if comm_row.get("notes"):
                            st.write(f"**Notes:** {comm_row['notes']}")


elif menu == "Retention Report":
    st.header("Client Retention Report")
    st.caption("See which clients come back, lifetime value, and average time between bookings.")

    df_all = get_all_appointments_dataframe()
    if df_all.empty:
        st.info("No appointment data yet.")
    else:
        df_all["appointment_date"] = pd.to_datetime(df_all["appointment_date"], errors="coerce")
        df_all["fee"] = pd.to_numeric(df_all["fee"], errors="coerce").fillna(0)

        client_stats = df_all.groupby("client_name").agg(
            total_appointments=("id", "count"),
            first_appointment=("appointment_date", "min"),
            last_appointment=("appointment_date", "max"),
            lifetime_value=("fee", "sum"),
        ).reset_index()

        client_stats["days_as_client"] = (
            client_stats["last_appointment"] - client_stats["first_appointment"]
        ).dt.days

        client_stats["avg_days_between"] = (
            client_stats["days_as_client"] / (client_stats["total_appointments"] - 1)
        ).where(client_stats["total_appointments"] > 1).fillna(0).round(0).astype(int)

        client_stats["returning"] = client_stats["total_appointments"] > 1

        one_time = len(client_stats[~client_stats["returning"]])
        returning = len(client_stats[client_stats["returning"]])
        retention_rate = returning / len(client_stats) * 100 if len(client_stats) > 0 else 0

        is_dark_ret = dark_mode
        cb = "#1e293b" if is_dark_ret else "#f8fafc"
        cborder = "#334155" if is_dark_ret else "#e2e8f0"
        clabel = "#94a3b8" if is_dark_ret else "#64748b"
        cval = "#f1f5f9" if is_dark_ret else "#0f172a"

        st.markdown(f"""
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:0.5rem;margin-bottom:1rem;">
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};font-weight:500;">Total Clients</div>
                <div style="font-size:1.3rem;font-weight:700;color:{cval};">{len(client_stats)}</div>
            </div>
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};font-weight:500;">Returning</div>
                <div style="font-size:1.3rem;font-weight:700;color:#22c55e;">{returning}</div>
            </div>
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};font-weight:500;">One-Time</div>
                <div style="font-size:1.3rem;font-weight:700;color:#f59e0b;">{one_time}</div>
            </div>
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};font-weight:500;">Retention Rate</div>
                <div style="font-size:1.3rem;font-weight:700;color:{cval};">{retention_rate:.0f}%</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.subheader("Top Clients by Lifetime Value")
        top_clients = client_stats.sort_values("lifetime_value", ascending=False).head(10)
        st.bar_chart(top_clients.set_index("client_name")["lifetime_value"])

        st.subheader("All Client Stats")
        display_stats = client_stats.copy()
        display_stats["first_appointment"] = display_stats["first_appointment"].dt.strftime("%Y-%m-%d")
        display_stats["last_appointment"] = display_stats["last_appointment"].dt.strftime("%Y-%m-%d")
        display_stats["returning"] = display_stats["returning"].map({True: "✅ Yes", False: "⬜ No"})
        display_stats["lifetime_value"] = display_stats["lifetime_value"].map("${:,.2f}".format)
        display_stats["avg_days_between"] = display_stats["avg_days_between"].map(
            lambda x: f"{x} days" if x > 0 else "—"
        )
        display_stats.columns = [
            "Client", "Appointments", "First Visit", "Last Visit",
            "Lifetime Value", "Days as Client", "Avg Days Between", "Returning"
        ]
        st.dataframe(display_stats[["Client","Appointments","First Visit","Last Visit",
                                     "Lifetime Value","Avg Days Between","Returning"]],
                     use_container_width=True, hide_index=True)

        st.download_button(
            "⬇️ Download Retention Report CSV",
            data=display_stats.to_csv(index=False).encode("utf-8"),
            file_name="client_retention_report.csv",
            mime="text/csv"
        )


elif menu == "Client Portal":
    st.header("Client Portal Links")
    st.caption("Share a read-only link with a client showing their appointment history and balance.")

    clients = get_clients()
    if not clients:
        st.info("No clients yet.")
    else:
        client_names = {f"{c[1]} ({c[3] or 'No email'})": c[0] for c in clients}
        selected = st.selectbox("Select Client", list(client_names.keys()))
        client_id = client_names[selected]

        # Generate a simple token — base64 of client_id + secret
        token = _make_portal_token(str(client_id))

        app_url = st.secrets.get("APP_URL", "https://your-app.streamlit.app")
        portal_url = f"{app_url}?portal={client_id}&token={token}"

        st.subheader("Portal Link")
        st.code(portal_url)
        st.caption(f"Send this link to your client. It shows their appointments and balance — no login required. Link expires in {PORTAL_TOKEN_DAYS} days.")

        # Copy button via markdown
        st.markdown(f"[📋 Open Portal Preview]({portal_url})")

        # Preview what the client sees
        st.divider()
        st.subheader("Preview — What Your Client Sees")

        df_all = get_all_appointments_dataframe()
        if not df_all.empty:
            client_df = df_all[df_all["client_id"] == client_id].copy()
            client_info = next((c for c in clients if c[0] == client_id), None)

            if client_info:
                st.write(f"**{client_info[1]}**")
                st.write(f"📧 {client_info[3] or '—'}  📞 {client_info[2] or '—'}")

            if client_df.empty:
                st.info("No appointments for this client.")
            else:
                all_paid = get_all_payment_totals()
                client_df["paid"] = client_df["id"].apply(lambda x: all_paid.get(x, 0.0))
                client_df["balance"] = (client_df["fee"].fillna(0).astype(float) - client_df["paid"]).clip(lower=0)
                total_balance = client_df["balance"].sum()

                if total_balance > 0:
                    st.warning(f"Outstanding balance: **${total_balance:,.2f}**")
                else:
                    st.success("Account is paid in full ✅")

                st.dataframe(
                    client_df[["appointment_date", "appointment_time", "signing_type",
                               "location", "fee", "paid", "balance", "status"]].sort_values("appointment_date", ascending=False),
                    use_container_width=True, hide_index=True
                )


elif menu == "View Clients":
    st.header("View Clients")

    clients = get_clients()

    if len(clients) == 0:
        st.warning("No clients found.")
    else:
        for client in clients:
            with st.expander(client[1]):
                st.write(f"**Client ID:** {client[0]}")
                st.write(f"**Phone:** {client[2] or ''}")
                st.write(f"**Email:** {client[3] or ''}")
                st.write(f"**Address:** {client[4] or ''}")
                st.write(f"**How They Heard About You:** {client[5] or ''}")
                st.write(f"**Notes:** {client[6] or ''}")


elif menu == "Edit Client":
    st.header("Edit / Delete Client")

    clients = get_clients()

    if not clients:
        st.info("No clients available.")
    else:
        choices = {f"{client[0]} - {client[1]}": client[0] for client in clients}
        selected = st.selectbox("Select Client", list(choices.keys()))
        client_id = choices[selected]
        client = get_client_by_id(client_id)

        with st.form("edit_client_form"):
            client_name = st.text_input("Client Name", value=client[1] or "")
            phone = st.text_input("Phone", value=client[2] or "")
            email = st.text_input("Email", value=client[3] or "")
            address = st.text_input("Address", value=client[4] or "")
            referral_source = st.selectbox(
                "How did they hear about you?",
                REFERRAL_SOURCES,
                index=REFERRAL_SOURCES.index(client[5]) if client[5] in REFERRAL_SOURCES else 0
            )
            notes = st.text_area("Notes", value=client[6] or "")

            submitted = st.form_submit_button("Update Client")

            if submitted:
                errors = validate_client_inputs(client_name, email)
                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    update_client(client_id, client_name, phone, email, address, referral_source, notes)
                    st.success("Client updated successfully.")

        st.warning("Deleting a client does not delete past appointments. It only removes the client record.")
        if st.button("Delete Client"):
            delete_client(client_id)
            st.success("Client deleted.")
            st.rerun()


elif menu == "Client History":
    st.header("Client History")

    clients = get_clients()
    df = get_all_appointments_dataframe()

    if not clients:
        st.info("No clients available.")
    else:
        choices = {f"{client[0]} - {client[1]}": client[0] for client in clients}
        selected = st.selectbox("Select Client", list(choices.keys()))
        client_id = choices[selected]
        client = get_client_by_id(client_id)

        st.subheader(client[1])
        st.write(f"**Phone:** {client[2] or ''}")
        st.write(f"**Email:** {client[3] or ''}")
        st.write(f"**Address:** {client[4] or ''}")
        st.write(f"**How They Heard About You:** {client[5] or ''}")
        st.write(f"**Notes:** {client[6] or ''}")

        if df.empty:
            st.info("No appointments found.")
        else:
            client_df = df[df["client_id"] == client_id].copy()

            if client_df.empty:
                st.info("No appointment history for this client.")
            else:
                client_df["fee"] = pd.to_numeric(client_df["fee"], errors="coerce").fillna(0)
                client_df["mileage"] = pd.to_numeric(client_df["mileage"], errors="coerce").fillna(0)

                col1, col2, col3 = st.columns([1, 1, 1])
                col1.metric("Appointments", len(client_df))
                col2.metric("Revenue", f"${client_df['fee'].sum():,.2f}")
                col3.metric("Mileage", f"{client_df['mileage'].sum():,.1f} mi")

                st.dataframe(client_df, use_container_width=True)


elif menu == "Add Appointment":
    st.header("Add Appointment")

    # Consume prefill from Quote Generator or Book Similar button
    prefill = st.session_state.pop("prefill_quote", {})
    book_similar_client_id = st.session_state.pop("book_similar_client_id", None)
    if prefill:
        source = "Book Similar" if book_similar_client_id else f"quote for: {prefill.get('client_name', '')}"
        st.info(f"Pre-filled from {source} — review and adjust before saving.")

    clients = get_clients()

    if len(clients) == 0:
        st.warning("Add a client first before creating an appointment.")
    else:
        client_choices = {
            f"{client[1]} - {client[3] or 'No Email'}": client[0]
            for client in clients
        }
        client_choice_keys = list(client_choices.keys())

        # If coming from Book Similar, pre-select that client
        default_client_idx = 0
        if book_similar_client_id:
            for i, (label, cid) in enumerate(client_choices.items()):
                if cid == book_similar_client_id:
                    default_client_idx = i
                    break

        selected_client = st.selectbox("Select Client", client_choice_keys, index=default_client_idx)
        client_id = client_choices[selected_client]
        client = get_client_by_id(client_id)

        client_name = client[1]
        client_phone = client[2] or ""
        client_email = client[3] or ""

        st.info(f"Selected client: {client_name}")

        with st.form("appointment_form"):
            appointment_date = st.date_input("Appointment Date", value=date.today())
            appointment_time = st.time_input("Appointment Time", value=time(9, 0))
            duration_minutes = st.number_input(
                "Duration in Minutes", min_value=15, max_value=480,
                value=int(prefill.get("duration_minutes") or 60), step=15
            )
            travel_buffer_minutes = st.number_input(
                "Travel Buffer Minutes",
                min_value=0,
                max_value=240,
                value=int(settings.get("default_travel_buffer") or 30),
                step=15
            )

            end_time = calculate_end_time(appointment_date, appointment_time, duration_minutes)
            invoice_date = st.date_input("Invoice Date", value=appointment_date)
            payment_due_date = st.date_input("Payment Due Date", value=appointment_date + timedelta(days=7))

            st.caption(f"Estimated end time: {end_time}")

            signing_type = st.selectbox("Signing Type", SIGNING_TYPES,
                index=SIGNING_TYPES.index(prefill["signing_type"]) if prefill.get("signing_type") in SIGNING_TYPES else 0)
            location = st.text_input("Location / Address", value=prefill.get("location") or client[4] or "")
            # Auto-fill fee from per-type defaults; prefill (Quote/Book Similar) overrides
            _type_fees = get_signing_type_fees(settings)
            _default_for_type = _type_fees.get(signing_type, settings.get("default_fee") or 0)
            fee = st.number_input("Fee", min_value=0.00, step=5.00,
                value=float(prefill.get("fee") if prefill.get("fee") is not None else _default_for_type))
            mileage = st.number_input("Mileage", min_value=0.0, step=1.0)
            status = st.selectbox("Status", STATUSES)
            notes = st.text_area("Notes", value=prefill.get("notes") or "")

            allow_buffer_override = st.checkbox("Allow save even if travel buffer warning exists")

            submitted = st.form_submit_button("Save Appointment")

            if submitted:
                errors = validate_appointment_inputs(client_name, client_email, duration_minutes, fee, mileage)
                duplicates = has_duplicate_appointment(client_id, appointment_date, appointment_time)

                if errors:
                    for error in errors:
                        st.error(error)
                elif duplicates:
                    st.error("Possible duplicate appointment detected. This appointment was not saved.")
                    for duplicate in duplicates:
                        st.warning(f"Duplicate with appointment ID {duplicate[0]} - {duplicate[1]} - {duplicate[2]}")
                else:
                    conflicts, buffer_warnings = check_schedule_issues(
                        appointment_date,
                        appointment_time,
                        duration_minutes,
                        travel_buffer_minutes
                    )

                    if conflicts:
                        st.error("Hard scheduling conflict detected. Appointment was not saved.")

                        for conflict in conflicts:
                            st.warning(
                                f"Conflict with {conflict['client']} "
                                f"({conflict['type']}) from {conflict['start']} to {conflict['end']} "
                                f"Status: {conflict['status']} | Location: {conflict['location']}"
                            )

                    elif buffer_warnings and not allow_buffer_override:
                        st.error("Travel buffer warning detected. Appointment was not saved.")

                        for warning in buffer_warnings:
                            st.warning(
                                f"{warning['message']} "
                                f"Gap: {warning['gap']} minutes. "
                                f"Other appointment: {warning['client']} "
                                f"({warning['type']}) from {warning['start']} to {warning['end']} "
                                f"Location: {warning['location']}"
                            )

                        st.info("Check the override box if you still want to save this appointment.")

                    else:
                        add_appointment(
                            client_id,
                            client_name,
                            client_phone,
                            client_email,
                            str(appointment_date),
                            appointment_time.strftime("%H:%M"),
                            duration_minutes,
                            end_time,
                            signing_type,
                            location,
                            fee,
                            mileage,
                            status,
                            notes,
                            str(invoice_date),
                            str(payment_due_date)
                        )

                        if buffer_warnings:
                            st.warning("Appointment saved with travel buffer warning.")

                        st.success("Appointment saved successfully!")


    # Save current form values as a template for future use
    if st.session_state.get("save_as_template_trigger"):
        tmpl_data = st.session_state.pop("save_as_template_trigger")
        with st.form("quick_save_template"):
            st.subheader("Save as Template")
            tmpl_name_input = st.text_input("Template Name", value=tmpl_data.get("signing_type", ""))
            if st.form_submit_button("Save"):
                save_template(
                    tmpl_name_input,
                    tmpl_data.get("signing_type", ""),
                    tmpl_data.get("location", ""),
                    tmpl_data.get("fee", 0),
                    0, 60, "", "", ""
                )
                st.success(f"Template '{tmpl_name_input}' saved!")
                st.rerun()


elif menu == "Recurring Scheduler":
    st.header("Recurring Appointment Scheduler")
    st.caption("Create multiple appointments at regular intervals for repeat clients — title companies, law firms, etc.")

    clients = get_clients()
    if not clients:
        st.info("Add a client first.")
    else:
        with st.form("recurring_form"):
            client_choices = {f"{c[1]} — {c[3] or 'No email'}": c[0] for c in clients}
            selected_client_label = st.selectbox("Client", list(client_choices.keys()))
            client_id = client_choices[selected_client_label]
            client_obj = next(c for c in clients if c[0] == client_id)

            signing_type = st.selectbox("Signing Type", SIGNING_TYPES)
            location = st.text_input("Location", value=client_obj[4] or "")

            _type_fees = get_signing_type_fees(settings)
            fee = st.number_input("Fee per Appointment", min_value=0.0, step=5.0,
                value=float(_type_fees.get(signing_type, settings.get("default_fee") or 0)))
            mileage = st.number_input("Mileage per Appointment", min_value=0.0, step=1.0)
            duration = st.number_input("Duration (minutes)", min_value=15, max_value=480, value=60, step=15)

            start_date = st.date_input("First Appointment Date", value=date.today() + timedelta(days=1))
            appt_time = st.time_input("Appointment Time", value=time(10, 0))
            recurrence = st.selectbox("Repeat Every", ["Weekly", "Biweekly (every 2 weeks)", "Monthly", "Custom (days)"])
            custom_days = st.number_input("Custom interval (days)", min_value=1, value=14,
                help="Only used if 'Custom' is selected above") if "Custom" in recurrence else None
            num_appointments = st.number_input("Number of Appointments to Create", min_value=2, max_value=52, value=4, step=1)
            notes = st.text_area("Notes (applied to all)")

            submitted = st.form_submit_button("📅 Create Recurring Appointments", type="primary")

        if submitted:
            interval_map = {
                "Weekly": 7,
                "Biweekly (every 2 weeks)": 14,
                "Monthly": 30,
            }
            interval_days = int(custom_days or 0) if "Custom" in recurrence else interval_map.get(recurrence, 7)

            created = []
            current_date = start_date
            time_str = appt_time.strftime("%H:%M")
            end_time = calculate_end_time(current_date, appt_time, duration)

            for i in range(int(num_appointments)):
                appt_id = add_appointment(
                    client_id, client_obj[1], client_obj[2], client_obj[3],
                    str(current_date), time_str, duration, end_time,
                    signing_type, location, fee, mileage, "Scheduled", notes
                )
                created.append((current_date, appt_id))
                if recurrence == "Monthly":
                    # Advance by one calendar month
                    month = current_date.month + 1
                    year = current_date.year + (month - 1) // 12
                    month = ((month - 1) % 12) + 1
                    import calendar as _cal
                    day = min(current_date.day, _cal.monthrange(year, month)[1])
                    current_date = current_date.replace(year=year, month=month, day=day)
                else:
                    current_date = current_date + timedelta(days=interval_days)

            st.success(f"✅ Created {len(created)} appointments!")
            for appt_date_val, appt_id in created:
                st.write(f"• {appt_date_val.strftime('%A, %B %d, %Y')} — ID {appt_id}")


elif menu == "View Appointments":
    st.header("View Appointments")

    col1, col2 = st.columns(2)
    with col1:
        search_text = st.text_input("Search")
    with col2:
        status_filter = st.selectbox("Filter by Status", ["All"] + STATUSES)

    appointments = get_appointments(search_text, status_filter)

    if len(appointments) == 0:
        st.warning("No appointments found.")
    else:
        # ── Bulk Status Update ────────────────────────────────────────────
        with st.expander("⚡ Bulk Status Update", expanded=False):
            st.caption("Select appointments and update all at once.")
            bulk_ids = st.multiselect(
                "Select Appointments",
                options=[a[0] for a in appointments],
                format_func=lambda x: next(
                    f"{a[1]} — {a[2]} — {a[8]}" for a in appointments if a[0] == x
                )
            )
            bulk_new_status = st.selectbox("New Status for Selected", STATUSES, key="bulk_status")
            if st.button("✅ Apply to Selected", type="primary", disabled=len(bulk_ids) == 0):
                for bid in bulk_ids:
                    update_status(bid, bulk_new_status)
                clear_all_caches()
                st.success(f"Updated {len(bulk_ids)} appointment(s) to '{bulk_new_status}'")
                st.rerun()

        # Single bulk query instead of per-row DB hit
        all_paid = get_all_payment_totals()
        for appt in appointments:
            appointment_id = appt[0]
            client_name = appt[1]
            appointment_date = appt[2]
            appointment_time = appt[3] or "No Time"
            signing_type = appt[4]
            location = appt[5]
            fee = appt[6] or 0
            mileage = appt[7] or 0
            status = appt[8]
            paid = all_paid.get(appointment_id, 0.0)
            balance = float(fee) - paid

            with st.expander(f"{client_name} - {appointment_date} at {appointment_time} - {status}"):
                st.write(f"**Appointment ID:** {appointment_id}")
                st.write(f"**Signing Type:** {signing_type}")
                st.write(f"**Location:** {location}")
                st.write(f"**Fee:** ${fee:,.2f}")
                st.write(f"**Paid:** ${paid:,.2f}")
                st.write(f"**Balance:** ${balance:,.2f}")
                st.write(f"**Mileage:** {mileage} miles")

                appt_detail = get_appointment_by_id(appointment_id)
                client_notes_val = (appt_detail[18] or "") if appt_detail and len(appt_detail) > 18 else ""
                internal_notes_val = (appt_detail[19] or "") if appt_detail and len(appt_detail) > 19 else ""
                # Build row dict for gcal URL builder
                appt_row = {
                    "appointment_date": appointment_date,
                    "appointment_time": appointment_time,
                    "end_time": appt_detail[8] if appt_detail else "",
                    "signing_type": signing_type,
                    "client_name": client_name,
                    "client_phone": appt_detail[3] if appt_detail else "",
                    "fee": fee,
                    "location": location,
                }

                if client_notes_val:
                    st.info(f"📋 **Client Notes:** {client_notes_val}")
                if internal_notes_val:
                    st.warning(f"🔒 **Internal Notes:** {internal_notes_val}")

                # 2×2 grid on mobile, 4-col on desktop (CSS handles collapse)
                col1, col2, col3, col4 = st.columns([1, 1, 1, 1])

                with col1:
                    if st.button("✅ Mark Paid", key=f"paid_{appointment_id}", use_container_width=True):
                        update_status(appointment_id, "Paid")
                        st.success("Marked as Paid")
                        st.rerun()

                with col2:
                    if st.button("🏁 Completed", key=f"completed_{appointment_id}", use_container_width=True):
                        update_status(appointment_id, "Completed")
                        if float(fee) > 0 and paid < float(fee):
                            st.session_state[f"nudge_payment_{appointment_id}"] = True
                        st.rerun()

                with col3:
                    if st.button("💰 Awaiting Pmt", key=f"awaiting_{appointment_id}", use_container_width=True):
                        update_status(appointment_id, "Awaiting Payment")
                        st.success("Status updated to Awaiting Payment")
                        st.rerun()

                with col4:
                    if st.button("📋 Book Similar", key=f"book_similar_{appointment_id}", use_container_width=True):
                        st.session_state["prefill_quote"] = {
                            "signing_type": signing_type,
                            "fee": float(fee),
                            "notes": "",
                            "client_id": None,
                            "location": location,
                            "duration_minutes": appt_detail[7] if appt_detail else 60,
                        }
                        st.session_state["book_similar_client_id"] = client_id
                        st.session_state.selected_menu = "Add Appointment"
                        st.rerun()

                # Payment nudge — shown after Completed is clicked when fee is outstanding
                if st.session_state.get(f"nudge_payment_{appointment_id}"):
                    st.warning(
                        f"This appointment has an outstanding balance of **${float(fee) - paid:,.2f}**. "
                        "Mark as Awaiting Payment?"
                    )
                    nc1, nc2 = st.columns(2)
                    with nc1:
                        if st.button("Yes — Awaiting Payment", key=f"nudge_yes_{appointment_id}"):
                            update_status(appointment_id, "Awaiting Payment")
                            del st.session_state[f"nudge_payment_{appointment_id}"]
                            st.rerun()
                    with nc2:
                        if st.button("No — Keep as Completed", key=f"nudge_no_{appointment_id}"):
                            del st.session_state[f"nudge_payment_{appointment_id}"]
                            st.rerun()


elif menu == "Edit Appointment":
    st.header("Edit Appointment")

    appointments = get_appointments()

    if len(appointments) == 0:
        st.warning("No appointments available to edit.")
    else:
        appointment_choices = {
            f"{appt[0]} - {appt[1]} - {appt[2]} {appt[3] or ''}": appt[0]
            for appt in appointments
        }

        selected = st.selectbox("Select Appointment", list(appointment_choices.keys()))
        appointment_id = appointment_choices[selected]
        appointment = get_appointment_by_id(appointment_id)

        if appointment:
            existing_date = date.fromisoformat(appointment[5])
            existing_time = datetime.strptime(appointment[6] or "09:00", "%H:%M").time()
            existing_duration = int(appointment[7] or 60)

            with st.form("edit_appointment_form"):
                client_name = st.text_input("Client Name", value=appointment[2] or "")
                client_phone = st.text_input("Client Phone", value=appointment[3] or "")
                client_email = st.text_input("Client Email", value=appointment[4] or "")

                appointment_date = st.date_input("Appointment Date", value=existing_date)
                appointment_time = st.time_input("Appointment Time", value=existing_time)
                duration_minutes = st.number_input(
                    "Duration in Minutes",
                    min_value=15,
                    max_value=480,
                    value=existing_duration,
                    step=15
                )

                travel_buffer_minutes = st.number_input(
                    "Travel Buffer Minutes",
                    min_value=0,
                    max_value=240,
                    value=int(settings.get("default_travel_buffer") or 30),
                    step=15
                )

                end_time = calculate_end_time(appointment_date, appointment_time, duration_minutes)

                existing_invoice_date = date.fromisoformat(appointment[15]) if len(appointment) > 15 and appointment[15] else appointment_date
                existing_due_date = date.fromisoformat(appointment[16]) if len(appointment) > 16 and appointment[16] else appointment_date + timedelta(days=7)

                invoice_date = st.date_input("Invoice Date", value=existing_invoice_date)
                payment_due_date = st.date_input("Payment Due Date", value=existing_due_date)

                st.caption(f"Estimated end time: {end_time}")

                signing_type = st.selectbox(
                    "Signing Type",
                    SIGNING_TYPES,
                    index=SIGNING_TYPES.index(appointment[9]) if appointment[9] in SIGNING_TYPES else 0
                )

                location = st.text_input("Location / Address", value=appointment[10] or "")
                fee = st.number_input("Fee", min_value=0.00, step=5.00, value=float(appointment[11] or 0))
                mileage = st.number_input("Mileage", min_value=0.0, step=1.0, value=float(appointment[12] or 0))

                status = st.selectbox(
                    "Status",
                    STATUSES,
                    index=STATUSES.index(appointment[13]) if appointment[13] in STATUSES else 0
                )

                notes = st.text_area("Notes", value=appointment[14] or "")

                st.divider()
                st.caption("These fields are visible to you only and are not included on invoices or emails.")
                col_cn, col_in = st.columns(2)
                with col_cn:
                    client_notes = st.text_area(
                        "📋 Client-Facing Notes",
                        value=appointment[18] or "" if len(appointment) > 18 else "",
                        help="Instructions or reminders to share with the client (e.g. 'Bring two forms of ID')"
                    )
                with col_in:
                    internal_notes = st.text_area(
                        "🔒 Internal Notes",
                        value=appointment[19] or "" if len(appointment) > 19 else "",
                        help="Private notes for your reference only (e.g. 'Slow signer — budget 90 min')"
                    )

                allow_buffer_override = st.checkbox("Allow update even if travel buffer warning exists")

                submitted = st.form_submit_button("Update Appointment")

                if submitted:
                    errors = validate_appointment_inputs(client_name, client_email, duration_minutes, fee, mileage)
                    duplicates = has_duplicate_appointment(appointment[1], appointment_date, appointment_time, exclude_appointment_id=appointment_id)

                    if errors:
                        for error in errors:
                            st.error(error)
                    elif duplicates:
                        st.error("Possible duplicate appointment detected. This appointment was not updated.")
                        for duplicate in duplicates:
                            st.warning(f"Duplicate with appointment ID {duplicate[0]} - {duplicate[1]} - {duplicate[2]}")
                    else:
                        conflicts, buffer_warnings = check_schedule_issues(
                            appointment_date,
                            appointment_time,
                            duration_minutes,
                            travel_buffer_minutes,
                            exclude_appointment_id=appointment_id
                        )

                        if conflicts:
                            st.error("Hard scheduling conflict detected. Appointment was not updated.")

                            for conflict in conflicts:
                                st.warning(
                                    f"Conflict with {conflict['client']} "
                                    f"({conflict['type']}) from {conflict['start']} to {conflict['end']} "
                                    f"Status: {conflict['status']} | Location: {conflict['location']}"
                                )

                        elif buffer_warnings and not allow_buffer_override:
                            st.error("Travel buffer warning detected. Appointment was not updated.")

                            for warning in buffer_warnings:
                                st.warning(
                                    f"{warning['message']} "
                                    f"Gap: {warning['gap']} minutes. "
                                    f"Other appointment: {warning['client']} "
                                    f"({warning['type']}) from {warning['start']} to {warning['end']} "
                                    f"Location: {warning['location']}"
                                )

                            st.info("Check the override box if you still want to update this appointment.")

                        else:
                            update_appointment(
                                appointment_id,
                                client_name,
                                client_phone,
                                client_email,
                                str(appointment_date),
                                appointment_time.strftime("%H:%M"),
                                duration_minutes,
                                end_time,
                                signing_type,
                                location,
                                fee,
                                mileage,
                                status,
                                notes,
                                client_notes=client_notes,
                                internal_notes=internal_notes
                            )

                            if buffer_warnings:
                                st.warning("Appointment updated with travel buffer warning.")

                            st.success("Appointment updated successfully!")


elif menu == "Delete Appointment":
    st.header("Delete Appointment")

    appointments = get_appointments()

    if len(appointments) == 0:
        st.warning("No appointments available to delete.")
    else:
        appointment_choices = {
            f"{appt[0]} - {appt[1]} - {appt[2]} {appt[3] or ''} - {appt[8]}": appt[0]
            for appt in appointments
        }

        selected = st.selectbox("Select Appointment to Delete", list(appointment_choices.keys()))
        appointment_id = appointment_choices[selected]

        st.warning("This will permanently delete the selected appointment, checklist, payments, and attachment records.")

        if st.button("Delete Appointment"):
            delete_appointment(appointment_id)
            st.success("Appointment deleted successfully!")
            st.rerun()


elif menu == "Job Checklist":
    st.header("Job Checklist")

    df = get_all_appointments_dataframe()
    appointment_id, row = appointment_selector(df)

    if appointment_id is None:
        st.info("No appointments available.")
    else:
        st.write(f"**Client:** {row['client_name']}")
        st.write(f"**Appointment:** {row['appointment_date']} {row['appointment_time']} - {row['signing_type']}")

        checklist_items = get_checklist(appointment_id)
        fee_val = float(row.get("fee") or 0)
        current_status = row.get("status", "")

        for item in checklist_items:
            checklist_id, item_name, completed = item
            new_value = st.checkbox(item_name, value=bool(completed), key=f"check_{checklist_id}")
            if new_value != bool(completed):
                update_checklist_item(checklist_id, new_value)
                # Auto-advance status when key checklist items are ticked
                if new_value:
                    if item_name == "Signing completed" and current_status == "Scheduled":
                        update_status(appointment_id, "Completed")
                        st.toast("✅ Status updated to Completed", icon="✅")
                    elif item_name == "Invoice sent" and current_status in ["Completed", "Scheduled"]:
                        if fee_val > 0:
                            update_status(appointment_id, "Awaiting Payment")
                            st.toast("💰 Status updated to Awaiting Payment", icon="💰")
                    elif item_name == "Payment received" and current_status != "Paid":
                        update_status(appointment_id, "Paid")
                        st.toast("🎉 Status updated to Paid", icon="🎉")
                st.rerun()


elif menu == "Payment Tracking":
    st.header("Payment Tracking")

    df = get_all_appointments_dataframe()

    if df.empty:
        st.info("No appointments available.")
    else:
        appointment_id, row = appointment_selector(df)

        st.subheader("Selected Appointment")
        st.write(f"**Client:** {row['client_name']}")
        st.write(f"**Fee:** ${float(row['fee'] or 0):,.2f}")

        paid = get_payment_total_for_appointment(appointment_id)
        balance = float(row["fee"] or 0) - paid

        col1, col2, col3 = st.columns([1, 1, 1])
        col1.metric("Billed", f"${float(row['fee'] or 0):,.2f}")
        col2.metric("Paid", f"${paid:,.2f}")
        col3.metric("Balance", f"${balance:,.2f}")

        with st.form("payment_form"):
            payment_date = st.date_input("Payment Date", value=date.today())
            amount_paid = st.number_input("Amount Paid", min_value=0.00, step=5.00)
            payment_method = st.selectbox("Payment Method", PAYMENT_METHODS)
            payment_notes = st.text_area("Payment Notes")

            submitted = st.form_submit_button("Save Payment")

            send_receipt = st.checkbox("Send payment receipt to client", value=bool(row.get("client_email")))

            if submitted:
                add_payment(appointment_id, str(payment_date), amount_paid, payment_method, payment_notes)
                st.success("Payment saved.")

                if send_receipt and row.get("client_email"):
                    total_paid_now = get_payment_total_for_appointment(appointment_id)
                    remaining = max(float(row.get("fee") or 0) - total_paid_now, 0)
                    receipt_body = (
                        f"Hi {row.get('client_name','')},\n\n"
                        f"Thank you — we've received your payment of ${float(amount_paid):,.2f} "
                        f"on {payment_date} via {payment_method}.\n\n"
                        f"Appointment: {row.get('signing_type','')} on {row.get('appointment_date','')}\n"
                        f"Total Fee:   ${float(row.get('fee') or 0):,.2f}\n"
                        f"Amount Paid: ${float(amount_paid):,.2f}\n"
                        f"Total Paid:  ${total_paid_now:,.2f}\n"
                        f"Balance Due: ${remaining:,.2f}\n\n"
                        f"{'Your account is now paid in full. Thank you!' if remaining == 0 else 'Please contact us if you have questions about your balance.'}\n\n"
                        f"— {settings.get('business_name','Your Notary')}\n"
                        f"{settings.get('business_phone','')}\n"
                        f"{settings.get('business_email','')}"
                    )
                    with st.spinner("Sending receipt..."):
                        ok, err = send_email(
                            row["client_email"],
                            f"Payment Receipt — {settings.get('business_name','')}",
                            receipt_body,
                            settings=settings
                        )
                    if ok:
                        st.success(f"Receipt sent to {row['client_email']} ✅")
                    else:
                        st.warning(f"Payment saved but receipt failed: {err}")

                st.rerun()

        st.divider()
        st.subheader("Payment History")

        payments_df = get_payments_dataframe()

        if payments_df.empty:
            st.info("No payments recorded.")
        else:
            st.dataframe(payments_df, use_container_width=True)



elif menu == "Invoice Status":
    st.header("Invoice Status")

    df = get_all_appointments_dataframe()

    if df.empty:
        st.info("No invoices available.")
    else:
        invoice_df = get_invoice_status_dataframe(df)

        status_filter = st.selectbox(
            "Invoice Status Filter",
            ["All", "Paid", "Unpaid", "Partially Paid", "Overdue", "No Charge"]
        )

        if status_filter != "All":
            invoice_df = invoice_df[invoice_df["invoice_status"] == status_filter]

        col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
        col1.metric("Count", len(invoice_df))
        col2.metric("Billed", f"${invoice_df['fee'].sum():,.2f}")
        col3.metric("Paid", f"${invoice_df['paid_amount'].sum():,.2f}")
        col4.metric("Balance", f"${invoice_df['balance_due'].sum():,.2f}")

        display_cols = [
            "id", "client_name", "appointment_date", "invoice_date", "payment_due_date",
            "signing_type", "fee", "paid_amount", "balance_due", "invoice_status", "status"
        ]
        st.dataframe(invoice_df[[c for c in display_cols if c in invoice_df.columns]], use_container_width=True)

        st.download_button(
            "Download Invoice Status CSV",
            data=invoice_df.to_csv(index=False).encode("utf-8"),
            file_name="invoice_status_report.csv",
            mime="text/csv"
        )

        # ── Invoice Aging Report ──────────────────────────────────────────
        st.divider()
        st.subheader("📊 Invoice Aging Report")
        st.caption("Unpaid and overdue invoices grouped by how long they've been outstanding.")

        unpaid_df = invoice_df[invoice_df["invoice_status"].isin(["Unpaid", "Overdue", "Partially Paid"])].copy()

        if unpaid_df.empty:
            st.success("🎉 No outstanding invoices!")
        else:
            today_ts = pd.Timestamp(date.today())
            unpaid_df["payment_due_date"] = pd.to_datetime(unpaid_df["payment_due_date"], errors="coerce")
            unpaid_df["days_overdue"] = (today_ts - unpaid_df["payment_due_date"]).dt.days.fillna(0).astype(int)

            def age_bucket(days):
                if days <= 0: return "Current"
                elif days <= 30: return "1-30 days"
                elif days <= 60: return "31-60 days"
                elif days <= 90: return "61-90 days"
                else: return "90+ days"

            unpaid_df["age_bucket"] = unpaid_df["days_overdue"].apply(age_bucket)
            bucket_order = ["Current", "1-30 days", "31-60 days", "61-90 days", "90+ days"]
            age_colors = {"Current": "#22c55e", "1-30 days": "#f59e0b",
                          "31-60 days": "#f97316", "61-90 days": "#ef4444", "90+ days": "#7f1d1d"}

            aging_summary = unpaid_df.groupby("age_bucket").agg(
                invoices=("id", "count"), balance=("balance_due", "sum")
            ).reindex(bucket_order).dropna()

            is_dark_ag = dark_mode
            cb = "#1e293b" if is_dark_ag else "#f8fafc"
            cborder = "#334155" if is_dark_ag else "#e2e8f0"
            clabel = "#94a3b8" if is_dark_ag else "#64748b"

            ag_cols = st.columns(min(len(aging_summary), 5))
            for i, (bucket, ag_row) in enumerate(aging_summary.iterrows()):
                color = age_colors.get(bucket, "#64748b")
                with ag_cols[i % len(ag_cols)]:
                    bal = float(ag_row["balance"])
                    inv = int(ag_row["invoices"])
                    st.markdown(
                        f'<div style="background:{cb};border:1px solid {cborder};border-radius:10px;'
                        f'padding:0.6rem;border-left:4px solid {color};">'
                        f'<div style="font-size:0.72rem;color:{clabel};">{bucket}</div>'
                        f'<div style="font-size:1.1rem;font-weight:700;color:{color};">'
                        + "${:,.2f}".format(bal) +
                        f'</div><div style="font-size:0.75rem;color:{clabel};">{inv} invoice(s)</div></div>',
                        unsafe_allow_html=True
                    )

            aging_detail = unpaid_df[[
                "client_name", "appointment_date", "payment_due_date",
                "days_overdue", "age_bucket", "balance_due", "invoice_status"
            ]].sort_values("days_overdue", ascending=False).copy()
            aging_detail.columns = ["Client","Appt Date","Due Date","Days Overdue","Age","Balance","Status"]
            aging_detail["Balance"] = aging_detail["Balance"].map("${:,.2f}".format)
            st.dataframe(aging_detail, use_container_width=True, hide_index=True)

            st.download_button(
                "⬇️ Download Aging Report CSV",
                data=unpaid_df.to_csv(index=False).encode("utf-8"),
                file_name="invoice_aging_report.csv", mime="text/csv"
            )


elif menu == "Follow-Up Tracker":
    st.header("Follow-Up Tracker")

    df = get_all_appointments_dataframe()

    if df.empty:
        st.info("No appointments available.")
    else:
        appointment_id, row = appointment_selector(df)

        st.subheader("Add Follow-Up")
        with st.form("followup_form"):
            followup_date = st.date_input("Follow-Up Date", value=date.today())
            followup_type = st.selectbox("Follow-Up Type", ["Call", "Email", "Text", "Voicemail", "Review Request", "Payment Reminder", "Other"])
            outcome = st.selectbox("Outcome", ["Pending", "Completed", "No Answer", "Left Message", "Client Responded", "Payment Received", "Review Requested", "Other"])
            followup_notes = st.text_area("Follow-Up Notes")
            completed = st.checkbox("Completed")

            submitted = st.form_submit_button("Save Follow-Up")

            if submitted:
                add_followup(appointment_id, str(followup_date), followup_type, outcome, followup_notes, completed)
                st.success("Follow-up saved.")
                st.rerun()

        st.divider()
        st.subheader("Follow-Up History")

        followups_df = get_followups_dataframe()

        if followups_df.empty:
            st.info("No follow-ups recorded.")
        else:
            st.dataframe(followups_df, use_container_width=True)


elif menu == "Referral Analytics":
    st.header("Referral Analytics")

    clients = get_clients()
    df = get_all_appointments_dataframe()

    if not clients:
        st.info("No clients available.")
    else:
        clients_df = pd.DataFrame(clients, columns=["id", "client_name", "phone", "email", "address", "referral_source", "notes"])

        st.subheader("Leads by Source")
        referral_counts = clients_df["referral_source"].fillna("Unknown").replace("", "Unknown").value_counts()
        st.bar_chart(referral_counts)

        if not df.empty:
            merged = df.merge(
                clients_df[["id", "referral_source"]],
                left_on="client_id",
                right_on="id",
                how="left",
                suffixes=("", "_client")
            )
            merged["fee"] = pd.to_numeric(merged["fee"], errors="coerce").fillna(0)
            merged["referral_source"] = merged["referral_source"].fillna("Unknown").replace("", "Unknown")

            st.subheader("Revenue by Referral Source")
            st.bar_chart(merged.groupby("referral_source")["fee"].sum().sort_values(ascending=False))

            st.subheader("Referral Detail")
            st.dataframe(
                merged[["client_name", "appointment_date", "signing_type", "fee", "referral_source"]],
                use_container_width=True
            )
        else:
            st.info("No appointment revenue yet.")


elif menu == "Quote Generator":
    st.header("Quote / Estimate Generator")

    client_name = st.text_input("Client Name")
    signing_type = st.selectbox("Service Type", SIGNING_TYPES)
    base_fee = st.number_input("Base Fee", min_value=0.00, step=5.00, value=float(settings.get("default_fee") or 0))
    travel_fee = st.number_input("Travel Fee", min_value=0.00, step=5.00)
    after_hours_fee = st.number_input("After-Hours Fee", min_value=0.00, step=5.00)
    extra_fee = st.number_input("Additional Fee", min_value=0.00, step=5.00)
    quote_notes = st.text_area("Quote Notes")

    quote = quote_text(settings, client_name, signing_type, base_fee, travel_fee, after_hours_fee, extra_fee, quote_notes)
    total_fee = float(base_fee or 0) + float(travel_fee or 0) + float(after_hours_fee or 0) + float(extra_fee or 0)

    st.text_area("Quote Preview", value=quote, height=400)

    col1, col2 = st.columns(2)

    with col1:
        st.download_button(
            "Download Quote Text",
            data=quote,
            file_name="quote_estimate.txt",
            mime="text/plain"
        )

    with col2:
        if st.button("📅 Save as Appointment", help="Pre-fill a new appointment using this quote"):
            st.session_state["prefill_quote"] = {
                "client_name": client_name,
                "signing_type": signing_type,
                "fee": total_fee,
                "notes": quote_notes,
            }
            st.session_state.selected_menu = "Add Appointment"
            st.info("Quote saved — switching to Add Appointment with fields pre-filled.")
            st.rerun()


elif menu == "Document Attachments":
    st.header("Document Attachments")

    df = get_all_appointments_dataframe()

    if df.empty:
        st.info("No appointments available.")
    else:
        appointment_id, row = appointment_selector(df)

        attach_tab1, attach_tab2 = st.tabs(["📁 Upload File", "📷 Take Photo"])

        with attach_tab1:
            uploaded_file = st.file_uploader("Upload File",
                type=["pdf","jpg","jpeg","png","doc","docx","txt","heic"])
        with attach_tab2:
            uploaded_file_cam = st.camera_input("Take a photo of the signed document")
            if uploaded_file_cam:
                uploaded_file = uploaded_file_cam

        attachment_notes = st.text_area("Attachment Notes")

        if st.button("Save Attachment"):
            if uploaded_file is None:
                st.error("Please choose a file or take a photo first.")
            else:
                file_bytes = uploaded_file.getbuffer()
                # Try Supabase Storage first, fall back to local filesystem
                storage_url = upload_to_supabase_storage(file_bytes, uploaded_file.name, appointment_id)
                if storage_url:
                    file_path = f"supabase://attachments/{appointment_id}/{uploaded_file.name}"
                    st.success("Uploaded to Supabase Storage ☁️")
                else:
                    appointment_folder = os.path.join(UPLOAD_FOLDER, str(appointment_id))
                    os.makedirs(appointment_folder, exist_ok=True)
                    file_path = os.path.join(appointment_folder, uploaded_file.name)
                    with open(file_path, "wb") as f:
                        f.write(file_bytes)
                    st.success("Attachment saved locally.")

                add_attachment(appointment_id, uploaded_file.name, file_path, attachment_notes)

        st.divider()
        st.subheader("Attachments for Selected Appointment")
        attachments_df = get_attachments(appointment_id)

        if attachments_df.empty:
            st.info("No attachments for this appointment.")
        else:
            for _, attachment in attachments_df.iterrows():
                with st.container():
                    st.write(f"**{attachment['file_name']}**")
                    st.write(f"Uploaded: {attachment['uploaded_at']}")
                    if attachment.get("notes"):
                        st.write(f"Notes: {attachment['notes']}")

                    fp = attachment["file_path"] or ""
                    if fp.startswith("supabase://"):
                        signed_url = get_supabase_attachment_url(fp)
                        if signed_url:
                            st.markdown(f"[⬇️ Download from Cloud]({signed_url})")
                        else:
                            st.warning("Could not generate download link.")
                    elif os.path.exists(fp):
                        with open(fp, "rb") as f:
                            st.download_button(
                                "⬇️ Download",
                                data=f.read(),
                                file_name=attachment["file_name"],
                                key=f"download_{attachment['id']}"
                            )
                    else:
                        st.caption("File not available locally.")
                    st.divider()


elif menu == "Quote Tracking":
    st.header("Quote Tracking")
    st.caption("Track which quotes were sent, accepted, declined, or converted to appointments.")

    quotes_df = get_quotes_dataframe()

    if quotes_df.empty:
        st.info("No quotes logged yet. Send a quote from the Quote Generator and click 'Log Quote as Sent'.")
    else:
        quotes_df["fee"] = pd.to_numeric(quotes_df["fee"], errors="coerce").fillna(0)

        # Summary
        status_counts = quotes_df["status"].value_counts()
        total_quoted = quotes_df["fee"].sum()
        accepted_val = quotes_df[quotes_df["status"] == "Accepted"]["fee"].sum()
        conversion = len(quotes_df[quotes_df["status"].isin(["Accepted","Converted"])]) / len(quotes_df) * 100

        is_dark_qt = dark_mode
        cb = "#1e293b" if is_dark_qt else "#f8fafc"
        cborder = "#334155" if is_dark_qt else "#e2e8f0"
        clabel = "#94a3b8" if is_dark_qt else "#64748b"

        st.markdown(f"""
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:0.5rem;margin-bottom:1rem;">
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};">Total Quoted</div>
                <div style="font-size:1.2rem;font-weight:700;color:#3b82f6;">${total_quoted:,.2f}</div>
            </div>
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};">Accepted Value</div>
                <div style="font-size:1.2rem;font-weight:700;color:#22c55e;">${accepted_val:,.2f}</div>
            </div>
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};">Conversion Rate</div>
                <div style="font-size:1.2rem;font-weight:700;color:#f59e0b;">{conversion:.0f}%</div>
            </div>
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};">Total Quotes</div>
                <div style="font-size:1.2rem;font-weight:700;">{len(quotes_df)}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        status_filter_qt = st.selectbox("Filter by Status", ["All"] + QUOTE_STATUSES)
        filtered_qt = quotes_df if status_filter_qt == "All" else quotes_df[quotes_df["status"] == status_filter_qt]

        for _, qrow in filtered_qt.iterrows():
            status_color = {"Sent":"#3b82f6","Accepted":"#22c55e","Declined":"#ef4444",
                           "Expired":"#94a3b8","Converted":"#8b5cf6"}.get(qrow["status"],"#64748b")
            with st.expander(
                f"{qrow['created_date']} — {qrow['client_name']} — "
                f"{qrow['signing_type']} — ${float(qrow['fee']):,.2f} — {qrow['status']}"
            ):
                new_status = st.selectbox("Update Status", QUOTE_STATUSES,
                    index=QUOTE_STATUSES.index(qrow["status"]) if qrow["status"] in QUOTE_STATUSES else 0,
                    key=f"qt_status_{qrow['id']}")
                if st.button("Save Status", key=f"qt_save_{qrow['id']}"):
                    update_quote_status(int(qrow["id"]), new_status)
                    st.success("Status updated!")
                    st.rerun()
                if qrow.get("notes"):
                    st.caption(f"Notes: {qrow['notes']}")


elif menu == "Reports / Export":
    st.header("Reports / Export")

    df = get_all_appointments_dataframe()

    if df.empty:
        st.info("No appointment data available.")
    else:
        df["fee"] = pd.to_numeric(df["fee"], errors="coerce").fillna(0)
        df["mileage"] = pd.to_numeric(df["mileage"], errors="coerce").fillna(0)
        df["appointment_date"] = pd.to_datetime(df["appointment_date"], errors="coerce")

        col1, col2, col3 = st.columns([1, 1, 1])

        with col1:
            start_date = st.date_input("Start Date", value=df["appointment_date"].min().date())

        with col2:
            end_date = st.date_input("End Date", value=df["appointment_date"].max().date())

        with col3:
            status_filter = st.selectbox("Status", ["All"] + STATUSES)

        filtered_df = df[
            (df["appointment_date"].dt.date >= start_date)
            & (df["appointment_date"].dt.date <= end_date)
        ]

        if status_filter != "All":
            filtered_df = filtered_df[filtered_df["status"] == status_filter]

        col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
        col1.metric("Appointments", len(filtered_df))
        col2.metric("Fees", f"${filtered_df['fee'].sum():,.2f}")
        col3.metric("Mileage", f"{filtered_df['mileage'].sum():,.1f} mi")
        col4.metric("Paid Total", f"${filtered_df[filtered_df['status'] == 'Paid']['fee'].sum():,.2f}")

        st.dataframe(filtered_df, use_container_width=True)

        csv_data = filtered_df.to_csv(index=False).encode("utf-8")

        st.download_button(
            label="Download CSV Report",
            data=csv_data,
            file_name="notary_report.csv",
            mime="text/csv"
        )


elif menu == "Mileage / Tax Report":
    st.header("Mileage / Tax Report")

    df = get_all_appointments_dataframe()

    if df.empty:
        st.info("No mileage data available.")
    else:
        df["mileage"] = pd.to_numeric(df["mileage"], errors="coerce").fillna(0)
        df["appointment_date"] = pd.to_datetime(df["appointment_date"], errors="coerce")
        df["month"] = df["appointment_date"].dt.strftime("%Y-%m")

        mileage_rate = st.number_input(
            "Mileage Rate",
            min_value=0.0,
            value=float(settings.get("default_mileage_rate") or 0.67),
            step=0.01
        )

        total_miles = df["mileage"].sum()
        deduction_estimate = total_miles * mileage_rate

        col1, col2 = st.columns([1, 1])
        col1.metric("Total Miles", f"{total_miles:,.1f}")
        col2.metric("Est. Deduction", f"${deduction_estimate:,.2f}")

        monthly_mileage = df.groupby("month")["mileage"].sum()
        st.bar_chart(monthly_mileage)

        mileage_report = df[
            [
                "appointment_date",
                "client_name",
                "signing_type",
                "location",
                "mileage",
                "status"
            ]
        ]

        st.dataframe(mileage_report, use_container_width=True)

        st.download_button(
            "Download Mileage CSV",
            data=mileage_report.to_csv(index=False).encode("utf-8"),
            file_name="mileage_report.csv",
            mime="text/csv"
        )

        # ── Schedule C / Tax Summary Export ──────────────────────────────
        st.divider()
        st.subheader("📊 Tax Summary Export (Schedule C)")

        tax_year = st.selectbox("Tax Year",
            sorted(df["appointment_date"].dt.year.dropna().unique().astype(int), reverse=True),
            key="tax_year_select"
        )
        tax_df = df[df["appointment_date"].dt.year == tax_year].copy()
        tax_df["fee"] = pd.to_numeric(tax_df["fee"], errors="coerce").fillna(0)

        all_paid_tax = get_all_payment_totals()
        tax_df["paid"] = tax_df["id"].apply(lambda x: all_paid_tax.get(int(x), 0.0))
        tax_df["quarter"] = tax_df["appointment_date"].dt.to_period("Q").astype(str)

        quarterly_tax = tax_df.groupby("quarter").agg(
            appointments=("id","count"),
            gross_income=("fee","sum"),
            collected=("paid","sum"),
            miles=("mileage","sum")
        ).reset_index()
        quarterly_tax["mileage_deduction"] = quarterly_tax["miles"] * mileage_rate
        quarterly_tax["net_income"] = quarterly_tax["gross_income"] - quarterly_tax["mileage_deduction"]

        annual_income = tax_df["fee"].sum()
        annual_collected = tax_df["paid"].sum()
        annual_miles = tax_df["mileage"].sum()
        annual_deduction = annual_miles * mileage_rate
        annual_net = annual_income - annual_deduction

        # Summary metric cards
        is_dark_tax = dark_mode
        cb = "#1e293b" if is_dark_tax else "#f8fafc"
        cborder = "#334155" if is_dark_tax else "#e2e8f0"
        clabel = "#94a3b8" if is_dark_tax else "#64748b"
        cval = "#f1f5f9" if is_dark_tax else "#0f172a"
        st.markdown(f"""
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:0.5rem;margin:0.5rem 0;">
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};font-weight:500;">Gross Income</div>
                <div style="font-size:1.2rem;font-weight:700;color:{cval};">${annual_income:,.2f}</div>
            </div>
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};font-weight:500;">Collected</div>
                <div style="font-size:1.2rem;font-weight:700;color:#22c55e;">${annual_collected:,.2f}</div>
            </div>
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};font-weight:500;">Miles Driven</div>
                <div style="font-size:1.2rem;font-weight:700;color:{cval};">{annual_miles:,.1f}</div>
            </div>
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};font-weight:500;">Mileage Deduction</div>
                <div style="font-size:1.2rem;font-weight:700;color:#f59e0b;">${annual_deduction:,.2f}</div>
            </div>
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};font-weight:500;">Est. Net Income</div>
                <div style="font-size:1.2rem;font-weight:700;color:{cval};">${annual_net:,.2f}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.dataframe(quarterly_tax.rename(columns={
            "quarter": "Quarter", "appointments": "Jobs",
            "gross_income": "Gross ($)", "collected": "Collected ($)",
            "miles": "Miles", "mileage_deduction": "Mileage Deduction ($)",
            "net_income": "Est. Net ($)"
        }), use_container_width=True, hide_index=True)

        # Downloadable tax report
        tax_lines = [
            f"SCHEDULE C TAX SUMMARY — {tax_year}",
            f"{settings.get('business_name','')}",
            f"Prepared: {date.today()}",
            "=" * 50,
            f"Gross Income (billed):    ${annual_income:>10,.2f}",
            f"Total Collected:          ${annual_collected:>10,.2f}",
            f"Total Miles Driven:       {annual_miles:>10,.1f} mi",
            f"Mileage Rate:             ${mileage_rate:>10.3f}/mi",
            f"Mileage Deduction:        ${annual_deduction:>10,.2f}",
            f"Est. Net Income:          ${annual_net:>10,.2f}",
            "",
            "QUARTERLY BREAKDOWN",
            "-" * 50,
        ]
        for _, qrow in quarterly_tax.iterrows():
            tax_lines.append(
                f"{qrow['quarter']}  Jobs: {int(qrow['appointments']):>3}  "
                f"Gross: ${float(qrow['gross_income']):>8,.2f}  "
                f"Miles: {float(qrow['miles']):>7,.1f}  "
                f"Deduction: ${float(qrow['mileage_deduction']):>8,.2f}"
            )
        tax_lines += [
            "",
            "APPOINTMENT DETAIL",
            "-" * 50,
        ]
        for _, ar in tax_df.sort_values("appointment_date").iterrows():
            tax_lines.append(
                f"{str(ar['appointment_date'])[:10]}  {str(ar['client_name']):<25}  "
                f"{str(ar['signing_type']):<20}  ${float(ar['fee']):>8,.2f}  {float(ar['mileage']):>5,.1f} mi"
            )

        st.download_button(
            "⬇️ Download Tax Summary (.txt)",
            data="\n".join(tax_lines),
            file_name=f"tax_summary_{tax_year}.txt",
            mime="text/plain"
        )


elif menu == "Invoice Generator":
    st.header("Invoice Generator")

    df = get_all_appointments_dataframe()

    if df.empty:
        st.info("No appointments available for invoices.")
    else:
        appointment_id, row = appointment_selector(df)

        invoice = invoice_text(row, settings)

        st.text_area("Invoice Preview", value=invoice, height=500)

        col1, col2 = st.columns([1, 1])

        with col1:
            st.download_button(
                label="⬇️ Invoice Text",
                data=invoice,
                file_name=f"invoice_{appointment_id}.txt",
                mime="text/plain"
            )

        with col2:
            if st.button("Generate PDF Invoice"):
                pdf_bytes, error = create_pdf_invoice(row, settings)
                if error:
                    st.error(error)
                else:
                    st.download_button(
                        "Download PDF Invoice",
                        data=pdf_bytes,
                        file_name=f"invoice_{appointment_id}.pdf",
                        mime="application/pdf"
                    )

        st.divider()
        st.subheader("📧 Email Invoice")
        client_email = row.get("client_email", "")
        email_to = st.text_input("Send To", value=client_email or "")

        col_send1, col_send2 = st.columns(2)
        with col_send1:
            send_text = st.button("Send Text Invoice")
        with col_send2:
            send_pdf_btn = st.button("Send PDF Invoice")

        if send_text or send_pdf_btn:
            if not email_to:
                st.error("No email address — add one to the client record first.")
            else:
                invoice_body = invoice_text(row, settings)
                pdf_data, pdf_err = None, None
                if send_pdf_btn:
                    pdf_data, pdf_err = create_pdf_invoice(row, settings)
                    if pdf_err:
                        st.error(pdf_err)

                if not pdf_err:
                    with st.spinner("Sending..."):
                        ok, err = send_email(
                            email_to,
                            f"Invoice from {settings.get('business_name', 'Your Notary')}",
                            invoice_body,
                            pdf_bytes=pdf_data,
                            pdf_filename=f"invoice_{appointment_id}.pdf" if pdf_data else None,
                            settings=settings
                        )
                    if ok:
                        st.success(f"Invoice sent to {email_to} ✅")
                    else:
                        st.error(f"Send failed: {err}")


elif menu == "Email Templates":
    st.header("Email Templates")

    df = get_all_appointments_dataframe()

    if df.empty:
        st.info("No appointments available.")
    else:
        appointment_id, row = appointment_selector(df)

        template_type = st.selectbox(
            "Template Type",
            ["Appointment Confirmation", "Payment Reminder"]
        )

        if template_type == "Appointment Confirmation":
            subject = appointment_subject(row)
            body = appointment_email_body(row, settings)
        else:
            subject = "Payment Reminder"
            body = payment_reminder_body(row, settings)

        st.text_input("Subject", value=subject)
        st.text_area("Message", value=body, height=350)

        email = row["client_email"] or ""
        mailto_link = f"mailto:{email}?subject={quote_plus(subject)}&body={quote_plus(body)}"

        # Pre-send checklist
        send_issues = []
        if not email:
            send_issues.append("❌ No client email address")
        if not row.get("location"):
            send_issues.append("⚠️ Location is empty")
        if not float(row.get("fee") or 0) > 0:
            send_issues.append("⚠️ Fee is $0 — confirm this is intentional")
        if not row.get("appointment_time"):
            send_issues.append("⚠️ No appointment time set")

        if send_issues:
            with st.expander("⚠️ Pre-Send Issues Detected", expanded=True):
                for issue in send_issues:
                    st.write(issue)

        if email and not any(i.startswith("❌") for i in send_issues):
            st.markdown(f"[Open in Email App]({mailto_link})")
        elif not email:
            st.warning("This appointment does not have a client email address — cannot send.")
        else:
            st.info("Fix the blocking issues above before sending.")

        # SMS confirmation link
        st.divider()
        st.subheader("📱 SMS Confirmation")
        sms_url, sms_err = get_sms_link(row, settings)
        if sms_url:
            st.markdown(f"[📱 Send Confirmation Text]({sms_url})")
            st.caption("Opens your phone's Messages app pre-filled with the appointment details.")
        else:
            st.caption(f"SMS unavailable: {sms_err}")


elif menu == "Map / Route Tools":
    st.header("Map / Route Tools")

    df = get_all_appointments_dataframe()

    if df.empty:
        st.info("No appointments available.")
    else:
        appointment_id, row = appointment_selector(df)

        location = row["location"] or ""

        st.write(f"**Client:** {row['client_name']}")
        st.write(f"**Date:** {row['appointment_date']}")
        st.write(f"**Time:** {row['appointment_time']} - {row['end_time']}")
        st.write(f"**Location:** {location}")

        if location:
            encoded_location = quote_plus(location)

            google_maps_search = f"https://www.google.com/maps/search/?api=1&query={encoded_location}"
            google_maps_directions = f"https://www.google.com/maps/dir/?api=1&destination={encoded_location}"

            st.markdown(f"[Open Location in Google Maps]({google_maps_search})")
            st.markdown(f"[Get Directions in Google Maps]({google_maps_directions})")

            st.subheader("Nearby Business Tools")

            col1, col2, col3 = st.columns(3)

            with col1:
                fedex_link = f"https://www.google.com/maps/search/FedEx+near+{encoded_location}"
                st.markdown(f"[Nearby FedEx]({fedex_link})")

            with col2:
                ups_link = f"https://www.google.com/maps/search/UPS+near+{encoded_location}"
                st.markdown(f"[Nearby UPS]({ups_link})")

            with col3:
                printer_link = f"https://www.google.com/maps/search/print+shop+near+{encoded_location}"
                st.markdown(f"[Nearby Print Shops]({printer_link})")
        else:
            st.warning("This appointment does not have a location.")



elif menu == "Admin / System Health":
    st.header("Admin / System Health")
    st.caption(f"App Version: {APP_VERSION}")

    exists, size_mb, modified, counts = database_health_snapshot()

    col1, col2, col3 = st.columns([1, 1, 1])
    col1.metric("DB Exists", "Yes" if exists else "No")
    col2.metric("DB Size", f"{size_mb:,.2f} MB")
    col3.metric("Last Modified", modified)

    st.subheader("Table Health")
    health_rows = []
    for table, count in counts.items():
        health_rows.append({
            "Table": table,
            "Rows": count if count is not None else "Error"
        })
    st.dataframe(pd.DataFrame(health_rows), use_container_width=True)

    st.divider()
    st.subheader("Maintenance Tools")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Recalculate Payment Statuses"):
            recalculate_payment_statuses()
            st.success("Payment statuses recalculated.")

    with col2:
        if st.button("Create Safety Backup"):
            backup_path = create_backup()
            st.success(f"Backup created: {backup_path}")

    st.divider()
    st.subheader("Export Database Snapshots")

    for table in ["clients", "appointments", "payments", "checklist", "attachments", "followups", "settings"]:
        try:
            table_df = export_table_csv(table)
            st.download_button(
                label=f"Download {table}.csv",
                data=table_df.to_csv(index=False).encode("utf-8"),
                file_name=f"{table}.csv",
                mime="text/csv",
                key=f"export_{table}"
            )
        except Exception as error:
            st.warning(f"Could not export {table}: {error}")

    st.divider()
    st.subheader("Reset Test Data")
    st.warning("This creates a backup first, then deletes appointments, payments, checklist items, attachment records, and optionally clients.")

    preserve_clients = st.checkbox("Preserve clients", value=True)
    confirm_reset = st.checkbox("I understand this will remove test/business data from the active database")

    if st.button("Reset Appointment/Test Data"):
        if not confirm_reset:
            st.error("Check the confirmation box first.")
        else:
            backup_path = reset_demo_data(preserve_clients=preserve_clients)
            st.success(f"Data reset complete. Backup created first: {backup_path}")
            st.rerun()



elif menu == "Cloud Database Setup":
    st.header("Cloud Database Setup")

    if using_supabase():
        st.success("✅ Connected to Supabase")
        st.code(f"Project: {st.secrets.get('SUPABASE_URL', '')}")
    else:
        st.error("❌ Not connected to Supabase — add credentials to Streamlit Secrets")
        st.caption("Go to Streamlit Cloud → Manage app → Secrets and add SUPABASE_URL and SUPABASE_KEY")

    st.divider()

    st.subheader("Step 1 — Create Tables in Supabase")
    st.caption("Run this SQL once in your Supabase SQL Editor (supabase.com → SQL Editor → New query)")

    supabase_sql = """
-- Run this entire block in your Supabase SQL Editor

create table if not exists settings (
    id integer primary key,
    business_name text, business_phone text, business_email text,
    business_website text, business_tagline text,
    default_fee real, default_mileage_rate real, default_travel_buffer integer,
    logo_path text, auth_enabled integer default 0,
    app_password text, cloud_database_url text,
    signing_type_fees text, revenue_goal real default 0,
    supabase_url text, supabase_key text
);

create table if not exists clients (
    id bigserial primary key,
    client_name text not null, phone text, email text,
    address text, notes text, referral_source text
);

create table if not exists appointments (
    id bigserial primary key,
    client_id bigint,
    client_name text, client_phone text, client_email text,
    appointment_date text, appointment_time text,
    duration_minutes integer, end_time text,
    signing_type text, location text,
    fee real, mileage real, status text, notes text,
    invoice_date text, payment_due_date text,
    client_notes text, internal_notes text
);

create table if not exists payments (
    id bigserial primary key,
    appointment_id bigint,
    payment_date text, amount_paid real,
    payment_method text, notes text
);

create table if not exists checklist (
    id bigserial primary key,
    appointment_id bigint,
    item_name text, completed boolean default false
);

create table if not exists attachments (
    id bigserial primary key,
    appointment_id bigint,
    file_name text, file_path text,
    uploaded_at text, notes text
);

create table if not exists followups (
    id bigserial primary key,
    appointment_id bigint,
    followup_date text, followup_type text,
    outcome text, notes text, completed boolean default false
);

create table if not exists templates (
    id bigserial primary key,
    template_name text not null,
    signing_type text, location text,
    fee real, mileage real, duration_minutes integer,
    notes text, client_notes text, internal_notes text,
    created_at text
);

-- Optional: add smtp columns to settings if migrating from older version
alter table settings add column if not exists smtp_host text;
alter table settings add column if not exists smtp_port integer;
alter table settings add column if not exists smtp_user text;
alter table settings add column if not exists smtp_password text;
alter table settings add column if not exists smtp_from_name text;
alter table settings add column if not exists notification_email text;
alter table settings add column if not exists gcal_enabled integer;

-- Disable Row Level Security (app uses secret key server-side)
alter table settings disable row level security;
alter table clients disable row level security;
alter table appointments disable row level security;
alter table payments disable row level security;
alter table checklist disable row level security;
alter table attachments disable row level security;
alter table followups disable row level security;
alter table templates disable row level security;
"""
    st.code(supabase_sql, language="sql")
    st.download_button("⬇️ Download SQL", data=supabase_sql,
                       file_name="create_notary_tables.sql", mime="text/plain")

    st.divider()
    st.subheader("Step 2 — Migrate Your Existing Data")
    st.caption("Copies all your current SQLite data into Supabase. Safe to run multiple times.")

    if not using_supabase():
        st.warning("Connect to Supabase first before migrating.")
    else:
        if st.button("🚀 Migrate SQLite → Supabase", type="primary"):
            with st.spinner("Migrating data..."):
                success, message = migrate_sqlite_to_supabase()
            if success:
                st.success("Migration complete!")
                st.text(message)
            else:
                st.error(message)

    st.divider()
    st.subheader("Connection Status")
    st.write(f"**Mode:** {'☁️ Supabase (cloud)' if using_supabase() else '💾 SQLite (local)'}")
    st.write(f"**SQLite path:** `{DB_NAME}`")

    if using_supabase():
        st.subheader("Supabase Storage")
        try:
            buckets = sb().storage.list_buckets()
            bucket_names = [b.name for b in buckets]
            if "attachments" in bucket_names:
                st.success("✅ 'attachments' storage bucket — file uploads persist.")
            else:
                st.error("❌ 'attachments' bucket missing → Supabase → Storage → New bucket → 'attachments' → Public.")

            if "backups" in bucket_names:
                st.success("✅ 'backups' storage bucket — auto-backups active.")
            else:
                st.warning("⚠️ 'backups' bucket missing → Supabase → Storage → New bucket → 'backups' → Private. Auto-backup will be enabled once created.")
        except Exception as e:
            st.warning(f"Could not check storage buckets: {e}")

    st.warning("Never commit your Supabase secret key to GitHub. Keep it in Streamlit Secrets only.")


elif menu == "Settings / Business Profile":
    st.header("Settings / Business Profile")

    with st.form("settings_form"):
        business_name = st.text_input("Business Name", value=settings.get("business_name") or "")
        business_phone = st.text_input("Business Phone", value=settings.get("business_phone") or "")
        business_email = st.text_input("Business Email", value=settings.get("business_email") or "")
        business_website = st.text_input("Business Website", value=settings.get("business_website") or "https://keysernotaryfl.com")
        business_tagline = st.text_input("Business Tagline", value=settings.get("business_tagline") or "")
        default_fee = st.number_input("Fallback Default Fee", min_value=0.00, step=5.00, value=float(settings.get("default_fee") or 0))
        default_mileage_rate = st.number_input("Default Mileage Rate", min_value=0.0, step=0.01, value=float(settings.get("default_mileage_rate") or 0.67))
        default_travel_buffer = st.number_input("Default Travel Buffer Minutes", min_value=0, max_value=240, step=15, value=int(settings.get("default_travel_buffer") or 30))
        revenue_goal = st.number_input("Monthly Revenue Goal ($)", min_value=0.0, step=100.0, value=float(settings.get("revenue_goal") or 0),
            help="Displays a progress bar on the Dashboard when set above $0")

        st.subheader("Per-Signing-Type Default Fees")
        st.caption("These auto-fill the Fee field when you pick a signing type on Add Appointment.")
        current_fees = get_signing_type_fees(settings)
        updated_fees = {}
        fee_cols = st.columns(2)
        for i, stype in enumerate(SIGNING_TYPES):
            with fee_cols[i % 2]:
                updated_fees[stype] = st.number_input(
                    stype, min_value=0.0, step=5.0,
                    value=float(current_fees.get(stype) or 0),
                    key=f"stfee_{stype}"
                )

        st.subheader("📧 Email / SMTP")
        st.caption("Used for sending invoices and daily digests.")

        # Quick preset selector
        smtp_preset = st.selectbox("Quick Setup (optional)", [
            "Custom / Manual",
            "Gmail (TLS 587)",
            "Gmail (SSL 465)",
            "Outlook / Hotmail (TLS 587)",
            "Yahoo Mail (SSL 465)",
            "GoDaddy / cPanel (SSL 465)",
        ], key="smtp_preset")

        preset_defaults = {
            "Gmail (TLS 587)":           ("smtp.gmail.com", 587),
            "Gmail (SSL 465)":           ("smtp.gmail.com", 465),
            "Outlook / Hotmail (TLS 587)": ("smtp.office365.com", 587),
            "Yahoo Mail (SSL 465)":      ("smtp.mail.yahoo.com", 465),
            "GoDaddy / cPanel (SSL 465)": ("mail.yourdomain.com", 465),
        }
        preset_host, preset_port = preset_defaults.get(smtp_preset, ("", 587))

        smtp_host = st.text_input("SMTP Host",
            value=preset_host or settings.get("smtp_host") or "",
            placeholder="smtp.gmail.com")
        smtp_port = st.number_input("SMTP Port", min_value=1, max_value=65535,
            value=preset_port or int(settings.get("smtp_port") or 587))

        # Show which mode will be used
        if smtp_port == 465:
            st.caption("🔒 SSL mode (SMTP_SSL) — encrypted from the start")
        else:
            st.caption(f"🔐 TLS/STARTTLS mode — upgrades to encrypted on port {smtp_port}")

        smtp_user = st.text_input("SMTP Username / Email", value=settings.get("smtp_user") or "")
        smtp_password = st.text_input("SMTP Password", value=settings.get("smtp_password") or "", type="password")
        smtp_from_name = st.text_input("From Name", value=settings.get("smtp_from_name") or "", placeholder="Sean Keyser, NSA")
        notification_email = st.text_input("Daily Digest Send To", value=settings.get("notification_email") or "",
            help="Where to send the daily appointment digest — usually your own email")
        st.caption("For Gmail: Google Account → Security → 2-Step Verification → App Passwords → generate one for 'Mail'")

        if st.form_submit_button("📧 Send Test Email", type="secondary"):
            if not smtp_host or not smtp_user or not smtp_password:
                st.warning("Fill in SMTP Host, Username, and Password first, then save and test.")
            else:
                test_settings = {
                    "smtp_host": smtp_host, "smtp_port": smtp_port,
                    "smtp_user": smtp_user, "smtp_password": smtp_password,
                    "smtp_from_name": smtp_from_name or "Notary Assistant",
                    "business_name": business_name,
                }
                with st.spinner("Sending test email..."):
                    ok, err = send_email(
                        smtp_user,
                        "✅ Notary Assistant — SMTP Test",
                        f"Your SMTP settings are working correctly!\n\nApp: {settings.get('business_name','')}\nHost: {smtp_host}:{smtp_port}",
                        settings=test_settings
                    )
                if ok:
                    st.success(f"Test email sent to {smtp_user} ✅")
                else:
                    st.error(f"Failed: {err}")

        st.subheader("🔐 Security")
        auth_enabled = st.checkbox("Enable Simple Login", value=bool(settings.get("auth_enabled")))
        app_password = st.text_input("App Password", value=settings.get("app_password") or "", type="password")

        st.subheader("Cloud / Supabase")
        cloud_database_url = st.text_input("Cloud Database URL / Notes", value=settings.get("cloud_database_url") or "")
        supabase_url = st.text_input("Supabase Project URL", value=settings.get("supabase_url") or "",
            help="e.g. https://xyzxyz.supabase.co — stored locally only, never committed to git")
        supabase_key = st.text_input("Supabase Anon Key", value=settings.get("supabase_key") or "",
            type="password", help="Your Supabase anon/public key")
        if supabase_url or supabase_key:
            st.caption("⚠️ Store these in Streamlit Secrets for production — not in the database.")

        logo_path_current = settings.get("logo_path") or ""
        logo_upload = st.file_uploader("Upload Logo", type=["png", "jpg", "jpeg"])

        submitted = st.form_submit_button("Save Settings")

        if submitted:
            logo_path = logo_path_current

            if logo_upload is not None:
                logo_folder = os.path.join(UPLOAD_FOLDER, "branding")
                os.makedirs(logo_folder, exist_ok=True)
                logo_path = os.path.join(logo_folder, logo_upload.name)

                with open(logo_path, "wb") as f:
                    f.write(logo_upload.getbuffer())

            import json as _json
            update_settings({
                "business_name": business_name,
                "business_phone": business_phone,
                "business_email": business_email,
                "business_website": business_website,
                "business_tagline": business_tagline,
                "default_fee": default_fee,
                "default_mileage_rate": default_mileage_rate,
                "default_travel_buffer": default_travel_buffer,
                "logo_path": logo_path,
                "auth_enabled": auth_enabled,
                "app_password": app_password,
                "cloud_database_url": cloud_database_url,
                "signing_type_fees": _json.dumps(updated_fees),
                "revenue_goal": revenue_goal,
                "supabase_url": supabase_url,
                "supabase_key": supabase_key,
                "smtp_host": smtp_host,
                "smtp_port": smtp_port,
                "smtp_user": smtp_user,
                "smtp_password": smtp_password,
                "smtp_from_name": smtp_from_name,
                "notification_email": notification_email,
            })

            st.success("Settings saved.")
            st.rerun()


elif menu == "Profit & Loss":
    st.header("Profit & Loss")
    st.caption("Income vs expenses by month and year.")

    df_pl = get_all_appointments_dataframe()
    exp_pl = get_expenses_dataframe()

    if df_pl.empty:
        st.info("No appointment data yet.")
    else:
        df_pl["fee"] = pd.to_numeric(df_pl["fee"], errors="coerce").fillna(0)
        df_pl["appointment_date"] = pd.to_datetime(df_pl["appointment_date"], errors="coerce")
        df_pl["month"] = df_pl["appointment_date"].dt.strftime("%Y-%m")
        df_pl["year"] = df_pl["appointment_date"].dt.year

        if not exp_pl.empty:
            exp_pl["amount"] = pd.to_numeric(exp_pl["amount"], errors="coerce").fillna(0)
            exp_pl["expense_date"] = pd.to_datetime(exp_pl["expense_date"], errors="coerce")
            exp_pl["month"] = exp_pl["expense_date"].dt.strftime("%Y-%m")
            exp_pl["year"] = exp_pl["expense_date"].dt.year

        years_pl = sorted(df_pl["year"].dropna().unique().astype(int), reverse=True)
        pl_year = st.selectbox("Year", years_pl, key="pl_year")

        df_yr = df_pl[df_pl["year"] == pl_year]
        all_paid_pl = get_all_payment_totals()
        df_yr = df_yr.copy()
        df_yr["collected"] = df_yr["id"].apply(lambda x: all_paid_pl.get(int(x), 0.0))

        monthly_revenue_pl = df_yr.groupby("month")["fee"].sum()
        monthly_collected_pl = df_yr.groupby("month")["collected"].sum()

        if not exp_pl.empty:
            exp_yr = exp_pl[exp_pl["year"] == pl_year]
            monthly_exp_pl = exp_yr.groupby("month")["amount"].sum()
        else:
            monthly_exp_pl = pd.Series(dtype=float)

        all_months = sorted(set(monthly_revenue_pl.index) | set(monthly_exp_pl.index))
        pl_data = []
        for m in all_months:
            rev = float(monthly_revenue_pl.get(m, 0))
            col = float(monthly_collected_pl.get(m, 0))
            exp = float(monthly_exp_pl.get(m, 0))
            net = col - exp
            pl_data.append({
                "Month": m,
                "Billed": rev,
                "Collected": col,
                "Expenses": exp,
                "Net": net
            })

        pl_df = pd.DataFrame(pl_data)

        total_billed = pl_df["Billed"].sum()
        total_collected = pl_df["Collected"].sum()
        total_expenses = pl_df["Expenses"].sum()
        total_net = total_collected - total_expenses

        is_dark_pl = dark_mode
        cb = "#1e293b" if is_dark_pl else "#f8fafc"
        cborder = "#334155" if is_dark_pl else "#e2e8f0"
        clabel = "#94a3b8" if is_dark_pl else "#64748b"

        st.markdown(f"""
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:0.5rem;margin-bottom:1rem;">
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};">Total Billed</div>
                <div style="font-size:1.3rem;font-weight:700;color:#3b82f6;">${total_billed:,.2f}</div>
            </div>
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};">Collected</div>
                <div style="font-size:1.3rem;font-weight:700;color:#22c55e;">${total_collected:,.2f}</div>
            </div>
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};">Expenses</div>
                <div style="font-size:1.3rem;font-weight:700;color:#ef4444;">${total_expenses:,.2f}</div>
            </div>
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.6rem 0.75rem;">
                <div style="font-size:0.72rem;color:{clabel};">Net Profit</div>
                <div style="font-size:1.3rem;font-weight:700;color:{'#22c55e' if total_net >= 0 else '#ef4444'};">${total_net:,.2f}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        if not pl_df.empty:
            chart_df = pl_df.set_index("Month")[["Collected","Expenses","Net"]]
            st.bar_chart(chart_df)

            st.subheader("Monthly Detail")
            display_pl = pl_df.copy()
            for col in ["Billed","Collected","Expenses","Net"]:
                display_pl[col] = display_pl[col].map("${:,.2f}".format)
            st.dataframe(display_pl, use_container_width=True, hide_index=True)

            st.download_button(
                "⬇️ Download P&L CSV",
                data=pl_df.to_csv(index=False).encode("utf-8"),
                file_name=f"profit_loss_{pl_year}.csv",
                mime="text/csv"
            )


elif menu == "Expense Tracker":
    st.header("Expense Tracker")
    st.caption("Track business expenses for Schedule C deductions.")

    exp_tab1, exp_tab2 = st.tabs(["📋 Log Expense", "📊 Expense Report"])

    with exp_tab1:
        with st.form("expense_form"):
            exp_date = st.date_input("Date", value=date.today())
            exp_category = st.selectbox("Category", EXPENSE_CATEGORIES)
            exp_description = st.text_input("Description", placeholder="e.g. Staples — notary stamps x2")
            exp_amount = st.number_input("Amount ($)", min_value=0.01, step=0.01)
            exp_notes = st.text_area("Notes", height=80)

            if st.form_submit_button("💾 Save Expense", type="primary"):
                if not exp_description.strip():
                    st.error("Description is required.")
                else:
                    add_expense(exp_date, exp_category, exp_description, exp_amount, exp_notes)
                    st.success(f"Expense of ${exp_amount:,.2f} saved!")
                    st.rerun()

    with exp_tab2:
        exp_df = get_expenses_dataframe()

        if exp_df.empty:
            st.info("No expenses recorded yet.")
        else:
            exp_df["amount"] = pd.to_numeric(exp_df["amount"], errors="coerce").fillna(0)
            exp_df["expense_date"] = pd.to_datetime(exp_df["expense_date"], errors="coerce")

            # Year filter
            years = sorted(exp_df["expense_date"].dt.year.dropna().unique().astype(int), reverse=True)
            exp_year = st.selectbox("Tax Year", years, key="exp_year")
            exp_df_yr = exp_df[exp_df["expense_date"].dt.year == exp_year]

            total_expenses = exp_df_yr["amount"].sum()
            is_dark_exp = dark_mode
            cb = "#1e293b" if is_dark_exp else "#f8fafc"
            cborder = "#334155" if is_dark_exp else "#e2e8f0"
            clabel = "#94a3b8" if is_dark_exp else "#64748b"
            cval = "#f1f5f9" if is_dark_exp else "#0f172a"

            # Summary by category
            cat_summary = exp_df_yr.groupby("category")["amount"].sum().sort_values(ascending=False)

            st.markdown(f"""
            <div style="background:{cb};border:1px solid {cborder};border-radius:10px;padding:0.75rem 1rem;margin-bottom:1rem;">
                <div style="font-size:0.75rem;color:{clabel};">Total Expenses {exp_year}</div>
                <div style="font-size:1.6rem;font-weight:700;color:#ef4444;">${total_expenses:,.2f}</div>
            </div>
            """, unsafe_allow_html=True)

            st.subheader("By Category")
            st.bar_chart(cat_summary)

            st.subheader("All Expenses")
            display_exp = exp_df_yr.copy()
            display_exp["expense_date"] = display_exp["expense_date"].dt.strftime("%Y-%m-%d")
            display_exp["amount"] = display_exp["amount"].map("${:,.2f}".format)

            for _, exp_row in exp_df_yr.sort_values("expense_date", ascending=False).iterrows():
                with st.expander(
                    f"{exp_row['expense_date'].strftime('%b %d')} — "
                    f"{exp_row['category']} — ${float(exp_row['amount']):,.2f} — {exp_row['description']}"
                ):
                    if exp_row.get("notes"):
                        st.caption(exp_row["notes"])
                    if st.button("🗑️ Delete", key=f"del_exp_{exp_row['id']}"):
                        delete_expense(int(exp_row["id"]))
                        st.rerun()

            # Export
            st.download_button(
                "⬇️ Download Expenses CSV",
                data=exp_df_yr[["expense_date","category","description","amount","notes"]
                               ].to_csv(index=False).encode("utf-8"),
                file_name=f"expenses_{exp_year}.csv",
                mime="text/csv"
            )


elif menu == "Signing Day Sheet":
    st.header("Signing Day Sheet")
    st.caption("A clean printable/downloadable summary of all appointments for a chosen day.")

    sheet_date = st.date_input("Select Date", value=date.today())

    df_all = get_all_appointments_dataframe()

    if df_all.empty:
        st.info("No appointments in the system.")
    else:
        df_all["appointment_date"] = pd.to_datetime(df_all["appointment_date"], errors="coerce")
        day_df = df_all[df_all["appointment_date"].dt.date == sheet_date].sort_values("appointment_time")

        if day_df.empty:
            st.info(f"No appointments scheduled for {sheet_date.strftime('%A, %B %d, %Y')}.")
        else:
            st.subheader(f"📅 {sheet_date.strftime('%A, %B %d, %Y')} — {len(day_df)} appointment(s)")

            all_paid = get_all_payment_totals()
            total_day_fee = day_df["fee"].fillna(0).astype(float).sum()

            # Summary bar
            sc1, sc2, sc3 = st.columns([1, 1, 1])
            sc1.metric("Appointments", len(day_df))
            sc2.metric("Total Fees", f"${total_day_fee:,.2f}")
            sc3.metric("Total Miles", f"{day_df['mileage'].fillna(0).astype(float).sum():,.1f}")

            st.divider()

            # Per-appointment cards
            for _, row in day_df.iterrows():
                appt_id = int(row["id"])
                paid = all_paid.get(appt_id, 0.0)
                fee = float(row.get("fee") or 0)
                balance = max(fee - paid, 0)

                checklist = get_checklist(appt_id)
                done = sum(1 for _, _, c in checklist if c)
                total_checks = len(checklist)

                with st.container():
                    st.markdown(
                        f"### {row['appointment_time']} — {row['end_time']}  |  "
                        f"{row['client_name']}  |  _{row['signing_type']}_"
                    )
                    dc1, dc2, dc3, dc4 = st.columns([1, 1, 1, 1])
                    dc1.write(f"📍 {row['location'] or '—'}")
                    dc2.write(f"💵 ${fee:,.2f}")
                    dc3.write(f"🚗 {float(row.get('mileage') or 0):,.1f} mi")
                    dc4.write(f"✅ Checklist: {done}/{total_checks}")

                    client_notes_val = row.get("client_notes") or ""
                    internal_notes_val = row.get("internal_notes") or ""
                    if client_notes_val:
                        st.info(f"📋 {client_notes_val}")
                    if internal_notes_val:
                        st.warning(f"🔒 {internal_notes_val}")

                    # Quick map link
                    if row.get("location"):
                        from urllib.parse import quote_plus as qp
                        directions = f"https://www.google.com/maps/dir/?api=1&destination={qp(row['location'])}"
                        st.markdown(f"[🗺️ Get Directions]({directions})")

                    gcal_url = get_gcal_add_url(appt_row)
                    if gcal_url:
                        st.markdown(f"[📅 Add to Google Calendar]({gcal_url})")

                    # Status timeline
                    history_df = get_status_history(appointment_id)
                    if not history_df.empty:
                        st.caption("**Status Timeline:**")
                        for _, h in history_df.iterrows():
                            st.caption(f"  {h['changed_at']} — {h['old_status']} → {h['new_status']}")

                    # Print-friendly detail download
                    if st.button("🖨️ Download Detail Sheet", key=f"print_{appointment_id}"):
                        checklist_items = get_checklist(appointment_id)
                        payment_hist = get_payments_dataframe()
                        appt_payments = payment_hist[
                            payment_hist["appointment_id"] == appointment_id
                        ] if not payment_hist.empty else pd.DataFrame()

                        detail_lines = [
                            f"APPOINTMENT DETAIL SHEET",
                            f"{settings.get('business_name','')}",
                            f"Generated: {date.today()}",
                            "=" * 55,
                            f"Client:       {client_name}",
                            f"Date:         {appointment_date}",
                            f"Time:         {appointment_time} — {appt_row.get('end_time','')}",
                            f"Type:         {signing_type}",
                            f"Location:     {location}",
                            f"Status:       {status}",
                            f"Fee:          ${float(fee):,.2f}",
                            f"Paid:         ${paid:,.2f}",
                            f"Balance:      ${balance:,.2f}",
                            f"Mileage:      {mileage} miles",
                        ]
                        if client_notes_val:
                            detail_lines.append(f"Client Notes: {client_notes_val}")
                        if internal_notes_val:
                            detail_lines.append(f"Int. Notes:   {internal_notes_val}")

                        detail_lines += ["", "CHECKLIST", "-" * 30]
                        for _, item_name, completed in checklist_items:
                            detail_lines.append(f"[{'✓' if completed else ' '}] {item_name}")

                        if not appt_payments.empty:
                            detail_lines += ["", "PAYMENTS", "-" * 30]
                            for _, prow in appt_payments.iterrows():
                                detail_lines.append(
                                    f"{prow['payment_date']}  ${float(prow['amount_paid']):,.2f}  "
                                    f"{prow['payment_method']}"
                                )

                        if not history_df.empty:
                            detail_lines += ["", "STATUS HISTORY", "-" * 30]
                            for _, h in history_df.iterrows():
                                detail_lines.append(f"{h['changed_at']}  {h['old_status']} → {h['new_status']}")

                        st.download_button(
                            "⬇️ Download",
                            data="\n".join(detail_lines),
                            file_name=f"appointment_{appointment_id}_{client_name.replace(' ','_')}.txt",
                            mime="text/plain",
                            key=f"dl_detail_{appointment_id}"
                        )

                    st.divider()

            # Downloadable plain-text version
            lines = [
                f"SIGNING DAY SHEET — {sheet_date.strftime('%A, %B %d, %Y')}",
                f"{settings.get('business_name', '')}",
                "=" * 55,
            ]
            for _, row in day_df.iterrows():
                appt_id = int(row["id"])
                paid = all_paid.get(appt_id, 0.0)
                fee = float(row.get("fee") or 0)
                lines += [
                    "",
                    f"{row['appointment_time']} – {row['end_time']}",
                    f"Client:   {row['client_name']}",
                    f"Type:     {row['signing_type']}",
                    f"Location: {row['location'] or '—'}",
                    f"Fee:      ${fee:,.2f}   Paid: ${paid:,.2f}   Balance: ${max(fee-paid,0):,.2f}",
                    f"Mileage:  {float(row.get('mileage') or 0):,.1f} mi",
                ]
                if row.get("client_notes"):
                    lines.append(f"Notes (client):   {row['client_notes']}")
                if row.get("internal_notes"):
                    lines.append(f"Notes (internal): {row['internal_notes']}")
                lines.append("-" * 40)
            lines += ["", f"Total Fees: ${total_day_fee:,.2f}"]
            sheet_text = "\n".join(lines)

            st.download_button(
                "⬇️ Download Day Sheet (.txt)",
                data=sheet_text,
                file_name=f"signing_day_{sheet_date.isoformat()}.txt",
                mime="text/plain"
            )


elif menu == "Service Area Map":
    st.header("Service Area Map")
    st.caption("See where you've worked and identify your busiest areas.")

    df_all = get_all_appointments_dataframe()

    if df_all.empty:
        st.info("No appointment data yet.")
    else:
        df_all["fee"] = pd.to_numeric(df_all["fee"], errors="coerce").fillna(0)

        # Filter controls
        map_col1, map_col2 = st.columns(2)
        with map_col1:
            map_status = st.selectbox("Filter by Status", ["All"] + STATUSES, key="map_status")
        with map_col2:
            map_type = st.selectbox("Filter by Type", ["All"] + SIGNING_TYPES, key="map_type")

        map_df = df_all.copy()
        if map_status != "All":
            map_df = map_df[map_df["status"] == map_status]
        if map_type != "All":
            map_df = map_df[map_df["signing_type"] == map_type]

        locations = map_df[map_df["location"].notna() & (map_df["location"] != "")]["location"].value_counts().reset_index()
        locations.columns = ["location", "count"]

        st.subheader(f"{len(locations)} Unique Locations")

        # Summary stats
        sm1, sm2, sm3 = st.columns(3)
        sm1.metric("Total Appointments", len(map_df))
        sm2.metric("Unique Locations", len(locations))
        sm3.metric("Total Revenue", f"${map_df['fee'].sum():,.2f}")

        st.divider()

        # Top locations table
        st.subheader("Most Visited Locations")
        top_locations = locations.head(20).copy()
        top_locations["revenue"] = top_locations["location"].apply(
            lambda loc: map_df[map_df["location"] == loc]["fee"].sum()
        )
        top_locations.columns = ["Location", "Visits", "Revenue ($)"]
        top_locations["Revenue ($)"] = top_locations["Revenue ($)"].map("${:,.2f}".format)
        st.dataframe(top_locations, use_container_width=True, hide_index=True)

        # Google Maps links for top locations
        st.divider()
        st.subheader("Quick Directions")
        st.caption("One-tap directions to your most visited locations.")
        for _, loc_row in locations.head(8).iterrows():
            from urllib.parse import quote_plus as qp
            maps_url = f"https://www.google.com/maps/search/{qp(loc_row['location'])}"
            st.markdown(f"[📍 {loc_row['location']} ({int(loc_row['count'])} visits)]({maps_url})")

        st.divider()
        # All locations as downloadable CSV
        st.download_button(
            "⬇️ Download Location Data CSV",
            data=map_df[["appointment_date","client_name","signing_type","location","fee","status"]
                       ].to_csv(index=False).encode("utf-8"),
            file_name="service_area_locations.csv",
            mime="text/csv"
        )


elif menu == "Notary Journal":
    st.header("Notary Journal Export")
    st.caption("Generate a formatted journal log for Florida notary record-keeping requirements.")

    df_nj = get_all_appointments_dataframe()
    if df_nj.empty:
        st.info("No appointments to export.")
    else:
        df_nj["appointment_date"] = pd.to_datetime(df_nj["appointment_date"], errors="coerce")
        df_nj["fee"] = pd.to_numeric(df_nj["fee"], errors="coerce").fillna(0)

        nj_col1, nj_col2 = st.columns(2)
        with nj_col1:
            nj_start = st.date_input("From Date", value=date(date.today().year, 1, 1), key="nj_start")
        with nj_col2:
            nj_end = st.date_input("To Date", value=date.today(), key="nj_end")

        nj_df = df_nj[
            (df_nj["appointment_date"].dt.date >= nj_start) &
            (df_nj["appointment_date"].dt.date <= nj_end)
        ].sort_values("appointment_date")

        st.write(f"**{len(nj_df)} entries** from {nj_start} to {nj_end}")

        if not nj_df.empty:
            # Preview table
            st.subheader("Journal Preview")
            preview_cols = ["appointment_date","appointment_time","client_name",
                           "signing_type","location","fee","status"]
            preview_df = nj_df[[c for c in preview_cols if c in nj_df.columns]].copy()
            preview_df["appointment_date"] = preview_df["appointment_date"].dt.strftime("%m/%d/%Y")
            st.dataframe(preview_df, use_container_width=True, hide_index=True)

            # Generate formatted text journal
            journal_lines = [
                "NOTARY PUBLIC JOURNAL OF OFFICIAL ACTS",
                f"Notary: {settings.get('business_name', '')}",
                f"State of Florida",
                f"Period: {nj_start.strftime('%B %d, %Y')} — {nj_end.strftime('%B %d, %Y')}",
                f"Generated: {date.today().strftime('%B %d, %Y')}",
                "=" * 70,
                "",
            ]

            for entry_num, (_, row) in enumerate(nj_df.iterrows(), 1):
                appt_date = row["appointment_date"].strftime("%m/%d/%Y") if pd.notna(row["appointment_date"]) else "—"
                journal_lines += [
                    f"ENTRY #{entry_num:04d}",
                    f"Date of Act:        {appt_date}",
                    f"Time:               {row.get('appointment_time', '—')}",
                    f"Type of Act:        {row.get('signing_type', '—')}",
                    f"Name of Signer:     {row.get('client_name', '—')}",
                    f"Address of Signer:  {row.get('location', '—')}",
                    f"ID Presented:       ___________________________",
                    f"ID Number:          ___________________________",
                    f"Fee Charged:        ${float(row.get('fee') or 0):,.2f}",
                    f"Thumbprint:         [ ]",
                    f"Notes:              {row.get('notes', '') or ''}",
                    "-" * 70,
                    "",
                ]

            journal_lines += [
                "",
                f"Total Entries: {len(nj_df)}",
                f"Total Fees:    ${nj_df['fee'].sum():,.2f}",
                "",
                "Notary Signature: _________________________  Date: ___________",
                "Commission #: _____________________________  Expires: _________",
            ]

            journal_text = "\n".join(journal_lines)

            dj_col1, dj_col2 = st.columns(2)
            with dj_col1:
                st.download_button(
                    "⬇️ Download Journal (.txt)",
                    data=journal_text,
                    file_name=f"notary_journal_{nj_start}_{nj_end}.txt",
                    mime="text/plain"
                )
            with dj_col2:
                # PDF version
                if st.button("📄 Generate PDF Journal"):
                    try:
                        from reportlab.lib.pagesizes import letter
                        from reportlab.pdfgen import canvas as rl_canvas
                        import io as _io
                        buf = _io.BytesIO()
                        c = rl_canvas.Canvas(buf, pagesize=letter)
                        w, h = letter
                        y = h - 50
                        c.setFont("Helvetica-Bold", 14)
                        c.drawString(50, y, "NOTARY PUBLIC JOURNAL OF OFFICIAL ACTS")
                        y -= 20
                        c.setFont("Helvetica", 10)
                        for line in [
                            f"Notary: {settings.get('business_name','')}",
                            f"State of Florida",
                            f"Period: {nj_start} — {nj_end}",
                        ]:
                            c.drawString(50, y, line); y -= 14
                        y -= 10
                        for entry_num, (_, row) in enumerate(nj_df.iterrows(), 1):
                            if y < 200:
                                c.showPage()
                                y = h - 50
                                c.setFont("Helvetica", 10)
                            appt_date = row["appointment_date"].strftime("%m/%d/%Y") if pd.notna(row["appointment_date"]) else "—"
                            c.setFont("Helvetica-Bold", 10)
                            c.drawString(50, y, f"Entry #{entry_num:04d} — {appt_date} — {row.get('signing_type','')}")
                            y -= 14
                            c.setFont("Helvetica", 9)
                            for label, val in [
                                ("Signer", row.get("client_name","")),
                                ("Location", row.get("location","")),
                                ("Fee", f"${float(row.get('fee') or 0):,.2f}"),
                                ("ID Presented", "_______________________"),
                                ("Thumbprint", "[ ]"),
                            ]:
                                c.drawString(60, y, f"{label}: {val}"); y -= 12
                            y -= 6
                        c.save()
                        buf.seek(0)
                        st.download_button(
                            "⬇️ Download PDF",
                            data=buf.getvalue(),
                            file_name=f"notary_journal_{nj_start}_{nj_end}.pdf",
                            mime="application/pdf"
                        )
                    except Exception as e:
                        st.error(f"PDF generation failed: {e}")


elif menu == "Appointment Templates":
    st.header("Appointment Templates")
    st.caption("Save frequently-used appointment setups to pre-fill new bookings instantly.")

    templates_df = get_templates()

    st.subheader("Saved Templates")
    if templates_df.empty:
        st.info("No templates saved yet. Create one below or use 'Save as Template' from Add Appointment.")
    else:
        for _, tmpl in templates_df.iterrows():
            with st.expander(f"📄 {tmpl['template_name']} — {tmpl['signing_type']}"):
                col1, col2, col3 = st.columns(3)
                col1.write(f"**Fee:** ${float(tmpl['fee'] or 0):,.2f}")
                col2.write(f"**Duration:** {int(tmpl['duration_minutes'] or 60)} min")
                col3.write(f"**Location:** {tmpl['location'] or '—'}")
                if tmpl.get('notes'):
                    st.write(f"**Notes:** {tmpl['notes']}")

                btn1, btn2 = st.columns(2)
                with btn1:
                    if st.button("📋 Use This Template", key=f"use_tmpl_{tmpl['id']}"):
                        st.session_state["prefill_quote"] = {
                            "signing_type": tmpl["signing_type"],
                            "fee": float(tmpl["fee"] or 0),
                            "location": tmpl["location"] or "",
                            "duration_minutes": int(tmpl["duration_minutes"] or 60),
                            "notes": tmpl["notes"] or "",
                        }
                        st.session_state.selected_menu = "Add Appointment"
                        st.rerun()
                with btn2:
                    if st.button("🗑️ Delete", key=f"del_tmpl_{tmpl['id']}"):
                        delete_template(int(tmpl["id"]))
                        st.success("Template deleted.")
                        st.rerun()

    st.divider()
    st.subheader("Create New Template")

    with st.form("template_form"):
        tmpl_name = st.text_input("Template Name", placeholder="e.g. Standard Loan Signing — Tavares")
        tmpl_signing_type = st.selectbox("Signing Type", SIGNING_TYPES)
        tmpl_location = st.text_input("Default Location")
        tmpl_fee = st.number_input("Fee", min_value=0.0, step=5.0,
            value=float(settings.get("default_fee") or 0))
        tmpl_mileage = st.number_input("Typical Mileage", min_value=0.0, step=1.0)
        tmpl_duration = st.number_input("Duration (minutes)", min_value=15, max_value=480, value=60, step=15)
        tmpl_notes = st.text_area("Notes")
        tmpl_client_notes = st.text_area("Client-Facing Notes")
        tmpl_internal_notes = st.text_area("Internal Notes")

        if st.form_submit_button("Save Template"):
            if not tmpl_name.strip():
                st.error("Template name is required.")
            else:
                save_template(tmpl_name, tmpl_signing_type, tmpl_location, tmpl_fee,
                              tmpl_mileage, tmpl_duration, tmpl_notes,
                              tmpl_client_notes, tmpl_internal_notes)
                st.success(f"Template '{tmpl_name}' saved!")
                st.rerun()


elif menu == "Backup / Restore":
    st.header("Backup / Restore")

    st.subheader("Backup Database")

    if os.path.exists(DB_NAME):
        with open(DB_NAME, "rb") as f:
            st.download_button(
                "Download Current Database",
                data=f.read(),
                file_name=DB_NAME,
                mime="application/octet-stream"
            )

    if st.button("Create Timestamped Backup"):
        backup_path = create_backup()
        st.success(f"Backup created: {backup_path}")

    st.divider()

    st.subheader("Restore Database")

    st.warning("Restoring a database will replace your current database. Create a backup first.")

    restore_file = st.file_uploader("Upload SQLite Database Backup", type=["db"])

    if st.button("Restore Uploaded Database"):
        if restore_file is None:
            st.error("Please upload a .db file first.")
        else:
            backup_path = create_backup()
            with open(DB_NAME, "wb") as f:
                f.write(restore_file.getbuffer())

            st.success(f"Database restored. Previous database backed up to: {backup_path}")
            st.rerun()