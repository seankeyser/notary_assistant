import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
from utils import *


def render(menu, settings, dark_mode):
    """Render tools pages."""
    menu = st.session_state.get("selected_menu", menu)
    settings = settings or get_settings()

    if menu == "Email Templates":
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
    
    

