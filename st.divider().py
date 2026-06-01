st.divider()

st.subheader("🚨 Alerts")

unpaid_orders = len(
    orders[
        orders["payment_status"].isin(
            ["Unpaid", "Partial", "Overdue"]
        )
    ]
)

overdue_invoices = len(
    orders[
        orders["invoice_status"] == "Overdue"
    ]
)

missing_mileage = len(
    orders[
        orders["mileage"] <= 0
    ]
)

followup_needed = len(
    orders[
        orders["status"] == "Follow-up Needed"
    ]
)

col1, col2 = st.columns(2)

with col1:
    if unpaid_orders:
        st.warning(f"⚠ {unpaid_orders} unpaid orders")

    if overdue_invoices:
        st.error(f"⚠ {overdue_invoices} overdue invoices")

with col2:
    if missing_mileage:
        st.info(f"⚠ {missing_mileage} orders missing mileage")
\
    if followup_needed:
        st.warning(f"⚠ {followup_needed} orders require follow-up")

if (
    unpaid_orders == 0
    and overdue_invoices == 0
    and missing_mileage == 0
    and followup_needed == 0
):
    st.success("No alerts. Everything looks good.")