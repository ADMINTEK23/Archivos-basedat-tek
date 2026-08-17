import logging
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from typing import List, Tuple, Optional

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import text

# Importaciones de tu base de datos y utilidades
from db_utils import ENGINE_GLOBAL, cargar_datos_por_mes

# -------------------------
# Configuración y constantes
# -------------------------
logger = logging.getLogger(__name__)

MESES_LISTA = ['ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN', 'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC']
MESES_NUM = {m: i + 1 for i, m in enumerate(MESES_LISTA)}
TTL_VENTAS_HIST = 60 * 60 * 8     # 8 horas

# -------------------------
# Capa de datos (consultas)
# -------------------------
@st.cache_data(ttl=TTL_VENTAS_HIST)
def obtener_ventas_historicas_resumidas(anio: int, mes_nombre: str) -> pd.DataFrame:
    """Devuelve totales por SUCURSAL y MARCA para el mes indicado (rango [inicio, fin))."""
    if mes_nombre not in MESES_NUM:
        raise ValueError(f"Mes inválido: {mes_nombre}")

    m_num = MESES_NUM[mes_nombre]
    fecha_inicio = date(anio, m_num, 1)
    fecha_fin = fecha_inicio + relativedelta(months=1)

    query = text("""
        SELECT 
            COALESCE(NULLIF(UPPER(TRIM(BOTH FROM "SUCURSAL")), ''), 'SIN SUCURSAL') AS SUC,
            COALESCE(NULLIF(UPPER(TRIM(BOTH FROM "MARCA")), ''), 'SIN MARCA') AS MRC,
            COALESCE(SUM("TOTAL"), 0) AS AS_TOTAL
        FROM ventas 
        WHERE "FECHA" >= :inicio AND "FECHA" < :fin
        GROUP BY "SUCURSAL", "MARCA"
    """)

    try:
        with ENGINE_GLOBAL.connect() as conn:
            df = pd.read_sql(query, conn, params={"inicio": fecha_inicio, "fin": fecha_fin})

        if df.empty:
            return df

        df.columns = df.columns.str.upper()
        df['SUC'] = df['SUC'].astype(str).str.strip().str.upper()
        df['MRC'] = df['MRC'].astype(str).str.strip().str.upper()
        df['AS_TOTAL'] = pd.to_numeric(df['AS_TOTAL'], errors='coerce').fillna(0).astype('float64')
        return df

    except Exception as e:
        logger.exception("Error en obtener_ventas_historicas_resumidas")
        st.error("❌ Error al obtener ventas históricas resumidas.")
        return pd.DataFrame()

# -------------------------
# Utilidades y helpers
# -------------------------
def generar_periodos_disponibles(desde_anio: int = 2023) -> List[str]:
    hoy = datetime.now()
    anio_actual = hoy.year
    mes_actual = hoy.month

    opciones: List[str] = []
    for anio in range(anio_actual, desde_anio - 1, -1):
        limite_mes = mes_actual if anio == anio_actual else 12
        for m_idx in range(limite_mes - 1, -1, -1):
            opciones.append(f"{MESES_LISTA[m_idx]} {anio}")
    return opciones

def safe_sum(df: pd.DataFrame, col: str) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors='coerce').fillna(0).sum())

def safe_unique_sorted(df: pd.DataFrame, col: str) -> List[str]:
    if df is None or df.empty or col not in df.columns:
        return []
    vals = df[col].dropna().unique()
    return sorted([str(v).strip() for v in vals])

def clear_caches_reportes():
    for fn in (cargar_datos_por_mes, obtener_ventas_historicas_resumidas):
        try:
            fn.clear()
        except Exception:
            pass

