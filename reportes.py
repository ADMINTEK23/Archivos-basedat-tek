import logging
from datetime import datetime, date, timedelta
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
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MESES_LISTA = ['ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN', 'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC']
MESES_NUM = {m: i + 1 for i, m in enumerate(MESES_LISTA)}

# TTLs de cache
TTL_VENTAS_HIST = 60 * 60 * 8     # 8 horas
TTL_AUDITORIA = 60 * 30           # 30 minutos

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
        st.error("❌ Error al obtener ventas históricas resumidas. Revisa logs del servidor.")
        return pd.DataFrame()


@st.cache_data(ttl=TTL_AUDITORIA)
def obtener_metricas_auditoria_usuarios(fecha_inicio: date, fecha_fin: date) -> pd.DataFrame:
    """Consulta consolidada de auditoría entre fechas. Se ajusta fin a medianoche exclusiva."""
    if fecha_fin < fecha_inicio:
        raise ValueError("fecha_fin debe ser >= fecha_inicio")

    fecha_fin_exclusiva = fecha_fin + timedelta(days=1)

    query = text("""
        SELECT 'Ventas' AS modulo, "USUARIO", "FECHA_CAPTURA", "TOTAL" 
        FROM ventas 
        WHERE "FECHA_CAPTURA" >= :inicio AND "FECHA_CAPTURA" < :fin_exclusiva
        UNION ALL
        SELECT 'Gastos' AS modulo, "USUARIO", "FECHA_CAPTURA", "TOTAL" 
        FROM gastos 
        WHERE "FECHA_CAPTURA" >= :inicio AND "FECHA_CAPTURA" < :fin_exclusiva
        UNION ALL
        SELECT 'Insumos' AS modulo, "USUARIO", "FECHA_CAPTURA", "TOTAL" 
        FROM insumos 
        WHERE "FECHA_CAPTURA" >= :inicio AND "FECHA_CAPTURA" < :fin_exclusiva
    """)

    try:
        with ENGINE_GLOBAL.connect() as conn:
            df = pd.read_sql(query, conn, params={"inicio": fecha_inicio, "fin_exclusiva": fecha_fin_exclusiva})

        if df.empty:
            return df

        # Normalizaciones y optimizaciones de memoria
        df['FECHA_CAPTURA'] = pd.to_datetime(df['FECHA_CAPTURA'], errors='coerce')
        df['USUARIO'] = df['USUARIO'].astype(str).str.strip().str.upper().astype('category')
        df['modulo'] = df['modulo'].astype('category')
        df['TOTAL'] = pd.to_numeric(df['TOTAL'], errors='coerce').fillna(0).astype('float32')
        return df

    except Exception as e:
        logger.exception("Error en obtener_metricas_auditoria_usuarios")
        st.error("❌ Error al consultar métricas de auditoría. Revisa logs del servidor.")
        return pd.DataFrame()

# -------------------------
# Utilidades y helpers
# -------------------------
def generar_periodos_disponibles(desde_anio: int = 2023) -> List[str]:
    """Genera lista descendente de periodos 'MES AAAA' desde el año actual."""
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
    """Suma segura de columna numérica en DataFrame."""
    if df is None or df.empty or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors='coerce').fillna(0).sum())


def safe_unique_sorted(df: pd.DataFrame, col: str) -> List[str]:
    """Devuelve lista ordenada de valores únicos de una columna."""
    if df is None or df.empty or col not in df.columns:
        return []
    vals = df[col].dropna().unique()
    return sorted([str(v).strip() for v in vals])


def clear_caches():
    """Invalidar cachés de funciones relevantes."""
    for fn in (cargar_datos_por_mes, obtener_ventas_historicas_resumidas, obtener_metricas_auditoria_usuarios):
        try:
            fn.clear()
        except Exception:
            logger.debug(f"No se pudo limpiar la caché de {getattr(fn, '__name__', 'función')}")

