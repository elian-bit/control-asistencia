import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import unicodedata
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# -------------------------------------------------------------------
# CONFIGURACIÓN DE PÁGINA
# -------------------------------------------------------------------
st.set_page_config(
    page_title="Control de Asistencia",
    page_icon="🏫",
    layout="wide"
)

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
# ENVÍO DE EMAIL DE ALERTA (SOLO A ELIAN@BUBER.EDU.AR)
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
        msg["Subject"] = f"🚨 ALERTA DE FUGA / AUSENCIA EN AULA - {comision}"

        lista_alumnos = "\n".join([f"• {a}" for a in ausentes])
        cuerpo = f"""
Se ha registrado un reporte de inasistencia en aula / posible fuga:

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
# FUNCIÓN REGISTRAR FUGA
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
# FUNCIÓN OBTENER ALUMNOS Y ASISTENCIA DIARIA
# -------------------------------------------------------------------
def obtener_alumnos_esperados(df_padron, comision_seleccionada):
    try:
        com_norm = normalizar_texto(comision_seleccionada)
        df_padron["_com_norm"] = df_padron["Materia - Docente"].apply(normalizar_texto)
        df_grupo = df_padron[df_padron["_com_norm"] == com_norm].copy()
        
        if df_grupo.empty:
            return pd.DataFrame()

        df_grupo["Estado"] = "Presente"
        df_grupo["_alum_norm"] = df_grupo["Alumno"].apply(normalizar_texto)

        hoy_dt = datetime.now()
        fecha_buscar_1 = f"{hoy_dt.day}/{hoy_dt.month}"
        fecha_buscar_2 = hoy_dt.strftime("%d/%m")
        fecha_buscar_3 = hoy_dt.strftime("%d/%m/%Y")

        divisiones = df_grupo["Division"].unique() if "Division" in df_grupo.columns else []
        
        for div in divisiones:
            div_str = str(div).strip()
            letra_div = "".join([c for c in div_str if c.isalpha()]).upper()
            if not letra_div:
                letra_div = div_str
                
            sheet_div = buscar_hoja_asistencia(sh, letra_div)
            if not sheet_div:
                continue

            values = sheet_div.get_all_values()
            if not values or len(values) < 2:
                continue

            headers = [str(h).strip() for h in values[0]]
            
            col_idx = -1
            for idx, h in enumerate(headers):
                h_norm = h.replace(" ", "")
                if fecha_buscar_1 in h_norm or fecha_buscar_2 in h_norm or fecha_buscar_3 in h_norm:
                    col_idx = idx
                    break

            if col_idx != -1:
                for row in values[1:]:
                    if len(row) > col_idx:
                        nombre_alum_hoja = normalizar_texto(row[0])
                        val_asistencia = str(row[col_idx]).strip().upper()
                        
                        if val_asistencia in ["A", "AUSENTE"] or val_asistencia.startswith("A "):
                            df_grupo.loc[df_grupo["_alum_norm"] == nombre_alum_hoja, "Estado"] = "Ausente"
                        elif val_asistencia in ["R", "RETIRO", "RETIRADO"] or val_asistencia.startswith("R "):
                            df_grupo.loc[df_grupo["_alum_norm"] == nombre_alum_hoja, "Estado"] = "Retirado"

        return df_grupo
    except Exception as e:
        st.error(f"Error al procesar la asistencia: {e}")
        return pd.DataFrame()

# -------------------------------------------------------------------
# INTERFAZ PRINCIPAL Y SELECCIÓN DE COMISIÓN
# -------------------------------------------------------------------
st.title("🏫 Control de Asistencia en Aula")

hoy_dt = datetime.now()
fecha_hoy = hoy_dt.strftime("%d/%m/%Y")
hora_actual = hoy_dt.strftime("%H:%M")

comision_seleccionada = None
docente_actual = ""

if sh:
    df_padron = cargar_padron()
    
    if not df_padron.empty:
        tab1, tab2 = st.tabs(["👤 Buscar por Docente", "📚 Buscar por Materia"])
        
        with tab1:
            if "Docente" in df_padron.columns:
                docentes = sorted(df_padron["Docente"].astype(str).str.strip().unique().tolist())
                docente_sel = st.selectbox("Seleccione Docente:", docentes, key="sel_docente")
                
                df_docente = df_padron[df_padron["Docente"].astype(str).str.strip() == docente_sel]
                materias_docente = df_docente["Materia - Docente"].unique().tolist()
                
                comision_docente = st.selectbox("Seleccione Materia / Grupo:", materias_docente, key="sel_mat_doc")
                if comision_docente:
                    comision_seleccionada = comision_docente
                    docente_actual = docente_sel

        with tab2:
            if "Materia - Docente" in df_padron.columns:
                comisiones_todas = sorted(df_padron["Materia - Docente"].astype(str).str.strip().unique().tolist())
                comision_mat = st.selectbox("Seleccione Materia / Grupo:", comisiones_todas, key="sel_mat_todas")
                if comision_mat and not comision_seleccionada:
                    comision_seleccionada = comision_mat
                    docente_actual = df_padron[df_padron["Materia - Docente"] == comision_mat]["Docente"].values[0]

# -------------------------------------------------------------------
# VISTA EN DOS COLUMNAS (PRESENTES Y AUSENTES/RETIRADOS)
# -------------------------------------------------------------------
if comision_seleccionada:
    st.divider()
    col_hdr1, col_hdr2 = st.columns([3, 1])
    with col_hdr1:
        st.subheader("📋 Alumnos esperados en aula")
        st.caption(f"Comisión: **{comision_seleccionada}** | Fecha: **{fecha_hoy}**")
    with col_hdr2:
        if st.button("🔄 Actualizar Datos", help="Haga clic para sincronizar las novedades más recientes del colegio"):
            st.rerun()

    df_esperados = obtener_alumnos_esperados(df_padron, comision_seleccionada)

    if df_esperados.empty:
        st.info("No se encontraron alumnos para esta comisión en Padron_Comisiones.")
    else:
        col_presentes, col_ausentes = st.columns(2)

        ausentes_en_aula = []
        ausentes_colegio = []
        retirados_colegio = []
        presentes_en_aula = []

        for index, row in df_esperados.iterrows():
            est = str(row.get("Estado", "")).strip().lower()
            if est == "ausente":
                ausentes_colegio.append(f"{row['Alumno']} ({row['Division']})")
            elif est == "retirado":
                retirados_colegio.append(f"{row['Alumno']} ({row['Division']})")

        # COLUMNA IZQUIERDA: Presentes en Aula
        with col_presentes:
            st.subheader("✅ Presentes en Aula")
            st.caption("Desmarque a quienes NO estén en la clase")
            
            for index, row in df_esperados.iterrows():
                est = str(row.get("Estado", "")).strip().lower()
                if est in ["ausente", "retirado"]:
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

        # COLUMNA DERECHA: Ausentes y Retirados
        with col_ausentes:
            st.subheader("❌ Ausentes / Fugas")
            st.caption("Alumnos fuera del aula o del colegio")
            
            if ausentes_colegio:
                st.markdown("**🏠 Ausentes en el Colegio (Planilla Diaria):**")
                for alum in ausentes_colegio:
                    st.warning(f"• {alum}")
            
            if retirados_colegio:
                st.markdown("**🚪 Retirados del Colegio:**")
                for alum in retirados_colegio:
                    st.warning(f"• {alum} *(Retirado)*")
            
            if ausentes_en_aula:
                st.markdown("**🚨 Ausentes en Aula / Posible Fuga:**")
                for ausente in ausentes_en_aula:
                    st.error(f"⚠️ {ausente}")
            
            if not ausentes_colegio and not retirados_colegio and not ausentes_en_aula:
                st.success("Todos los alumnos de esta comisión están presentes en la clase.")

        st.divider()
        
        # BOTÓN DE CONFIRMACIÓN
        if st.button("🚨 Confirmar Asistencia / Reportar Ausentes", type="primary"):
            if ausentes_en_aula:
                exito = registrar_fuga(ausentes_en_aula, comision_seleccionada, docente_actual, fecha_hoy, hora_actual)
                if exito:
                    st.error(f"⚠️ Alerta registrada y enviada por mail para {len(ausentes_en_aula)} alumno(s) ausente(s) en aula.")
            else:
                st.success("✅ Asistencia confirmada.")
