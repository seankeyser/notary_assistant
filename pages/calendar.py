# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
from utils import *


def render(menu, settings, dark_mode):
    """Render calendar pages."""
    menu = st.session_state.get("selected_menu", menu)
    settings = settings or get_settings()

    if menu == "Calendar View":
        st.header("Calendar View")
    
        df = get_all_appointments_dataframe()
    
        if df.empty:
            st.info("No appointments to display on the calendar.")
        else:
            df["appointment_date"] = pd.to_datetime(df["appointment_date"], errors="coerce")
            df = df.dropna(subset=["appointment_date"])
    
            calendar_events = []
    
            for _, row in df.iterrows():
                appointment_time = row["appointment_time"] if row["appointment_time"] else "09:00"
                end_time = row["end_time"] if row["end_time"] else "10:00"
    
                start_datetime = f"{row['appointment_date'].strftime('%Y-%m-%d')}T{appointment_time}"
                end_datetime = f"{row['appointment_date'].strftime('%Y-%m-%d')}T{end_time}"
    
                calendar_events.append({
                    "title": f"{row['client_name']} - {row['signing_type']}",
                    "start": start_datetime,
                    "end": end_datetime,
                    "backgroundColor": status_color(row["status"]),
                    "borderColor": status_color(row["status"])
                })
    
            calendar_options = {
                "initialView": "dayGridMonth",
                "height": 650,
                "headerToolbar": {
                    "left": "prev,next today",
                    "center": "title",
                    "right": "dayGridMonth,timeGridWeek,timeGridDay,listWeek"
                },
                "eventDisplay": "block"
            }
    
            calendar(events=calendar_events, options=calendar_options, key="notary_calendar")
    
            st.divider()
            st.subheader("Appointment Agenda")
    
            st.dataframe(
                df[
                    [
                        "appointment_date",
                        "appointment_time",
                        "end_time",
                        "client_name",
                        "signing_type",
                        "location",
                        "fee",
                        "mileage",
                        "status"
                    ]
                ].sort_values(["appointment_date", "appointment_time"]),
                use_container_width=True
            )
    
    

