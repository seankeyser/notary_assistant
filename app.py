import os
import sqlite3
from datetime import date, time

import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


DB_NAME = "notary_assistant.db"


st.set_page_config(
    page_title="Notary Assistant",
    page_icon="🖋️",
    layout="wide"
)


# ---------------- DATABASE ----------------

def get_connection():
    return sqlite3.connect(DB_NAME)


def create_tables():
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            address TEXT,
            company TEXT,
            notes TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            client_name TEXT NOT NULL,
            signing_type TEXT,
            appointment_date TEXT,
            appointment_time TEXT,
            location TEXT,
            status TEXT,
            notary_fee REAL DEFAULT 0,
            travel_fee REAL DEFAULT 0,
            printing_fee REAL DEFAULT 0,
            scanback_fee REAL DEFAULT 0,
            mileage REAL DEFAULT 0,
            payment_method TEXT DEFAULT 'Not Paid',
            invoice_sent TEXT DEFAULT 'No',
            notes TEXT
        )
    """)

    conn.commit()
    conn.close()


def migrate_database():
    conn = get_connection()
    c = conn.cursor()

    c.execute("PRAGMA table_info(appointments)")
    existing_columns = [row[1] for row in c.fetchall()]

    required_columns = {
        "client_id": "INTEGER",
        "payment_method": "TEXT DEFAULT 'Not Paid'",
        "invoice_sent": "TEXT DEFAULT 'No'",
        "notary_fee": "REAL DEFAULT 0",
        "travel_fee": "REAL DEFAULT 0",
        "printing_fee": "REAL DEFAULT 0",
        "scanback_fee": "REAL DEFAULT 0",
        "mileage": "REAL DEFAULT 0",
    }

    for column, column_type in required_columns.items():
        if column not in existing_columns:
            c.execute(f"ALTER TABLE appointments ADD COLUMN {column} {column_type}")

    conn.commit()
    conn.close()


def load_table(table_name):
    conn = get_connection()
    df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
    conn.close()
    return df


def add_client(name, phone, email, address, company, notes):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO clients (name, phone, email, address, company, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (name, phone, email, address, company, notes))
    conn.commit()
    conn.close()


def update_client(client_id, name, phone, email, address, company, notes):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE clients
        SET name = ?, phone = ?, email = ?, address = ?, company = ?, notes = ?
        WHERE id = ?
    """, (name, phone, email, address, company, notes, client_id))
    conn.commit()
    conn.close()


def delete_client(client_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    conn.commit()
    conn.close()


def add_appointment(
    client_id,
    client_name,
    signing_type,
    appointment_date,
    appointment_time,
    location,
    status,
    notary_fee,
    travel_fee,
    printing_fee,
    scanback_fee,
    mileage,
    payment_method,
    invoice_sent,
    notes
):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO appointments (
            client_id, client_name, signing_type, appointment_date, appointment_time,
            location, status, notary_fee, travel_fee, printing_fee, scanback_fee,
            mileage, payment_method, invoice_sent, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        client_id,
        client_name,
        signing_type,
        str(appointment_date),
        str(appointment_time),
        location,
        status,
        notary_fee,
        travel_fee,
        printing_fee,
        scanback_fee,
        mileage,
        payment_method,
        invoice_sent,
        notes
    ))
    conn.commit()
    conn.close()


