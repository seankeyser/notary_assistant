import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
from utils import *


def render(menu, settings, dark_mode):
    """Render finance pages."""
    menu = st.session_state.get("selected_menu", menu)
    settings = settings or get_settings()

    if menu == "Payment Tracking":
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
    
    

