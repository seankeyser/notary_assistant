import sqlite3
from config import DB_PATH


def get_connection():
    return sqlite3.connect(DB_PATH)


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
            payment_terms TEXT,
            notes TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT,
            company_name TEXT,
            signer_name TEXT,
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


def run_query(query, params=()):
    conn = get_connection()
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    conn.close()


def fetch_all(query, params=()):
    conn = get_connection()
    c = conn.cursor()
    c.execute(query, params)
    rows = c.fetchall()
    columns = [description[0] for description in c.description]
    conn.close()
    return rows, columns