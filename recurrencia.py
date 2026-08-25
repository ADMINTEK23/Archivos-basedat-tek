import logging
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime
from dateutil.relativedelta import relativedelta

import pandas as pd
import streamlit as st
import unicodedata

# 🚀 IMPORTACIÓN CORREGIDA: Ahora apunta a tu archivo utils.py
from db_utils import cargar_datos_por_rango 

# -------------------------
# Config y constantes[span_3](start_span)[span_3](end_span)
# -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

COL_PROVEEDOR = "PROVEEDOR"
COL_ANIO_MES = "AÑO_MES"
COL_ANIO_MES_ISO = "AÑO_MES_ISO" 
COL_TOTAL = "TOTAL"
COL_MARCA = "MARCA"
COL_FECHA = "FECHA"
COL_FORMA_PAGO = "FORMA PAGO"
COL_ORIGEN = "ORIGEN"
COL_CONCEPTO = "CONCEPTO"
COL_CATEGORIA = "CATEGORÍA"
COL_RECURRENCIA = "RECURRENCIA"
COL_TIPO = "TIPO"
COL_COSTO = "COSTO"
COL_UNIDAD = "UNIDAD"
COL_CORREGIDO = "CORREGIDO"

TIPO_AMBOS = "ambos"
TIPO_GASTOS = "gastos"
TIPO_INSUMOS = "insumos"

