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
    layout="wide"
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

# -------------------------------------------------------------------
# FUNCIÓN PARA OBTENER ALUMNOS Y CRUZAR CON LA ASISTENCIA DIARIA
# -------------------------------------------------------------------
def obtener_alumnos_esperados(comision_seleccionada, fecha_hoy):
    try:
        # 1. Cargar alumnos de la comisión seleccionada desde Padron_Comisiones
        sheet_padron = sh.worksheet("Padron_Comisiones")
        df_padron = pd.DataFrame(sheet_padron.get_all_records())
        df_grupo = df_padron[df_padron["Materia - Docente"] == comision_seleccionada].copy()
        
        # Estado por defecto
        df_grupo["Estado"] = "Presente"

        # Formatos posibles para la fecha de hoy en la fila 1 de la planilla (ej: "20/10")
        hoy_dt = datetime.now()
        fecha_buscar_1 = f"{hoy_dt.day}/{hoy_dt.month}"
        fecha_buscar_2 = hoy_dt.strftime("%d/%m")

        # 2. Buscar asistencia en las pestañas Asistencia A, B, C, D
        divisiones = df_grupo["Division"].unique()
        
        for div in divisiones:
            # Obtener la letra de la división (ej: "4A" -> "A")
            letra_div = str(div).replace("4", "").strip().upper()
            nombre_pestaña = f"Asistencia {letra_div}"
            
            try:
                sheet_div = sh.worksheet(nombre_pestaña)
                records = sheet_div.get_all_records()
                if not records:
                    continue
                df_div = pd.DataFrame(records)
                
                # Identificar la columna de la fecha actual
                col_fecha = None
                for col in df_div.columns:
                    col_str = str(col).strip()
                    if col_str in [fecha_buscar_1, fecha_buscar_2]:
                        col_fecha = col
                        break
                
                # Si encontró la columna de hoy y la columna Alumno
                if col_fecha and "Alumno" in df_div.columns:
                    for _, row_div in df_div.iterrows():
                        nombre_alum = str(row_div["Alumno"]).strip()
                        val_asistencia = str(row_div[col_fecha]).strip().upper()
                        
                        # Si tiene una 'A' en la planilla diaria, está ausente en el colegio
                        if val_asistencia == "A":
                            df_grupo.loc[df_grupo["Alumno"].str.strip() == nombre_alum, "Estado"] = "Ausente"
            except Exception:
                pass

        return df_grupo
    except Exception as e:
        st.error(f"Error al procesar la asistencia: {e}")
        return pd.DataFrame()


# -------------------------------------------------------------------
# VISTA EN DOS COLUMNAS (PRESENTES Y AUSENTES)
# -------------------------------------------------------------------
if comision_seleccionada:
    st.divider()
    st.subheader("📋 Alumnos esperados en aula")
    st.caption(f"Comisión: **{comision_seleccionada}** | Fecha: **{fecha_hoy}**")

    df_esperados = obtener_alumnos_esperados(comision_seleccionada, fecha_hoy)

    if df_esperados.empty:
        st.info("No se encontraron alumnos para esta comisión en Padron_Comisiones.")
    else:
        col_presentes, col_ausentes = st.columns(2)

        ausentes_en_aula = []
        ausentes_colegio = []
        presentes_en_aula = []

        # Filtrar quiénes ya figuran ausentes en el colegio
        for index, row in df_esperados.iterrows():
            if str(row.get("Estado", "")).strip().lower() == "ausente":
                ausentes_colegio.append(f"{row['Alumno']} ({row['Division']})")

        # COLUMNA IZQUIERDA: Presentes en Aula
        with col_presentes:
            st.subheader("✅ Presentes en Aula")
            st.caption("Desmarque a quienes NO estén en la clase")
            
            for index, row in df_esperados.iterrows():
                # Si el alumno está ausente en el colegio, se omite de esta lista
                if str(row.get("Estado", "")).strip().lower() == "ausente":
                    continue
                
                nombre_alumno = f"{row['Alumno']} ({row['Division']})"
                
                esta_presente = st.checkbox(
                    nombre_alumno, 
                    value=True, 
                    key=f"check_{index}_{row['Alumno']}"
                )
                
                if esta_presente:
                    presentes_en_aula.append(row['Alumno'])
                else:
                    ausentes_en_aula.append(row['Alumno'])

        # COLUMNA DERECHA: Ausentes (Colegio + Aula)
        with col_ausentes:
            st.subheader("❌ Ausentes / Fugas")
            st.caption("Alumnos fuera del aula o del colegio")
            
            # 1. Ausentes reportados en el colegio
            if ausentes_colegio:
                st.markdown("**🏠 Ausentes en el Colegio (Planilla Diaria):**")
                for alum in ausentes_colegio:
                    st.warning(f"• {alum}")
            
            # 2. Ausentes en el aula / Fugas desmarcadas por el docente
            if ausentes_en_aula:
                st.markdown("**🚨 Ausentes en Aula / Posible Fuga:**")
                for ausente in ausentes_en_aula:
                    st.error(f"⚠️ {ausente}")
            
            if not ausentes_colegio and not ausentes_en_aula:
                st.success("Todos los alumnos de esta comisión están presentes en la clase.")

        st.divider()
        
        # BOTÓN DE CONFIRMACIÓN Y REGISTRO
        if st.button("🚨 Confirmar Asistencia / Reportar Ausentes", type="primary"):
            if ausentes_en_aula:
                exito = registrar_fuga(ausentes_en_aula, comision_seleccionada, docente_actual, fecha_hoy, hora_actual)
                if exito:
                    st.error(f"⚠️ Alerta registrada y enviada para {len(ausentes_en_aula)} alumno(s) ausente(s) en aula.")
            else:
                st.success("✅ Asistencia confirmada.")
