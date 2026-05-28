# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
from utils import *


def render(menu, settings, dark_mode):
    """Render dashboard pages."""
    menu = st.session_state.get("selected_menu", menu)
    settings = settings or get_settings()

    if menu == "Dashboard":
        st.header("Dashboard")
    
        df = get_all_appointments_dataframe()
    
        if df.empty:
            st.info("No appointments yet.")
        else:
            df["fee"] = pd.to_numeric(df["fee"], errors="coerce").fillna(0)
            df["mileage"] = pd.to_numeric(df["mileage"], errors="coerce").fillna(0)
            df["duration_minutes"] = pd.to_numeric(df["duration_minutes"], errors="coerce").fillna(60)
            df["appointment_date"] = pd.to_datetime(df["appointment_date"], errors="coerce")
            df["month"] = df["appointment_date"].dt.strftime("%Y-%m")
    
            today = pd.Timestamp(date.today())
            upcoming_df = df[
                (df["appointment_date"] >= today)
                & (df["status"].isin(["Scheduled", "Awaiting Payment"]))
            ].sort_values(["appointment_date", "appointment_time"]).head(10)
    
            total_paid_actual, total_unpaid, df = get_dashboard_payment_summary(df)
    
            total_appointments = len(df)
            total_fees = df["fee"].sum()
            total_mileage = df["mileage"].sum()
    
            # HTML metric grid — immune to Streamlit's column layout on mobile
            is_dark = dark_mode
            card_bg = "#1e293b" if is_dark else "#f8fafc"
            card_border = "#334155" if is_dark else "#e2e8f0"
            label_color = "#94a3b8" if is_dark else "#64748b"
            value_color = "#f1f5f9" if is_dark else "#0f172a"
    
            st.markdown(f"""
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:0.5rem;margin-bottom:0.75rem;">
                <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                    <div style="font-size:0.75rem;color:{label_color};font-weight:500;">Appointments</div>
                    <div style="font-size:1.4rem;font-weight:700;color:{value_color};">{total_appointments}</div>
                </div>
                <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                    <div style="font-size:0.75rem;color:{label_color};font-weight:500;">Total Billed</div>
                    <div style="font-size:1.4rem;font-weight:700;color:{value_color};">${total_fees:,.0f}</div>
                </div>
                <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                    <div style="font-size:0.75rem;color:{label_color};font-weight:500;">Paid Invoices</div>
                    <div style="font-size:1.4rem;font-weight:700;color:#22c55e;">${total_paid_actual:,.0f}</div>
                </div>
                <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                    <div style="font-size:0.75rem;color:{label_color};font-weight:500;">Balance Due</div>
                    <div style="font-size:1.4rem;font-weight:700;color:#f59e0b;">${total_unpaid:,.0f}</div>
                </div>
                <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                    <div style="font-size:0.75rem;color:{label_color};font-weight:500;">Mileage</div>
                    <div style="font-size:1.4rem;font-weight:700;color:{value_color};">{total_mileage:,.1f} mi</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
    
            # Revenue goal tracker
            revenue_goal = float(settings.get("revenue_goal") or 0)
            if revenue_goal > 0:
                current_month = pd.Timestamp(date.today()).strftime("%Y-%m")
                month_revenue = df[df["month"] == current_month]["fee"].sum()
                pct = min(month_revenue / revenue_goal, 1.0)
                st.divider()
                st.subheader(f"Monthly Revenue Goal — {pd.Timestamp(date.today()).strftime('%B %Y')}")
                goal_col1, goal_col2 = st.columns([3, 1])
                with goal_col1:
                    st.progress(pct)
                with goal_col2:
                    st.write(f"**${month_revenue:,.0f}** / ${revenue_goal:,.0f}")
                if pct >= 1.0:
                    st.success("🎉 Monthly goal reached!")
                else:
                    remaining = revenue_goal - month_revenue
                    st.caption(f"${remaining:,.0f} remaining to hit your goal")
    
            invoice_df = get_invoice_status_dataframe(df)
            paid_count = len(invoice_df[invoice_df["invoice_status"] == "Paid"])
            unpaid_count = len(invoice_df[invoice_df["invoice_status"] == "Unpaid"])
            partial_count = len(invoice_df[invoice_df["invoice_status"] == "Partially Paid"])
            overdue_count = len(invoice_df[invoice_df["invoice_status"] == "Overdue"])
    
            st.subheader("Invoice Status")
            st.markdown(f"""
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:0.5rem;margin-bottom:0.75rem;">
                <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                    <div style="font-size:0.75rem;color:{label_color};font-weight:500;">✅ Paid</div>
                    <div style="font-size:1.4rem;font-weight:700;color:#22c55e;">{paid_count}</div>
                </div>
                <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                    <div style="font-size:0.75rem;color:{label_color};font-weight:500;">🔲 Unpaid</div>
                    <div style="font-size:1.4rem;font-weight:700;color:{value_color};">{unpaid_count}</div>
                </div>
                <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                    <div style="font-size:0.75rem;color:{label_color};font-weight:500;">⏳ Partial</div>
                    <div style="font-size:1.4rem;font-weight:700;color:#f59e0b;">{partial_count}</div>
                </div>
                <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:0.6rem 0.75rem;">
                    <div style="font-size:0.75rem;color:{label_color};font-weight:500;">🔴 Overdue</div>
                    <div style="font-size:1.4rem;font-weight:700;color:#ef4444;">{overdue_count}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
    
            if overdue_count > 0:
                st.warning(f"⚠️ {overdue_count} invoice(s) are overdue.")
    
            # Pending follow-up alerts
            followups_df = get_followups_dataframe()
            if not followups_df.empty:
                today_str = str(date.today())
                pending_followups = followups_df[
                    (followups_df["completed"] == 0) &
                    (followups_df["followup_date"] <= today_str)
                ]
                if not pending_followups.empty:
                    st.warning(f"📋 {len(pending_followups)} follow-up(s) are due or overdue.")
                    with st.expander("View Pending Follow-Ups"):
                        st.dataframe(
                            pending_followups[["followup_date", "client_name", "followup_type", "outcome", "notes"]],
                            use_container_width=True
                        )
    
            st.divider()
    
            st.subheader("Upcoming Appointments")
    
            if upcoming_df.empty:
                st.success("No upcoming appointments.")
            else:
                st.dataframe(
                    upcoming_df[
                        [
                            "appointment_date",
                            "appointment_time",
                            "end_time",
                            "client_name",
                            "signing_type",
                            "location",
                            "status"
                        ]
                    ],
                    use_container_width=True
                )
    
            st.divider()
    
            st.subheader("Paid Invoices")
            paid_invoice_df = df[(df["paid_amount"] > 0) | (df["status"] == "Paid")].copy()
    
            if paid_invoice_df.empty:
                st.info("No paid invoices yet.")
            else:
                st.dataframe(
                    paid_invoice_df[
                        [
                            "appointment_date",
                            "appointment_time",
                            "client_name",
                            "signing_type",
                            "fee",
                            "paid_amount",
                            "balance_due",
                            "status"
                        ]
                    ].sort_values(["appointment_date", "appointment_time"], ascending=False),
                    use_container_width=True
                )
    
            st.divider()
    
            col1, col2 = st.columns(2)
    
            with col1:
                st.subheader("Revenue by Month")
                st.bar_chart(df.groupby("month")["fee"].sum())
    
            with col2:
                st.subheader("Appointments by Status")
                st.bar_chart(df["status"].value_counts())
    
            # Quarterly income summary
            st.divider()
            st.subheader("Quarterly Income Summary")
            df["quarter"] = df["appointment_date"].dt.to_period("Q").astype(str)
            quarterly = df.groupby("quarter").agg(
                appointments=("id", "count"),
                billed=("fee", "sum"),
                mileage=("mileage", "sum")
            ).reset_index()
            quarterly["mileage_deduction"] = quarterly["mileage"] * float(settings.get("default_mileage_rate") or 0.67)
            quarterly.columns = ["Quarter", "Appointments", "Billed ($)", "Miles", "Mileage Deduction ($)"]
            quarterly["Billed ($)"] = quarterly["Billed ($)"].map("${:,.2f}".format)
            quarterly["Mileage Deduction ($)"] = quarterly["Mileage Deduction ($)"].map("${:,.2f}".format)
            quarterly["Miles"] = quarterly["Miles"].map("{:,.1f}".format)
            st.dataframe(quarterly, use_container_width=True, hide_index=True)
    
            col1, col2 = st.columns(2)
    
            with col1:
                st.subheader("Revenue by Signing Type")
                st.bar_chart(df.groupby("signing_type")["fee"].sum().sort_values(ascending=False))
    
            with col2:
                st.subheader("Mileage by Signing Type")
                st.bar_chart(df.groupby("signing_type")["mileage"].sum().sort_values(ascending=False))
    
            # ── Year-over-Year Revenue Comparison ──────────────────────────────
            st.divider()
            st.subheader("Year-over-Year Revenue")
            df["year"] = df["appointment_date"].dt.year
            df["month_num"] = df["appointment_date"].dt.month
            years = sorted(df["year"].dropna().unique().astype(int), reverse=True)
    
            if len(years) >= 2:
                yoy_data = {}
                month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
                for yr in years[:3]:  # Show up to 3 years
                    monthly = df[df["year"] == yr].groupby("month_num")["fee"].sum()
                    yoy_data[str(yr)] = [monthly.get(m, 0) for m in range(1, 13)]
    
                yoy_df = pd.DataFrame(yoy_data, index=month_names)
                st.bar_chart(yoy_df)
    
                # Running annual totals
                st.caption("Annual totals:")
                ann_cols = st.columns(min(len(years[:3]), 3))
                for i, yr in enumerate(years[:3]):
                    annual_total = df[df["year"] == yr]["fee"].sum()
                    ann_cols[i].metric(str(yr), f"${annual_total:,.0f}")
            else:
                st.info("Add appointments across multiple years to see year-over-year comparison.")
    
            # ── Busiest Days / Times Heatmap ───────────────────────────────────
            st.divider()
            st.subheader("Busiest Days & Times")
            df["day_of_week"] = df["appointment_date"].dt.day_name()
            df["hour"] = pd.to_datetime(df["appointment_time"], format="%H:%M", errors="coerce").dt.hour
    
            day_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
            day_counts = df["day_of_week"].value_counts().reindex(day_order, fill_value=0)
    
            heat_col1, heat_col2 = st.columns(2)
            with heat_col1:
                st.caption("Appointments by day of week")
                st.bar_chart(day_counts)
    
            with heat_col2:
                st.caption("Appointments by hour of day")
                hour_counts = df["hour"].value_counts().sort_index()
                if not hour_counts.empty:
                    hour_counts.index = [f"{h:02d}:00" for h in hour_counts.index]
                    st.bar_chart(hour_counts)
    
            # ── Revenue vs Expenses ────────────────────────────────────────────
            st.divider()
            st.subheader("Revenue vs Expenses")
            exp_df_dash = get_expenses_dataframe()
            if not exp_df_dash.empty:
                exp_df_dash["expense_date"] = pd.to_datetime(exp_df_dash["expense_date"], errors="coerce")
                exp_df_dash["amount"] = pd.to_numeric(exp_df_dash["amount"], errors="coerce").fillna(0)
                exp_df_dash["month"] = exp_df_dash["expense_date"].dt.strftime("%Y-%m")
                monthly_expenses = exp_df_dash.groupby("month")["amount"].sum()
                monthly_revenue = df.groupby("month")["fee"].sum()
                rev_exp_df = pd.DataFrame({
                    "Revenue": monthly_revenue,
                    "Expenses": monthly_expenses
                }).fillna(0).sort_index()
                if not rev_exp_df.empty:
                    st.bar_chart(rev_exp_df)
            else:
                st.caption("Add expenses in the Expense Tracker to see revenue vs expenses here.")
    
            # ── Notification digest button ──────────────────────────────────────
            st.divider()
            notify_col1, notify_col2 = st.columns([3, 1])
            with notify_col1:
                st.caption("Send yourself a daily digest with tomorrow's appointments and overdue invoices.")
            with notify_col2:
                if st.button("📬 Send Daily Digest"):
                    with st.spinner("Sending..."):
                        ok, err = send_daily_digest(settings)
                    if ok:
                        st.success("Digest sent!")
                    else:
                        st.warning(err)
    
            # ── Real-time refresh button ────────────────────────────────────────
            rt_col1, rt_col2 = st.columns([3, 1])
            with rt_col1:
                st.caption("Force a fresh data pull from Supabase.")
            with rt_col2:
                if st.button("🔄 Refresh Data"):
                    clear_all_caches()
                    st.rerun()
    
    
    
    elif menu == "Global Search":
        st.header("🔍 Global Search")
        st.caption("Search across all clients, appointments, notes, and locations.")
    
        query = st.text_input("Search", placeholder="Client name, location, signing type, date...",
                              label_visibility="collapsed")
    
        if query:
            with st.spinner("Searching..."):
                results = global_search(query)
    
            if not results:
                st.info(f"No results found for **{query}**")
            else:
                total = sum(
                    len(v) if not hasattr(v, '__len__') else
                    len(v) if isinstance(v, list) else len(v)
                    for v in results.values()
                )
                st.success(f"Found results across {len(results)} category(ies)")
    
                if "clients" in results:
                    st.subheader(f"👤 Clients ({len(results['clients'])})")
                    for c in results["clients"]:
                        with st.expander(f"{c[1]} — {c[3] or 'No email'}"):
                            st.write(f"📞 {c[2] or '—'}")
                            st.write(f"📍 {c[4] or '—'}")
                            if st.button("View History", key=f"gs_client_{c[0]}"):
                                st.session_state.selected_menu = "Client History"
                                st.session_state["history_client_id"] = c[0]
                                st.rerun()
    
                if "appointments" in results:
                    appts = results["appointments"]
                    st.subheader(f"📅 Appointments ({len(appts)})")
                    all_paid = get_all_payment_totals()
                    for _, row in appts.sort_values("appointment_date", ascending=False).iterrows():
                        paid = all_paid.get(int(row["id"]), 0.0)
                        balance = max(float(row.get("fee") or 0) - paid, 0)
                        with st.expander(
                            f"{row['appointment_date']} — {row['client_name']} — "
                            f"{row['signing_type']} — {row['status']}"
                        ):
                            st.write(f"📍 {row.get('location') or '—'}")
                            st.write(f"💵 Fee: ${float(row.get('fee') or 0):,.2f}  "
                                    f"Paid: ${paid:,.2f}  Balance: ${balance:,.2f}")
                            if row.get("notes"):
                                st.caption(f"Notes: {row['notes']}")
        else:
            st.caption("Start typing to search across your entire database.")
    
    

