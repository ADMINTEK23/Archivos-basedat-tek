import logging
from typing import Dict, Any, Optional
import pandas as pd
from sqlalchemy import text
from db_utils import ENGINE_GLOBAL
import streamlit as st
from datetime import date, datetime

logger = logging.getLogger(__name__)

# --- CSS PERSONALIZADO AVANZADO ---
st.markdown(
    """
    <style>
    .block-container { padding-left: 0.5rem !important; padding-right: 0.5rem !important; max-width: 99% !important; }
    .stDataFrame, .stDataEditor { width: 100% !important; }
    [data-testid="stDataFrame"] > div { width: 100% !important; max-width: 100% !important; }
    [data-testid="stDataFrame"] [data-testid="stTable"] { width: 100% !important; table-layout: fixed !important; }
    .stColumn { padding: 0 0.25rem !important; }
    </style>
    """,
    unsafe_allow_html=True
)

# Mapeo seguro de filtros a expresiones SQL
FILTRO_COLUMNAS = {
    "Fecha Captura": 'CAST("FECHA_CAPTURA" AS date)',
    "Fecha Sistema": 'CAST("FECHA" AS date)'
}

# ==========================================
# FUNCIONES AUXILIARES
# ==========================================
@st.cache_data(ttl=300, show_spinner=False)
def obtener_marcas_activas() -> list:
    """Obtiene dinámicamente las marcas existentes en la base de datos."""
    query = text('''
        SELECT DISTINCT "MARCA" FROM ventas WHERE "MARCA" IS NOT NULL
        UNION 
        SELECT DISTINCT "MARCA" FROM insumos WHERE "MARCA" IS NOT NULL
        UNION 
        SELECT DISTINCT "MARCA" FROM gastos WHERE "MARCA" IS NOT NULL
    ''')
    try:
        with ENGINE_GLOBAL.connect() as conn:
            resultados = conn.execute(query).fetchall()
            marcas = sorted([str(r[0]).strip().upper() for r in resultados if str(r[0]).strip()])
            return ["TODAS LAS MARCAS"] + marcas
    except Exception:
        logger.exception("Error al obtener marcas")
        return ["TODAS LAS MARCAS"]

# ==========================================
# LÓGICA DE BACKEND (SERVER-SIDE)
# ==========================================

@st.cache_data(ttl=300, show_spinner=False)
def obtener_resumen_usuario_rango_cached(usuario: str, fecha_inicio: date, fecha_fin: date, tipo_filtro: str, marca_filtro: str) -> Dict[str, Any]:
    usuario_u = str(usuario).strip().upper()
    if tipo_filtro not in FILTRO_COLUMNAS:
        raise ValueError("Filtro no válido")
    
    col_date_expr = FILTRO_COLUMNAS[tipo_filtro]
    
    # Lógica dinámica para el filtro de marca
    condicion_marca = ""
    params = {"u": usuario_u, "f_ini": fecha_inicio, "f_fin": fecha_fin}
    
    if marca_filtro != "TODAS LAS MARCAS":
        condicion_marca = " AND UPPER(marca_campo) = :m "
        params["m"] = marca_filtro

    q_totales = text(f"""
        SELECT 
            concepto, 
            COALESCE(forma_pago, 'NO APLICA') as forma_pago,
            COALESCE(SUM(total), 0) AS total, 
            COUNT(*) AS cnt 
        FROM (
            SELECT 'ventas' AS concepto, "TOTAL"::numeric AS total, NULL as forma_pago, {col_date_expr} AS fecha, "USUARIO", "MARCA" AS marca_campo FROM ventas
            UNION ALL
            SELECT 'insumos' AS concepto, "TOTAL"::numeric AS total, UPPER("FORMA PAGO") as forma_pago, {col_date_expr} AS fecha, "USUARIO", "MARCA" AS marca_campo FROM insumos
            UNION ALL
            SELECT 'gastos' AS concepto, "TOTAL"::numeric AS total, UPPER("FORMA PAGO") as forma_pago, {col_date_expr} AS fecha, "USUARIO", "MARCA" AS marca_campo FROM gastos
        ) t
        WHERE "USUARIO" = :u AND fecha BETWEEN :f_ini AND :f_fin {condicion_marca}
        GROUP BY concepto, forma_pago
    """)

    try:
        with ENGINE_GLOBAL.connect() as conn:
            rows = conn.execute(q_totales, params).fetchall()
            
            totales = {"ventas": 0.0, "insumos": 0.0, "gastos": 0.0}
            counts = {"ventas": 0, "insumos": 0, "gastos": 0}
            desglose_pagos = {"insumos": {}, "gastos": {}}
            
            for r in rows:
                concepto, forma_pago, monto, conteo = r[0], r[1], float(r[2]), int(r[3])
                totales[concepto] += monto
                counts[concepto] += conteo
                if concepto in desglose_pagos and forma_pago != 'NO APLICA':
                    desglose_pagos[concepto][forma_pago] = monto

            detalles_meta = {
                "ventas": {"cols": ["Número de Venta", "FECHA", "Producto", "Cantidad", "TOTAL", "SUCURSAL", "MARCA"]},
                "insumos": {"cols": ["FECHA", "INSUMO", "TIPO", "PROVEEDOR", "FORMA PAGO", "UNIDAD", "COSTO", "TOTAL", "MARCA"]}, 
                "gastos": {"cols": ["FECHA", "GASTO DE", "TIPO", "CATEGORÍA", "PROVEEDOR", "FORMA PAGO", "UNIDAD", "COSTO", "TOTAL", "RECURRENCIA", "MARCA"]} 
            }

        return {"totales": totales, "counts": counts, "desglose_pagos": desglose_pagos, "detalles_meta": detalles_meta}
    except Exception:
        logger.exception("Error en BD al obtener resumen")
        return {"totales": {}, "counts": {}, "desglose_pagos": {}, "detalles_meta": {}}

