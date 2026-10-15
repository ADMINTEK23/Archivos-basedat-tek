import logging
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from typing import List, Tuple, Optional
from itertools import combinations
from collections import Counter
import numpy as np

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
    for fn in (cargar_datos_por_mes, obtener_ventas_historicas_resumidas, preparar_serie_diaria_simple):
        try:
            fn.clear()
        except Exception:
            pass

# -------------------------
# Funciones de Análisis de Productos y Comportamiento
# -------------------------
def analizar_horarios(df: pd.DataFrame) -> pd.DataFrame:
    """Agrupa las ventas usando la columna 'HORA NÚMERO'."""
    if 'HORA NÚMERO' not in df.columns: 
        return pd.DataFrame()
    return df.groupby('HORA NÚMERO')['TOTAL'].sum().reset_index()

def analizar_venta_cruzada(df: pd.DataFrame, top_n=10) -> pd.DataFrame:
    """Encuentra los pares de productos usando 'NÚMERO DE VENTA' y 'PRODUCTO'."""
    if 'NÚMERO DE VENTA' not in df.columns or 'PRODUCTO' not in df.columns:
        return pd.DataFrame()
    
    # Agrupar productos únicos por cada Número de Venta
    tickets = df.groupby('NÚMERO DE VENTA')['PRODUCTO'].apply(lambda x: list(set(x.dropna())))
    
    # Contar combinaciones de 2 productos
    pares = Counter()
    for productos_en_ticket in tickets:
        if len(productos_en_ticket) > 1:
            for par in combinations(sorted(productos_en_ticket), 2):
                pares[par] += 1
                
    # Convertir a DataFrame
    df_pares = pd.DataFrame(pares.most_common(top_n), columns=['Par_de_Productos', 'Frecuencia'])
    if not df_pares.empty:
        df_pares['Producto 1'] = df_pares['Par_de_Productos'].apply(lambda x: x[0])
        df_pares['Producto 2'] = df_pares['Par_de_Productos'].apply(lambda x: x[1])
        return df_pares[['Producto 1', 'Producto 2', 'Frecuencia']]
    return pd.DataFrame()


