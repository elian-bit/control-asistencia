import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone, timedelta
import unicodedata
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import re

# -------------------------------------------------------------------
# CONFIGURACIÓN DE PÁGINA Y ZONA HORARIA
# -------------------------------------------------------------------
st.set_page_config(
    page_title="Control de Asistencia",
    page_icon="🏫",
    layout="wide"
)

# ZONA HORARIA ARGENTINA (UTC-3)
TZ_ARG = timezone(timedelta(hours=-3))

def obtener_ahora():
    return datetime.now(TZ_ARG)

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

def normalizar_division(div):
    if not div or pd.isna(div):
        return ""
    d = str(div).strip().upper()
    d = re.sub(r'[^A-Z0-9]', '', d)
    return d

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
        
        if "Division" in df_padron.columns:
            df_padron["Division_Clean"] = df_padron["Division"].apply(normalizar_division)
        else:
            df_padron["Division_Clean"] = ""

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
        try:
            sheet_fugas = sh.worksheet("Fugas")
            for alum in ausentes:
                sheet_fugas.append_row([fecha, hora, alum, comision, docente, "Reportado por Docente"])
        except Exception:
            pass
        
        enviar_email_alerta(ausentes, comision, docente, fecha, hora)
        return True
    except Exception as e:
        st.error(f"Error al registrar reporte: {e}")
        return False

# -------------------------------------------------------------------
# FUNCIÓN OBTENER ALUMNOS Y ASISTENCIA CON FILTRO EXCLUSIVO DE DIVISIÓN
# -------------------------------------------------------------------
def obtener_alumnos_esperados(df_padron, comision_seleccionada, division_seleccionada):
    try:
        com_norm = normalizar_texto(comision_seleccionada)
        df_padron["_com_norm"] = df_padron["Materia - Docente"].apply(normalizar_texto)
        df_grupo = df_padron[df_padron["_com_norm"] == com_norm].copy()
        
        if df_grupo.empty:
            return pd.DataFrame()

        # Filtrar por división si se especificó una distinta a "Todas las divisiones"
        if division_seleccionada and division_seleccionada not in ["Todas las divisiones", "-- Seleccionar Curso --"]:
            div_norm_target = normalizar_division(division_seleccionada)
            df_grupo = df_grupo[df_grupo["Division_Clean"] == div_norm_target]

        if df_grupo.empty:
            return pd.DataFrame()

        df_grupo["Estado"] = "Presente"
        df_grupo["InfoRetiro"] = ""
        df_grupo["_alum_norm"] = df_grupo["Alumno"].apply(normalizar_texto)

        hoy_dt = obtener_ahora()
        hora_actual_str = hoy_dt.strftime("%H:%M")
        fecha_buscar_1 = f"{hoy_dt.day}/{hoy_dt.month}"
        fecha_buscar_2 = hoy_dt.strftime("%d/%m")
        fecha_buscar_3 = hoy_dt.strftime("%d/%m/%Y")

        divisiones = df_grupo["Division_Clean"].unique() if "Division_Clean" in df_grupo.columns else []
        
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

            col_alum_det = -1
            col_fecha_det = -1
            col_salida_det = -1

            for idx, h in enumerate(headers):
                h_norm = normalizar_texto(h)
                if "alumno" in h_norm and "deta" in h_norm:
                    col_alum_det = idx
                elif "fecha" in h_norm and "deta" in h_norm:
                    col_fecha_det = idx
                elif "bloque" in h_norm and "salida" in h_norm:
                    col_salida_det = idx

            detalles_retiro = {}
            if col_alum_det != -1 and col_salida_det != -1:
                for row in values[1:]:
                    if len(row) > max(col_alum_det, col_salida_det):
                        nombre_det = normalizar_texto(row[col_alum_det])
                        fecha_det = str(row[col_fecha_det]).strip() if col_fecha_det != -1 and len(row) > col_fecha_det else ""
                        bloque_val = row[col_salida_det]
                        
                        if not fecha_det or fecha_buscar_1 in fecha_det or fecha_buscar_2 in fecha_det or fecha_buscar_3 in fecha_det:
                            hora_ini, num_b = obtener_hora_inicio_bloque(bloque_val)
                            if num_b and hora_ini:
                                detalles_retiro[nombre_det] = {
                                    "bloque": num_b,
                                    "hora_inicio": hora_ini
                                }

            for row in values[1:]:
                if len(row) > 0:
                    nombre_alum_hoja = normalizar_texto(row[0])
                    val_asistencia = str(row[col_idx]).strip().upper() if col_idx != -1 and len(row) > col_idx else ""
                    
                    if val_asistencia in ["A", "AUSENTE"] or val_asistencia.startswith("A "):
                        df_grupo.loc[df_grupo["_alum_norm"] == nombre_alum_hoja, "Estado"] = "Ausente"
                    
                    elif val_asistencia in ["R", "RETIRO", "RETIRADO"] or val_asistencia.startswith("R ") or nombre_alum_hoja in detalles_retiro:
                        if nombre_alum_hoja in detalles_retiro:
                            info_det = detalles_retiro[nombre_alum_hoja]
                            hora_ini = info_det["hora_inicio"]
                            num_b = info_det["bloque"]
                            
                            if hora_actual_str < hora_ini:
                                df_grupo.loc[df_grupo["_alum_norm"] == nombre_alum_hoja, "Estado"] = "Presente"
                                df_grupo.loc[df_grupo["_alum_norm"] == nombre_alum_hoja, "InfoRetiro"] = f"🚪 (Retiro prog.: Bloque {num_b} - {hora_ini} hs)"
                            else:
                                df_grupo.loc[df_grupo["_alum_norm"] == nombre_alum_hoja, "Estado"] = "Retirado"
                        else:
                            df_grupo.loc[df_grupo["_alum_norm"] == nombre_alum_hoja, "Estado"] = "Retirado"

        return df_grupo
    except Exception as e:
        st.error(f"Error al procesar la asistencia: {e}")
        return pd.DataFrame()

