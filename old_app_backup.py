import os
import streamlit as st

from config import (
    APP_NAME,
    BUSINESS_NAME,
    BUSINESS_TITLE,
    TAGLINE,
    LOGO_PATH
)

from database import create_tables


# -----------------------------
# PAGE CONFIG
# -----------------------------

st.set_page_config(
    page_title=APP_NAME,
    page_icon="🖋️",
    layout="wide"
)


# -----------------------------
# LOAD CSS
# -----------------------------

if os.path.exists("styles/styles.css"):
    with open("styles/styles.css", "r", encoding="utf-8") as f:
        st.markdown(
            f"<style>{f.read()}</style>",
            unsafe_allow_html=True
        )


# -----------------------------
# INITIALIZE DATABASE
# -----------------------------

create_tables()


# -----------------------------
# HEADER
# -----------------------------

col1, col2 = st.columns([1, 3])

with col1:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=220)

with col2:
    st.markdown(f"""
    <div class="brand-header">
        <h1>{BUSINESS_NAME}</h1>
        <h2>{BUSINESS_TITLE}</h2>
        <p>{TAGLINE}</p>
    </div>
    """, unsafe_allow_html=True)


# -----------------------------
# DASHBOARD PLACEHOLDER
# -----------------------------

st.header("Dashboard")

st.info(
    "V3 foundation created successfully. "
    "Next we will build Orders, Companies, Expenses, Reports, and Invoices."
)

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown("""
    <div class="metric-card">
        <h4>TOTAL ORDERS</h4>
        <h2>0</h2>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown("""
    <div class="metric-card">
        <h4>REVENUE</h4>
        <h2>$0</h2>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown("""
    <div class="metric-card">
        <h4>OUTSTANDING</h4>
        <h2>$0</h2>
    </div>
    """, unsafe_allow_html=True)

with col4:
    st.markdown("""
    <div class="metric-card">
        <h4>MILEAGE</h4>
        <h2>0</h2>
    </div>
    """, unsafe_allow_html=True)

st.divider()

st.subheader("Recent Orders")

st.write(
    "Orders table will appear here once we build the Orders module."
)