# -------------------------
# Modelos ligeros de Pronóstico
# -------------------------
@st.cache_data(ttl=TTL_VENTAS_HIST)
def preparar_serie_diaria_simple(df: pd.DataFrame, marca: Optional[str]=None, sucursal: Optional[str]=None) -> pd.DataFrame:
    """Devuelve serie diaria agregada con columnas ds (datetime) y y (float)."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["ds","y"])
    d = df.copy()
    d['FECHA'] = pd.to_datetime(d['FECHA'])
    if marca and marca.upper() not in ("TODAS","TODOS","ALL"):
        d = d[d['MARCA'].astype(str).str.strip().str.upper() == marca.strip().upper()]
    if sucursal and sucursal.upper() not in ("TODAS","TODOS","ALL"):
        d = d[d['SUCURSAL'].astype(str).str.strip().str.upper() == sucursal.strip().upper()]
    s = d.groupby(d['FECHA'].dt.date)['TOTAL'].sum().reset_index()
    s.columns = ['ds','y']
    s['ds'] = pd.to_datetime(s['ds'])
    idx = pd.date_range(s['ds'].min(), s['ds'].max(), freq='D')
    s = s.set_index('ds').reindex(idx).fillna(0.0).rename_axis('ds').reset_index()
    return s

# Métricas
def mae(y_true, y_pred): return float(np.mean(np.abs(y_true - y_pred)))
def rmse(y_true, y_pred): return float(np.sqrt(np.mean((y_true - y_pred)**2)))
def mape(y_true, y_pred):
    denom = np.where(y_true==0, 1, y_true)
    return float(np.mean(np.abs((y_true - y_pred) / denom)) * 100)

# 1) Naive estacional por día de semana
def forecast_naive_seasonal(serie: pd.DataFrame, horizon: int):
    df = serie.copy().set_index('ds')
    df['dow'] = df.index.dayofweek
    seasonal_mean = df.groupby('dow')['y'].mean()
    last_date = df.index.max()
    future_idx = pd.date_range(last_date + pd.Timedelta(days=1), periods=horizon, freq='D')
    preds = [float(seasonal_mean.get(d.dayofweek, df['y'].mean())) for d in future_idx]
    return pd.DataFrame({'ds': future_idx, 'yhat': preds})

# 2) Media móvil simple
def forecast_moving_average(serie: pd.DataFrame, horizon: int, window: int = 7):
    df = serie.copy().set_index('ds')
    last_mean = df['y'].rolling(window).mean().iloc[-1]
    last_mean = float(last_mean) if not np.isnan(last_mean) else float(df['y'].mean())
    future_idx = pd.date_range(df.index.max() + pd.Timedelta(days=1), periods=horizon, freq='D')
    preds = [last_mean] * horizon
    return pd.DataFrame({'ds': future_idx, 'yhat': preds})

# 3) Suavizado exponencial simple (SES)
def forecast_exp_smoothing(serie: pd.DataFrame, horizon: int, alpha: Optional[float] = None):
    y = serie['y'].values
    if len(y) == 0:
        return pd.DataFrame({'ds': [], 'yhat': []})
    if alpha is None:
        alpha = 0.3
    s = y[0]
    for val in y[1:]:
        s = alpha * val + (1 - alpha) * s
    preds = [float(s)] * horizon
    future_idx = pd.date_range(serie['ds'].max() + pd.Timedelta(days=1), periods=horizon, freq='D')
    return pd.DataFrame({'ds': future_idx, 'yhat': preds})

# 4) Tendencia lineal
def forecast_linear_trend(serie: pd.DataFrame, horizon: int):
    df = serie.copy().reset_index(drop=True)
    x = np.arange(len(df))
    y = df['y'].values
    if len(x) < 2:
        return forecast_moving_average(serie, horizon, window=7)
    slope, intercept = np.polyfit(x, y, 1)
    future_x = np.arange(len(df), len(df) + horizon)
    preds = intercept + slope * future_x
    preds = [float(max(0, p)) for p in preds]
    future_idx = pd.date_range(df['ds'].iloc[-1] + pd.Timedelta(days=1), periods=horizon, freq='D')
    return pd.DataFrame({'ds': future_idx, 'yhat': preds})

# 5) Modelo aditivo simple 
def forecast_additive_simple(serie: pd.DataFrame, horizon: int, holidays: Optional[List[date]] = None, trend_degree: int = 1):
    df = serie.copy().reset_index(drop=True)
    df['t'] = np.arange(len(df))  
    
    if len(df) < 2:
        trend_vals = np.zeros(horizon)
    else:
        coef = np.polyfit(df['t'].values, df['y'].values, deg=trend_degree)
        future_t = np.arange(len(df), len(df) + horizon)
        trend_vals = np.polyval(coef, future_t)

    df_idxed = serie.copy().set_index('ds')
    df_idxed['dow'] = df_idxed.index.dayofweek
    seasonal_mean = df_idxed.groupby('dow')['y'].mean().to_dict()
    future_idx = pd.date_range(serie['ds'].max() + pd.Timedelta(days=1), periods=horizon, freq='D')
    seasonal_vals = np.array([seasonal_mean.get(d.dayofweek, df_idxed['y'].mean()) for d in future_idx])

    holiday_effect = np.zeros(horizon)
    if holidays:
        hist_effects = []
        for h in holidays:
            try:
                h_dt = pd.to_datetime(h).date()
            except Exception:
                continue
            if h_dt in df_idxed.index.date:
                val = df_idxed.loc[df_idxed.index.date == h_dt, 'y'].sum()
                dow = pd.to_datetime(h_dt).dayofweek
                baseline = seasonal_mean.get(dow, df_idxed['y'].mean())
                hist_effects.append(val - baseline)
        avg_holiday_effect = np.mean(hist_effects) if hist_effects else 0.0
        holiday_effect = np.array([avg_holiday_effect if d.date() in [pd.to_datetime(h).date() for h in holidays] else 0.0 for d in future_idx])

    preds = trend_vals + seasonal_vals + holiday_effect
    preds = [float(max(0, p)) for p in preds]
    return pd.DataFrame({'ds': future_idx, 'yhat': preds})

# Ensemble simple
def forecast_ensemble_all(serie: pd.DataFrame, horizon: int, holidays: Optional[List[date]] = None):
    preds_list = []
    preds_list.append(forecast_naive_seasonal(serie, horizon)['yhat'].values)
    preds_list.append(forecast_moving_average(serie, horizon)['yhat'].values)
    preds_list.append(forecast_exp_smoothing(serie, horizon)['yhat'].values)
    preds_list.append(forecast_linear_trend(serie, horizon)['yhat'].values)
    preds_list.append(forecast_additive_simple(serie, horizon, holidays=holidays)['yhat'].values)
    avg = np.mean(np.vstack(preds_list), axis=0)
    future_idx = pd.date_range(serie['ds'].max() + pd.Timedelta(days=1), periods=horizon, freq='D')
    return pd.DataFrame({'ds': future_idx, 'yhat': avg})

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


        # --- SECCIÓN: ANÁLISIS DE PRODUCTOS Y COMPORTAMIENTO ---
        st.markdown("---")
        st.markdown("<h3 style='text-align: center;'>ANÁLISIS DE PRODUCTOS Y COMPORTAMIENTO</h3>", unsafe_allow_html=True)
        
        tab1, tab2, tab3, tab4 = st.tabs(["🏆 Top Productos", "📂 Categorías", "⏰ Horarios de Venta", "🛒 Venta Cruzada"])

        # TAB 1: Top Productos
        with tab1:
            if 'PRODUCTO' in df_v_filtrado.columns:
                top_prods = df_v_filtrado.groupby('PRODUCTO')['TOTAL'].sum().nlargest(10).reset_index()
                fig_prod = px.bar(top_prods, x='TOTAL', y='PRODUCTO', orientation='h', 
                                  title='Top 10 Productos por Ingreso', color='TOTAL', color_continuous_scale='Purples')
                fig_prod.update_layout(yaxis={'categoryorder':'total ascending'})
                st.plotly_chart(fig_prod, use_container_width=True)
            else:
                st.info("La columna 'PRODUCTO' no está disponible en los datos.")

        # TAB 2: Categorías
        with tab2:
            if 'CATEGORÍA' in df_v_filtrado.columns:
                cat_sales = df_v_filtrado.groupby('CATEGORÍA')['TOTAL'].sum().reset_index()
                fig_cat = px.pie(cat_sales, values='TOTAL', names='CATEGORÍA', hole=0.4, 
                                 title='Distribución de Ventas por Categoría')
                st.plotly_chart(fig_cat, use_container_width=True)
            else:
                st.info("La columna 'CATEGORÍA' no está disponible en los datos.")

        # TAB 3: Horarios de Venta
        with tab3:
            df_horas = analizar_horarios(df_v_filtrado)
            if not df_horas.empty:
                fig_horas = px.bar(df_horas, x='HORA NÚMERO', y='TOTAL', 
                                   title='Ingresos por Hora del Día',
                                   labels={'HORA NÚMERO': 'Hora del día', 'TOTAL': 'Ventas ($)'})
                fig_horas.update_xaxes(tickmode='linear', tick0=0, dtick=1)
                st.plotly_chart(fig_horas, use_container_width=True)
            else:
                st.info("No se pudo extraer la información de 'HORA NÚMERO'.")

        # TAB 4: Venta Cruzada (Market Basket)
        with tab4:
            df_cruzada = analizar_venta_cruzada(df_v_filtrado)
            if not df_cruzada.empty:
                st.write("**Los 10 pares de productos que más se compran juntos en el mismo Número de Venta:**")
                st.dataframe(df_cruzada, use_container_width=True, hide_index=True)
            else:
                st.info("No se detectaron ventas cruzadas suficientes con las columnas 'NÚMERO DE VENTA' y 'PRODUCTO'.")


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

        # -------------------------
        # Bloque: Pronóstico con los 5 modelos principales 
        # -------------------------
        st.sidebar.markdown("### 🔮 Pronóstico: 5 modelos principales")
        horizon = st.sidebar.slider("Horizonte (días)", 7, 60, 14)
        modelo_sel = st.sidebar.selectbox("Modelo", options=[
            "Naive estacional (dow)",
            "Media móvil (rolling)",
            "Suavizado exponencial (SES)",
            "Tendencia lineal",
            "Aditivo simple (Prophet-like)",
            "Ensemble (promedio de los 5)"
        ], index=5)
        
        feriados_input = st.sidebar.text_input("Feriados (YYYY-MM-DD, separados por coma)", value="")
        feriados = []
        if feriados_input:
            for part in feriados_input.split(","):
                p = part.strip()
                if p:
                    try:
                        feriados.append(pd.to_datetime(p).date())
                    except Exception:
                        pass

        btn_run = st.sidebar.button("Generar pronóstico (5 modelos)")

        if btn_run:
            with st.spinner("Generando pronóstico con los 5 modelos..."):
                serie = preparar_serie_diaria_simple(df_ventas, marca=marca_sel if marca_sel!="Todas" else None, sucursal=suc_sel if suc_sel!="Todas" else None)
                if serie.empty or len(serie) < 7:
                    st.warning("No hay datos suficientes para pronosticar.")
                else:
                    if modelo_sel == "Naive estacional (dow)":
                        fc = forecast_naive_seasonal(serie, horizon)
                    elif modelo_sel == "Media móvil (rolling)":
                        fc = forecast_moving_average(serie, horizon, window=7)
                    elif modelo_sel == "Suavizado exponencial (SES)":
                        fc = forecast_exp_smoothing(serie, horizon, alpha=0.25)
                    elif modelo_sel == "Tendencia lineal":
                        fc = forecast_linear_trend(serie, horizon)
                    elif modelo_sel == "Aditivo simple (Prophet-like)":
                        fc = forecast_additive_simple(serie, horizon, holidays=feriados, trend_degree=1)
                    else:
                        fc = forecast_ensemble_all(serie, horizon, holidays=feriados)

                    # Visualización: histórico + pronóstico
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(x=serie['ds'], y=serie['y'], name='Histórico', line=dict(color='#3B75AF')))
                    fig.add_trace(go.Scatter(x=fc['ds'], y=fc['yhat'], name='Pronóstico', line=dict(color='#7030A0', dash='dash')))
                    # Banda heurística +/- 20%
                    upper = fc['yhat'] * 1.2
                    lower = fc['yhat'] * 0.8
                    fig.add_trace(go.Scatter(x=fc['ds'], y=upper, line=dict(width=0), showlegend=False))
                    fig.add_trace(go.Scatter(x=fc['ds'], y=lower, line=dict(width=0), fill='tonexty', fillcolor='rgba(112,48,160,0.1)', showlegend=False))
                    fig.update_layout(height=420, margin=dict(t=20,b=20,l=10,r=10))
                    st.plotly_chart(fig, use_container_width=True)

                    # Backtest holdout simple
                    if len(serie) > horizon * 2:
                        train = serie.iloc[:-horizon].reset_index(drop=True)
                        test = serie.iloc[-horizon:].reset_index(drop=True)
                        if modelo_sel == "Naive estacional (dow)":
                            fc_train = forecast_naive_seasonal(train, horizon)
                        elif modelo_sel == "Media móvil (rolling)":
                            fc_train = forecast_moving_average(train, horizon, window=7)
                        elif modelo_sel == "Suavizado exponencial (SES)":
                            fc_train = forecast_exp_smoothing(train, horizon, alpha=0.25)
                        elif modelo_sel == "Tendencia lineal":
                            fc_train = forecast_linear_trend(train, horizon)
                        elif modelo_sel == "Aditivo simple (Prophet-like)":
                            fc_train = forecast_additive_simple(train, horizon, holidays=feriados, trend_degree=1)
                        else:
                            fc_train = forecast_ensemble_all(train, horizon, holidays=feriados)
                        y_true = test['y'].values
                        y_pred = fc_train['yhat'].values
                        st.write({"MAE": mae(y_true,y_pred), "RMSE": rmse(y_true,y_pred), "MAPE": mape(y_true,y_pred)})
                    else:
                        st.info("No hay suficiente historia para backtest robusto.")

                    # Descargar pronóstico
                    csv = fc.to_csv(index=False).encode('utf-8')
                    st.download_button("Descargar pronóstico (CSV)", data=csv, file_name=f"pronostico_{modelo_sel.replace(' ','_')}_{horizon}d.csv", mime="text/csv")

    except Exception as e:
        logger.exception("Error de visualización en reportes")
        st.error(f"Error de visualización en reportes: {e}")