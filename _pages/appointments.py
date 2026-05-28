# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
from utils import *


def render(menu, settings, dark_mode):
    """Render appointments pages."""
    menu = st.session_state.get("selected_menu", menu)
    settings = settings or get_settings()

    if menu == "Add Appointment":
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

            if client is None:
                st.error("Could not load client details. Please try selecting the client again.")
                st.stop()

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
    
    

