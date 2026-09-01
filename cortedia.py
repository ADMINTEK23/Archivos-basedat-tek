import logging
import time
from datetime import datetime
from typing import Dict, Optional

import pandas as pd
import streamlit as st
from sqlalchemy import text

# Import original helpers (se mantienen)
from db_utils import obtener_engine_maestro, cargar_datos_optimizados

# -------------------------
# Configuración y constantes
# -------------------------
logger = logging.getLogger("traspasos")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

SELECT_PROMPT = "-- Seleccionar --"
CACHE_TTL_DATOS = 300  # segundos

# SQL templates
SQL_INSERT_INSUMO = text(
    '''
    INSERT INTO insumos 
    ("FECHA", "INSUMO", "TIPO", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "DÍA", "USUARIO", "MARCA", "FORMA PAGO") 
    VALUES 
    (:f, :ins, :t, :p, :u, :c, :tot, :d, :user, :m, :fp)
    '''
)

SQL_INSERT_GASTO = text(
    '''
    INSERT INTO gastos 
    ("FECHA", "GASTO DE", "TIPO", "CATEGORÍA", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "DÍA", "RECURRENCIA", "USUARIO", "MARCA", "FORMA PAGO") 
    VALUES 
    (:f, :g, :t, :c, :p, :u, :co, :tot, :d, :rec, :user, :m, :fp)
    '''
)

# -------------------------
# Caché y utilidades
# -------------------------
@st.cache_resource
def get_engine():
    logger.info("Creando engine de base de datos.")
    return obtener_engine_maestro()

@st.cache_data(ttl=CACHE_TTL_DATOS)
def get_datos_cache(anio: int):
    logger.info("Cargando datos optimizados para año %s", anio)
    return cargar_datos_optimizados(anio)

@st.cache_data
def opciones_por_marca(df: pd.DataFrame, col: str) -> Dict[str, list]:
    if df is None or df.empty or 'MARCA' not in df.columns or col not in df.columns:
        return {}
    grouped = df.groupby('MARCA')[col].apply(lambda s: s.value_counts().index.tolist())
    return grouped.to_dict()

def normalizar(valor: Optional[str]) -> str:
    return str(valor).strip().upper() if valor else ""

def obtener_opciones(df: pd.DataFrame, columna: str, defecto: Optional[str] = None):
    if df is not None and not df.empty and columna in df.columns:
        return sorted(list(df[columna].dropna().unique()))
    return [defecto] if defecto else []

def obtener_opciones_frecuentes_cached(map_por_marca: Dict[str, list], marca: str, defecto: Optional[str] = None):
    if not map_por_marca:
        return [defecto] if defecto else []
    return map_por_marca.get(marca, [])

def obtener_ultimo_costo(df: pd.DataFrame, fecha_col: str = "FECHA", costo_col: str = "COSTO"):
    if df is None or df.empty:
        return 0.0
    try:
        idx = df[fecha_col].idxmax()
        return float(df.at[idx, costo_col])
    except Exception:
        return float(df.nlargest(1, fecha_col)[costo_col].iat[0])

# -------------------------
# Inserciones DB encapsuladas
# -------------------------
def insertar_insumo(conn, params: dict):
    conn.execute(SQL_INSERT_INSUMO, params)

def insertar_gasto(conn, params: dict):
    conn.execute(SQL_INSERT_GASTO, params)

# -------------------------
# UI principal
# -------------------------
def mostrar_historial(df: pd.DataFrame, filtro_col: str, filtro_val):
    if df is None or df.empty:
        st.info("Sin registros previos.")
        return
    df_f = df[df[filtro_col] == filtro_val] if filtro_col in df.columns else df
    if df_f.empty:
        st.info("Sin registros previos.")
        return

    df_hist = df_f.sort_values(by='FECHA', ascending=False).drop_duplicates(subset=['COSTO'], keep='first')
    cols = [c for c in ['FECHA', filtro_col, 'TIPO', 'PROVEEDOR', 'COSTO'] if c in df_hist.columns]
    st.dataframe(df_hist[cols].head(10), hide_index=True, use_container_width=True)
    st.caption("Mostrando los últimos 10 precios únicos registrados.")

