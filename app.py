import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------------------------------------------------
# CONFIGURACIÓN DE PÁGINA
# ---------------------------------------------------------
st.set_page_config(
    page_title="Control de Asistencia",
    page_icon="🏫",
    layout="centered"
)

# ---------------------------------------------------------
# CONEXIÓN A GOOGLE SHEETS
# ---------------------------------------------------------
@st.cache_resource
def conectar_google_sheets():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_dict = dict(st.secrets["gcp_service_account"])
    # Corregir formato de clave privada si viene con saltos de línea
    if "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(credentials)
    return client.open_by_key(st.secrets["spreadsheet_id"])

try:
    sh = conectar_google_sheets()
except Exception as e:
    st.error(f"Error al conectar con Google Sheets: {e}")
    st.stop()

# ---------------------------------------------------------
# FUNCIONES AUXILIARES
# ---------------------------------------------------------
@st.cache_data(ttl=60)
def cargar_padron():
    try:
        ws = sh.worksheet("Padron_Comisiones")
        datos = ws.get_all_records()
        df = pd.DataFrame(datos)
        
        # Limpieza de nombres de columnas
        df.columns = [str(col).strip() for col in df.columns]
        
        # Renombrar columnas si existen
        col_map = {}
        for c in df.columns:
            if c.lower() in ["estudiante", "alumno"]:
                col_map[c] = "Alumno"
            elif c.lower() in ["división", "division"]:
                col_map[c] = "Division"
            elif c.lower() == "materia":
                col_map[c] = "Materia"
            elif c.lower() in ["docente", "profesor"]:
                col_map[c] = "Docente"
        
        df = df.rename(columns=col_map)
        
        # Crear columna identificadora de comisión
        if "Materia" in df.columns and "Docente" in df.columns:
            df["Materia_Comision"] = df["Materia"].astype(str) + " - " + df["Docente"].astype(str)
        return df
    except Exception as e:
        st.error(f"Error cargando Padron_Comisiones: {e}")
        return pd.DataFrame()

def obtener_alumnos_esperados(comision_seleccionada, fecha_str):
    hojas_asistencia = ["Asistencia A", "Asistencia B", "Asistencia C", "Asistencia D"]
    estado_alumnos = {}

    for nombre_hoja in hojas_asistencia:
        try:
            ws = sh.worksheet(nombre_hoja)
            datos = ws.get_all_values()
            if not datos or len(datos) < 2:
                continue
            
            headers = [str(h).strip() for h in datos[0]]
            col_idx = -1
            for idx, h in enumerate(headers):
                if h == fecha_str or h.startswith(fecha_str):
                    col_idx = idx
                    break
            
            for fila in datos[1:]:
                if len(fila) > 0:
                    nombre_alumno = str(fila[0]).strip()
                    estado = str(fila[col_idx]).strip().upper() if (col_idx != -1 and len(fila) > col_idx) else "P"
                    estado_alumnos[nombre_alumno] = estado
        except Exception:
            continue

    df_padron = cargar_padron()
    if df_padron.empty:
        return pd.DataFrame()

    df_padron["Alumno"] = df_padron["Alumno"].astype(str).str.strip()
    df_padron["Materia_Comision"] = df_padron["Materia_Comision"].astype(str).str.strip()

    df_comision = df_padron[df_padron["Materia_Comision"] == comision_seleccionada].copy()
    df_comision["Estado_Matutino"] = df_comision["Alumno"].map(lambda x: estado_alumnos.get(x, "P"))
    
    # P, T1 (tomados como presentes) y T (llegadas tardes) están presentes en la escuela
    estados_validos_en_escuela = ["P", "T1", "T"]
    
    esperados = df_comision[df_comision["Estado_Matutino"].isin(estados_validos_en_escuela)].copy()
    return esperados.sort_values(by="Alumno")

def enviar_email_alerta(alumnos_fuga, comision, docente, fecha_str, hora_str):
    try:
        cfg = st.secrets["email"]
        msg = MIMEMultipart()
        msg['From'] = cfg["sender_email"]
        msg['To'] = ", ".join(cfg["recipients"])
        msg['Subject'] = f"⚠️ ALERTA DE FUGA EN AULA - {comision}"

        lista_alumnos = "\n".join([f"- {a}" for a in alumnos_fuga])
        cuerpo = f"""
        Se ha registrado una inasistencia no justificada en aula (fuga):

        • Fecha y Hora: {fecha_str} {hora_str}
        • Materia / Comisión: {comision}
        • Docente: {docente}

        Estudiantes ausentes en aula:
        {lista_alumnos}

        Este mensaje fue generado automáticamente por el sistema de Control de Asistencia.
        """
        msg.attach(MIMEText(cuerpo, 'plain'))

        with smtplib.SMTP_SSL(cfg["smtp_server"], int(cfg["smtp_port"])) as server:
            server.login(cfg["sender_email"], cfg["sender_password"])
            server.sendmail(cfg["sender_email"], cfg["recipients"], msg.as_string())
        return True
    except Exception as e:
        st.warning(f"No se pudo enviar el correo de alerta: {e}")
        return False

