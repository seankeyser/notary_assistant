# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
from utils import *


def render(menu, settings, dark_mode):
    """Render clients pages."""
    menu = st.session_state.get("selected_menu", menu)
    settings = settings or get_settings()

    if menu == "Add Client":
        st.header("Add Client")

        with st.form("client_form"):
            client_name = st.text_input("Client Name")
            phone = st.text_input("Phone")
            email = st.text_input("Email")
            address = st.text_input("Address")
            referral_source = st.selectbox("How did they hear about you?", REFERRAL_SOURCES)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Client")

        # Handle submission OUTSIDE the form so we can show buttons freely
        if submitted:
            errors = validate_client_inputs(client_name, email)
            if errors:
                for error in errors:
                    st.error(error)
            else:
                existing_clients = get_clients()
                name_lower = client_name.strip().lower()
                duplicates = [
                    c for c in existing_clients
                    if c[1].strip().lower() == name_lower
                    or (email and c[3] and c[3].strip().lower() == email.strip().lower())
                ]
                if duplicates and not st.session_state.get("confirm_add_client"):
                    # Store form values so we can save after confirmation
                    st.session_state["pending_client"] = {
                        "client_name": client_name, "phone": phone, "email": email,
                        "address": address, "referral_source": referral_source, "notes": notes
                    }
                    st.warning(
                        f"A client named **{duplicates[0][1]}** already exists "
                        f"(ID {duplicates[0][0]}). Save anyway?"
                    )
                else:
                    st.session_state.pop("confirm_add_client", None)
                    st.session_state.pop("pending_client", None)
                    add_client(client_name, phone, email, address, referral_source, notes)
                    st.success("Client saved successfully!")

        # Duplicate confirmation buttons — outside the form, only shown when needed
        if st.session_state.get("pending_client"):
            col1, col2 = st.columns(2)
            with col1:
                if st.button("✅ Yes, save as new client", type="primary"):
                    p = st.session_state.pop("pending_client")
                    st.session_state.pop("confirm_add_client", None)
                    add_client(p["client_name"], p["phone"], p["email"],
                               p["address"], p["referral_source"], p["notes"])
                    st.success("Client saved successfully!")
                    st.rerun()
            with col2:
                if st.button("❌ Cancel"):
                    st.session_state.pop("pending_client", None)
                    st.session_state.pop("confirm_add_client", None)
                    st.rerun()
    
    
    
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
    
    