# -------------------------
# Vistas Streamlit
# -------------------------
def mostrar_pestana_reportes():
    """Renderiza la pestaña de Reportes Financieros."""
    try:
        st.sidebar.header("📅 Filtros de Reportes")

        if st.sidebar.button("🔄 Sincronizar Datos Completo", use_container_width=True, key="sync_reportes"):
            clear_caches()
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
            st.warning(f"⚠️ No se encontraron transacciones operativas para el periodo {mes_sel} - {anio_sel}.")
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

        status_utilidad = ("EXCESIVA" if u_dec > 0.25 else
                           "EXCELENTE" if u_dec > 0.18 else
                           "SALUDABLE" if u_dec > 0.12 else
                           "REGULAR" if u_dec > 0.08 else
                           "DE RIESGO")
        status_gastos = ("DE RIESGO" if g_dec > 0.55 else
                         "REGULAR" if g_dec > 0.50 else
                         "SALUDABLE" if g_dec > 0.47 else
                         "EXCELENTE")
        status_insumos = ("DE RIESGO" if i_dec > 0.41 else
                          "REGULAR" if i_dec > 0.38 else
                          "SALUDABLE" if i_dec > 0.35 else
                          "EXCELENTE")

        # --- CAPA VISUAL SUPERIOR ---
        col_graf1, col_texto, col_graf2 = st.columns([1.5, 1.1, 1.5])

        with col_graf1:
            st.markdown("<h3 style='text-align: center;'>UTILIDAD NETA</h3>", unsafe_allow_html=True)
            df_pie = pd.DataFrame({
                'Concepto': ['Utilidad', 'Gastos', 'Insumos'],
                'Monto': [max(0, utilidad_neta), total_gastos, total_insumos]
            })
            fig_pie = px.pie(
                df_pie, values='Monto', names='Concepto', color='Concepto',
                color_discrete_map={'Utilidad': '#7030A0', 'Gastos': '#70AD47', 'Insumos': '#FFC000'}
            )
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

            df_barras = pd.DataFrame({
                'Métrica': ['CONSOLIDADA', 'BOKOBA', 'SOTUTA', 'INSUMOS', 'GASTOS', 'UTILIDAD'],
                'Monto': [total_ventas, v_bokoba, v_sotuta, total_insumos, total_gastos, utilidad_neta]
            })
            fig_bar = px.bar(
                df_barras, x='Monto', y='Métrica', orientation='h', text='Monto', color='Métrica',
                color_discrete_map={'CONSOLIDADA': '#3B75AF', 'BOKOBA': '#A5C8E1', 'SOTUTA': '#4F94CD', 'INSUMOS': '#FFC000', 'GASTOS': '#70AD47', 'UTILIDAD': '#7030A0'}
            )
            fig_bar.update_traces(texttemplate='%{x:,.2f}')
            fig_bar.update_layout(
                showlegend=False,
                yaxis={'categoryorder': 'array', 'categoryarray': ['UTILIDAD', 'GASTOS', 'INSUMOS', 'SOTUTA', 'BOKOBA', 'CONSOLIDADA'], 'title': None},
                xaxis={'title': None, 'showticklabels': False}
            )
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


