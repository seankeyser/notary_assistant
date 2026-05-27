import streamlit as st
import pandas as pd
from utils import *


def render(menu, settings, dark_mode):
    """Render admin pages."""
    menu = st.session_state.get("selected_menu", menu)
    settings = settings or get_settings()

    if menu == "Admin / System Health":
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
    
