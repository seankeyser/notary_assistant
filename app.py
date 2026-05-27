"""
Notary Digital Assistant — Entry Point
Handles: startup, portal intercept, auth, sidebar nav, page dispatch.
All business logic lives in utils.py. Page UI lives in pages/*.py.
"""

import streamlit as st
import pandas as pd
from utils import (
    ensure_folders, create_tables, get_settings, get_clients,
    get_all_appointments_dataframe, get_all_payment_totals,
    using_supabase, sb, auto_backup_to_supabase, clear_all_caches,
    _make_portal_token, _verify_portal_token, PORTAL_TOKEN_DAYS,
    get_invoice_status_dataframe, APP_VERSION,
)

# ── Startup ───────────────────────────────────────────────────────────────────
ensure_folders()
create_tables()
settings = get_settings()

st.set_page_config(
    page_title="Notary Digital Assistant",
    layout="centered",
    initial_sidebar_state="auto"
)

# ── Auto-backup (once per session) ────────────────────────────────────────────
if "auto_backup_done" not in st.session_state:
    st.session_state.auto_backup_done = True
    try:
        auto_backup_to_supabase()
    except Exception:
        pass

# ── Client Portal intercept ───────────────────────────────────────────────────
_qp = st.query_params
if "portal" in _qp:
    _portal_client_id = _qp.get("portal")
    _portal_token = _qp.get("token", "")
    st.markdown("<style>[data-testid='stSidebar']{display:none!important;}</style>",
                unsafe_allow_html=True)
    try:
        _client_id_int = int(_portal_client_id)
    except (ValueError, TypeError):
        st.error("Invalid portal link.")
        st.stop()

    if not _verify_portal_token(_portal_client_id, _portal_token):
        st.error("⛔ This link is invalid or has expired.")
        st.stop()

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
    st.stop()

# ── Simple login ──────────────────────────────────────────────────────────────
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

# ── Responsive CSS ────────────────────────────────────────────────────────────
st.markdown("""
<style>
* { -webkit-text-size-adjust: 100% !important; text-size-adjust: 100% !important; box-sizing: border-box !important; }
.block-container { max-width: 100% !important; padding: 0.75rem 1rem !important; }
.stButton > button, [data-testid="baseButton-secondary"], [data-testid="stDownloadButton"] > button { min-height: 44px !important; }
.stTextInput > div > div > input, .stNumberInput > div > div > input, .stTextArea > div > div > textarea,
.stSelectbox > div > div, .stDateInput > div > div > input, .stTimeInput > div > div > input { min-height: 44px !important; font-size: 1rem !important; }
[data-testid="metric-container"] { padding: 0.5rem 0.6rem !important; margin-bottom: 0.25rem !important; }
@media (max-width: 1024px) {
    [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: 0.4rem !important; }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width: 30% !important; flex: 1 1 30% !important; }
}
@media (max-width: 640px) {
    .block-container { padding: 0.5rem !important; }
    [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: 0.3rem !important; width: 100% !important; }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width: 0 !important; flex: 1 1 calc(50% - 0.3rem) !important; max-width: calc(50% - 0.3rem) !important; }
    [data-testid="stDataFrame"] { overflow-x: auto !important; -webkit-overflow-scrolling: touch !important; }
    .stButton > button { width: 100% !important; }
    section[data-testid="stSidebar"] { min-width: 260px !important; max-width: 82vw !important; }
    h1 { font-size: 1.3rem !important; } h2 { font-size: 1.15rem !important; }
}
@media (max-width: 380px) {
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width: 100% !important; flex: 1 1 100% !important; }
}
</style>
""", unsafe_allow_html=True)

# ── Sidebar navigation ────────────────────────────────────────────────────────
MENU_GROUPS = {
    "📊 Overview":    ["Dashboard", "Calendar View", "Global Search"],
    "👤 Clients":     ["Add Client", "View Clients", "Edit Client", "Client History",
                       "Client Portal", "Retention Report", "Communication Log"],
    "📅 Appointments":["Add Appointment", "View Appointments", "Edit Appointment",
                       "Delete Appointment", "Job Checklist", "Appointment Templates",
                       "Recurring Scheduler"],
    "💰 Finance":     ["Payment Tracking", "Invoice Status", "Invoice Generator",
                       "Quote Generator", "Quote Tracking", "Reports / Export",
                       "Mileage / Tax Report", "Expense Tracker", "Profit & Loss",
                       "Follow-Up Tracker", "Referral Analytics"],
    "📬 Tools":       ["Email Templates", "Map / Route Tools", "Document Attachments",
                       "Signing Day Sheet", "Service Area Map", "Notary Journal"],
    "⚙️ Admin":       ["Admin / System Health", "Cloud Database Setup",
                       "Settings / Business Profile", "Backup / Restore"],
}

if "selected_menu" not in st.session_state:
    st.session_state.selected_menu = "Dashboard"

st.sidebar.title("Navigation")
dark_mode = st.sidebar.toggle("Dark Mode", value=False)

st.sidebar.divider()

logo_path = settings.get("logo_path", "")
import os
if logo_path and os.path.exists(logo_path):
    st.sidebar.image(logo_path, width=160)

business_name = settings.get("business_name", "Notary Assistant")
tagline = settings.get("business_tagline", "")
st.sidebar.markdown(f"**{business_name}**")
if tagline:
    st.sidebar.caption(tagline)