def mostrar_pestana_auditoria_usuarios():
    """Renderiza la pestaña de Auditoría de Usuarios con validaciones ampliadas y modo debug."""
    st.sidebar.header("👤 Filtros de Auditoría")

    # Debug toggle (no mostrado por defecto)
    debug_mode = st.sidebar.checkbox("Mostrar debug (df_logs)", value=False)

    # Rango de fechas por defecto: últimos 30 días
    hoy = date.today()
    hace_un_mes = hoy - timedelta(days=30)

    fechas_sel = st.sidebar.date_input(
        "Rango de Fechas",
        value=(hace_un_mes, hoy),
        max_value=hoy
    )

    # Validar selección de fechas
    if not (isinstance(fechas_sel, tuple) and len(fechas_sel) == 2):
        st.info("Selecciona la fecha inicial y final en el menú lateral.")
        return
    f_inicio, f_fin = fechas_sel
    if f_fin < f_inicio:
        st.error("La fecha final debe ser mayor o igual a la inicial.")
        return

    # Botón para invalidar cache y recargar
    if st.sidebar.button("🔄 Sincronizar Logs", use_container_width=True, key="sync_auditoria"):
        try:
            obtener_metricas_auditoria_usuarios.clear()
        except Exception:
            logger.debug("No se pudo limpiar cache de obtener_metricas_auditoria_usuarios")
        st.success("¡Logs actualizados!")
        st.rerun()

    # Obtener datos desde la capa de datos (función cacheada)
    df_logs = obtener_metricas_auditoria_usuarios(f_inicio, f_fin)

    # Debug visual opcional
    if debug_mode:
        st.write("DEBUG: df_logs (raw)", None if df_logs is None else df_logs.head(20))
        st.write("DEBUG: df_logs shape", None if df_logs is None else df_logs.shape)
        st.write("DEBUG: df_logs columns", None if df_logs is None else df_logs.columns.tolist())
        st.write("DEBUG: df_logs dtypes", None if df_logs is None else df_logs.dtypes.to_dict())

    # Validaciones básicas
    if df_logs is None or df_logs.empty:
        st.warning(f"No se encontraron registros de captura entre {f_inicio} y {f_fin}.")
        return

    # Asegurar FECHA_CAPTURA como datetime
    if 'FECHA_CAPTURA' not in df_logs.columns:
        st.error("La columna FECHA_CAPTURA no está presente en los datos. Revisa la consulta SQL.")
        return
    df_logs['FECHA_CAPTURA'] = pd.to_datetime(df_logs['FECHA_CAPTURA'], errors='coerce')
    if debug_mode:
        st.write("DEBUG: FECHA_CAPTURA nulls:", int(df_logs['FECHA_CAPTURA'].isna().sum()))

    # Asegurar USUARIO presente y normalizado
    if 'USUARIO' not in df_logs.columns:
        st.error("La columna USUARIO no está presente en los datos. Revisa la consulta SQL.")
        return
    df_logs['USUARIO'] = df_logs['USUARIO'].astype(str).str.strip().str.upper()

    # Asegurar modulo y TOTAL si existen, pero no fallar si faltan
    if 'modulo' not in df_logs.columns:
        df_logs['modulo'] = 'Desconocido'
    if 'TOTAL' not in df_logs.columns:
        df_logs['TOTAL'] = 0.0
    df_logs['TOTAL'] = pd.to_numeric(df_logs['TOTAL'], errors='coerce').fillna(0.0)

    # Construir lista de usuarios de forma segura (compatible con pandas moderno)
    if isinstance(df_logs['USUARIO'].dtype, pd.CategoricalDtype):
        usuarios_cat = list(df_logs['USUARIO'].cat.categories)
    else:
        usuarios_cat = sorted(df_logs['USUARIO'].unique())

    lista_usuarios = ["Todos"] + usuarios_cat
    usuario_sel = st.sidebar.selectbox("Selecciona Usuario", lista_usuarios, index=0)

    # Filtrar por usuario si aplica
    if usuario_sel and usuario_sel != "Todos":
        before_count = len(df_logs)
        df_logs = df_logs[df_logs['USUARIO'] == usuario_sel]
        after_count = len(df_logs)
        if debug_mode:
            st.write(f"DEBUG: Filtrado por usuario {usuario_sel}: {before_count} -> {after_count} filas")

    # Ordenar y calcular diferencias entre capturas por usuario
    df_logs = df_logs.sort_values(['USUARIO', 'FECHA_CAPTURA'])
    df_logs['diferencia_minutos'] = df_logs.groupby('USUARIO', observed=False)['FECHA_CAPTURA'].diff().dt.total_seconds() / 60.0

    # Capturas continuas (<= 30 minutos)
    capturas_continuas = df_logs[df_logs['diferencia_minutos'] <= 30]
    tiempo_promedio_captura = capturas_continuas['diferencia_minutos'].mean()
    tiempo_prom_str = f"{tiempo_promedio_captura:.1f} min" if pd.notnull(tiempo_promedio_captura) else "N/A"

    # Jornada diaria por usuario
    df_logs['FECHA_DIA'] = df_logs['FECHA_CAPTURA'].dt.date
    jornadas = df_logs.groupby(['USUARIO', 'FECHA_DIA'], observed=False)['FECHA_CAPTURA'].agg(['min', 'max'])
    jornadas['horas_activas'] = (jornadas['max'] - jornadas['min']).dt.total_seconds() / 3600.0
    promedio_horas_dia = jornadas[jornadas['horas_activas'] > 0]['horas_activas'].mean()

    # Tarjetas de métricas clave
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Capturas", f"{len(df_logs):,}")
    with col2:
        st.metric("Monto Procesado", f"${df_logs['TOTAL'].sum():,.2f}")
    with col3:
        st.metric("Prom. entre Capturas", tiempo_prom_str)
    with col4:
        st.metric("Horas Activas / Día", f"{promedio_horas_dia:.1f} hrs" if pd.notnull(promedio_horas_dia) else "N/A")

    st.markdown("---")

    # Gráficos analíticos
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        st.markdown("<h4 style='text-align: center;'>Capturas por Módulo</h4>", unsafe_allow_html=True)
        df_modulo = df_logs.groupby('modulo', observed=False)['TOTAL'].sum().reset_index()
        if not df_modulo.empty:
            fig_modulo = px.pie(df_modulo, names='modulo', values='TOTAL', hole=0.4, color_discrete_sequence=px.colors.qualitative.Set2)
            fig_modulo.update_layout(height=280, margin=dict(t=20, b=20, l=10, r=10))
            st.plotly_chart(fig_modulo, use_container_width=True)
        else:
            st.info("No hay datos por módulo para graficar.")

    with col_g2:
        st.markdown("<h4 style='text-align: center;'>Actividad Diaria (Volumen)</h4>", unsafe_allow_html=True)
        df_tendencia = df_logs.groupby(['FECHA_DIA', 'modulo'], observed=False).size().reset_index(name='Cantidad')
        if not df_tendencia.empty:
            fig_linea = px.line(df_tendencia, x='FECHA_DIA', y='Cantidad', color='modulo', markers=True)
            fig_linea.update_layout(height=280, margin=dict(t=20, b=20, l=10, r=10), xaxis_title=None)
            st.plotly_chart(fig_linea, use_container_width=True)
        else:
            st.info("No hay actividad diaria para graficar.")

    # Tabla detallada (limitada a 100 filas)
    st.markdown("#### Últimos Movimientos Registrados")
    cols_to_show = ['FECHA_CAPTURA', 'USUARIO', 'modulo', 'TOTAL']
    cols_present = [c for c in cols_to_show if c in df_logs.columns]
    if cols_present:
        st.dataframe(
            df_logs[cols_present].sort_values('FECHA_CAPTURA', ascending=False).head(100),
            use_container_width=True
        )
    else:
        st.info("No hay columnas disponibles para mostrar en la tabla.")

    # Si debug activo, mostrar resumen final
    if debug_mode:
        st.write("DEBUG: resumen jornadas (primeras filas)", jornadas.reset_index().head(10))
        st.write("DEBUG: conteos por modulo", df_modulo if 'df_modulo' in locals() else "no existe")

# -------------------------
# Main
# -------------------------
def main():
    st.set_page_config(page_title="Dashboard Administrativo", layout="wide")
    st.sidebar.title("Navegación")
    menu = st.sidebar.radio("Ir a:", ["Reportes Financieros", "Auditoría de Usuarios"])
    st.sidebar.markdown("---")

    if menu == "Reportes Financieros":
        st.title("📊 Dashboard de Reportes Financieros")
        mostrar_pestana_reportes()
    else:
        st.title("👥 Panel de Auditoría y Actividad")
        mostrar_pestana_auditoria_usuarios()

if __name__ == "__main__":
    main()