MESES_MAP = {'ENE': 1, 'FEB': 2, 'MAR': 3, 'ABR': 4, 'MAY': 5, 'JUN': 6,
             'JUL': 7, 'AGO': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DIC': 12}
MESES_INV = {v: k for k, v in MESES_MAP.items()}

try:
    MONEDA_CONFIG = st.column_config.NumberColumn(format="$ %,.2f")
except Exception:
    MONEDA_CONFIG = None

# -------------------------
# Helpers[span_4](start_span)[span_4](end_span)
# -------------------------
def normalize_text(s: Optional[str]) -> Optional[str]:
    """Limpia espacios extra, convierte a mayúsculas y quita acentos."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    t = str(s).strip()
    t = " ".join(t.split())
    t = unicodedata.normalize("NFKD", t).encode("ASCII", "ignore").decode("ASCII")
    return t.upper()

def meses_anteriores(fecha_hasta: datetime, n: int) -> List[Dict[str, Any]]:
    """Devuelve lista cronológica de n meses hasta fecha_hasta."""
    meses = []
    base = datetime(fecha_hasta.year, fecha_hasta.month, 1)
    for i in range(n - 1, -1, -1):
        f = base - relativedelta(months=i)
        meses.append({
            "anio": f.year,
            "mes_num": f.month,
            "etiqueta": f"{f.year} - {MESES_INV[f.month]}",
            "anio_mes_iso": f"{f.year:04d}-{f.month:02d}",
            "fecha_inicio": datetime(f.year, f.month, 1),
            "fecha_fin": (datetime(f.year, f.month, 1) + relativedelta(months=1) - relativedelta(days=1))
        })
    return meses

# -------------------------
# Carga de datos optimizada
# -------------------------
@st.cache_data(ttl=300)
def cargar_rango_cache(fecha_inicio: datetime, fecha_fin: datetime) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Usa la función optimizada por BETWEEN en utils.py"""
    try:
        # Nota: cargar_datos_por_rango devuelve (ventas, insumos, gastos), ignoramos ventas con "_"
        _, insumos, gastos = cargar_datos_por_rango(fecha_inicio, fecha_fin)
        return insumos, gastos
    except Exception as e:
        logger.error(f"Error al cargar datos por rango: {e}")
        return pd.DataFrame(), pd.DataFrame()

# -------------------------
# Normalización y enriquecimiento[span_5](start_span)[span_5](end_span)
# -------------------------
def preparar_datos_rango(insumos: pd.DataFrame, gastos: pd.DataFrame, meses_meta: List[Dict[str, Any]]) -> pd.DataFrame:
    def preparar(df: pd.DataFrame, origen: str, col_detalle: str):
        if df is None or df.empty:
            return None
        df = df.copy()
        
        df[COL_PROVEEDOR] = df[COL_PROVEEDOR].apply(normalize_text) if COL_PROVEEDOR in df.columns else pd.NA
        df[COL_CONCEPTO] = df[col_detalle] if col_detalle in df.columns else pd.NA
        df[COL_ORIGEN] = origen

        if COL_FECHA in df.columns:
            df[COL_FECHA] = pd.to_datetime(df[COL_FECHA], errors='coerce')
        else:
            df[COL_FECHA] = pd.NaT

        columnas_req = [COL_CATEGORIA, COL_RECURRENCIA, COL_TIPO, COL_COSTO, COL_UNIDAD, COL_CORREGIDO, COL_FORMA_PAGO, COL_MARCA]
        for c in columnas_req:
            if c not in df.columns:
                df[c] = pd.NA

        df['AÑO_MES_FROM_FECHA'] = df[COL_FECHA].dt.to_period('M').astype(str).replace({None: pd.NA})
        
        def etiqueta_from_row(row):
            if pd.notna(row['AÑO_MES_FROM_FECHA']) and row['AÑO_MES_FROM_FECHA'] != 'NaT':
                y, m = row['AÑO_MES_FROM_FECHA'].split('-')
                return f"{int(y)} - {MESES_INV[int(m)]}"
            return pd.NA

        df[COL_ANIO_MES] = df.apply(lambda r: etiqueta_from_row(r), axis=1)
        df[COL_ANIO_MES_ISO] = df['AÑO_MES_FROM_FECHA']

        if df[COL_ANIO_MES].isna().any():
            map_iso = {m['anio_mes_iso']: m['etiqueta'] for m in meses_meta}
            df.loc[df[COL_ANIO_MES].isna(), COL_ANIO_MES] = df.loc[df[COL_ANIO_MES].isna(), COL_ANIO_MES_ISO].map(map_iso)

        df[COL_TOTAL] = pd.to_numeric(df.get(COL_TOTAL, 0.0), errors='coerce').fillna(0.0)
        df[COL_COSTO] = pd.to_numeric(df.get(COL_COSTO, pd.NA), errors='coerce')

        return df

    ins_prep = preparar(insumos, 'INSUMO', 'INSUMO')
    gas_prep = preparar(gastos, 'GASTO', 'GASTO DE')

    frames = [f for f in (ins_prep, gas_prep) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
        
    df_unido = pd.concat(frames, ignore_index=True, sort=False)
    df_unido[COL_PROVEEDOR] = df_unido[COL_PROVEEDOR].replace({None: pd.NA})
    return df_unido.reset_index(drop=True)

# -------------------------
# Análisis y métricas[span_6](start_span)[span_6](end_span)
# -------------------------
def clasificacion_abc(df: pd.DataFrame, top_col: str = COL_TOTAL) -> pd.DataFrame:
    agg = df.groupby(COL_PROVEEDOR)[top_col].sum().reset_index().sort_values(by=top_col, ascending=False)
    agg['TOTAL_ACUM'] = agg[top_col].cumsum()
    total = agg[top_col].sum()
    agg['CUM_PCT'] = agg['TOTAL_ACUM'] / total
    agg['ABC'] = agg['CUM_PCT'].apply(lambda p: 'A (Crítico)' if p <= 0.80 else ('B (Medio)' if p <= 0.95 else 'C (Bajo)'))
    return agg[[COL_PROVEEDOR, top_col, 'CUM_PCT', 'ABC']]

def costo_unitario_mensual(df: pd.DataFrame) -> pd.DataFrame:
    dfc = df.copy()
    def costo_unit(row):
        if pd.notna(row.get(COL_COSTO)): return row[COL_COSTO]
        if pd.notna(row.get(COL_UNIDAD)) and row[COL_UNIDAD] not in (0, '0', None) and pd.notna(row.get(COL_TOTAL)):
            try: return float(row[COL_TOTAL]) / float(row[COL_UNIDAD])
            except Exception: return pd.NA
        return pd.NA
    dfc['COSTO_UNIT'] = dfc.apply(costo_unit, axis=1)
    resumen = dfc.groupby([COL_ORIGEN, COL_CONCEPTO, COL_ANIO_MES])['COSTO_UNIT'].mean().reset_index()
    return resumen.pivot_table(index=[COL_ORIGEN, COL_CONCEPTO], columns=COL_ANIO_MES, values='COSTO_UNIT')

def auditoria_recurrencia(df: pd.DataFrame) -> pd.DataFrame:
    conteos = df.groupby([COL_PROVEEDOR, COL_CONCEPTO, COL_RECURRENCIA, COL_ANIO_MES])[COL_TOTAL].count().reset_index(name='TRANS_POR_MES')
    pivot = conteos.pivot_table(index=[COL_PROVEEDOR, COL_CONCEPTO, COL_RECURRENCIA], columns=COL_ANIO_MES, values='TRANS_POR_MES', fill_value=0)
    
    def alerta(row):
        rec = row.name[2] if isinstance(row.name, tuple) and len(row.name) > 2 else None
        vals = row.values
        if rec and isinstance(rec, str) and rec.upper().startswith('M'): 
            if (vals == 0).any(): return '⚠️ OMITIDO'
        if (vals > 1).any(): return '🚨 POTENCIAL DUPLICADO'
        return '✅ OK'
        
    pivot['ESTATUS'] = pivot.apply(alerta, axis=1)
    return pivot.reset_index()

def concentracion_categoria_forma_pago(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    por_cat = df.groupby(COL_CATEGORIA)[COL_TOTAL].sum().reset_index().sort_values(COL_TOTAL, ascending=False)
    por_cat['PCT'] = por_cat[COL_TOTAL] / por_cat[COL_TOTAL].sum()
    
    por_fp = df.groupby(COL_FORMA_PAGO)[COL_TOTAL].sum().reset_index().sort_values(COL_TOTAL, ascending=False)
    por_fp['PCT'] = por_fp[COL_TOTAL] / por_fp[COL_TOTAL].sum()
    return por_cat, por_fp

def mom_variation(df: pd.DataFrame) -> pd.DataFrame:
    resumen = df.groupby([COL_PROVEEDOR, COL_ANIO_MES])[COL_TOTAL].sum().reset_index()
    # Crear un pivot para calcular la variación mes a mes más fácil
    pivot = resumen.pivot_table(index=COL_PROVEEDOR, columns=COL_ANIO_MES, values=COL_TOTAL, fill_value=0)
    return pivot

# -------------------------
# UI (Streamlit)
# -------------------------
def mostrar_pestana_recurrencia():
    st.title("🔄 Inteligencia de Pagos y Proveedores")

    # --- Controles en Sidebar ---
    st.sidebar.header("Filtros de Análisis")
    fecha_hasta = st.sidebar.date_input("Fecha de corte (hasta)", value=datetime.today())
    meses_ventana = st.sidebar.selectbox("Meses a analizar", [3, 6, 12], index=1)
    tipo_modulo = st.sidebar.selectbox("Módulo", [TIPO_AMBOS, TIPO_GASTOS, TIPO_INSUMOS], index=0)
    
    # Checkbox para ignorar o incluir corregidos (Asumiendo 1 = Validado/Auditado)[span_7](start_span)[span_7](end_span)
    solo_corregidos = st.sidebar.checkbox("Excluir registros sin auditar (CORREGIDO != 1)", value=False)

    # Calcular rango[span_8](start_span)[span_8](end_span)
    meses_meta = meses_anteriores(datetime(fecha_hasta.year, fecha_hasta.month, 1), meses_ventana)
    fecha_inicio = meses_meta[0]['fecha_inicio']
    fecha_fin = meses_meta[-1]['fecha_fin']

    # --- Cargar datos ---
    with st.spinner(f"Consultando BD desde {fecha_inicio.date()} hasta {fecha_fin.date()}..."):
        insumos, gastos = cargar_rango_cache(fecha_inicio, fecha_fin)

    if (insumos is None or insumos.empty) and (gastos is None or gastos.empty):
        st.warning("No se encontraron registros en el rango seleccionado.")
        return

    # --- Preparar y normalizar[span_9](start_span)[span_9](end_span) ---
    df_unido = preparar_datos_rango(insumos, gastos, meses_meta)
    if df_unido.empty:
        st.warning("No hay datos después de la normalización.")
        return

    # Filtro de registros auditados
    if solo_corregidos and COL_CORREGIDO in df_unido.columns:
        df_unido = df_unido[df_unido[COL_CORREGIDO].astype(str).isin(['1', 'True', 'true', '1.0'])]

    # Filtro por Marca en Sidebar
    marcas = sorted([str(m) for m in df_unido[COL_MARCA].dropna().unique() if str(m) != '<NA>'])
    marca_sel = st.sidebar.selectbox("Filtrar por Marca", ["TODAS"] + marcas)
    if marca_sel != "TODAS":
        df_unido = df_unido[df_unido[COL_MARCA] == marca_sel]

    # ---------------------------------------------------------
    # 📑 PESTAÑAS DE VISUALIZACIÓN
    # ---------------------------------------------------------
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 Resumen y Evolución", 
        "🥇 Análisis Pareto (ABC)", 
        "📈 Costos Unitarios", 
        "🕵️ Auditoría Operativa",
        "🔍 Detalle por Proveedor"
    ])

    # --- TAB 1: Resumen General y Evolución ---
    with tab1:
        st.subheader("Resumen: Totales por Proveedor (por mes)")
        pivot = df_unido.pivot_table(index=COL_PROVEEDOR, columns=COL_ANIO_MES, values=COL_TOTAL, aggfunc='sum', fill_value=0)
        columnas_orden = [m['etiqueta'] for m in meses_meta if m['etiqueta'] in pivot.columns]
        pivot = pivot.reindex(columns=columnas_orden).fillna(0.0)
        pivot['TOTAL_PERIODO'] = pivot.sum(axis=1)
        pivot = pivot.sort_values('TOTAL_PERIODO', ascending=False)

        # Calcular Variación Mensual (MoM %) si hay más de 1 mes
        if len(columnas_orden) >= 2:
            mes_act = columnas_orden[-1]
            mes_ant = columnas_orden[-2]
            pivot['Var. Mes Actual %'] = ((pivot[mes_act] - pivot[mes_ant]) / pivot[mes_ant].replace(0, pd.NA))

        st.dataframe(pivot.style.format(
            "{:.1%}", subset=['Var. Mes Actual %'] if 'Var. Mes Actual %' in pivot.columns else []
        ).format(
            "${:,.2f}", subset=columnas_orden + ['TOTAL_PERIODO']
        ), use_container_width=True)

    # --- TAB 2: Clasificación ABC (Pareto)[span_10](start_span)[span_10](end_span) ---
    with tab2:
        st.subheader("Clasificación de Gasto ABC")
        st.markdown("Identifica los proveedores que representan el **80% de tu capital** (Clase A) para enfocar tus negociaciones.")
        abc = clasificacion_abc(df_unido)
        st.dataframe(abc.style.format({COL_TOTAL: "${:,.2f}", 'CUM_PCT': "{:.2%}"}), use_container_width=True, hide_index=True)

    # --- TAB 3: Costo Unitario[span_11](start_span)[span_11](end_span) ---
    with tab3:
        st.subheader("Costo Unitario Promedio por Mes")
        st.info("Útil para rastrear la inflación y detectar incrementos de precios injustificados.")
        pivot_cost_unit = costo_unitario_mensual(df_unido)
        if not pivot_cost_unit.empty:
            st.dataframe(pivot_cost_unit.style.format("${:,.2f}"), use_container_width=True)
        else:
            st.info("No hay datos de costo unitario o unidades registradas.")

    # --- TAB 4: Auditoría y Concentración[span_12](start_span)[span_12](end_span) ---
    with tab4:
        st.subheader("Auditoría de Recurrencia Programada vs Real")
        if COL_RECURRENCIA in df_unido.columns:
            # Filtramos solo los que tienen recurrencia asignada
            df_rec = df_unido[df_unido[COL_RECURRENCIA].notna() & (df_unido[COL_RECURRENCIA] != "")]
            if not df_rec.empty:
                audit = auditoria_recurrencia(df_rec)
                st.dataframe(audit, use_container_width=True, hide_index=True)
            else:
                st.info("Ningún registro analizado tiene el campo 'RECURRENCIA' lleno.")
        
        st.divider()
        st.subheader("Concentración de Presupuesto")
        colA, colB = st.columns(2)
        por_cat, por_fp = concentracion_categoria_forma_pago(df_unido)
        with colA:
            st.markdown("**Por Categoría Operativa**")
            st.dataframe(por_cat.style.format({COL_TOTAL: "${:,.2f}", 'PCT': "{:.1%}"}), use_container_width=True, hide_index=True)
        with colB:
            st.markdown("**Por Forma de Pago**")
            st.dataframe(por_fp.style.format({COL_TOTAL: "${:,.2f}", 'PCT': "{:.1%}"}), use_container_width=True, hide_index=True)

    # --- TAB 5: Lupa de Movimientos[span_13](start_span)[span_13](end_span) ---
    with tab5:
        st.subheader("Detalle Analítico por Proveedor")
        proveedores = sorted([str(p) for p in df_unido[COL_PROVEEDOR].dropna().unique()])
        prov_sel = st.selectbox("Selecciona un proveedor:", ["(Ninguno)"] + proveedores)
        
        if prov_sel and prov_sel != "(Ninguno)":
            detalle = df_unido[df_unido[COL_PROVEEDOR] == prov_sel].copy()
            if detalle[COL_FECHA].notna().any():
                detalle = detalle.sort_values(COL_FECHA, ascending=False)
            else:
                detalle = detalle.sort_values(COL_ANIO_MES_ISO, ascending=False)
                
            cols_show = [COL_FECHA, COL_ANIO_MES, COL_ORIGEN, COL_CONCEPTO, COL_CATEGORIA, COL_RECURRENCIA, COL_FORMA_PAGO, COL_TOTAL]
            cols_show = [c for c in cols_show if c in detalle.columns]
            
            st.dataframe(detalle[cols_show].style.format({COL_TOTAL: "${:,.2f}"}), use_container_width=True, hide_index=True)

if __name__ == "__main__":
    mostrar_pestana_recurrencia()
