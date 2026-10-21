import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import unicodedata
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import re

# -------------------------------------------------------------------
# CONFIGURACIÓN DE PÁGINA
# -------------------------------------------------------------------
st.set_page_config(
    page_title="Control de Asistencia",
    page_icon="🏫",
    layout="wide"
)

# HORARIOS DE INICIO DE CADA BLOQUE
BLOQUES_HORARIOS_INICIO = {
    1: "07:40",
    2: "09:10",
    3: "10:45",
    4: "12:15",
    5: "12:55",
    6: "13:45",
    7: "15:10"
}

def obtener_hora_inicio_bloque(bloque_val):
    try:
        match = re.search(r'\d+', str(bloque_val))
        if match:
            num_bloque = int(match.group())
            return BLOQUES_HORARIOS_INICIO.get(num_bloque, None), num_bloque
    except Exception:
        pass
    return None, None

# -------------------------------------------------------------------
# FUNCIONES DE NORMALIZACIÓN Y AYUDA
# -------------------------------------------------------------------
def normalizar_texto(texto):
    if not texto:
        return ""
    texto = str(texto).lower().strip()
    texto = unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('utf-8')
    texto = texto.replace(',', ' ').replace('-', ' ').replace('_', ' ')
    return " ".join(texto.split())

# -------------------------------------------------------------------
# CONEXIÓN A GOOGLE SHEETS
# -------------------------------------------------------------------
@st.cache_resource
def conectar_google_sheets():
    try:
        credentials = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        gc = gspread.authorize(credentials)
        sh = gc.open_by_key(st.secrets["spreadsheet_id"])
        return sh
    except Exception as e:
        st.error(f"Error al conectar con Google Sheets: {e}")
        return None

sh = conectar_google_sheets()

# -------------------------------------------------------------------
# FUNCIÓN CARGAR Y PREPARAR PADRÓN
# -------------------------------------------------------------------
def cargar_padron():
    try:
        sheet_padron = sh.worksheet("Padron_Comisiones")
        df_padron = pd.DataFrame(sheet_padron.get_all_records())
        
        col_map = {}
        for c in df_padron.columns:
            c_clean = str(c).strip()
            c_norm = normalizar_texto(c_clean)
            if "division" in c_norm:
                col_map[c] = "Division"
            elif "alumno" in c_norm:
                col_map[c] = "Alumno"
            elif "materia" in c_norm and "docente" in c_norm:
                col_map[c] = "Materia - Docente"
            elif "materia" in c_norm:
                col_map[c] = "Materia"
            elif "docente" in c_norm:
                col_map[c] = "Docente"
            else:
                col_map[c] = c_clean
                
        df_padron = df_padron.rename(columns=col_map)
        
        if "Materia - Docente" not in df_padron.columns:
            if "Materia" in df_padron.columns and "Docente" in df_padron.columns:
                df_padron["Materia - Docente"] = df_padron["Materia"].astype(str).str.strip() + " - " + df_padron["Docente"].astype(str).str.strip()
        
        return df_padron
    except Exception as e:
        st.error(f"Error al cargar Padron_Comisiones: {e}")
        return pd.DataFrame()

# -------------------------------------------------------------------
# BÚSQUEDA ROBUSTA DE HOJA DE ASISTENCIA
# -------------------------------------------------------------------
def buscar_hoja_asistencia(sh, letra_div):
    letra_target = letra_div.upper().strip()
    try:
        return sh.worksheet(f"Asistencia {letra_target}")
    except Exception:
        pass
    
    try:
        for ws in sh.worksheets():
            t_norm = normalizar_texto(ws.title)
            if f"asistencia {letra_target.lower()}" in t_norm or t_norm.endswith(letra_target.lower()):
                return ws
    except Exception:
        pass
    return None

# -------------------------------------------------------------------
# ENVÍO DE EMAIL DE ALERTA (A ELIAN@BUBER.EDU.AR)
# -------------------------------------------------------------------
def enviar_email_alerta(ausentes, comision, docente, fecha, hora):
    try:
        if "email" not in st.secrets:
            return

        remitente = st.secrets["email"]["user"]
        password = st.secrets["email"]["password"]
        
        destinatarios = [
            "Elian@buber.edu.ar"
        ]

        msg = MIMEMultipart()
        msg["From"] = remitente
        msg["To"] = ", ".join(destinatarios)
        msg["Subject"] = f"🚨 ALERTA DE AUSENCIA / RETIRO ANTICIPADO - {comision}"

        lista_alumnos = "\n".join([f"• {a}" for a in ausentes])
        cuerpo = f"""
Se ha registrado un reporte de inasistencia en aula / retiro anticipado:

📅 Fecha / Hora: {fecha} - {hora} hs
📚 Comisión / Grupo: {comision}
👨‍🏫 Docente a cargo: {docente}

⚠️ Alumnos ausentes en el aula:
{lista_alumnos}

---
Mensaje enviado automáticamente por la App de Control de Asistencia.
"""
        msg.attach(MIMEText(cuerpo, "plain"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(remitente, password)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        st.warning(f"No se pudo enviar la notificación por mail: {e}")

# -------------------------------------------------------------------
# FUNCIÓN REGISTRAR RETIRO
# -------------------------------------------------------------------
def registrar_fuga(ausentes, comision, docente, fecha, hora):
    try:
        # 1. Guarda en Google Sheets (Pestaña Fugas)
        try:
            sheet_fugas = sh.worksheet("Fugas")
            for alum in ausentes:
                sheet_fugas.append_row([fecha, hora, alum, comision, docente, "Reportado por Docente"])
        except Exception:
            pass
        
        # 2. Envía alerta por Mail
        enviar_email_alerta(ausentes, comision, docente, fecha, hora)
        return True
    except Exception as e:
        st.error(f"Error al registrar reporte: {e}")
        return False

# -------------------------------------------------------------------