st.sidebar.caption(f"App Version: {APP_VERSION}")
st.sidebar.divider()

active_group = next(
    (g for g, items in MENU_GROUPS.items() if st.session_state.selected_menu in items),
    list(MENU_GROUPS.keys())[0]
)
for group_label, group_items in MENU_GROUPS.items():
    with st.sidebar.expander(group_label, expanded=(group_label == active_group)):
        for item in group_items:
            label = f"**{item}**" if item == st.session_state.selected_menu else item
            if st.button(label, key=f"nav_{item}", use_container_width=True):
                st.session_state.selected_menu = item
                st.rerun()

st.sidebar.divider()
if st.sidebar.button("🔄 Refresh", use_container_width=True):
    clear_all_caches()
    st.rerun()

if using_supabase():
    st.sidebar.caption("☁️ Supabase connected")
else:
    st.sidebar.warning("⚠️ Offline — using local SQLite")

st.sidebar.caption(f"Version {APP_VERSION}")

# ── Dark mode CSS ─────────────────────────────────────────────────────────────
if dark_mode:
    st.markdown("""<style>
    .stApp, .stApp > * { background-color: #0f172a !important; color: #e2e8f0 !important; }
    [data-testid="stSidebar"], [data-testid="stSidebar"] > div { background-color: #020617 !important; }
    [data-testid="stSidebar"] * { color: #e2e8f0 !important; }
    h1,h2,h3,h4,h5,h6,p,label,span,div,.stMarkdown,.stMarkdown p { color: #e2e8f0 !important; }
    [data-testid="stForm"],section[data-testid="stForm"] { background-color: #1e293b !important; border: 1px solid #334155 !important; border-radius: 8px !important; }
    .stExpander,[data-testid="stExpander"] { background-color: #1e293b !important; border: 1px solid #334155 !important; border-radius: 8px !important; }
    [data-testid="stExpander"] summary,[data-testid="stExpander"] summary p { color: #e2e8f0 !important; background-color: #1e293b !important; }
    [data-testid="stMetric"],[data-testid="metric-container"] { background-color: #1e293b !important; border: 1px solid #334155 !important; border-radius: 12px !important; padding: 12px !important; }
    [data-testid="stMetricValue"],[data-testid="stMetricLabel"] { color: #e2e8f0 !important; }
    .stTextInput > div > div > input,.stNumberInput > div > div > input,.stTextArea > div > div > textarea,input[type="text"],input[type="number"],input[type="password"],textarea { background-color: #1e293b !important; color: #f1f5f9 !important; border-color: #475569 !important; }
    div[data-baseweb="select"] > div,div[data-baseweb="select"] > div > div { background-color: #1e293b !important; color: #f1f5f9 !important; border-color: #475569 !important; }
    div[data-baseweb="popover"] *,div[data-baseweb="menu"] *,div[role="listbox"],li[role="option"],div[role="option"] { background-color: #1e293b !important; color: #f1f5f9 !important; }
    .stButton > button,[data-testid="baseButton-secondary"] { background-color: #1e293b !important; color: #f1f5f9 !important; border: 1px solid #475569 !important; border-radius: 8px !important; }
    [data-testid="stAlert"],[data-testid="stFileUploader"],[data-testid="stFileUploaderDropzone"] { background-color: #1e293b !important; color: #e2e8f0 !important; border-color: #475569 !important; }
    [data-testid="stProgress"] > div { background-color: #334155 !important; }
    [data-testid="stProgress"] > div > div { background-color: #3b82f6 !important; }
    hr { border-color: #334155 !important; }
    code,pre,[data-testid="stCode"] { background-color: #1e293b !important; color: #e2e8f0 !important; border: 1px solid #334155 !important; }
    @media (max-width: 640px) { .stApp { background-color: #0f172a !important; } [data-testid="metric-container"] { background-color: #1e293b !important; border: 1px solid #334155 !important; } }
    </style>""", unsafe_allow_html=True)

# ── Page dispatch ─────────────────────────────────────────────────────────────
menu = st.session_state.selected_menu

# Lazy import and dispatch to the right page module
if menu in ["Dashboard", "Global Search"]:
    from pages.dashboard import render
elif menu == "Calendar View":
    from pages.calendar import render
elif menu in ["Add Client", "View Clients", "Edit Client", "Client History",
               "Client Portal", "Retention Report", "Communication Log"]:
    from pages.clients import render
elif menu in ["Add Appointment", "View Appointments", "Edit Appointment",
               "Delete Appointment", "Job Checklist", "Appointment Templates",
               "Recurring Scheduler"]:
    from pages.appointments import render
elif menu in ["Payment Tracking", "Invoice Status", "Invoice Generator",
               "Quote Generator", "Quote Tracking", "Reports / Export",
               "Mileage / Tax Report", "Expense Tracker", "Profit & Loss",
               "Follow-Up Tracker", "Referral Analytics"]:
    from pages.finance import render
elif menu in ["Email Templates", "Map / Route Tools", "Document Attachments",
               "Signing Day Sheet", "Service Area Map", "Notary Journal"]:
    from pages.tools import render
elif menu in ["Admin / System Health", "Cloud Database Setup",
               "Settings / Business Profile", "Backup / Restore"]:
    from pages.admin import render
else:
    from pages.dashboard import render

render(menu, settings, dark_mode)