def fetch_page(table: str, select_cols: list, usuario: str, col_date_expr: str, fecha_inicio: date, fecha_fin: date, marca_filtro: str, limit: int = 50, offset: int = 0) -> pd.DataFrame:
    if table not in {"ventas", "insumos", "gastos"}:
        raise ValueError("Tabla no permitida")

    condicion_marca = ""
    params = {"u": str(usuario).strip().upper(), "f_ini": fecha_inicio, "f_fin": fecha_fin, "limit": limit, "offset": offset}
    
    if marca_filtro != "TODAS LAS MARCAS":
        condicion_marca = ' AND UPPER("MARCA") = :m '
        params["m"] = marca_filtro

    cols_sql = ", ".join([f'"{c}"' for c in select_cols])
    q = text(f"""
        SELECT {cols_sql}
        FROM {table}
        WHERE "USUARIO" = :u AND {col_date_expr} BETWEEN :f_ini AND :f_fin {condicion_marca}
        ORDER BY "FECHA" DESC
        LIMIT :limit OFFSET :offset
    """)
    
    try:
        with ENGINE_GLOBAL.connect() as conn:
            df = pd.read_sql(q, conn, params=params)
        if not df.empty:
            df.columns = df.columns.str.upper()
            if "FECHA" in df.columns:
                df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce").dt.date
            if "MARCA" in df.columns:
                df["MARCA"] = df["MARCA"].astype(str).str.strip().str.upper().replace({"": None, "NAN": None})
        return df
    except Exception:
        logger.exception(f"Error en fetch_page para {table}")
        return pd.DataFrame(columns=[c.upper() for c in select_cols])


# ==========================================
# LÓGICA DE FRONTEND (STREAMLIT UI)
# ==========================================

