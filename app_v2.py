import os
import sqlite3
from datetime import date, time

import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


DB_NAME = "notary_assistant_v2.db"
BUSINESS_NAME = "Sean M. Keyser"
BUSINESS_SUBTITLE = "Notary & Signing Agent"
TAGLINE = "Sign with confidence. Seal with trust."
LOGO_FILE = "quill_logo.png"


st.set_page_config(
    page_title="Keyser Notary Assistant",
    page_icon="🖋️",
    layout="wide"
)


def load_css():
    if os.path.exists("styles.css"):
        with open("styles.css", "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


load_css()


def get_connection():
    return sqlite3.connect(DB_NAME)


def create_tables():
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            contact_name TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            notes TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT,
            company_name TEXT,
            client_name TEXT,
            signing_type TEXT,
            appointment_date TEXT,
            appointment_time TEXT,
            location TEXT,
            status TEXT,
            agreed_fee REAL DEFAULT 0,
            amount_paid REAL DEFAULT 0,
            mileage REAL DEFAULT 0,
            invoice_status TEXT,
            payment_status TEXT,
            notes TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expense_date TEXT,
            category TEXT,
            vendor TEXT,
            amount REAL DEFAULT 0,
            notes TEXT
        )
    """)

    conn.commit()
    conn.close()


def load_table(table):
    conn = get_connection()
    df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
    conn.close()
    return df


def add_company(company_name, contact_name, phone, email, address, notes):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO companies
        (company_name, contact_name, phone, email, address, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (company_name, contact_name, phone, email, address, notes))
    conn.commit()
    conn.close()


def add_order(
    order_number, company_name, client_name, signing_type, appointment_date,
    appointment_time, location, status, agreed_fee, amount_paid, mileage,
    invoice_status, payment_status, notes
):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO orders (
            order_number, company_name, client_name, signing_type,
            appointment_date, appointment_time, location, status,
            agreed_fee, amount_paid, mileage, invoice_status,
            payment_status, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        order_number, company_name, client_name, signing_type,
        str(appointment_date), str(appointment_time), location, status,
        agreed_fee, amount_paid, mileage, invoice_status, payment_status, notes
    ))
    conn.commit()
    conn.close()


def update_order(
    order_id, order_number, company_name, client_name, signing_type,
    appointment_date, appointment_time, location, status, agreed_fee,
    amount_paid, mileage, invoice_status, payment_status, notes
):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE orders
        SET order_number = ?, company_name = ?, client_name = ?,
            signing_type = ?, appointment_date = ?, appointment_time = ?,
            location = ?, status = ?, agreed_fee = ?, amount_paid = ?,
            mileage = ?, invoice_status = ?, payment_status = ?, notes = ?
        WHERE id = ?
    """, (
        order_number, company_name, client_name, signing_type,
        str(appointment_date), str(appointment_time), location, status,
        agreed_fee, amount_paid, mileage, invoice_status, payment_status,
        notes, order_id
    ))
    conn.commit()
    conn.close()