def registrar_fuga(alumnos_fuga, comision, docente, fecha_str, hora_str):
    try:
        ws = sh.worksheet("Alertas_Fugas")
        filas = []
        for alumno in alumnos_fuga:
            filas.append([fecha_str, hora_str, alumno, comision, docente])
        ws.append_rows(filas)
        enviar_email_alerta(alumnos_fuga, comision, docente, fecha_str, hora_str)
        return True
    except Exception as e:
        st.error(f"Error al registrar alerta en Google Sheets: {e}")
        return False

# ---------------------------------------------------------
# INTERFAZ DE USUARIO
# ---------------------------------------------------------
st.title("🏫 Control de Asistencia en Aula")

df_padron = cargar_padron()

if df_padron.empty:
    st.warning("No se encontraron datos en 'Padron_Comisiones'. Por favor verifica la planilla.")
    st.stop()

# Selección de fecha y hora actual
ahora = datetime.now()
fecha_hoy = ahora.strftime("%d/%m")
hora_actual = ahora.strftime("%H:%M")

tab1, tab2 = st.tabs(["👤 Buscar por Docente", "📚 Buscar por Materia"])

comision_seleccionada = None
docente_actual = ""

with tab1:
    docentes_disponibles = sorted(df_padron["Docente"].dropna().unique().tolist())
    docente_sel = st.selectbox("Seleccione Docente:", ["-- Seleccionar --"] + docentes_disponibles)
    
    if docente_sel != "-- Seleccionar --":
        df_doc = df_padron[df_padron["Docente"] == docente_sel]
        materias_doc = sorted(df_doc["Materia_Comision"].unique().tolist())
        comision_seleccionada = st.selectbox("Seleccione Materia / Grupo:", materias_doc, key="sel_doc")
        docente_actual = docente_sel

with tab2:
    materias_disponibles = sorted(df_padron["Materia"].dropna().unique().tolist())
    materia_sel = st.selectbox("Seleccione Materia:", ["-- Seleccionar --"] + materias_disponibles)
    
    if materia_sel != "-- Seleccionar --":
        df_mat = df_padron[df_padron["Materia"] == materia_sel]
        comisiones_mat = sorted(df_mat["Materia_Comision"].unique().tolist())
        comision_seleccionada = st.selectbox("Seleccione Grupo / Docente:", comisiones_mat, key="sel_mat")
        if not df_mat.empty:
            docente_actual = df_mat[df_mat["Materia_Comision"] == comision_seleccionada]["Docente"].values[0]

# ---------------------------------------------------------
# LISTADO DE ALUMNOS Y TOMA DE ASISTENCIA
# ---------------------------------------------------------
if comision_seleccionada:
    st.divider()
    st.subheader(f"📋 Alumnos esperados en aula")
    st.caption(f"Comisión: **{comision_seleccionada}** | Fecha: **{fecha_hoy}**")

    df_esperados = obtener_alumnos_esperados(comision_seleccionada, fecha_hoy)

    if df_esperados.empty:
        st.info("No hay alumnos esperados para esta comisión (o todos están ausentes en el colegio).")
    else:
        st.write("Desmarque los alumnos que **NO están presentes** en el aula:")
        
        ausentes_en_aula = []
        for _, row in df_esperados.iterrows():
            alumno_nombre = row["Alumno"]
            # Por defecto marcado como Presente (True)
            presente = st.checkbox(f"{alumno_nombre} (División {row['Division']})", value=True, key=f"chk_{alumno_nombre}")
            if not presente:
                ausentes_en_aula.append(alumno_nombre)

        st.divider()
        if st.button("🚨 Confirmar Asistencia / Reportar Ausentes", type="primary"):
            if ausentes_en_aula:
                exito = registrar_fuga(ausentes_en_aula, comision_seleccionada, docente_actual, fecha_hoy, hora_actual)
                if exito:
                    st.error(f"⚠️ Alerta registrada y enviada para {len(ausentes_en_aula)} alumno(s) ausente(s).")
            else:
                st.success("✅ Asistencia completa. Todos los alumnos esperados están presentes en el aula.")