@st.fragment
def renderizar_tabla_paginada(nombre_tabla: str, counts: dict, meta: dict, usuario_conectado: str, col_date_expr: str, fecha_inicio: date, fecha_fin: date, marca_filtro: str):
    total_registros = counts.get(nombre_tabla, 0)
    
    if total_registros == 0:
        st.caption(f"No se encontraron registros de {nombre_tabla.upper()} para este periodo y marca.")
        return

    PAGE_SIZE = 50
    estado_key = f"pagina_{nombre_tabla}"
    st.session_state.setdefault(estado_key, 0)
    
    pagina_actual = st.session_state[estado_key]
    total_paginas = (total_registros // PAGE_SIZE) + (1 if total_registros % PAGE_SIZE > 0 else 0)
    
    col_izq, col_centro, col_der = st.columns([1, 2, 1])
    
    with col_izq:
        if st.button("⬅️ Anterior", key=f"prev_{nombre_tabla}", disabled=(pagina_actual <= 0)):
            st.session_state[estado_key] -= 1
            st.rerun()
            
    with col_centro:
        st.markdown(f"<div style='text-align: center; padding-top: 5px;'>Página <b>{pagina_actual + 1}</b> de {total_paginas} (Total: {total_registros} regs)</div>", unsafe_allow_html=True)
        
    with col_der:
        if st.button("Siguiente ➡️", key=f"next_{nombre_tabla}", disabled=((pagina_actual + 1) >= total_paginas)):
            st.session_state[estado_key] += 1
            st.rerun()

    offset_actual = pagina_actual * PAGE_SIZE
    columnas_sql = meta[nombre_tabla]["cols"]
    
    df_pagina = fetch_page(
        table=nombre_tabla,
        select_cols=columnas_sql,
        usuario=usuario_conectado,
        col_date_expr=col_date_expr,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        marca_filtro=marca_filtro,
        limit=PAGE_SIZE,
        offset=offset_actual
    )
    
    st.dataframe(
        df_pagina,
        use_container_width=True,
        hide_index=True,
        column_config={"FECHA": st.column_config.DateColumn("FECHA", format="YYYY-MM-DD", width="small")}
    )

def mostrar_pestana_resumen():
    st.header("📋 Resumen Diario de Capturas")
    
    usuario_conectado = str(st.session_state.get("usuario_actual", "ANÓNIMO")).strip().upper()
    st.info(f"👤 Mostrando actividad del usuario: **{usuario_conectado}**")
    
    # ZONA DE FILTROS (Ahora a 3 columnas)
    col_filtro1, col_filtro2, col_filtro3 = st.columns(3)
    
    with col_filtro1:
        tipo_filtro_sel = st.selectbox("1. Criterio de búsqueda:", ["Fecha Captura", "Fecha Sistema"])
        
    with col_filtro2:
        fecha_actual = datetime.now().date()
        fechas_seleccionadas = st.date_input("2. Selecciona Rango de Fechas:", value=(fecha_actual, fecha_actual))
        
    with col_filtro3:
        lista_marcas_db = obtener_marcas_activas()
        marca_sel = st.selectbox("3. Selecciona Marca:", lista_marcas_db)
        
    if isinstance(fechas_seleccionadas, tuple) and len(fechas_seleccionadas) == 2:
        fecha_inicio, fecha_fin = fechas_seleccionadas
    else:
        fecha_inicio = fecha_fin = fechas_seleccionadas[0] if isinstance(fechas_seleccionadas, tuple) else fechas_seleccionadas

    # RESET DE PAGINACIÓN AL CAMBIAR CUALQUIER FILTRO (Incluyendo Marca)
    firma_filtros = f"{fecha_inicio}_{fecha_fin}_{tipo_filtro_sel}_{marca_sel}"
    if st.session_state.get("firma_filtros_anterior") != firma_filtros:
        for t in ["ventas", "insumos", "gastos"]:
            st.session_state[f"pagina_{t}"] = 0
        st.session_state.firma_filtros_anterior = firma_filtros

    # EXTRACCIÓN DE DATOS PRINCIPAL
    data_resumen = obtener_resumen_usuario_rango_cached(usuario_conectado, fecha_inicio, fecha_fin, tipo_filtro_sel, marca_sel)
    totales, counts = data_resumen.get("totales", {}), data_resumen.get("counts", {})
    pagos, meta = data_resumen.get("desglose_pagos", {}), data_resumen.get("detalles_meta", {})
    
    st.markdown("---")

    # MÉTRICAS GENERALES
    st.subheader("Totales del Periodo")
    col_v, col_i, col_g = st.columns(3)
    
    with col_v:
        col_v.metric(label="🛒 TOTAL VENTAS INYECTADAS", value=f"${totales.get('ventas', 0):,.2f}")
    
    with col_i:
        col_i.metric(label="📦 TOTAL INSUMOS REGISTRADOS", value=f"${totales.get('insumos', 0):,.2f}")
        
    with col_g:
        col_g.metric(label="💸 TOTAL GASTOS REGISTRADOS", value=f"${totales.get('gastos', 0):,.2f}")
        
    # DESGLOSE DE VENTAS POR PRODUCTO (EXPANDER / MENÚ DESPLEGABLE)
    col_sub_v, col_sub_i, col_sub_g = st.columns(3)
    
    with col_sub_v:
        with st.container(border=True):
            with st.expander("Filtrar producto específico"):
                col_date_expr = FILTRO_COLUMNAS[tipo_filtro_sel]
                
                # Dinamismo de marca también para el buscador de productos
                condicion_marca_prod = ""
                params_prod = {"u": usuario_conectado, "f_ini": fecha_inicio, "f_fin": fecha_fin}
                if marca_sel != "TODAS LAS MARCAS":
                    condicion_marca_prod = ' AND UPPER("MARCA") = :m '
                    params_prod["m"] = marca_sel

                # Consultar lista única de productos vendidos
                q_prods = text(f"""
                    SELECT DISTINCT "Producto" 
                    FROM ventas 
                    WHERE "USUARIO" = :u AND {col_date_expr} BETWEEN :f_ini AND :f_fin {condicion_marca_prod}
                    ORDER BY "Producto" ASC
                """)
                try:
                    with ENGINE_GLOBAL.connect() as conn:
                        df_prods = pd.read_sql(q_prods, conn, params=params_prod)
                    
                    lista_productos = df_prods["Producto"].dropna().tolist() if not df_prods.empty else []
                except:
                    lista_productos = []
                
                if lista_productos:
                    prod_seleccionado = st.selectbox("Selecciona Producto:", lista_productos, key="sb_filtro_prod_ventas")
                    
                    params_prod["prod"] = prod_seleccionado
                    # Consultar métricas específicas para el producto seleccionado respetando la marca
                    q_det_prod = text(f"""
                        SELECT COALESCE(SUM("Cantidad"), 0) as total_cant, COALESCE(SUM("TOTAL"), 0) as total_monto
                        FROM ventas
                        WHERE "USUARIO" = :u AND {col_date_expr} BETWEEN :f_ini AND :f_fin AND "Producto" = :prod {condicion_marca_prod}
                    """)
                    with ENGINE_GLOBAL.connect() as conn:
                        res_prod = conn.execute(q_det_prod, params_prod).fetchone()
                    
                    cant_prod = float(res_prod[0]) if res_prod else 0.0
                    monto_prod = float(res_prod[1]) if res_prod else 0.0
                    
                    st.markdown(f"📦 Cantidad total: **{cant_prod:,.2f}**")
                    st.markdown(f"💵 Total general: **${monto_prod:,.2f}**")
                else:
                    st.caption("No hay productos registrados en este rango/marca.")
    
    for col, categoria, icono in zip([col_sub_i, col_sub_g], ["insumos", "gastos"], ["📦", "💸"]):
        with col:
            with st.container(border=True):
                st.markdown(f"**Desglose de {categoria.capitalize()}:**")
                st.caption(f"💵 Efectivo: **${pagos.get(categoria, {}).get('EFECTIVO', 0.0):,.2f}**")
                st.caption(f"💳 Tarjeta: **${pagos.get(categoria, {}).get('TARJETA', 0.0):,.2f}**")
                st.caption(f"🏦 Transferencia: **${pagos.get(categoria, {}).get('TRANSFERENCIA', 0.0):,.2f}**")

    st.markdown("---")

    # VISTA DETALLADA
    st.subheader("Desglose Capturado")
    tab_v, tab_i, tab_g = st.tabs(["VENTAS", "INSUMOS", "GASTOS"])
    col_date_expr = FILTRO_COLUMNAS[tipo_filtro_sel]

    if "ventas" in meta:
        with tab_v: renderizar_tabla_paginada("ventas", counts, meta, usuario_conectado, col_date_expr, fecha_inicio, fecha_fin, marca_sel)
    if "insumos" in meta:
        with tab_i: renderizar_tabla_paginada("insumos", counts, meta, usuario_conectado, col_date_expr, fecha_inicio, fecha_fin, marca_sel)
    if "gastos" in meta:
        with tab_g: renderizar_tabla_paginada("gastos", counts, meta, usuario_conectado, col_date_expr, fecha_inicio, fecha_fin, marca_sel)