# -------------------------
# Vistas Streamlit
# -------------------------
def mostrar_pestana_reportes():
    """Renderiza la pestaña de Reportes Financieros."""
    try:
        st.sidebar.header("📅 Filtros de Reportes")

        if st.sidebar.button("🔄 Sincronizar Datos Completo", use_container_width=True, key="sync_reportes"):
            clear_caches_reportes()
            st.success("¡Caché de consultas limpiada con éxito!")
            st.rerun()

        lista_periodos = generar_periodos_disponibles(desde_anio=2023)
        if not lista_periodos:
            st.warning("No hay periodos disponibles.")
            return

        periodo_sel = st.sidebar.selectbox("Selecciona el Período", options=lista_periodos, index=0)
        mes_sel, anio_sel_str = periodo_sel.split(" ")
        anio_sel = int(anio_sel_str)
        anio_anterior = anio_sel - 1

        st.session_state.setdefault("version_tabla_insumos", 0)
        st.session_state.setdefault("version_tabla_gastos", 0)
        st.session_state.setdefault("version_tabla_ventas", 0)
        version_global = (st.session_state.version_tabla_insumos +
                          st.session_state.version_tabla_gastos +
                          st.session_state.version_tabla_ventas)

        df_ventas, df_insumos, df_gastos = cargar_datos_por_mes(anio_sel, mes_sel, version_global)

        if (df_ventas is None or df_ventas.empty) and (df_insumos is None or df_insumos.empty) and (df_gastos is None or df_gastos.empty):
            st.warning(f"⚠️ No se encontraron transacciones operativas para {mes_sel} - {anio_sel}.")
            return

        todas_las_marcas = set()
        for df in (df_ventas, df_insumos, df_gastos):
            if df is not None and not df.empty and 'MARCA' in df.columns:
                todas_las_marcas.update([str(x).strip().upper() for x in df['MARCA'].dropna().unique()])

        marcas_options = ["Todas"] + sorted(list(todas_las_marcas))
        marca_sel = st.sidebar.selectbox("🏷️ Selecciona Marca", options=marcas_options, index=0)

        def filtrar_por_marca(df: Optional[pd.DataFrame], marca: str) -> pd.DataFrame:
            if df is None or df.empty:
                return pd.DataFrame()
            if not marca or marca.upper() in ("TODAS", "TODOS", "ALL"):
                return df.copy()
            if 'MARCA' not in df.columns:
                return df.copy()
            return df[df['MARCA'].astype(str).str.strip().str.upper() == marca.strip().upper()].copy()

        df_v_filtrado = filtrar_por_marca(df_ventas, marca_sel)
        df_g_filtrado = filtrar_por_marca(df_gastos, marca_sel)
        df_i_filtrado = filtrar_por_marca(df_insumos, marca_sel)

        sucursales = ["Todas"]
        sucursales += safe_unique_sorted(df_v_filtrado, 'SUCURSAL') if not df_v_filtrado.empty else safe_unique_sorted(df_ventas, 'SUCURSAL')
        suc_sel = st.sidebar.selectbox("📍 Selecciona Sucursal", options=sucursales, index=0)

        if suc_sel and suc_sel.upper() != "TODAS" and not df_v_filtrado.empty and 'SUCURSAL' in df_v_filtrado.columns:
            df_v_filtrado = df_v_filtrado[df_v_filtrado['SUCURSAL'].astype(str).str.strip().str.upper() == suc_sel.strip().upper()]

        total_ventas = safe_sum(df_v_filtrado, 'TOTAL')
        total_gastos = safe_sum(df_g_filtrado, 'TOTAL')
        total_insumos = safe_sum(df_i_filtrado, 'TOTAL')

        utilidad_neta = total_ventas - (total_insumos + total_gastos)
        denominador = total_ventas if total_ventas > 0 else 1.0

        pct_utilidad = (utilidad_neta / denominador) * 100
        pct_gastos = (total_gastos / denominador) * 100
        pct_insumos = (total_insumos / denominador) * 100

        u_dec, g_dec, i_dec = pct_utilidad / 100, pct_gastos / 100, pct_insumos / 100

        status_utilidad = "EXCESIVA" if u_dec > 0.25 else "EXCELENTE" if u_dec > 0.18 else "SALUDABLE" if u_dec > 0.12 else "REGULAR" if u_dec > 0.08 else "DE RIESGO"
        status_gastos = "DE RIESGO" if g_dec > 0.55 else "REGULAR" if g_dec > 0.50 else "SALUDABLE" if g_dec > 0.47 else "EXCELENTE"
        status_insumos = "DE RIESGO" if i_dec > 0.41 else "REGULAR" if i_dec > 0.38 else "SALUDABLE" if i_dec > 0.35 else "EXCELENTE"

        # --- CAPA VISUAL SUPERIOR ---
        col_graf1, col_texto, col_graf2 = st.columns([1.5, 1.1, 1.5])

        with col_graf1:
            st.markdown("<h3 style='text-align: center;'>UTILIDAD NETA</h3>", unsafe_allow_html=True)
            df_pie = pd.DataFrame({'Concepto': ['Utilidad', 'Gastos', 'Insumos'], 'Monto': [max(0, utilidad_neta), total_gastos, total_insumos]})
            fig_pie = px.pie(df_pie, values='Monto', names='Concepto', color='Concepto', color_discrete_map={'Utilidad': '#7030A0', 'Gastos': '#70AD47', 'Insumos': '#FFC000'})
            fig_pie.update_traces(textposition='inside', textinfo='percent+label', textfont_color="white")
            fig_pie.update_layout(showlegend=False, height=280, margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig_pie, use_container_width=True, key="pie_reportes")

        with col_texto:
            st.markdown(f"*FILTROS ACTIVOS: {mes_sel} {anio_sel} | {suc_sel} | {marca_sel}*")
            st.markdown(f"EL % DE LA UTILIDAD NETA ES:  \n*{pct_utilidad:.2f}% {status_utilidad}*")
            st.markdown(f"EL % DEL GASTO TOTAL ES:  \n*{pct_gastos:.2f}% {status_gastos}*")
            st.markdown(f"EL % DE INSUMOS ES:  \n*{pct_insumos:.2f}% {status_insumos}*")

        with col_graf2:
            st.markdown("<h3 style='text-align: center;'>MÉTRICAS CONSOLIDADAS</h3>", unsafe_allow_html=True)
            v_bokoba = safe_sum(df_v_filtrado[df_v_filtrado['SUCURSAL'].astype(str).str.upper() == 'BOKOBA'] if not df_v_filtrado.empty else pd.DataFrame(), 'TOTAL')
            v_sotuta = safe_sum(df_v_filtrado[df_v_filtrado['SUCURSAL'].astype(str).str.upper() == 'SOTUTA'] if not df_v_filtrado.empty else pd.DataFrame(), 'TOTAL')
            df_barras = pd.DataFrame({'Métrica': ['CONSOLIDADA', 'BOKOBA', 'SOTUTA', 'INSUMOS', 'GASTOS', 'UTILIDAD'], 'Monto': [total_ventas, v_bokoba, v_sotuta, total_insumos, total_gastos, utilidad_neta]})
            fig_bar = px.bar(df_barras, x='Monto', y='Métrica', orientation='h', text='Monto', color='Métrica', color_discrete_map={'CONSOLIDADA': '#3B75AF', 'BOKOBA': '#A5C8E1', 'SOTUTA': '#4F94CD', 'INSUMOS': '#FFC000', 'GASTOS': '#70AD47', 'UTILIDAD': '#7030A0'})
            fig_bar.update_traces(texttemplate='%{x:,.2f}')
            fig_bar.update_layout(showlegend=False, yaxis={'categoryorder': 'array', 'categoryarray': ['UTILIDAD', 'GASTOS', 'INSUMOS', 'SOTUTA', 'BOKOBA', 'CONSOLIDADA'], 'title': None}, xaxis={'title': None, 'showticklabels': False})
            st.plotly_chart(fig_bar, use_container_width=True, key="barras_reportes")

        # --- SECCIÓN YoY ---
        st.markdown("---")
        st.markdown(f"<h3 style='text-align: center;'>COMPARACIÓN INTERANUAL: {mes_sel} {anio_anterior} vs {mes_sel} {anio_sel}</h3>", unsafe_allow_html=True)

        df_historico_raw = obtener_ventas_historicas_resumidas(anio_anterior, mes_sel)
        marca_filtro = str(marca_sel).strip().upper()
        suc_filtro = str(suc_sel).strip().upper()

        df_ant_filtrado = df_historico_raw.copy() if not df_historico_raw.empty else pd.DataFrame()
        if not df_ant_filtrado.empty and marca_filtro not in ("TODAS", "TODA", "TODOS", "ALL"):
            df_ant_filtrado = df_ant_filtrado[df_ant_filtrado['MRC'] == marca_filtro]

        tarjetas_render: List[Tuple[str, float, float]] = []

        if suc_filtro in ("TODAS", "TODA", "TODOS", "ALL"):
            sucursales_activas = safe_unique_sorted(df_v_filtrado, 'SUCURSAL') if not df_v_filtrado.empty else safe_unique_sorted(df_ant_filtrado, 'SUC')
            for s in sucursales_activas:
                s_norm = str(s).strip().upper()
                monto_act_suc = safe_sum(df_v_filtrado[df_v_filtrado['SUCURSAL'].astype(str).str.upper() == s_norm] if not df_v_filtrado.empty else pd.DataFrame(), 'TOTAL')
                monto_ant_suc = safe_sum(df_ant_filtrado[df_ant_filtrado['SUC'].astype(str).str.upper() == s_norm] if not df_ant_filtrado.empty else pd.DataFrame(), 'AS_TOTAL')
                tarjetas_render.append((f"📍 {s_norm}", monto_act_suc, monto_ant_suc))
        else:
            monto_act_suc = safe_sum(df_v_filtrado, 'TOTAL')
            monto_ant_suc = safe_sum(df_ant_filtrado[df_ant_filtrado['SUC'].astype(str).str.upper() == suc_filtro] if not df_ant_filtrado.empty else pd.DataFrame(), 'AS_TOTAL')
            tarjetas_render.append((f"📍 SUCURSAL: {suc_filtro}", monto_act_suc, monto_ant_suc))

        total_act_global = safe_sum(df_v_filtrado, 'TOTAL')
        total_ant_global = safe_sum(df_ant_filtrado, 'AS_TOTAL')
        if suc_filtro in ("TODAS", "TODA", "TODOS", "ALL"):
            tarjetas_render.append(("🏢 CONSOLIDADO TOTAL", total_act_global, total_ant_global))

        if tarjetas_render:
            max_cols = 6
            for i in range(0, len(tarjetas_render), max_cols):
                slice_tarjetas = tarjetas_render[i:i + max_cols]
                columnas_dinamicas = st.columns(len(slice_tarjetas))
                for idx, (lbl_titulo, val_hoy, val_ayer) in enumerate(slice_tarjetas):
                    with columnas_dinamicas[idx]:
                        fig_dinamica = go.Figure(go.Indicator(
                            mode="number+delta",
                            value=val_hoy,
                            delta={'reference': val_ayer, 'relative': True, 'valueformat': '.2%'},
                            title={"text": f"<span style='font-size:1.0em;color:#444;font-weight:bold;'>{lbl_titulo}</span><br><span style='font-size:0.8em;color:gray;'>Año anterior: ${val_ayer:,.2f}</span>"},
                            number={'prefix': "$", 'valueformat': ',.2f'}
                        ))
                        fig_dinamica.update_layout(height=240, margin=dict(t=40, b=10, l=10, r=10))
                        st.plotly_chart(fig_dinamica, use_container_width=True, key=f"yoy_dinamico_{i}_{idx}")
        else:
            st.caption("No hay datos comparativos interanuales disponibles.")

    except Exception as e:
        logger.exception("Error de visualización en reportes")
        st.error(f"Error de visualización en reportes: {e}")