def delete_order(order_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()


def add_expense(expense_date, category, vendor, amount, notes):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO expenses
        (expense_date, category, vendor, amount, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (str(expense_date), category, vendor, amount, notes))
    conn.commit()
    conn.close()


def money(value):
    return float(value or 0)


def prepare_orders(df):
    if df.empty:
        return df

    for col in ["agreed_fee", "amount_paid", "mileage"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["balance_due"] = df["agreed_fee"] - df["amount_paid"]
    return df


def generate_invoice(order):
    os.makedirs("invoices", exist_ok=True)
    filename = f"invoices/order_{order['id']}.pdf"

    agreed_fee = money(order.get("agreed_fee", 0))
    amount_paid = money(order.get("amount_paid", 0))
    balance_due = agreed_fee - amount_paid
    mileage = money(order.get("mileage", 0))

    pdf = canvas.Canvas(filename, pagesize=letter)

    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(50, 750, BUSINESS_NAME)

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, 728, BUSINESS_SUBTITLE)

    pdf.setFont("Helvetica-Oblique", 11)
    pdf.drawString(50, 710, TAGLINE)

    pdf.line(50, 695, 550, 695)

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(50, 665, "INVOICE")

    y = 630
    pdf.setFont("Helvetica", 11)

    fields = [
        ("Order Number", order.get("order_number", "")),
        ("Company", order.get("company_name", "")),
        ("Signer", order.get("client_name", "")),
        ("Signing Type", order.get("signing_type", "")),
        ("Appointment Date", order.get("appointment_date", "")),
        ("Appointment Time", order.get("appointment_time", "")),
        ("Location", order.get("location", "")),
        ("Order Status", order.get("status", "")),
        ("Invoice Status", order.get("invoice_status", "")),
        ("Payment Status", order.get("payment_status", "")),
    ]

    for label, value in fields:
        pdf.drawString(50, y, f"{label}: {value}")
        y -= 22

    y -= 10
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(50, y, "Financial Summary")
    y -= 28

    pdf.setFont("Helvetica", 11)
    pdf.drawString(50, y, f"Agreed Fee: ${agreed_fee:,.2f}")
    y -= 22
    pdf.drawString(50, y, f"Amount Paid: ${amount_paid:,.2f}")
    y -= 22
    pdf.drawString(50, y, f"Balance Due: ${balance_due:,.2f}")
    y -= 22
    pdf.drawString(50, y, f"Mileage: {mileage:,.1f} miles")
    y -= 35

    notes = str(order.get("notes", "") or "")
    if notes:
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(50, y, "Notes:")
        y -= 16
        pdf.setFont("Helvetica", 10)
        pdf.drawString(50, y, notes[:150])

    pdf.save()
    return filename


def render_brand_header():
    if os.path.exists(LOGO_FILE):
        col1, col2 = st.columns([1, 3])
        with col1:
            st.image(LOGO_FILE, width=220)
        with col2:
            st.markdown(f"""
            <div class="brand-header">
                <h1>{BUSINESS_NAME}</h1>
                <h2>{BUSINESS_SUBTITLE}</h2>
                <p>{TAGLINE}</p>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div class="brand-header">
            <h1>{BUSINESS_NAME}</h1>
            <h2>{BUSINESS_SUBTITLE}</h2>
            <p>{TAGLINE}</p>
        </div>
        """, unsafe_allow_html=True)


def metric_card(title, value, note=""):
    st.markdown(f"""
    <div class="metric-card">
        <h4>{title}</h4>
        <h2>{value}</h2>
        <p>{note}</p>
    </div>
    """, unsafe_allow_html=True)


create_tables()
render_brand_header()


menu = st.sidebar.radio(
    "Navigation",
    [
        "Dashboard",
        "Companies",
        "Add Order",
        "Manage Orders",
        "Expenses",
        "Reports"
    ]
)


if menu == "Dashboard":
    st.header("Dashboard")

    orders = prepare_orders(load_table("orders"))
    expenses = load_table("expenses")
    companies = load_table("companies")

    total_expenses = 0
    if not expenses.empty:
        expenses["amount"] = pd.to_numeric(expenses["amount"], errors="coerce").fillna(0)
        total_expenses = expenses["amount"].sum()

    if orders.empty:
        st.info("No orders yet.")
    else:
        net_income = orders["amount_paid"].sum() - total_expenses

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            metric_card("TOTAL ORDERS", len(orders), "All time")
        with col2:
            metric_card("INCOME PAID", f"${orders['amount_paid'].sum():,.2f}", "Collected")
        with col3:
            metric_card("OUTSTANDING", f"${orders['balance_due'].sum():,.2f}", "Balance due")
        with col4:
            metric_card("TOTAL MILEAGE", f"{orders['mileage'].sum():,.1f}", "Miles")

        col5, col6, col7 = st.columns(3)
        with col5:
            metric_card("EXPENSES", f"${total_expenses:,.2f}", "Recorded")
        with col6:
            metric_card("NET INCOME", f"${net_income:,.2f}", "Paid minus expenses")
        with col7:
            metric_card("COMPANIES", len(companies), "Saved")

        st.subheader("Recent Orders")
        st.dataframe(
            orders.sort_values("appointment_date", ascending=False).head(10),
            use_container_width=True
        )


elif menu == "Companies":
    st.header("Companies / Signing Services")

    with st.form("add_company_form"):
        company_name = st.text_input("Company Name")
        contact_name = st.text_input("Contact Name")
        phone = st.text_input("Phone")
        email = st.text_input("Email")
        address = st.text_area("Address")
        notes = st.text_area("Notes")

        if st.form_submit_button("Save Company"):
            if company_name.strip():
                add_company(company_name, contact_name, phone, email, address, notes)
                st.success("Company saved.")
                st.rerun()
            else:
                st.error("Company name is required.")

    st.subheader("Saved Companies")
    st.dataframe(load_table("companies"), use_container_width=True)


elif menu == "Add Order":
    st.header("Add Signing Order")

    companies = load_table("companies")

    with st.form("add_order_form"):
        order_number = st.text_input("Order Number")

        if not companies.empty:
            company_name = st.selectbox("Company", companies["company_name"].tolist())
        else:
            company_name = st.text_input("Company Name")

        client_name = st.text_input("Signer / Client Name")

        signing_type = st.selectbox(
            "Signing Type",
            [
                "Loan Signing", "Refinance", "Purchase", "Seller Package",
                "HELOC", "Reverse Mortgage", "General Notary",
                "Power of Attorney", "I-9 Verification", "Mobile Fingerprinting",
                "Apostille", "Remote Online Notary", "Other"
            ]
        )

        col1, col2 = st.columns(2)
        with col1:
            appointment_date = st.date_input("Appointment Date", value=date.today())
        with col2:
            appointment_time = st.time_input("Appointment Time", value=time(9, 0))

        location = st.text_area("Signing Location")

        status = st.selectbox(
            "Order Status",
            [
                "Scheduled", "Completed", "Docs Dropped", "Scanbacks Sent",
                "Invoiced", "Paid", "Canceled", "No Show", "Follow-up Needed"
            ]
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            agreed_fee = st.number_input("Agreed Fee", min_value=0.0, step=5.0)
        with col2:
            amount_paid = st.number_input("Amount Paid", min_value=0.0, step=5.0)
        with col3:
            mileage = st.number_input("Mileage", min_value=0.0, step=1.0)

        invoice_status = st.selectbox("Invoice Status", ["Not Sent", "Sent", "Overdue", "Paid", "Not Required"])
        payment_status = st.selectbox("Payment Status", ["Unpaid", "Partial", "Paid", "Overdue", "Canceled"])
        notes = st.text_area("Order Notes")

        if st.form_submit_button("Save Order"):
            if client_name.strip():
                add_order(
                    order_number, company_name, client_name, signing_type,
                    appointment_date, appointment_time, location, status,
                    agreed_fee, amount_paid, mileage, invoice_status,
                    payment_status, notes
                )
                st.success("Order saved.")
                st.rerun()
            else:
                st.error("Client/signer name is required.")


elif menu == "Manage Orders":
    st.header("Manage Orders")

    orders = prepare_orders(load_table("orders"))

    if orders.empty:
        st.info("No orders yet.")
    else:
        search = st.text_input("Search orders")

        if search:
            orders = orders[
                orders.apply(
                    lambda row: search.lower() in row.astype(str).str.lower().to_string(),
                    axis=1
                )
            ]

        payment_filter = st.selectbox("Filter by Payment Status", ["All", "Unpaid", "Partial", "Paid", "Overdue", "Canceled"])

        if payment_filter != "All":
            orders = orders[orders["payment_status"] == payment_filter]

        for _, row in orders.iterrows():
            title = f"{row['client_name']} | {row['company_name']} | {row['appointment_date']} | Balance: ${row['balance_due']:,.2f}"

            with st.expander(title):
                with st.form(f"edit_order_{row['id']}"):
                    order_number = st.text_input("Order Number", value=row["order_number"] or "")
                    company_name = st.text_input("Company", value=row["company_name"] or "")
                    client_name = st.text_input("Signer / Client", value=row["client_name"] or "")
                    signing_type = st.text_input("Signing Type", value=row["signing_type"] or "")
                    appointment_date = st.text_input("Date", value=row["appointment_date"] or "")
                    appointment_time = st.text_input("Time", value=row["appointment_time"] or "")
                    location = st.text_area("Location", value=row["location"] or "")
                    status = st.text_input("Order Status", value=row["status"] or "")

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        agreed_fee = st.number_input("Agreed Fee", value=money(row["agreed_fee"]), step=5.0, key=f"agreed_{row['id']}")
                    with col2:
                        amount_paid = st.number_input("Amount Paid", value=money(row["amount_paid"]), step=5.0, key=f"paid_{row['id']}")
                    with col3:
                        mileage = st.number_input("Mileage", value=money(row["mileage"]), step=1.0, key=f"mileage_{row['id']}")

                    invoice_status = st.text_input("Invoice Status", value=row["invoice_status"] or "")
                    payment_status = st.text_input("Payment Status", value=row["payment_status"] or "")
                    notes = st.text_area("Notes", value=row["notes"] or "")

                    if st.form_submit_button("Update Order"):
                        update_order(
                            row["id"], order_number, company_name, client_name,
                            signing_type, appointment_date, appointment_time, location,
                            status, agreed_fee, amount_paid, mileage, invoice_status,
                            payment_status, notes
                        )
                        st.success("Order updated.")
                        st.rerun()

                st.subheader("Invoice")

                invoice_file = generate_invoice(row)

                with open(invoice_file, "rb") as pdf_file:
                    st.download_button(
                        label="Download Invoice PDF",
                        data=pdf_file,
                        file_name=os.path.basename(invoice_file),
                        mime="application/pdf",
                        key=f"download_invoice_{row['id']}"
                    )

                if st.button("Delete Order", key=f"delete_order_{row['id']}"):
                    delete_order(row["id"])
                    st.success("Order deleted.")
                    st.rerun()


elif menu == "Expenses":
    st.header("Expenses")

    with st.form("expense_form"):
        expense_date = st.date_input("Expense Date", value=date.today())
        category = st.selectbox(
            "Category",
            ["Printing", "Paper", "Ink / Toner", "Postage", "Mileage / Fuel", "Software", "Marketing", "Supplies", "Training", "Other"]
        )
        vendor = st.text_input("Vendor")
        amount = st.number_input("Amount", min_value=0.0, step=1.0)
        notes = st.text_area("Notes")

        if st.form_submit_button("Save Expense"):
            add_expense(expense_date, category, vendor, amount, notes)
            st.success("Expense saved.")
            st.rerun()

    st.subheader("Saved Expenses")
    st.dataframe(load_table("expenses"), use_container_width=True)


elif menu == "Reports":
    st.header("Reports")

    orders = prepare_orders(load_table("orders"))
    expenses = load_table("expenses")

    if orders.empty:
        st.info("No report data yet.")
    else:
        total_expenses = 0
        if not expenses.empty:
            expenses["amount"] = pd.to_numeric(expenses["amount"], errors="coerce").fillna(0)
            total_expenses = expenses["amount"].sum()

        net_income = orders["amount_paid"].sum() - total_expenses

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Agreed Revenue", f"${orders['agreed_fee'].sum():,.2f}")
        col2.metric("Paid Income", f"${orders['amount_paid'].sum():,.2f}")
        col3.metric("Outstanding", f"${orders['balance_due'].sum():,.2f}")
        col4.metric("Net Income", f"${net_income:,.2f}")

        st.subheader("Revenue by Company")
        st.dataframe(orders.groupby("company_name")["amount_paid"].sum().reset_index(), use_container_width=True)

        st.subheader("Revenue by Signing Type")
        st.dataframe(orders.groupby("signing_type")["amount_paid"].sum().reset_index(), use_container_width=True)

        st.subheader("Outstanding Balances")
        st.dataframe(orders[orders["balance_due"] > 0], use_container_width=True)

        st.subheader("Expenses by Category")
        if expenses.empty:
            st.info("No expenses yet.")
        else:
            st.dataframe(expenses.groupby("category")["amount"].sum().reset_index(), use_container_width=True)