def update_appointment(
    appointment_id,
    client_name,
    signing_type,
    appointment_date,
    appointment_time,
    location,
    status,
    notary_fee,
    travel_fee,
    printing_fee,
    scanback_fee,
    mileage,
    payment_method,
    invoice_sent,
    notes
):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE appointments
        SET client_name = ?, signing_type = ?, appointment_date = ?, appointment_time = ?,
            location = ?, status = ?, notary_fee = ?, travel_fee = ?, printing_fee = ?,
            scanback_fee = ?, mileage = ?, payment_method = ?, invoice_sent = ?, notes = ?
        WHERE id = ?
    """, (
        client_name,
        signing_type,
        str(appointment_date),
        str(appointment_time),
        location,
        status,
        notary_fee,
        travel_fee,
        printing_fee,
        scanback_fee,
        mileage,
        payment_method,
        invoice_sent,
        notes,
        appointment_id
    ))
    conn.commit()
    conn.close()


def delete_appointment(appointment_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()


def calculate_total(row):
    return (
        float(row.get("notary_fee", 0) or 0)
        + float(row.get("travel_fee", 0) or 0)
        + float(row.get("printing_fee", 0) or 0)
        + float(row.get("scanback_fee", 0) or 0)
    )


# ---------------- PDF INVOICE ----------------

def generate_invoice(row):
    os.makedirs("invoices", exist_ok=True)

    filename = f"invoices/invoice_{row['id']}.pdf"

    notary_fee = float(row.get("notary_fee", 0) or 0)
    travel_fee = float(row.get("travel_fee", 0) or 0)
    printing_fee = float(row.get("printing_fee", 0) or 0)
    scanback_fee = float(row.get("scanback_fee", 0) or 0)
    mileage = float(row.get("mileage", 0) or 0)

    total = notary_fee + travel_fee + printing_fee + scanback_fee

    pdf = canvas.Canvas(filename, pagesize=letter)

    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(50, 750, "Keyser Notary Services")

    pdf.setFont("Helvetica", 12)
    pdf.drawString(50, 725, "Invoice / Receipt")

    pdf.line(50, 710, 550, 710)

    y = 675

    fields = [
        ("Invoice ID", row["id"]),
        ("Client", row.get("client_name", "")),
        ("Signing Type", row.get("signing_type", "")),
        ("Date", row.get("appointment_date", "")),
        ("Time", row.get("appointment_time", "")),
        ("Location", row.get("location", "")),
        ("Status", row.get("status", "")),
        ("Payment Method", row.get("payment_method", "Not Paid")),
        ("Invoice Sent", row.get("invoice_sent", "No")),
    ]

    for label, value in fields:
        pdf.drawString(50, y, f"{label}: {value}")
        y -= 22

    y -= 15
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, y, "Fees")
    y -= 25

    pdf.setFont("Helvetica", 12)
    fee_fields = [
        ("Notary Fee", notary_fee),
        ("Travel Fee", travel_fee),
        ("Printing Fee", printing_fee),
        ("Scan-back Fee", scanback_fee),
    ]

    for label, value in fee_fields:
        pdf.drawString(50, y, f"{label}: ${value:,.2f}")
        y -= 22

    pdf.drawString(50, y, f"Mileage: {mileage:,.1f} miles")
    y -= 35

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(50, y, f"Total Due: ${total:,.2f}")

    y -= 45
    pdf.setFont("Helvetica", 10)
    notes = row.get("notes", "")
    if notes:
        pdf.drawString(50, y, "Notes:")
        y -= 15
        pdf.drawString(50, y, str(notes)[:100])

    pdf.save()

    return filename


# ---------------- STARTUP ----------------

create_tables()
migrate_database()


# ---------------- APP HEADER ----------------

st.title("🖋️ Notary Assistant")
st.caption("Client management, appointments, fees, mileage, invoices, and reports")

menu = st.sidebar.radio(
    "Navigation",
    [
        "Dashboard",
        "Clients",
        "Appointments",
        "Manage Appointments",
        "Reports"
    ]
)


# ---------------- DASHBOARD ----------------

if menu == "Dashboard":
    st.header("Dashboard")

    clients = load_table("clients")
    appointments = load_table("appointments")

    if appointments.empty:
        st.info("No appointments yet.")
    else:
        appointments["total_fee"] = appointments.apply(calculate_total, axis=1)

        col1, col2, col3, col4 = st.columns(4)

        col1.metric("Clients", len(clients))
        col2.metric("Appointments", len(appointments))
        col3.metric("Revenue", f"${appointments['total_fee'].sum():,.2f}")
        col4.metric("Mileage", f"{appointments['mileage'].sum():,.1f}")

        st.subheader("Recent Appointments")
        st.dataframe(
            appointments.sort_values("appointment_date", ascending=False).head(10),
            use_container_width=True
        )


# ---------------- CLIENTS ----------------

elif menu == "Clients":
    st.header("Clients")

    tab1, tab2, tab3 = st.tabs(["Add Client", "Edit / Delete Client", "All Clients"])

    with tab1:
        with st.form("add_client_form"):
            name = st.text_input("Client Name")
            phone = st.text_input("Phone")
            email = st.text_input("Email")
            address = st.text_area("Address")
            company = st.text_input("Company / Title Agency")
            notes = st.text_area("Notes")

            submitted = st.form_submit_button("Add Client")

            if submitted:
                if name.strip():
                    add_client(name, phone, email, address, company, notes)
                    st.success("Client added.")
                    st.rerun()
                else:
                    st.error("Client name is required.")

    with tab2:
        clients = load_table("clients")

        if clients.empty:
            st.info("No clients saved yet.")
        else:
            selected_id = st.selectbox(
                "Select Client",
                clients["id"],
                format_func=lambda x: clients.loc[clients["id"] == x, "name"].iloc[0]
            )

            client = clients[clients["id"] == selected_id].iloc[0]

            with st.form("edit_client_form"):
                name = st.text_input("Client Name", value=client["name"] or "")
                phone = st.text_input("Phone", value=client["phone"] or "")
                email = st.text_input("Email", value=client["email"] or "")
                address = st.text_area("Address", value=client["address"] or "")
                company = st.text_input("Company / Title Agency", value=client["company"] or "")
                notes = st.text_area("Notes", value=client["notes"] or "")

                update = st.form_submit_button("Update Client")

                if update:
                    update_client(selected_id, name, phone, email, address, company, notes)
                    st.success("Client updated.")
                    st.rerun()

            if st.button("Delete Client", key="delete_client_button"):
                delete_client(selected_id)
                st.success("Client deleted.")
                st.rerun()

    with tab3:
        clients = load_table("clients")

        if clients.empty:
            st.info("No clients saved yet.")
        else:
            st.dataframe(clients, use_container_width=True)


# ---------------- APPOINTMENTS ----------------

elif menu == "Appointments":
    st.header("Add Appointment")

    clients = load_table("clients")

    with st.form("appointment_form"):
        if not clients.empty:
            selected_client_id = st.selectbox(
                "Client",
                clients["id"],
                format_func=lambda x: clients.loc[clients["id"] == x, "name"].iloc[0]
            )
            client_name = clients.loc[clients["id"] == selected_client_id, "name"].iloc[0]
        else:
            selected_client_id = None
            client_name = st.text_input("Client Name")

        signing_type = st.selectbox(
            "Signing Type",
            [
                "General Notary",
                "Loan Signing",
                "Power of Attorney",
                "I-9 Verification",
                "Mobile Fingerprinting",
                "Apostille",
                "Remote Online Notary",
                "Other"
            ]
        )

        col1, col2 = st.columns(2)

        with col1:
            appointment_date = st.date_input("Date", value=date.today())

        with col2:
            appointment_time = st.time_input("Time", value=time(9, 0))

        location = st.text_area("Location")

        status = st.selectbox(
            "Status",
            ["Scheduled", "Completed", "Unpaid", "Paid", "Canceled", "Follow-up Needed"]
        )

        st.subheader("Fees")

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            notary_fee = st.number_input("Notary Fee", min_value=0.0, step=5.0)

        with col2:
            travel_fee = st.number_input("Travel Fee", min_value=0.0, step=5.0)

        with col3:
            printing_fee = st.number_input("Printing Fee", min_value=0.0, step=5.0)

        with col4:
            scanback_fee = st.number_input("Scan-back Fee", min_value=0.0, step=5.0)

        mileage = st.number_input("Mileage", min_value=0.0, step=1.0)

        payment_method = st.selectbox(
            "Payment Method",
            ["Not Paid", "Cash", "Card", "Zelle", "Check", "Online", "Other"]
        )

        invoice_sent = st.selectbox("Invoice Sent?", ["No", "Yes"])

        notes = st.text_area("Notes")

        submitted = st.form_submit_button("Save Appointment")

        if submitted:
            if str(client_name).strip():
                add_appointment(
                    selected_client_id,
                    client_name,
                    signing_type,
                    appointment_date,
                    appointment_time,
                    location,
                    status,
                    notary_fee,
                    travel_fee,
                    printing_fee,
                    scanback_fee,
                    mileage,
                    payment_method,
                    invoice_sent,
                    notes
                )
                st.success("Appointment saved.")
                st.rerun()
            else:
                st.error("Client name is required.")


# ---------------- MANAGE APPOINTMENTS ----------------

elif menu == "Manage Appointments":
    st.header("Manage Appointments")

    appointments = load_table("appointments")

    if appointments.empty:
        st.info("No appointments saved yet.")
    else:
        search = st.text_input("Search appointments")

        if search:
            appointments = appointments[
                appointments.apply(
                    lambda row: search.lower() in row.astype(str).str.lower().to_string(),
                    axis=1
                )
            ]

        status_filter = st.selectbox(
            "Filter by Status",
            ["All", "Scheduled", "Completed", "Unpaid", "Paid", "Canceled", "Follow-up Needed"]
        )

        if status_filter != "All":
            appointments = appointments[appointments["status"] == status_filter]

        appointments["total_fee"] = appointments.apply(calculate_total, axis=1)

        for _, row in appointments.iterrows():
            title = f"{row['client_name']} — {row['appointment_date']} — ${row['total_fee']:,.2f}"

            with st.expander(title):
                with st.form(f"edit_appointment_{row['id']}"):
                    client_name = st.text_input(
                        "Client Name",
                        value=row.get("client_name", "") or ""
                    )

                    signing_options = [
                        "General Notary",
                        "Loan Signing",
                        "Power of Attorney",
                        "I-9 Verification",
                        "Mobile Fingerprinting",
                        "Apostille",
                        "Remote Online Notary",
                        "Other"
                    ]

                    current_signing = row.get("signing_type", "")
                    signing_index = signing_options.index(current_signing) if current_signing in signing_options else 0

                    signing_type = st.selectbox(
                        "Signing Type",
                        signing_options,
                        index=signing_index
                    )

                    appointment_date = st.text_input(
                        "Date",
                        value=row.get("appointment_date", "") or ""
                    )

                    appointment_time = st.text_input(
                        "Time",
                        value=row.get("appointment_time", "") or ""
                    )

                    location = st.text_area(
                        "Location",
                        value=row.get("location", "") or ""
                    )

                    status_options = [
                        "Scheduled",
                        "Completed",
                        "Unpaid",
                        "Paid",
                        "Canceled",
                        "Follow-up Needed"
                    ]

                    current_status = row.get("status", "")
                    status_index = status_options.index(current_status) if current_status in status_options else 0

                    status = st.selectbox(
                        "Status",
                        status_options,
                        index=status_index
                    )

                    col1, col2, col3, col4 = st.columns(4)

                    with col1:
                        notary_fee = st.number_input(
                            "Notary Fee",
                            value=float(row.get("notary_fee", 0) or 0),
                            step=5.0,
                            key=f"notary_fee_{row['id']}"
                        )

                    with col2:
                        travel_fee = st.number_input(
                            "Travel Fee",
                            value=float(row.get("travel_fee", 0) or 0),
                            step=5.0,
                            key=f"travel_fee_{row['id']}"
                        )

                    with col3:
                        printing_fee = st.number_input(
                            "Printing Fee",
                            value=float(row.get("printing_fee", 0) or 0),
                            step=5.0,
                            key=f"printing_fee_{row['id']}"
                        )

                    with col4:
                        scanback_fee = st.number_input(
                            "Scan-back Fee",
                            value=float(row.get("scanback_fee", 0) or 0),
                            step=5.0,
                            key=f"scanback_fee_{row['id']}"
                        )

                    mileage = st.number_input(
                        "Mileage",
                        value=float(row.get("mileage", 0) or 0),
                        step=1.0,
                        key=f"mileage_{row['id']}"
                    )

                    payment_options = [
                        "Not Paid",
                        "Cash",
                        "Card",
                        "Zelle",
                        "Check",
                        "Online",
                        "Other"
                    ]

                    current_payment = row.get("payment_method", "Not Paid")
                    payment_index = payment_options.index(current_payment) if current_payment in payment_options else 0

                    payment_method = st.selectbox(
                        "Payment Method",
                        payment_options,
                        index=payment_index,
                        key=f"payment_method_{row['id']}"
                    )

                    current_invoice = row.get("invoice_sent", "No")
                    invoice_index = 1 if current_invoice == "Yes" else 0

                    invoice_sent = st.selectbox(
                        "Invoice Sent?",
                        ["No", "Yes"],
                        index=invoice_index,
                        key=f"invoice_sent_{row['id']}"
                    )

                    notes = st.text_area(
                        "Notes",
                        value=row.get("notes", "") or "",
                        key=f"notes_{row['id']}"
                    )

                    update = st.form_submit_button("Update Appointment")

                    if update:
                        update_appointment(
                            row["id"],
                            client_name,
                            signing_type,
                            appointment_date,
                            appointment_time,
                            location,
                            status,
                            notary_fee,
                            travel_fee,
                            printing_fee,
                            scanback_fee,
                            mileage,
                            payment_method,
                            invoice_sent,
                            notes
                        )
                        st.success("Appointment updated.")
                        st.rerun()

                invoice_file = generate_invoice(row)

                with open(invoice_file, "rb") as pdf_file:
                    st.download_button(
                        label="Download Invoice PDF",
                        data=pdf_file,
                        file_name=os.path.basename(invoice_file),
                        mime="application/pdf",
                        key=f"invoice_{row['id']}"
                    )

                if st.button("Delete Appointment", key=f"delete_appt_{row['id']}"):
                    delete_appointment(row["id"])
                    st.success("Appointment deleted.")
                    st.rerun()


# ---------------- REPORTS ----------------

elif menu == "Reports":
    st.header("Reports")

    appointments = load_table("appointments")

    if appointments.empty:
        st.info("No report data yet.")
    else:
        appointments["total_fee"] = appointments.apply(calculate_total, axis=1)

        col1, col2, col3 = st.columns(3)

        col1.metric("Total Revenue", f"${appointments['total_fee'].sum():,.2f}")
        col2.metric("Total Mileage", f"{appointments['mileage'].sum():,.1f}")
        col3.metric("Unpaid Jobs", len(appointments[appointments["status"] == "Unpaid"]))

        st.subheader("Revenue by Signing Type")
        signing_report = appointments.groupby("signing_type")["total_fee"].sum().reset_index()
        st.dataframe(signing_report, use_container_width=True)

        st.subheader("Revenue by Status")
        status_report = appointments.groupby("status")["total_fee"].sum().reset_index()
        st.dataframe(status_report, use_container_width=True)

        st.subheader("All Appointment Data")
        st.dataframe(appointments, use_container_width=True)