# -------------------------------------------------------------------
# DETECCIÓN DE LOGO Y ENCABEZADO
# -------------------------------------------------------------------
logo_encontrado = None
posibles_nombres = ["logo.png", "logo.PNG", "logo.jpg", "logo.jpeg", "Logo.png", "Logo.PNG"]

for nombre in posibles_nombres:
    if os.path.exists(nombre):
        logo_encontrado = nombre
        break

if logo_encontrado:
    col_logo, col_titulo = st.columns([0.8, 9.2], vertical_alignment="center")
    with col_logo:
        st.image(logo_encontrado, width=65)
    with col_titulo:
        st.title("Control de Asistencia en Aula")
else:
    st.title("🏫 Control de Asistencia en Aula")

hoy_dt = obtener_ahora()
fecha_hoy = hoy_dt.strftime("%d/%m/%Y")
hora_actual = hoy_dt.strftime("%H:%M")

comision_seleccionada = None
division_seleccionada = "Todas las divisiones"
docente_actual = ""

if sh:
    df_padron = cargar_padron()
    
    if not df_padron.empty:
        col_filtro1, col_filtro2, col_filtro3 = st.columns(3)
        
        with col_filtro1:
            docentes = sorted(df_padron["Docente"].astype(str).str.strip().unique().tolist())
            docente_sel = st.selectbox("1. Seleccione Docente:", ["-- Seleccionar Docente --"] + docentes, key="sel_docente_main")
        
        if docente_sel and docente_sel != "-- Seleccionar Docente --":
            docente_actual = docente_sel
            df_docente = df_padron[df_padron["Docente"].astype(str).str.strip() == docente_sel]
            materias_docente = sorted(df_docente["Materia - Docente"].unique().tolist())
            
            with col_filtro2:
                comision_docente = st.selectbox("2. Seleccione Materia / Grupo:", ["-- Seleccionar Materia --"] + materias_docente, key="sel_mat_main")
                
            if comision_docente and comision_docente != "-- Seleccionar Materia --":
                comision_seleccionada = comision_docente
                
                df_sub = df_docente[df_docente["Materia - Docente"] == comision_docente]
                divs_posibles = sorted([d for d in df_sub["Division_Clean"].unique() if d])
                
                # Si hay más de una división, muestra el selector opcional (preseleccionado en "Todas las divisiones")
                if len(divs_posibles) > 1:
                    with col_filtro3:
                        division_seleccionada = st.selectbox(
                            "3. Filtrar por Curso / División (Opcional):",
                            ["Todas las divisiones"] + divs_posibles,
                            key="sel_div_main"
                        )
                else:
                    division_seleccionada = "Todas las divisiones"

# -------------------------------------------------------------------
# VISTA EN DOS COLUMNAS (PRESENTES Y AUSENTES/RETIRADOS)
# -------------------------------------------------------------------
if comision_seleccionada and division_seleccionada:
    st.divider()
    col_hdr1, col_hdr2 = st.columns([3, 1])
    with col_hdr1:
        st.subheader("📋 Alumnos esperados en aula")
        div_label = f" | Curso: **{division_seleccionada}**" if division_seleccionada != "Todas las divisiones" else " | Curso: **Todos los cursos**"
        st.caption(f"Comisión: **{comision_seleccionada}**{div_label} | Fecha: **{fecha_hoy}**")
    with col_hdr2:
        if st.button("🔄 Actualizar Datos", help="Haga clic para sincronizar las novedades más recientes del colegio"):
            st.rerun()

    df_esperados = obtener_alumnos_esperados(df_padron, comision_seleccionada, division_seleccionada)

    if df_esperados.empty:
        st.info("No se encontraron alumnos para este filtro en Padron_Comisiones.")
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
                
                info_ret = str(row.get("InfoRetiro", "")).strip()
                nombre_alumno = f"{row['Alumno']} ({row['Division']})"
                label_check = f"{nombre_alumno} {info_ret}" if info_ret else nombre_alumno
                
                esta_presente = st.checkbox(
                    label_check, 
                    value=True, 
                    key=f"check_{index}_{row['Alumno']}"
                )
                
                if esta_presente:
                    presentes_en_aula.append(row['Alumno'])
                else:
                    ausentes_en_aula.append(row['Alumno'])

        # COLUMNA DERECHA: Ausentes y Retirados
        with col_ausentes:
            st.subheader("❌ Ausentes / Retiros anticipados")
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
                st.markdown("**🚨 Ausentes en Aula / Retiro anticipado:**")
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
