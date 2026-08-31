import os
import logging
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime
from dateutil.relativedelta import relativedelta
import pandas as pd
import numpy as np
import streamlit as st
from sqlalchemy import text, bindparam
from db_utils import ENGINE_GLOBAL  # Asegurar conexión vía PgBouncer/Supavisor (ej. puerto 6543)[span_0](start_span)[span_0](end_span)

# Optional sklearn for anomaly detection[span_1](start_span)[span_1](end_span)
try:
    from sklearn.ensemble import IsolationForest
    SKLEARN_AVAILABLE = True
except Exception:
    SKLEARN_AVAILABLE = False

# Optional statsmodels for SARIMA forecasting
try:
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    STATSMODELS_AVAILABLE = True
except Exception:
    STATSMODELS_AVAILABLE = False

# -------------------------
# Config y constantes[span_2](start_span)[span_2](end_span)
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
# Helpers de Fecha[span_3](start_span)[span_3](end_span)
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
# DB: índices, materialized view y tabla de auditoría[span_4](start_span)[span_4](end_span)
# -------------------------
def crear_indices_y_mv(run: bool = False):
    sqls = [
        "CREATE INDEX IF NOT EXISTS idx_insumos_fecha_id ON insumos (FECHA DESC, id DESC);",
        "CREATE INDEX IF NOT EXISTS idx_gastos_fecha_id ON gastos (FECHA DESC, id DESC);",
        "CREATE INDEX IF NOT EXISTS idx_insumos_marca ON insumos (MARCA);",
        "CREATE INDEX IF NOT EXISTS idx_gastos_marca ON gastos (MARCA);",
        "CREATE INDEX IF NOT EXISTS idx_insumos_proveedor ON insumos (PROVEEDOR);",
        "CREATE INDEX IF NOT EXISTS idx_gastos_proveedor ON gastos (PROVEEDOR);",
        """
        CREATE TABLE IF NOT EXISTS admin_ops_log (
            id BIGSERIAL PRIMARY KEY,
            op_type TEXT NOT NULL,
            details JSONB,
            executed_by TEXT,
            executed_at TIMESTAMP WITH TIME ZONE DEFAULT now()
        );
        """,
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_resumen_mensual AS
        SELECT origen, proveedor, date_trunc('month', fecha) AS mes, marca, "FORMA PAGO" AS forma_pago, SUM(total) AS total
        FROM (
            SELECT 'INSUMO'::text AS origen, PROVEEDOR, FECHA, MARCA, "FORMA PAGO", TOTAL FROM insumos
            UNION ALL
            SELECT 'GASTO'::text AS origen, PROVEEDOR, FECHA, MARCA, "FORMA PAGO", TOTAL FROM gastos
        ) t
        GROUP BY origen, proveedor, date_trunc('month', fecha), marca, "FORMA PAGO";
        """,
        "CREATE INDEX IF NOT EXISTS idx_mv_resumen_mes ON mv_resumen_mensual (mes);",
        # Índice único requerido para REFRESH CONCURRENTLY
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_resumen_unique ON mv_resumen_mensual (origen, proveedor, mes, marca, forma_pago);"
    ]
    if not run:
        return sqls

    results = []
    try:
        with ENGINE_GLOBAL.connect() as conn:
            for s in sqls:
                conn.execute(text(s))
                results.append(f"OK: {s.splitlines()[0][:80]}")
        return results
    except Exception as e:
        logger.exception("Error creando índices/MV")
        return [f"ERROR: {e}"]

def refresh_materialized_views(run: bool = False, executed_by: Optional[str] = None):
    sql_refresh = "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_resumen_mensual;"
    if not run:
        return [sql_refresh]
    try:
        with ENGINE_GLOBAL.connect() as conn:
            try:
                conn.execute(text(sql_refresh))
            except Exception as e:
                logger.warning("Fallo en REFRESH CONCURRENTLY (puede estar vacía). Ejecutando refresh estándar: %s", e)
                conn.execute(text("REFRESH MATERIALIZED VIEW mv_resumen_mensual;"))
            
            try:
                conn.execute(text(
                    "INSERT INTO admin_ops_log (op_type, details, executed_by) VALUES (:op, :details, :user)"
                ), {"op": "REFRESH_MV", "details": {"mv": "mv_resumen_mensual"}, "user": executed_by})
            except Exception:
                logger.exception("No se pudo registrar admin_ops_log")
        return ["OK: refreshed mv_resumen_mensual"]
    except Exception as e:
        logger.exception("Error refrescando materialized view")
        return [f"ERROR: {e}"]

# -------------------------
# Helpers de MARCAS[span_5](start_span)[span_5](end_span)
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
        logger.exception("Error obteniendo marcas")
        raise RuntimeError("Error al obtener marcas desde la base de datos: " + str(e))

# =====================================================================
# 1. MODO AGGREGATE: Consultas SQL que devuelven resúmenes[span_6](start_span)[span_6](end_span)
# =====================================================================
@st.cache_data(ttl=300)
def obtener_resumen_agregado(fecha_inicio: datetime, fecha_fin: datetime, origen: str, marcas: Optional[List[str]] = None, _refresh: int = 0) -> pd.DataFrame:
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
        if df.empty:
            return df
        df[COL_ANIO_MES] = df["AÑO_MES_ISO"].apply(
            lambda x: f"{x.split('-')[0]} - {MESES_INV[int(x.split('-')[1])]}" if pd.notna(x) else pd.NA
        )
        return df
    except Exception as e:
        logger.exception("Error en obtener_resumen_agregado")
        raise RuntimeError("Error al consultar resumen agregado: " + str(e))

@st.cache_data(ttl=300)
def obtener_metricas_agregadas(fecha_inicio: datetime, fecha_fin: datetime, origen: str, marcas: Optional[List[str]] = None, _refresh: int = 0) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    marca_clause = ""
    if marcas:
        marca_clause = ' AND "MARCA" IN :marcas '
    q_cat = text(f"""
        SELECT "CATEGORÍA", SUM("TOTAL") AS "TOTAL" FROM gastos 
        WHERE "FECHA" BETWEEN :inicio AND :fin AND "CATEGORÍA" IS NOT NULL {marca_clause} GROUP BY "CATEGORÍA"
    """)
    q_ins = text(f"""
        SELECT "INSUMO", SUM("TOTAL") AS "TOTAL" FROM insumos 
        WHERE "FECHA" BETWEEN :inicio AND :fin AND "INSUMO" IS NOT NULL {marca_clause} GROUP BY "INSUMO"
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
                q_ins = q_ins.bindparams(bindparam("marcas", expanding=True))
                q_fp_insumos = q_fp_insumos.bindparams(bindparam("marcas", expanding=True))
                q_fp_gastos = q_fp_gastos.bindparams(bindparam("marcas", expanding=True))
                params["marcas"] = marcas
            df_cat = pd.DataFrame()
            df_ins = pd.DataFrame()
            df_fp_i = pd.DataFrame()
            df_fp_g = pd.DataFrame()
            if origen in [TIPO_AMBOS, TIPO_GASTOS]:
                df_cat = pd.read_sql(q_cat, conn, params=params)
                df_fp_g = pd.read_sql(q_fp_gastos, conn, params=params)
            if origen in [TIPO_AMBOS, TIPO_INSUMOS]:
                df_ins = pd.read_sql(q_ins, conn, params=params)
                df_fp_i = pd.read_sql(q_fp_insumos, conn, params=params)
    except Exception as e:
        logger.exception("Error en obtener_metricas_agregadas")
        raise RuntimeError("Error al consultar métricas agregadas: " + str(e))
    return df_cat, df_ins, df_fp_g, df_fp_i

# =====================================================================
# 2. MODO DETAIL: Seek Cursor Paginado[span_7](start_span)[span_7](end_span)
# =====================================================================
def obtener_detalle_proveedor_paginado(proveedor: str, fecha_inicio: datetime, fecha_fin: datetime, 
                                       limit: int = 15, last_fecha: str = None, last_id: int = None, marcas: Optional[List[str]] = None) -> Tuple[pd.DataFrame, bool]:
    where_clauses = [
        '"PROVEEDOR" = :proveedor',
        '"FECHA" >= :inicio',
        '"FECHA" <= :fin'
    ]
    params = {
        "proveedor": proveedor,
        "inicio": fecha_inicio.strftime('%Y-%m-%d'),
        "fin": fecha_fin.strftime('%Y-%m-%d'),
        "limit": limit + 1
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
    try:
        with ENGINE_GLOBAL.connect() as conn:
            df = pd.read_sql(final_query, conn, params=params, parse_dates=["FECHA"])
    except Exception as e:
        logger.exception("Error en obtener_detalle_proveedor_paginado")
        raise RuntimeError("Error al consultar detalle paginado: " + str(e))
    if df.empty:
        return df, False
    has_more = len(df) > limit
    if has_more:
        df = df.iloc[:limit]
    return df, has_more

# =====================================================================
# 3. LÓGICA DE ANÁLISIS EN MEMORIA[span_8](start_span)[span_8](end_span)
# =====================================================================
def clasificacion_abc(df_agregado: pd.DataFrame) -> pd.DataFrame:
    if df_agregado.empty: return pd.DataFrame()
    agg = df_agregado.groupby(COL_PROVEEDOR)[COL_TOTAL].sum().reset_index().sort_values(by=COL_TOTAL, ascending=False)
    agg['TOTAL_ACUM'] = agg[COL_TOTAL].cumsum()
    total = agg[COL_TOTAL].sum()
    agg['CUM_PCT'] = agg['TOTAL_ACUM'] / total
    agg['ABC'] = agg['CUM_PCT'].apply(lambda p: 'A (Crítico)' if p <= 0.80 else ('B (Medio)' if p <= 0.95 else 'C (Bajo)'))
    return agg[[COL_PROVEEDOR, COL_TOTAL, 'CUM_PCT', 'ABC']]

def pareto_evolucion(df_resumen: pd.DataFrame, meses_meta: List[Dict[str, Any]]) -> pd.DataFrame:
    if df_resumen.empty:
        return pd.DataFrame()
    df_month = df_resumen.groupby([COL_ANIO_MES, COL_PROVEEDOR])[COL_TOTAL].sum().reset_index()
    out = []
    for mes in sorted(df_month[COL_ANIO_MES].unique()):
        tmp = df_month[df_month[COL_ANIO_MES] == mes].sort_values(COL_TOTAL, ascending=False)
        tmp['pct'] = tmp[COL_TOTAL] / tmp[COL_TOTAL].sum()
        tmp['cum_pct'] = tmp['pct'].cumsum()
        tmp['mes'] = mes
        out.append(tmp[['mes', COL_PROVEEDOR, COL_TOTAL, 'pct', 'cum_pct']])
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

@st.cache_data(ttl=600, show_spinner=False)
def forecast_sarima(df_resumen: pd.DataFrame, periods: int = 3) -> pd.DataFrame:
    if df_resumen.empty:
        return pd.DataFrame()
    df = df_resumen.copy()
    df['mes_iso'] = df['AÑO_MES_ISO'].apply(lambda x: pd.to_datetime(x + "-01") if pd.notna(x) else pd.NaT)
    series = df.groupby('mes_iso')[COL_TOTAL].sum().reset_index().sort_values('mes_iso')
    
    if series.shape[0] < 2:
        return series.rename(columns={COL_TOTAL: 'total'}).assign(forecast=np.nan)
    
    series['x'] = np.arange(len(series))
    future_x = np.arange(len(series), len(series) + periods)
    future_dates = [series['mes_iso'].max() + relativedelta(months=i+1) for i in range(periods)]
    
    if STATSMODELS_AVAILABLE and len(series) >= 12:
        try:
            model = SARIMAX(series[COL_TOTAL].values, order=(1, 1, 1), seasonal_order=(1, 1, 1, 12))
            fit_model = model.fit(disp=False)
            series['forecast'] = fit_model.fittedvalues
            forecast_vals = fit_model.forecast(steps=periods)
        except Exception as e:
            logger.warning("SARIMA falló, usando regresión lineal: %s", e)
            coef = np.polyfit(series['x'], series[COL_TOTAL], 1)
            poly = np.poly1d(coef)
            series['forecast'] = poly(series['x'])
            forecast_vals = poly(future_x)
    else:
        coef = np.polyfit(series['x'], series[COL_TOTAL], 1)
        poly = np.poly1d(coef)
        series['forecast'] = poly(series['x'])
        forecast_vals = poly(future_x)

    df_future = pd.DataFrame({'mes_iso': future_dates, 'total': np.nan, 'forecast': forecast_vals})
    series = series[['mes_iso', COL_TOTAL, 'forecast']].rename(columns={COL_TOTAL: 'total'})
    return pd.concat([series, df_future], ignore_index=True)

@st.cache_data(ttl=600)
def obtener_stats_proveedor(fecha_inicio: datetime, fecha_fin: datetime, marcas: Optional[List[str]] = None) -> pd.DataFrame:
    marca_clause = ""
    if marcas:
        marca_clause = ' AND "MARCA" IN :marcas '
    q = text(f"""
        SELECT proveedor AS "PROVEEDOR",
               AVG(total) AS mean_total,
               STDDEV_POP(total) AS std_total,
               COUNT(*) AS n
        FROM (
            SELECT PROVEEDOR, TOTAL FROM insumos WHERE "FECHA" BETWEEN :inicio AND :fin {marca_clause}
            UNION ALL
            SELECT PROVEEDOR, TOTAL FROM gastos WHERE "FECHA" BETWEEN :inicio AND :fin {marca_clause}
        ) t
        GROUP BY proveedor
    """)
    try:
        with ENGINE_GLOBAL.connect() as conn:
            params = {"inicio": fecha_inicio.strftime('%Y-%m-%d'), "fin": fecha_fin.strftime('%Y-%m-%d')}
            if marcas:
                q = q.bindparams(bindparam("marcas", expanding=True))
                params["marcas"] = marcas
            df = pd.read_sql(q, conn)
            return df
    except Exception as e:
        logger.exception("Error en obtener_stats_proveedor")
        raise RuntimeError("Error al calcular estadísticas por proveedor: " + str(e))

@st.cache_data(ttl=600, show_spinner=False)
def aislar_anomalias_iforest(vals: np.ndarray) -> np.ndarray:
    iso = IsolationForest(contamination=0.01, random_state=42)
    preds = iso.fit_predict(vals)
    return preds == -1

def detectar_anomalias_con_stats(df_detalle: pd.DataFrame, stats_df: pd.DataFrame, z_thresh: float = 3.0) -> pd.DataFrame:
    if df_detalle.empty:
        return df_detalle
    df = df_detalle.copy()
    df['TOTAL_NUM'] = pd.to_numeric(df['TOTAL'], errors='coerce').fillna(0.0)
    df = df.merge(stats_df, on='PROVEEDOR', how='left')
    df['std_total'] = df['std_total'].replace({0: np.nan})
    df['z'] = (df['TOTAL_NUM'] - df['mean_total']) / df['std_total']
    df['anomaly_z'] = df['z'].abs() > z_thresh
    
    if SKLEARN_AVAILABLE:
        try:
            vals = df[['TOTAL_NUM']].fillna(0.0).values
            df['anomaly_iforest'] = aislar_anomalias_iforest(vals)
        except Exception as e:
            logger.warning("IsolationForest falló: %s", e)
            df['anomaly_iforest'] = False
    else:
        df['anomaly_iforest'] = False
        
    df['anomaly'] = df['anomaly_z'] | df['anomaly_iforest']
    return df.drop(columns=['TOTAL_NUM', 'z', 'anomaly_z', 'anomaly_iforest'], errors='ignore')

# =====================================================================
# 4. INTERFAZ STREAMLIT[span_9](start_span)[span_9](end_span)
# =====================================================================
def render_tab_resumen(df_resumen: pd.DataFrame, meses_meta: List[Dict[str, Any]]):
    st.subheader("Resumen: Totales por Proveedor")
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
    st.markdown("**Evolución total por mes**")
    series_total = df_resumen.groupby('AÑO_MES_ISO')[COL_TOTAL].sum().reset_index().sort_values('AÑO_MES_ISO')
    series_total['mes_dt'] = pd.to_datetime(series_total['AÑO_MES_ISO'] + "-01")
    st.line_chart(series_total.set_index('mes_dt')[COL_TOTAL])

def render_tab_pareto(df_resumen: pd.DataFrame, meses_meta: List[Dict[str, Any]]):
    st.subheader("Clasificación de Gasto ABC")
    abc = clasificacion_abc(df_resumen)
    st.dataframe(abc.style.format({COL_TOTAL: "${:,.2f}", 'CUM_PCT': "{:.2%}"}), use_container_width=True, hide_index=True)
    st.markdown("**Evolución Pareto mensual**")
    pareto_df = pareto_evolucion(df_resumen, meses_meta)
    if not pareto_df.empty:
        st.dataframe(pareto_df.sort_values(['mes', 'cum_pct'], ascending=[True, False]).head(50).style.format({COL_TOTAL: "${:,.2f}", 'pct': "{:.1%}", 'cum_pct': "{:.1%}"}), use_container_width=True, hide_index=True)
    else:
        st.info("Sin datos para Pareto.")

def render_tab_concentracion_gastos(df_cat: pd.DataFrame, df_fp_g: pd.DataFrame, df_resumen: pd.DataFrame):
    st.subheader("Concentración de Gastos")
    colA1, colA2, colA3 = st.columns(3)
    with colA1:
        st.markdown("**Por Categoría**")
        if not df_cat.empty:
            df_cat['PCT'] = df_cat["TOTAL"] / df_cat["TOTAL"].sum()
            st.dataframe(df_cat.sort_values("TOTAL", ascending=False).style.format({"TOTAL": "${:,.2f}", 'PCT': "{:.1%}"}), hide_index=True)
        else:
            st.info("Sin datos.")
    with colA2:
        st.markdown("**Por Proveedor**")
        df_prov_g = df_resumen[df_resumen['ORIGEN'] == 'GASTO'].groupby(COL_PROVEEDOR)[COL_TOTAL].sum().reset_index()
        if not df_prov_g.empty:
            df_prov_g['PCT'] = df_prov_g["TOTAL"] / df_prov_g["TOTAL"].sum()
            st.dataframe(df_prov_g.sort_values("TOTAL", ascending=False).style.format({"TOTAL": "${:,.2f}", 'PCT': "{:.1%}"}), hide_index=True)
        else:
            st.info("Sin datos.")
    with colA3:
        st.markdown("**Por Forma de Pago**")
        if not df_fp_g.empty:
            df_fp_g['PCT'] = df_fp_g["TOTAL"] / df_fp_g["TOTAL"].sum()
            st.dataframe(df_fp_g.sort_values("TOTAL", ascending=False).style.format({"TOTAL": "${:,.2f}", 'PCT': "{:.1%}"}), hide_index=True)
        else:
            st.info("Sin datos.")

def render_tab_concentracion_insumos(df_ins: pd.DataFrame, df_fp_i: pd.DataFrame, df_resumen: pd.DataFrame):
    st.subheader("Concentración de Insumos")
    colB1, colB2, colB3 = st.columns(3)
    with colB1:
        st.markdown("**Por Insumo**")
        if not df_ins.empty:
            df_ins['PCT'] = df_ins["TOTAL"] / df_ins["TOTAL"].sum()
            st.dataframe(df_ins.sort_values("TOTAL", ascending=False).style.format({"TOTAL": "${:,.2f}", 'PCT': "{:.1%}"}), hide_index=True)
        else:
            st.info("Sin datos.")
    with colB2:
        st.markdown("**Por Proveedor**")
        df_prov_i = df_resumen[df_resumen['ORIGEN'] == 'INSUMO'].groupby(COL_PROVEEDOR)[COL_TOTAL].sum().reset_index()
        if not df_prov_i.empty:
            df_prov_i['PCT'] = df_prov_i["TOTAL"] / df_prov_i["TOTAL"].sum()
            st.dataframe(df_prov_i.sort_values("TOTAL", ascending=False).style.format({"TOTAL": "${:,.2f}", 'PCT': "{:.1%}"}), hide_index=True)
        else:
            st.info("Sin datos.")
    with colB3:
        st.markdown("**Por Forma de Pago**")
        if not df_fp_i.empty:
            df_fp_i['PCT'] = df_fp_i["TOTAL"] / df_fp_i["TOTAL"].sum()
            st.dataframe(df_fp_i.sort_values("TOTAL", ascending=False).style.format({"TOTAL": "${:,.2f}", 'PCT': "{:.1%}"}), hide_index=True)
        else:
            st.info("Sin datos.")

def render_tab_detalle(df_resumen: pd.DataFrame, fecha_inicio: datetime, fecha_fin: datetime, marcas_param: Optional[List[str]]):
    st.subheader("Buscador de Movimientos")
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
        try:
            df_pagina, has_more = obtener_detalle_proveedor_paginado(
                proveedor=prov_sel, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, 
                limit=limit, last_fecha=cur_fecha, last_id=cur_id, marcas=marcas_param
            )
        except Exception as e:
            st.error(f"Error al obtener detalle: {e}")
            return
        st.dataframe(df_pagina.style.format({"TOTAL": "${:,.2f}"}), use_container_width=True, hide_index=True)
        b1, b2, b3 = st.columns([1, 1, 4])
        with b1:
            prev_disabled = len(st.session_state.cursor_stack_prov) == 0
            if st.button("⬅️ Anterior", disabled=prev_disabled, key="prev_page") and not prev_disabled:
                st.session_state.current_cursor_prov = st.session_state.cursor_stack_prov.pop()
                st.rerun()
        with b2:
            next_disabled = (not has_more)
            if st.button("Siguiente ➡️", disabled=next_disabled, key="next_page") and not next_disabled and not df_pagina.empty:
                st.session_state.cursor_stack_prov.append(st.session_state.current_cursor_prov)
                ultimo = df_pagina.iloc[-1]
                st.session_state.current_cursor_prov = (ultimo["FECHA"].strftime('%Y-%m-%d %H:%M:%S'), int(ultimo["id"]))
                st.rerun()
        with b3:
            st.markdown(f"**Página:** {len(st.session_state.cursor_stack_prov) + 1}  —  **Hay más registros:** {'Sí' if has_more else 'No'}")

def render_tab_analisis_avanzados(df_resumen: pd.DataFrame, fecha_inicio: datetime, fecha_fin: datetime, marcas_param: Optional[List[str]]):
    st.subheader("Análisis Avanzados")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Forecast Estacional (SARIMA)**")
        with st.spinner("Proyectando tendencias..."):
            forecast_df = forecast_sarima(df_resumen, periods=3)
        if not forecast_df.empty:
            df_plot = forecast_df.copy()
            df_plot['mes'] = pd.to_datetime(df_plot['mes_iso'])
            df_plot = df_plot.set_index('mes')[['total', 'forecast']]
            st.line_chart(df_plot.ffill())
            st.dataframe(forecast_df.style.format({ 'total': "${:,.2f}", 'forecast': "${:,.2f}"}), use_container_width=True)
        else:
            st.info("No hay datos suficientes para forecast.")
    with col2:
        st.markdown("**Detección de anomalías (Caché Optimizada)**")
        try:
            stats_df = obtener_stats_proveedor(fecha_inicio, fecha_fin, marcas=marcas_param)
        except Exception as e:
            st.error(f"Error calculando estadísticas por proveedor: {e}")
            return
        q = text("""
            SELECT id, 'INSUMO' AS "ORIGEN", "FECHA", "PROVEEDOR", COALESCE("INSUMO", "GASTO DE") AS "CONCEPTO",
                   "FORMA PAGO", "MARCA", "TOTAL"
            FROM (
                SELECT id, 'INSUMO' AS origen, FECHA, PROVEEDOR, INSUMO, NULL::text AS "GASTO DE", "FORMA PAGO", "MARCA", TOTAL FROM insumos
                UNION ALL
                SELECT id, 'GASTO' AS origen, FECHA, PROVEEDOR, NULL::text AS INSUMO, "GASTO DE", "FORMA PAGO", "MARCA", TOTAL FROM gastos
            ) t
            WHERE "FECHA" BETWEEN :inicio AND :fin
            ORDER BY "FECHA" DESC
            LIMIT 20000
        """)
        try:
            with ENGINE_GLOBAL.connect() as conn:
                df_detalle_for_anom = pd.read_sql(q, conn, params={"inicio": fecha_inicio.strftime('%Y-%m-%d'), "fin": fecha_fin.strftime('%Y-%m-%d')}, parse_dates=["FECHA"])
        except Exception as e:
            st.error(f"Error trayendo detalle para anomalías: {e}")
            return
        if df_detalle_for_anom is not None and not df_detalle_for_anom.empty:
            with st.spinner("Analizando desviaciones..."):
                df_anom = detectar_anomalias_con_stats(df_detalle_for_anom, stats_df)
            st.dataframe(df_anom.sort_values('FECHA', ascending=False).head(200).style.format({"TOTAL": "${:,.2f}"}), use_container_width=True, hide_index=True)
            st.markdown(f"Total registros analizados: **{len(df_anom)}** — Anomalías detectadas: **{int(df_anom['anomaly'].sum())}**")
        else:
            st.info("No hay datos para análisis de anomalías.")
    st.markdown("---")
    st.markdown("**Heatmap: gasto por proveedor vs mes**")
    try:
        heat = df_resumen.pivot_table(index=COL_PROVEEDOR, columns=COL_ANIO_MES, values=COL_TOTAL, aggfunc='sum', fill_value=0)
        if not heat.empty:
            heat_norm = heat.div(heat.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
            st.dataframe(heat_norm.style.format("{:.1%}"), use_container_width=True)
        else:
            st.info("Sin datos para heatmap.")
    except Exception:
        st.info("No se pudo generar heatmap.")

# -------------------------
# Main UI[span_10](start_span)[span_10](end_span)
# -------------------------
def mostrar_pestana_recurrencia():
    try:
        st.set_page_config(page_title="Pagos y Proveedores", layout="wide")
    except Exception:
        pass
    st.title("🔄 Pagos y Proveedores - Dashboard optimizado")
    if "cursor_stack_prov" not in st.session_state:
        st.session_state.cursor_stack_prov = []
    if "current_cursor_prov" not in st.session_state:
        st.session_state.current_cursor_prov = (None, None)
    if "last_prov_searched" not in st.session_state:
        st.session_state.last_prov_searched = None
    if "refresh_counter" not in st.session_state:
        st.session_state.refresh_counter = 0
    st.sidebar.header("Controles")
    if st.sidebar.button("🔄 Actualizar datos (forzar)", key="force_refresh"):
        st.session_state.refresh_counter += 1
        st.rerun()
    
    ADMIN_MODE = os.getenv("APP_ADMIN_MODE", "false").lower() in ("1", "true", "yes")
    with st.sidebar.expander("DB: índices y materialized view (opcional)"):
        st.markdown("Crear índices y materialized view recomendados para mejorar rendimiento.")
        sqls = crear_indices_y_mv(run=False)
        if ADMIN_MODE:
            st.markdown("**SQL a ejecutar (vista previa)**")
            for i, s in enumerate(sqls):
                st.code(s, language='sql')  # <-- CORREGIDO (sin key)
            st.markdown("**Advertencia:** ejecutar DDL puede afectar la base de datos. Ejecuta solo en horario de baja carga.")
            with st.form("exec_ddl_form"):
                run_confirm = st.checkbox("Confirmo que soy administrador y quiero ejecutar estas operaciones", value=False, key="confirm_admin")
                submit = st.form_submit_button("Ejecutar creación en BD", disabled=not run_confirm, key="exec_ddl_btn")
                if submit:
                    if st.session_state.get("ddl_running"):
                        st.warning("Otra operación DDL está en curso. Espera a que termine.")
                    else:
                        st.session_state["ddl_running"] = True
                        try:
                            with st.spinner("Ejecutando SQL en la base de datos..."):
                                res = crear_indices_y_mv(run=True)
                                st.success("Operación completada.")
                                st.write(res)
                                try:
                                    st.cache_data.clear()
                                    st.info("Cache invalidado para reflejar cambios.")
                                except Exception:
                                    logger.exception("No se pudo invalidar cache automáticamente.")
                        except Exception as e:
                            logger.exception("Error ejecutando SQL en BD")
                            st.error(f"Error ejecutando SQL en BD: {e}")
                        finally:
                            st.session_state["ddl_running"] = False
            
            with st.form("refresh_mv_form"):
                refresh_confirm = st.checkbox("Refrescar materialized view mv_resumen_mensual (concurrently)", value=False, key="confirm_refresh")
                submit_refresh = st.form_submit_button("Refrescar MV", disabled=not refresh_confirm, key="refresh_mv_btn")
                if submit_refresh:
                    if st.session_state.get("mv_running"):
                        st.warning("Otra operación de refresh está en curso.")
                    else:
                        st.session_state["mv_running"] = True
                        try:
                            with st.spinner("Refrescando materialized view..."):
                                res = refresh_materialized_views(run=True, executed_by=os.getenv("USER") or os.getenv("USERNAME") or "app_admin")
                                st.write(res)
                                try:
                                    st.cache_data.clear()
                                    st.info("Cache invalidado tras refresh.")
                                except Exception:
                                    logger.exception("No se pudo invalidar cache automáticamente.")
                        except Exception as e:
                            logger.exception("Error refrescando MV")
                            st.error(f"Error refrescando MV: {e}")
                        finally:
                            st.session_state["mv_running"] = False
        else:
            st.info("Operación restringida: habilita APP_ADMIN_MODE en el entorno para ejecutar DDL desde la UI.")
            st.markdown("**SQL sugerido (solo vista previa)**")
            for i, s in enumerate(sqls):
                st.code(s, language='sql')  # <-- CORREGIDO (sin key)
            st.markdown("Recomendación: ejecutar estos scripts desde una herramienta de administración (psql, pgAdmin) o programar un job (pg_cron / Supabase Edge Function).")
    st.sidebar.header("Filtros de Análisis")
    try:
        marcas_disponibles = obtener_marcas_disponibles()
    except Exception as e:
        st.error(f"Error al cargar marcas: {e}")
        st.stop()
    opciones_marcas = ["TODAS"] + marcas_disponibles
    marcas_sel_ui = st.sidebar.multiselect("Filtrar por Marca", options=opciones_marcas, default=[], help="Selecciona una o varias marcas.")
    if not marcas_sel_ui:
        st.info("👈 **Por favor, selecciona al menos una MARCA.**")
        st.stop()
    marcas_param = None if "TODAS" in marcas_sel_ui else marcas_sel_ui
    fecha_hasta = st.sidebar.date_input("Fecha de corte (hasta)", value=datetime.today())
    meses_ventana = st.sidebar.selectbox("Meses a analizar", [3, 6, 12, 24], index=1)
    tipo_modulo = st.sidebar.selectbox("Módulo", [TIPO_AMBOS, TIPO_GASTOS, TIPO_INSUMOS], index=0)
    meses_meta = meses_anteriores(datetime(fecha_hasta.year, fecha_hasta.month, 1), meses_ventana)
    fecha_inicio = meses_meta[0]['fecha_inicio']
    fecha_fin = meses_meta[-1]['fecha_fin']
    with st.spinner("Consultando agregados desde la Base de Datos..."):
        try:
            df_resumen = obtener_resumen_agregado(fecha_inicio, fecha_fin, tipo_modulo, marcas=marcas_param, _refresh=st.session_state.refresh_counter)
            df_cat, df_ins, df_fp_g, df_fp_i = obtener_metricas_agregadas(fecha_inicio, fecha_fin, tipo_modulo, marcas=marcas_param, _refresh=st.session_state.refresh_counter)
        except Exception as e:
            st.error(f"Error consultando datos: {e}")
            st.stop()
    if df_resumen.empty:
        st.warning("No se encontraron registros en el rango y marca(s) seleccionados.")
        return
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "Resumen y Evolución", 
        "Análisis Pareto", 
        "Concentración Gastos",
        "Concentración Insumos",
        "Detalle",
        "Análisis Avanzados"
    ])
    with tab1:
        render_tab_resumen(df_resumen, meses_meta)
    with tab2:
        render_tab_pareto(df_resumen, meses_meta)
    with tab3:
        render_tab_concentracion_gastos(df_cat, df_fp_g, df_resumen)
    with tab4:
        render_tab_concentracion_insumos(df_ins, df_fp_i, df_resumen)
    with tab5:
        render_tab_detalle(df_resumen, fecha_inicio, fecha_fin, marcas_param)
    with tab6:
        render_tab_analisis_avanzados(df_resumen, fecha_inicio, fecha_fin, marcas_param)

if __name__ == "__main__":
    mostrar_pestana_recurrencia()
