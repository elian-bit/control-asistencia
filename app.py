import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# -------------------------------------------------------------------
# CONFIGURACIÓN DE PÁGINA
# -------------------------------------------------------------------
st.set_page_config(
    page_title="Control de Asistencia",
    page_icon="🏫",
    layout="wide"
)

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
        
        # Mapeo y normalización de columnas para evitar fallos por tildes o espacios
        col_map = {}
        for c in df_padron.columns:
            c_clean = str(c).strip()
            if c_clean.lower() in ["división", "division"]:
                col_map[c] = "Division"
            elif c_clean.lower() in ["alumno", "alumnos"]:
                col_map[c] = "Alumno"
            elif c_clean.lower() in ["materia", "materias"]:
                col_map[c] = "Materia"
            elif c_clean.lower() in ["docente", "docentes"]:
                col_map[c] = "Docente"
            elif "materia" in c_clean.lower() and "docente" in c_clean.lower():
                col_map[c] = "Materia - Docente"
            else:
                col_map[c] = c_clean
                
        df_padron = df_padron.rename(columns=col_map)
        
        # Generar "Materia - Docente" si no vino armada
        if "Materia - Docente" not in df_padron.columns:
            if "Materia" in df_padron.columns and "Docente" in df_padron.columns:
                df_padron["Materia - Docente"] = df_padron["Materia"].astype(str).str.strip() + " - " + df_padron["Docente"].astype(str).str.strip()
        
        return df_padron
    except Exception as e:
        st.error(f"Error al cargar Padron_Comisiones: {e}")
        return pd.DataFrame()

# -------------------------------------------------------------------
# FUNCIÓN REGISTRAR FUGA
# -------------------------------------------------------------------
def registrar_fuga(ausentes, comision, docente, fecha, hora):
    try:
        try:
            sheet_fugas = sh.worksheet("Fugas")
            for alum in ausentes:
                sheet_fugas.append_row([fecha, hora, alum, comision, docente, "Reportado por Docente"])
        except Exception:
            pass
        return True
    except Exception as e:
        st.error(f"Error al registrar reporte: {e}")
        return False

# -------------------------------------------------------------------
# FUNCIÓN OBTENER ALUMNOS Y ASISTENCIA DIARIA
# -------------------------------------------------------------------
def obtener_alumnos_esperados(df_padron, comision_seleccionada):
    try:
        df_grupo = df_padron[df_padron["Materia - Docente"].astype(str).str.strip() == str(comision_seleccionada).strip()].copy()
        df_grupo["Estado"] = "Presente"

        hoy_dt = datetime.now()
        fecha_buscar_1 = f"{hoy_dt.day}/{hoy_dt.month}"
        fecha_buscar_2 = hoy_dt.strftime("%d/%m")

        divisiones = df_grupo["Division"].unique() if "Division" in df_grupo.columns else []
        
        for div in divisiones:
            letra_div = str(div).replace("4", "").strip().upper()
            nombre_pestaña = f"Asistencia {letra_div}"
            
            try:
                sheet_div = sh.worksheet(nombre_pestaña)
                records = sheet_div.get_all_records()
                if not records:
                    continue
                df_div = pd.DataFrame(records)
                df_div.columns = [str(c).strip() for c in df_div.columns]
                
                col_fecha = None
                for col in df_div.columns:
                    col_str = str(col).strip()
                    if col_str in [fecha_buscar_1, fecha_buscar_2]:
                        col_fecha = col
                        break
                
                col_alumno_div = None
                for col in df_div.columns:
                    if str(col).strip().lower() in ["alumno", "alumnos"]:
                        col_alumno_div = col
                        break

                if col_fecha and col_alumno_div:
                    for _, row_div in df_div.iterrows():
                        nombre_alum = str(row_div[col_alumno_div]).strip()
                        val_asistencia = str(row_div[col_fecha]).strip().upper()
                        
                        if val_asistencia == "A":
                            df_grupo.loc[df_grupo["Alumno"].astype(str).str.strip() == nombre_alum, "Estado"] = "Ausente"
            except Exception:
                pass

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
# VISTA EN DOS COLUMNAS (PRESENTES Y AUSENTES)
# -------------------------------------------------------------------
if comision_seleccionada:
    st.divider()
    st.subheader("📋 Alumnos esperados en aula")
    st.caption(f"Comisión: **{comision_seleccionada}** | Fecha: **{fecha_hoy}**")

    df_esperados = obtener_alumnos_esperados(df_padron, comision_seleccionada)

    if df_esperados.empty:
        st.info("No se encontraron alumnos para esta comisión en Padron_Comisiones.")
    else:
        col_presentes, col_ausentes = st.columns(2)

        ausentes_en_aula = []
        ausentes_colegio = []
        presentes_en_aula = []

        for index, row in df_esperados.iterrows():
            if str(row.get("Estado", "")).strip().lower() == "ausente":
                ausentes_colegio.append(f"{row['Alumno']} ({row['Division']})")

        # COLUMNA IZQUIERDA: Presentes en Aula
        with col_presentes:
            st.subheader("✅ Presentes en Aula")
            st.caption("Desmarque a quienes NO estén en la clase")
            
            for index, row in df_esperados.iterrows():
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
            
            if ausentes_colegio:
                st.markdown("**🏠 Ausentes en el Colegio (Planilla Diaria):**")
                for alum in ausentes_colegio:
                    st.warning(f"• {alum}")
            
            if ausentes_en_aula:
                st.markdown("**🚨 Ausentes en Aula / Posible Fuga:**")
                for ausente in ausentes_en_aula:
                    st.error(f"⚠️ {ausente}")
            
            if not ausentes_colegio and not ausentes_en_aula:
                st.success("Todos los alumnos de esta comisión están presentes en la clase.")

        st.divider()
        
        # BOTÓN DE CONFIRMACIÓN
        if st.button("🚨 Confirmar Asistencia / Reportar Ausentes", type="primary"):
            if ausentes_en_aula:
                exito = registrar_fuga(ausentes_en_aula, comision_seleccionada, docente_actual, fecha_hoy, hora_actual)
                if exito:
                    st.error(f"⚠️ Alerta registrada y enviada para {len(ausentes_en_aula)} alumno(s) ausente(s) en aula.")
            else:
                st.success("✅ Asistencia confirmada.")