def mostrar_modulo_traspasos():
    st.header("🔄 Traspasos/Préstamos")
    st.markdown("Transfiere de forma directa entre sucursales.")

    # Inicializar estado (se elimina pending_traspaso)
    st.session_state.setdefault("log_sesion", [])
    st.session_state.setdefault("usuario_actual", st.session_state.get("usuario_actual", "ANÓNIMO"))

    operador_actual = normalizar(st.session_state.get("usuario_actual", "ANÓNIMO"))
    engine = get_engine()
    anio_actual = datetime.now().year

    # Cargar datos cacheados
    try:
        datos = get_datos_cache(anio_actual)
        if isinstance(datos, tuple) and len(datos) >= 3:
            _, df_insumos, df_gastos = datos
        else:
            df_insumos = datos.get("insumos") if isinstance(datos, dict) else None
            df_gastos = datos.get("gastos") if isinstance(datos, dict) else None
    except Exception as e:
        logger.exception("Error cargando datos optimizados: %s", e)
        st.error("No se pudieron cargar los datos. Intenta recargar la app.")
        return

    tipo_traspaso = st.radio("¿Qué deseas traspasar?", ["Insumos", "Gastos"], horizontal=True)
    st.markdown("---")

    ins_por_marca = opciones_por_marca(df_insumos, 'INSUMO') if df_insumos is not None else {}
    gas_por_marca = opciones_por_marca(df_gastos, 'GASTO DE') if df_gastos is not None else {}

    marcas_ins = obtener_opciones(df_insumos, 'MARCA')
    marcas_gas = obtener_opciones(df_gastos, 'MARCA')
    marcas_existentes = sorted(list(set(marcas_ins + marcas_gas)))

    if not marcas_existentes:
        st.warning("No hay datos suficientes en las tablas de Insumos/Gastos.")
        return

    col_o, col_d, col_f = st.columns([2, 2, 2])
    with col_o:
        marca_origen = st.selectbox("MARCA ORIGEN", marcas_existentes, key="marca_out")
    with col_d:
        default_idx = 1 if len(marcas_existentes) > 1 else 0
        marca_destino = st.selectbox("MARCA DESTINO", marcas_existentes, index=default_idx, key="marca_in")
    with col_f:
        fecha_traspaso = st.date_input("🗓️ Fecha del Traspaso", datetime.now())

    if marca_origen == marca_destino:
        st.warning("⚠️ La sucursal de origen y destino no pueden ser la misma.")
        return

    dia_semana = ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO"][fecha_traspaso.weekday()]

    # 1. Selección dinámica
    concepto_sel = SELECT_PROMPT
    tipo_sel = None
    cat_sel = None
    rec_sel = None
    ultimo_costo = 0.0

    if tipo_traspaso == "Insumos":
        ins_lista = obtener_opciones_frecuentes_cached(ins_por_marca, marca_origen)
        concepto_sel = st.selectbox("Selecciona el Insumo", [SELECT_PROMPT] + ins_lista)
        if concepto_sel != SELECT_PROMPT:
            df_filtro = df_insumos[df_insumos['INSUMO'] == concepto_sel]
            tipo_sel = st.selectbox("Tipo", obtener_opciones(df_filtro, 'TIPO'))
            ultimo_costo = obtener_ultimo_costo(df_filtro) if not df_filtro.empty else 0.0
    else:
        gasto_lista = obtener_opciones_frecuentes_cached(gas_por_marca, marca_origen)
        concepto_sel = st.selectbox("Selecciona el Gasto:", [SELECT_PROMPT] + gasto_lista)
        if concepto_sel != SELECT_PROMPT:
            df_filtro = df_gastos[df_gastos['GASTO DE'] == concepto_sel]
            tipo_sel = st.selectbox("Tipo", obtener_opciones(df_filtro, 'TIPO', "OPERATIVO"))
            cat_sel = st.selectbox("Categoría:", obtener_opciones(df_filtro, 'CATEGORÍA'))
            rec_sel = st.selectbox("Recurrencia:", obtener_opciones(df_filtro, 'RECURRENCIA', "VARIABLE"))
            ultimo_costo = obtener_ultimo_costo(df_filtro) if not df_filtro.empty else 0.0

    # 2. Formulario de ejecución directa
    with st.form(key="traspaso_form"):
        col1, col2 = st.columns(2)
        cantidad = col1.number_input("Unidades", value=1.00, step=1e-14, format="%.2f")
        costo_unit = col2.number_input("Costo Unitario", value=float(ultimo_costo), step=1e-14, format="%.2f")

        btn_ejecutar = st.form_submit_button("✅ Ejecutar Traspaso", type="primary")

    if btn_ejecutar:
        if concepto_sel == SELECT_PROMPT:
            st.error("Por favor, selecciona un concepto válido.")
        elif cantidad == 0:
            st.error("Las unidades no puede ser exactamente 0.")
        elif costo_unit == 0:
            st.error("El costo unitario no puede ser exactamente 0.")
        else:
            total_traspaso = round(cantidad * costo_unit, 2)
            forma_pago_traspaso = "TRASPASO"
            proveedor_texto = f"{marca_origen} A {marca_destino}"
            
            try:
                with engine.begin() as conn:
                    params_origen = {
                        "f": str(fecha_traspaso),
                        "p": proveedor_texto,
                        "u": -cantidad,
                        "d": dia_semana,
                        "user": operador_actual,
                        "fp": forma_pago_traspaso
                    }
                    params_destino = params_origen.copy()
                    params_destino["u"] = cantidad

                    if tipo_traspaso == "Insumos":
                        params_origen.update({
                            "ins": concepto_sel, "t": tipo_sel, "c": costo_unit, "tot": -total_traspaso, "m": marca_origen
                        })
                        params_destino.update({
                            "ins": concepto_sel, "t": tipo_sel, "c": costo_unit, "tot": total_traspaso, "m": marca_destino
                        })
                        insertar_insumo(conn, params_origen)
                        insertar_insumo(conn, params_destino)
                    else:
                        params_origen.update({
                            "g": concepto_sel, "t": tipo_sel, "c": cat_sel, "co": costo_unit, "tot": -total_traspaso, "rec": rec_sel, "m": marca_origen
                        })
                        params_destino.update({
                            "g": concepto_sel, "t": tipo_sel, "c": cat_sel, "co": costo_unit, "tot": total_traspaso, "rec": rec_sel, "m": marca_destino
                        })
                        insertar_gasto(conn, params_origen)
                        insertar_gasto(conn, params_destino)

                st.success("✅ Traspaso ejecutado correctamente.")
                st.session_state["log_sesion"].insert(0, {
                    "Hora": datetime.now().strftime("%H:%M:%S"),
                    "Concepto": concepto_sel,
                    "Cant": cantidad,
                    "Total": f"${total_traspaso:.2f}",
                    "Ruta": f"{marca_origen} -> {marca_destino}"
                })
                try:
                    get_datos_cache.clear()
                except Exception:
                    logger.debug("No se pudo limpiar cache de datos.")
                
                time.sleep(1.0)
                st.rerun()

            except Exception as e:
                logger.exception("Error al escribir en base de datos: %s", e)
                st.error("Error al escribir en base de datos. Mandale una captura a Alan.")

    # Mostrar log de sesión
    if st.session_state["log_sesion"]:
        st.markdown("---")
        with st.expander("📋 Traspasos realizados en esta sesión", expanded=False):
            st.dataframe(pd.DataFrame(st.session_state["log_sesion"]), use_container_width=True, hide_index=True)

    st.markdown("---")
    
    # Mostrar historial en base al concepto seleccionado en la interfaz
    if 'concepto_sel' in locals() and concepto_sel != SELECT_PROMPT:
        st.subheader("Historial de precios del concepto")
        target_df = df_insumos if tipo_traspaso == "Insumos" else df_gastos
        target_col = 'INSUMO' if tipo_traspaso == "Insumos" else 'GASTO DE'
        mostrar_historial(target_df, target_col, concepto_sel)
    else:
        st.info("💡 Selecciona un insumo o gasto para ver su historial de precios recientes.")

if __name__ == "__main__":
    mostrar_modulo_traspasos()