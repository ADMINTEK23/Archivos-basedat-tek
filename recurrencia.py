import logging
import os
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime
from dateutil.relativedelta import relativedelta
import pandas as pd
import streamlit as st
from sqlalchemy import text, bindparam
from db_utils import ENGINE_GLOBAL

# -------------------------
# Config y constantes
# -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

COL_PROVEEDOR = "PROVEEDOR"
COL_ANIO_MES = "AÑO_MES"
COL_TOTAL = "TOTAL"

TIPO_AMBOS = "ambos"
TIPO_GASTOS = "gastos"
TIPO_INSUMOS = "insumos"

MESES_MAP = {'ENE': 1, 'FEB': 2, 'MAR': 3, 'ABR': 4, 'MAY': 5, 'JUN': 6,
             'JUL': 7, 'AGO': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DIC': 12}
MESES_INV = {v: k for k, v in MESES_MAP.items()}

# -------------------------
# Helpers de Fecha
# -------------------------
def meses_anteriores(fecha_hasta: datetime, n: int) -> List[Dict[str, Any]]:
    meses = []
    base = datetime(fecha_hasta.year, fecha_hasta.month, 1)
    for i in range(n - 1, -1, -1):
        f = base - relativedelta(months=i)
        meses.append({
            "etiqueta": f"{f.year} - {MESES_INV[f.month]}",
            "fecha_inicio": datetime(f.year, f.month, 1),
            "fecha_fin": (datetime(f.year, f.month, 1) + relativedelta(months=1) - relativedelta(days=1))
        })
    return meses

# -------------------------
# Helpers de MARCAS
# -------------------------
@st.cache_data(ttl=600)
def obtener_marcas_disponibles() -> List[str]:
    q = text('SELECT DISTINCT "MARCA" FROM (SELECT "MARCA" FROM insumos UNION ALL SELECT "MARCA" FROM gastos) t WHERE "MARCA" IS NOT NULL')
    try:
        with ENGINE_GLOBAL.connect() as conn:
            df = pd.read_sql(q, conn)
        marcas = sorted([str(m).strip() for m in df["MARCA"].dropna().unique()])
        return marcas
    except Exception as e:
        logger.error(f"Error obteniendo marcas: {e}")
        return []

# =====================================================================
# 1. MODO AGGREGATE: Consultas SQL que devuelven resúmenes (CON CACHÉ)
# =====================================================================
@st.cache_data(ttl=300)
def obtener_resumen_agregado(fecha_inicio: datetime, fecha_fin: datetime, origen: str, marcas: Optional[List[str]] = None) -> pd.DataFrame:
    
    base_insumos = """
        SELECT 'INSUMO' AS "ORIGEN", "PROVEEDOR", TO_CHAR("FECHA", 'YYYY-MM') AS "AÑO_MES_ISO", 
               SUM("TOTAL") AS "TOTAL"
        FROM insumos
        WHERE "FECHA" BETWEEN :inicio AND :fin
    """
    
    base_gastos = """
        SELECT 'GASTO' AS "ORIGEN", "PROVEEDOR", TO_CHAR("FECHA", 'YYYY-MM') AS "AÑO_MES_ISO", 
               SUM("TOTAL") AS "TOTAL"
        FROM gastos
        WHERE "FECHA" BETWEEN :inicio AND :fin
    """
    
    marca_clause = ""
    if marcas:
        marca_clause = ' AND "MARCA" IN :marcas '
    
    queries = []
    if origen in [TIPO_AMBOS, TIPO_INSUMOS]:
        queries.append(base_insumos + marca_clause + " GROUP BY \"PROVEEDOR\", TO_CHAR(\"FECHA\", 'YYYY-MM')")
    if origen in [TIPO_AMBOS, TIPO_GASTOS]:
        queries.append(base_gastos + marca_clause + " GROUP BY \"PROVEEDOR\", TO_CHAR(\"FECHA\", 'YYYY-MM')")
    
    final_query = " UNION ALL ".join(queries)
    
    try:
        with ENGINE_GLOBAL.connect() as conn:
            stmt = text(final_query)
            params = {
                "inicio": fecha_inicio.strftime('%Y-%m-%d'),
                "fin": fecha_fin.strftime('%Y-%m-%d')
            }
            if marcas:
                stmt = stmt.bindparams(bindparam("marcas", expanding=True))
                params["marcas"] = marcas
            df = pd.read_sql(stmt, conn, params=params)
        
        df[COL_ANIO_MES] = df["AÑO_MES_ISO"].apply(
            lambda x: f"{x.split('-')[0]} - {MESES_INV[int(x.split('-')[1])]}" if pd.notna(x) else pd.NA
        )
        return df
    except Exception as e:
        logger.error(f"Error en aggregate de resumen: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=300)
def obtener_metricas_agregadas(fecha_inicio: datetime, fecha_fin: datetime, origen: str, marcas: Optional[List[str]] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    
    marca_clause = ""
    if marcas:
        marca_clause = ' AND "MARCA" IN :marcas '
    
    q_cat = text(f"""
        SELECT "CATEGORÍA", SUM("TOTAL") AS "TOTAL" FROM gastos 
        WHERE "FECHA" BETWEEN :inicio AND :fin AND "CATEGORÍA" IS NOT NULL {marca_clause} GROUP BY "CATEGORÍA"
    """)
    q_fp_insumos = text(f"""
        SELECT "FORMA PAGO", SUM("TOTAL") AS "TOTAL" FROM insumos 
        WHERE "FECHA" BETWEEN :inicio AND :fin AND "FORMA PAGO" IS NOT NULL {marca_clause} GROUP BY "FORMA PAGO"
    """)
    q_fp_gastos = text(f"""
        SELECT "FORMA PAGO", SUM("TOTAL") AS "TOTAL" FROM gastos 
        WHERE "FECHA" BETWEEN :inicio AND :fin AND "FORMA PAGO" IS NOT NULL {marca_clause} GROUP BY "FORMA PAGO"
    """)
    
    try:
        with ENGINE_GLOBAL.connect() as conn:
            params = {"inicio": fecha_inicio.strftime('%Y-%m-%d'), "fin": fecha_fin.strftime('%Y-%m-%d')}
            if marcas:
                q_cat = q_cat.bindparams(bindparam("marcas", expanding=True))
                q_fp_insumos = q_fp_insumos.bindparams(bindparam("marcas", expanding=True))
                q_fp_gastos = q_fp_gastos.bindparams(bindparam("marcas", expanding=True))
                params["marcas"] = marcas
            df_cat = pd.read_sql(q_cat, conn, params=params)
            df_fp_i = pd.read_sql(q_fp_insumos, conn, params=params) if origen in [TIPO_AMBOS, TIPO_INSUMOS] else pd.DataFrame()
            df_fp_g = pd.read_sql(q_fp_gastos, conn, params=params) if origen in [TIPO_AMBOS, TIPO_GASTOS] else pd.DataFrame()
    except Exception as e:
        logger.error(f"Error obteniendo métricas agregadas: {e}")
        return pd.DataFrame(), pd.DataFrame()
    
    df_fp = pd.concat([df_fp_i, df_fp_g]).groupby("FORMA PAGO")["TOTAL"].sum().reset_index() if not (df_fp_i.empty and df_fp_g.empty) else pd.DataFrame()
    
    return df_cat, df_fp

# =====================================================================
# 2. MODO DETAIL: Seek Cursor Paginado (SIN CACHÉ)
# =====================================================================
def obtener_detalle_proveedor_paginado(proveedor: str, fecha_inicio: datetime, fecha_fin: datetime, 
                                       limit: int = 15, last_fecha: str = None, last_id: int = None, marcas: Optional[List[str]] = None) -> pd.DataFrame:
    
    where_clauses = [
        '"PROVEEDOR" = :proveedor',
        '"FECHA" >= :inicio',
        '"FECHA" <= :fin'
    ]
    params = {
        "proveedor": proveedor,
        "inicio": fecha_inicio.strftime('%Y-%m-%d'),
        "fin": fecha_fin.strftime('%Y-%m-%d'),
        "limit": limit
    }

    if marcas:
        where_clauses.append('"MARCA" IN :marcas')
        params["marcas"] = marcas

    if last_fecha and last_id:
        where_clauses.append('("FECHA" < :last_fecha OR ("FECHA" = :last_fecha AND id < :last_id))')
        params["last_fecha"] = last_fecha
        params["last_id"] = last_id

    str_where = " AND ".join(where_clauses)
    
    q_insumos = f"""
        SELECT id, 'INSUMO' AS "ORIGEN", "FECHA", "PROVEEDOR", "INSUMO" AS "CONCEPTO", 
               "FORMA PAGO", "MARCA", "TOTAL"
        FROM insumos WHERE {str_where}
    """
    q_gastos = f"""
        SELECT id, 'GASTO' AS "ORIGEN", "FECHA", "PROVEEDOR", "GASTO DE" AS "CONCEPTO", 
               "FORMA PAGO", "MARCA", "TOTAL"
        FROM gastos WHERE {str_where}
    """
    
    final_query = text(f"({q_insumos}) UNION ALL ({q_gastos}) ORDER BY \"FECHA\" DESC, id DESC LIMIT :limit")
    if marcas:
        final_query = final_query.bindparams(bindparam("marcas", expanding=True))

    with ENGINE_GLOBAL.connect() as conn:
        df = pd.read_sql(final_query, conn, params=params, parse_dates=["FECHA"])
    return df

# =====================================================================
# 3. LÓGICA DE ANÁLISIS EN MEMORIA (Sobre datos pre-agregados)
# =====================================================================
def clasificacion_abc(df_agregado: pd.DataFrame) -> pd.DataFrame:
    if df_agregado.empty: return pd.DataFrame()
    agg = df_agregado.groupby(COL_PROVEEDOR)[COL_TOTAL].sum().reset_index().sort_values(by=COL_TOTAL, ascending=False)
    agg['TOTAL_ACUM'] = agg[COL_TOTAL].cumsum()
    total = agg[COL_TOTAL].sum()
    agg['CUM_PCT'] = agg['TOTAL_ACUM'] / total
    agg['ABC'] = agg['CUM_PCT'].apply(lambda p: 'A (Crítico)' if p <= 0.80 else ('B (Medio)' if p <= 0.95 else 'C (Bajo)'))
    return agg[[COL_PROVEEDOR, COL_TOTAL, 'CUM_PCT', 'ABC']]

# =====================================================================
# 4. INTERFAZ STREAMLIT
# =====================================================================
def mostrar_pestana_recurrencia():
    st.title("🔄 Pagos y Proveedores")

    # --- Inicializar cursores UI ---
    if "cursor_stack_prov" not in st.session_state:
        st.session_state.cursor_stack_prov = []
    if "current_cursor_prov" not in st.session_state:
        st.session_state.current_cursor_prov = (None, None)
    if "last_prov_searched" not in st.session_state:
        st.session_state.last_prov_searched = None

    # --- Controles en Sidebar ---
    st.sidebar.header("Filtros de Análisis")
    fecha_hasta = st.sidebar.date_input("Fecha de corte (hasta)", value=datetime.today())
    meses_ventana = st.sidebar.selectbox("Meses a analizar", [3, 6, 12], index=1)
    tipo_modulo = st.sidebar.selectbox("Módulo", [TIPO_AMBOS, TIPO_GASTOS, TIPO_INSUMOS], index=0)

    # --- Filtro global de MARCA (opción Todas) ---
    marcas_disponibles = obtener_marcas_disponibles()
    # Insertamos la opción especial "(Todas)" al inicio
    opciones_marcas = ["(Todas)"] + marcas_disponibles
    marcas_sel_ui = st.sidebar.multiselect("Filtrar por Marca", options=opciones_marcas, default=["(Todas)"])


    if not marcas_sel_ui or "(Todas)" in marcas_sel_ui:
        marcas_param = None
    else:
        marcas_param = marcas_sel_ui  # lista de marcas a filtrar

    # Calcular rango
    meses_meta = meses_anteriores(datetime(fecha_hasta.year, fecha_hasta.month, 1), meses_ventana)
    fecha_inicio = meses_meta[0]['fecha_inicio']
    fecha_fin = meses_meta[-1]['fecha_fin']

    # --- Cargar datos agregados (Ligeros) ---
    with st.spinner("Consultando agregados desde la Base de Datos..."):
        df_resumen = obtener_resumen_agregado(fecha_inicio, fecha_fin, tipo_modulo, marcas=marcas_param)
        df_cat, df_fp = obtener_metricas_agregadas(fecha_inicio, fecha_fin, tipo_modulo, marcas=marcas_param)

    if df_resumen.empty:
        st.warning("No se encontraron registros en el rango seleccionado.")
        return

    # ---------------------------------------------------------
    # 📑 PESTAÑAS DE VISUALIZACIÓN
    # ---------------------------------------------------------
    tab1, tab2, tab3, tab4 = st.tabs([
        "Resumen y Evolución", 
        "Análisis Pareto", 
        "Concentración de Gasto",
        "Detalle"
    ])

    # --- TAB 1: Resumen General ---
    with tab1:
        st.subheader("Resumen: Totales por Proveedor (Agregado en SQL)")
        pivot = df_resumen.pivot_table(index=COL_PROVEEDOR, columns=COL_ANIO_MES, values=COL_TOTAL, aggfunc='sum', fill_value=0)
        columnas_orden = [m['etiqueta'] for m in meses_meta if m['etiqueta'] in pivot.columns]
        pivot = pivot.reindex(columns=columnas_orden).fillna(0.0)
        pivot['TOTAL_PERIODO'] = pivot.sum(axis=1)
        pivot = pivot.sort_values('TOTAL_PERIODO', ascending=False)

        if len(columnas_orden) >= 2:
            mes_act = columnas_orden[-1]
            mes_ant = columnas_orden[-2]
            pivot['Var. Mes Actual %'] = ((pivot[mes_act] - pivot[mes_ant]) / pivot[mes_ant].replace(0, pd.NA))

        st.dataframe(pivot.style.format(
            "{:.1%}", subset=['Var. Mes Actual %'] if 'Var. Mes Actual %' in pivot.columns else []
        ).format("${:,.2f}", subset=columnas_orden + ['TOTAL_PERIODO']), use_container_width=True)

    # --- TAB 2: ABC ---
    with tab2:
        st.subheader("Clasificación de Gasto ABC")
        abc = clasificacion_abc(df_resumen)
        st.dataframe(abc.style.format({COL_TOTAL: "${:,.2f}", 'CUM_PCT': "{:.2%}"}), use_container_width=True, hide_index=True)

    # --- TAB 3: Concentración ---
    with tab3:
        st.subheader("Concentración de Presupuesto (Directo BD)")
        colA, colB = st.columns(2)
        
        with colA:
            st.markdown("**Por Categoría Operativa (Solo Gastos)**")
            if not df_cat.empty:
                df_cat['PCT'] = df_cat["TOTAL"] / df_cat["TOTAL"].sum()
                st.dataframe(df_cat.sort_values("TOTAL", ascending=False).style.format({"TOTAL": "${:,.2f}", 'PCT': "{:.1%}"}), hide_index=True)
            else:
                st.info("No hay datos de categorías para el filtro seleccionado.")
                
        with colB:
            st.markdown("**Por Forma de Pago**")
            if not df_fp.empty:
                df_fp['PCT'] = df_fp["TOTAL"] / df_fp["TOTAL"].sum()
                st.dataframe(df_fp.sort_values("TOTAL", ascending=False).style.format({"TOTAL": "${:,.2f}", 'PCT': "{:.1%}"}), hide_index=True)
            else:
                st.info("No hay datos de formas de pago para el filtro seleccionado.")

    # --- TAB 4: Detalle Paginado (Seek Cursor) ---
    with tab4:
        st.subheader("Buscador de Movimientos (Sin cargar todo en memoria)")
        proveedores = sorted([str(p) for p in df_resumen[COL_PROVEEDOR].dropna().unique()])
        prov_sel = st.selectbox("Selecciona un proveedor:", ["(Ninguno)"] + proveedores)
        
        if prov_sel != st.session_state.last_prov_searched or st.session_state.get("last_marcas_param") != marcas_param:
            st.session_state.cursor_stack_prov = []
            st.session_state.current_cursor_prov = (None, None)
            st.session_state.last_prov_searched = prov_sel
            st.session_state.last_marcas_param = marcas_param

        if prov_sel and prov_sel != "(Ninguno)":
            limit = st.slider("Registros por página", 5, 50, 15)
            cur_fecha, cur_id = st.session_state.current_cursor_prov
            
            df_pagina = obtener_detalle_proveedor_paginado(
                proveedor=prov_sel, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, 
                limit=limit, last_fecha=cur_fecha, last_id=cur_id, marcas=marcas_param
            )
            
            st.dataframe(df_pagina.style.format({"TOTAL": "${:,.2f}"}), use_container_width=True, hide_index=True)
            
            # Botones de navegación Seek Cursor
            b1, b2, _ = st.columns([1, 1, 4])
            with b1:
                if st.button("⬅️ Anterior") and len(st.session_state.cursor_stack_prov) > 0:
                    st.session_state.current_cursor_prov = st.session_state.cursor_stack_prov.pop()
                    st.rerun()
            with b2:
                if st.button("Siguiente ➡️") and not df_pagina.empty and len(df_pagina) == limit:
                    st.session_state.cursor_stack_prov.append(st.session_state.current_cursor_prov)
                    ultimo = df_pagina.iloc[-1]
                    # Convertir fecha a string ISO para el query
                    st.session_state.current_cursor_prov = (ultimo["FECHA"].strftime('%Y-%m-%d %H:%M:%S'), ultimo["id"])
                    st.rerun()

if __name__ == "__main__":
    mostrar_pestana_recurrencia()