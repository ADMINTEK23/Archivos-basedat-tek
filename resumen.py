import logging
import re
from typing import Dict, Any, List, Tuple, Optional
from decimal import Decimal, InvalidOperation
from datetime import date, datetime
import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from db_utils import ENGINE_GLOBAL
import streamlit as st

logger = logging.getLogger(__name__)
st.set_page_config(layout="wide")

# --- CSS compacto ---
st.markdown(
    """<style>.block-container{padding-left:.5rem!important;padding-right:.5rem!important;max-width:99%!important}.stDataFrame,.stDataEditor{width:100%!important}[data-testid="stDataFrame"]>div{width:100%!important;max-width:100%!important}[data-testid="stDataFrame"] [data-testid="stTable"]{width:100%!important;table-layout:fixed!important}.stColumn{padding:0 .25rem!important}</style>""",
    unsafe_allow_html=True
)

# --- Configuración y listas blancas ---
FILTRO_COLUMNAS = {
    "Fecha Captura": 'CAST("FECHA_CAPTURA" AS date)',
    "Fecha Sistema": 'CAST("FECHA" AS date)'
}
TABLAS_PERMITIDAS = {"ventas", "insumos", "gastos"}
META_COLUMNAS = {
    "ventas": ["id", "Número de Venta", "FECHA", "Producto", "Cantidad", "TOTAL", "SUCURSAL", "MARCA"],
    "insumos": ["id", "FECHA", "INSUMO", "TIPO", "PROVEEDOR", "FORMA PAGO", "UNIDAD", "COSTO", "TOTAL", "MARCA", "CORREGIDO"],
    "gastos": ["id", "FECHA", "GASTO DE", "TIPO", "CATEGORÍA", "PROVEEDOR", "FORMA PAGO", "UNIDAD", "COSTO", "TOTAL", "RECURRENCIA", "MARCA", "CORREGIDO"]
}
_VALID_COL_RE = re.compile(r'^[A-Z0-9 _]+$')

# --- Helpers de validación y normalización ---
def validar_tabla(nombre_tabla: str) -> str:
    nt = str(nombre_tabla).strip().lower()
    if nt not in TABLAS_PERMITIDAS:
        raise ValueError("Tabla no permitida")
    return nt

def validar_nombre_col(col: str) -> str:
    cu = str(col).strip().upper()
    if not _VALID_COL_RE.match(cu):
        raise ValueError(f"Nombre de columna inválido: {col}")
    return cu

def columnas_permitidas_para_tabla(nombre_tabla: str) -> List[str]:
    return [c.upper() for c in META_COLUMNAS.get(validar_tabla(nombre_tabla), [])]

def _normalize_value(col: str, val: Any) -> Any:
    if val is None:
        return None
    cu = col.upper()
    try:
        if cu in {"COSTO", "TOTAL", "UNIDAD", "CANTIDAD"}:
            # Mantener Decimal para precisión
            return Decimal(str(val))
        if cu == "FECHA":
            if isinstance(val, (date, datetime)):
                return val.date() if isinstance(val, datetime) else val
            return pd.to_datetime(val, errors="raise").date()
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Valor inválido para {col}: {val}") from e
    return val

# --- Cache utilities ---
@st.cache_data(ttl=300, show_spinner=False)
def obtener_marcas_activas() -> List[str]:
    q = text(
        '''SELECT DISTINCT UPPER(TRIM("MARCA")) AS m FROM ventas WHERE "MARCA" IS NOT NULL
           UNION SELECT DISTINCT UPPER(TRIM("MARCA")) FROM insumos WHERE "MARCA" IS NOT NULL
           UNION SELECT DISTINCT UPPER(TRIM("MARCA")) FROM gastos WHERE "MARCA" IS NOT NULL'''
    )
    try:
        with ENGINE_GLOBAL.connect() as conn:
            res = conn.execute(q).fetchall()
            marcas = sorted([r[0] for r in res if r[0]])
            return ["TODAS LAS MARCAS"] + marcas
    except SQLAlchemyError:
        logger.exception("Error al obtener marcas")
        return ["TODAS LAS MARCAS"]

@st.cache_data(ttl=300, show_spinner=False)
def obtener_recurrencias_activas() -> List[str]:
    q = text('SELECT DISTINCT UPPER(TRIM("RECURRENCIA")) FROM gastos WHERE "RECURRENCIA" IS NOT NULL')
    try:
        with ENGINE_GLOBAL.connect() as conn:
            res = conn.execute(q).fetchall()
            return sorted([r[0] for r in res if r[0]])
    except SQLAlchemyError:
        logger.exception("Error al obtener recurrencias")
        return []

@st.cache_data(ttl=300, show_spinner=False)
def obtener_usuarios_activos() -> List[str]:
    q = text('''
        SELECT DISTINCT UPPER(TRIM("USUARIO")) FROM ventas WHERE "USUARIO" IS NOT NULL
        UNION SELECT DISTINCT UPPER(TRIM("USUARIO")) FROM insumos WHERE "USUARIO" IS NOT NULL
        UNION SELECT DISTINCT UPPER(TRIM("USUARIO")) FROM gastos WHERE "USUARIO" IS NOT NULL
    ''')
    try:
        with ENGINE_GLOBAL.connect() as conn:
            res = conn.execute(q).fetchall()
            return sorted([r[0] for r in res if r[0]])
    except SQLAlchemyError:
        logger.exception("Error al obtener usuarios")
        return []

@st.cache_data(ttl=300, show_spinner=False)
def obtener_ventas_por_sucursal(usuario: str, fecha_inicio: date, fecha_fin: date, tipo_filtro: str, marca_filtro: str) -> Dict[str, float]:
    if tipo_filtro not in FILTRO_COLUMNAS:
        return {}
    
    col_date_expr = FILTRO_COLUMNAS[tipo_filtro]
    usuario_u = str(usuario).strip().upper()
    marca_cond = ' AND UPPER("MARCA") = :m ' if marca_filtro != "TODAS LAS MARCAS" else ""
    usuario_cond = ' AND "USUARIO" = :u ' if usuario_u != "TODOS" else ""
    
    params = {"f_ini": fecha_inicio, "f_fin": fecha_fin}
    if usuario_u != "TODOS":
        params["u"] = usuario_u
    if marca_filtro != "TODAS LAS MARCAS":
        params["m"] = marca_filtro

    q = text(f'''
        SELECT COALESCE("SUCURSAL", 'SIN SUCURSAL'), COALESCE(SUM("TOTAL"), 0)
        FROM ventas
        WHERE {col_date_expr} BETWEEN :f_ini AND :f_fin {usuario_cond} {marca_cond}
        GROUP BY "SUCURSAL"
        ORDER BY "SUCURSAL" ASC
    ''')
    
    try:
        with ENGINE_GLOBAL.connect() as conn:
            rows = conn.execute(q, params).fetchall()
            return {r[0]: float(r[1]) for r in rows}
    except SQLAlchemyError:
        logger.exception("Error al obtener ventas por sucursal")
        return {}

@st.cache_data(ttl=300, show_spinner=False)
def obtener_resumen_usuario_rango_cached(usuario: str, fecha_inicio: date, fecha_fin: date, tipo_filtro: str, marca_filtro: str) -> Dict[str, Any]:
    usuario_u = str(usuario).strip().upper()
    if tipo_filtro not in FILTRO_COLUMNAS:
        raise ValueError("Filtro no válido")
    col_date_expr = FILTRO_COLUMNAS[tipo_filtro]
    
    marca_cond = ""
    usuario_cond = ""
    params = {"f_ini": fecha_inicio, "f_fin": fecha_fin}
    
    if usuario_u != "TODOS":
        usuario_cond = ' AND "USUARIO" = :u '
        params["u"] = usuario_u
        
    if marca_filtro != "TODAS LAS MARCAS":
        marca_cond = ' AND UPPER("MARCA") = :m '
        params["m"] = marca_filtro
        
    q = text(f"""
        SELECT concepto, forma_pago, COALESCE(SUM(total),0) AS total, COUNT(*) AS cnt
        FROM (
          SELECT 'ventas' AS concepto, "TOTAL"::numeric AS total, NULL AS forma_pago, {col_date_expr} AS fecha, "USUARIO", "MARCA" AS marca_campo
            FROM ventas WHERE {col_date_expr} BETWEEN :f_ini AND :f_fin {usuario_cond} {marca_cond}
          UNION ALL
          SELECT 'insumos', "TOTAL"::numeric, UPPER("FORMA PAGO"), {col_date_expr}, "USUARIO", "MARCA"
            FROM insumos WHERE {col_date_expr} BETWEEN :f_ini AND :f_fin {usuario_cond} {marca_cond}
          UNION ALL
          SELECT 'gastos', "TOTAL"::numeric, UPPER("FORMA PAGO"), {col_date_expr}, "USUARIO", "MARCA"
            FROM gastos WHERE {col_date_expr} BETWEEN :f_ini AND :f_fin {usuario_cond} {marca_cond}
        ) t
        GROUP BY concepto, forma_pago
    """)
    try:
        with ENGINE_GLOBAL.connect() as conn:
            rows = conn.execute(q, params).fetchall()
        totales = {"ventas": 0.0, "insumos": 0.0, "gastos": 0.0}
        counts = {"ventas": 0, "insumos": 0, "gastos": 0}
        desglose_pagos = {"insumos": {}, "gastos": {}}
        for r in rows:
            concepto, forma_pago, monto, conteo = r[0], r[1], float(r[2]), int(r[3])
            totales[concepto] += monto
            counts[concepto] += conteo
            if concepto in desglose_pagos and forma_pago and forma_pago != 'NO APLICA':
                desglose_pagos[concepto][forma_pago] = desglose_pagos[concepto].get(forma_pago, 0.0) + monto
        detalles_meta = {k: {"cols": v} for k, v in META_COLUMNAS.items()}
        return {"totales": totales, "counts": counts, "desglose_pagos": desglose_pagos, "detalles_meta": detalles_meta}
    except SQLAlchemyError:
        logger.exception("Error en BD al obtener resumen")
        return {"totales": {}, "counts": {}, "desglose_pagos": {}, "detalles_meta": {}}

def _clear_caches():
    try:
        obtener_marcas_activas.clear()
        obtener_recurrencias_activas.clear()
        obtener_usuarios_activos.clear()
        obtener_ventas_por_sucursal.clear()
        obtener_resumen_usuario_rango_cached.clear()
    except Exception:
        pass

# --- Fetch con paginación (OFFSET o cursor) ---
def fetch_page(table: str, select_cols: List[str], usuario: str, col_date_expr: str, fecha_inicio: date, fecha_fin: date, marca_filtro: str, limit: int = 50, offset: int = 0, cursor: Optional[Tuple[Optional[date], Optional[int]]] = None) -> pd.DataFrame:
    t = validar_tabla(table)
    cols_allowed = columnas_permitidas_para_tabla(t)
    select_cols_upper = [c.upper() for c in select_cols]
    for c in select_cols_upper:
        if c not in cols_allowed:
            raise ValueError(f"Columna no permitida: {c}")
            
    usuario_u = str(usuario).strip().upper()
    condicion_marca = ""
    condicion_usuario = ""
    params = {"f_ini": fecha_inicio, "f_fin": fecha_fin, "limit": limit, "offset": offset}
    
    if usuario_u != "TODOS":
        condicion_usuario = ' AND "USUARIO" = :u '
        params["u"] = usuario_u
        
    if marca_filtro != "TODAS LAS MARCAS":
        condicion_marca = ' AND UPPER("MARCA") = :m '
        params["m"] = marca_filtro
        
    cols_sql = ", ".join([f'"{c}"' for c in select_cols])
    
    if cursor and cursor[0] is not None and cursor[1] is not None:
        params.update({"last_fecha": cursor[0], "last_id": cursor[1]})
        q = text(f'SELECT {cols_sql} FROM {t} WHERE {col_date_expr} BETWEEN :f_ini AND :f_fin {condicion_usuario} {condicion_marca} AND ( "FECHA" < :last_fecha OR ( "FECHA" = :last_fecha AND id < :last_id ) ) ORDER BY "FECHA" DESC, id DESC LIMIT :limit')
    else:
        q = text(f'SELECT {cols_sql} FROM {t} WHERE {col_date_expr} BETWEEN :f_ini AND :f_fin {condicion_usuario} {condicion_marca} ORDER BY "FECHA" DESC LIMIT :limit OFFSET :offset')
        
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
    except SQLAlchemyError:
        logger.exception("Error en fetch_page para %s", table)
        return pd.DataFrame(columns=[c.upper() for c in select_cols])

# --- Guardado en batch con validación y RETURNING ---
def guardar_correcciones_db_batch(nombre_tabla: str, cambios: Dict[str, Dict[str, Any]], df_original: pd.DataFrame, batch_size: int = 200, usuario_actual: Optional[str] = None):
    if not cambios:
        return
        
    # --- AUTO-CÁLCULAR TOTAL (usar Decimal para precisión) ---
    for row_idx, cols_changed in cambios.items():
        if "UNIDAD" in cols_changed or "COSTO" in cols_changed:
            try:
                idx = int(row_idx)
                val_u = cols_changed.get("UNIDAD", df_original.iloc[idx].get("UNIDAD", 0))
                val_c = cols_changed.get("COSTO", df_original.iloc[idx].get("COSTO", 0))

                u = Decimal(str(val_u)) if pd.notna(val_u) else Decimal(0)
                c = Decimal(str(val_c)) if pd.notna(val_c) else Decimal(0)

                # Guardar como Decimal (no float) para mantener precisión
                cols_changed["TOTAL"] = u * c
            except Exception as e:
                logger.error(f"Error calculando TOTAL para fila {row_idx}: {e}")
    # -----------------------------------

    t = validar_tabla(nombre_tabla)
    allowed_cols = columnas_permitidas_para_tabla(t)
    cols_set = set()
    for _, cols_changed in cambios.items():
        for col in cols_changed.keys():
            cu = validar_nombre_col(col)
            if cu not in allowed_cols:
                raise ValueError(f"Columna no permitida para actualizar: {cu}")
            if cu == "ID":
                continue
            cols_set.add(cu)
    cols = sorted(cols_set)
    if not cols:
        return
    
    rows_params = []
    for row_idx, cols_changed in cambios.items():
        try:
            idx = int(row_idx)
            row_id = int(df_original.iloc[idx]["ID"])
        except Exception:
            logger.exception("ID inválido en df_original para row_idx=%s", row_idx)
            continue
        normalized_vals = []
        for c in cols:
            raw = cols_changed.get(c)
            try:
                normalized_vals.append(_normalize_value(c, raw))
            except ValueError as e:
                logger.exception("Normalización fallida para id=%s col=%s val=%s", row_id, c, raw)
                raise

        # validación de respaldo en servidor: no permitir valores en 0 en campos monetarios/unidad/total
        for col_name, norm_val in zip(cols, normalized_vals):
            if col_name in {"COSTO", "UNIDAD", "TOTAL"} and norm_val is not None:
                try:
                    v_dec = Decimal(str(norm_val))
                except (InvalidOperation, TypeError):
                    raise ValueError(f"Valor inválido para {col_name} en id={row_id}: {norm_val}")
                if v_dec == Decimal("0"):
                    raise ValueError(f"No se permiten valores 0 en {col_name} para id={row_id}. Usa un valor distinto de 0.")

        rows_params.append((row_id, *normalized_vals))
    if not rows_params:
        return
    
    col_list_sql = ", ".join([f'"{c}" = v."{c}"' for c in cols])
    for i in range(0, len(rows_params), batch_size):
        batch = rows_params[i:i+batch_size]
        values_sql_parts = []
        params = {}
        for idx, row in enumerate(batch):
            placeholders = []
            params[f"id_{idx}"] = row[0]
            placeholders.append(f":id_{idx}")
            for j, val in enumerate(row[1:], start=0):
                key = f"r_{idx}_{j}"
                params[key] = val
                placeholders.append(f":{key}")
            values_sql_parts.append("(" + ", ".join(placeholders) + ")")
        v_cols = ["id"] + cols
        v_cols_sql = ", ".join([f'"{c}"' for c in v_cols])
        values_sql = ", ".join(values_sql_parts)
        update_sql = text(f'UPDATE {t} AS tgt SET {col_list_sql}, "CORREGIDO" = COALESCE(tgt."CORREGIDO",0) + 1 FROM (VALUES {values_sql}) AS v({v_cols_sql}) WHERE tgt.id = v.id RETURNING tgt.id')
        try:
            with ENGINE_GLOBAL.begin() as conn:
                res = conn.execute(update_sql, params)
                updated = [r[0] for r in res.fetchall()] if res.returns_rows else []
            logger.info("Batch aplicado en %s, filas=%d, updated_ids=%s", t, len(batch), updated)
        except SQLAlchemyError:
            logger.exception("Error aplicando batch de correcciones en %s", t)
            raise
            
    _clear_caches()
    st.session_state["refresh_flag"] = True
    st.toast(f"¡Correcciones guardadas en {t.upper()}!", icon="✅")

# --- FRONTEND compactado y con mejor UX ---
@st.fragment
def renderizar_tabla_paginada(nombre_tabla: str, counts: Dict[str, int], meta: Dict[str, Any], usuario_conectado: str, col_date_expr: str, fecha_inicio: date, fecha_fin: date, marca_filtro: str):
    total_registros = counts.get(nombre_tabla, 0)
    if total_registros == 0:
        st.caption(f"No se encontraron registros de {nombre_tabla.upper()} para este periodo y marca.")
        return
        
    PAGE_SIZE = 50
    estado_key = f"pagina_{nombre_tabla}"
    st.session_state.setdefault(estado_key, 0)
    pagina_actual = st.session_state[estado_key]
    total_paginas = (total_registros // PAGE_SIZE) + (1 if total_registros % PAGE_SIZE > 0 else 0)
    
    # Preparar parámetros para la extracción
    offset_actual = pagina_actual * PAGE_SIZE
    columnas_sql = meta[nombre_tabla]["cols"]
    cursor_state = st.session_state.get(f"cursor_{nombre_tabla}", None)
    
    df_pagina = fetch_page(table=nombre_tabla, select_cols=columnas_sql, usuario=usuario_conectado, col_date_expr=col_date_expr, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, marca_filtro=marca_filtro, limit=PAGE_SIZE, offset=offset_actual, cursor=cursor_state)
    
    col_izq, col_centro, col_der = st.columns([1, 2, 1])
    
    with col_izq:
        if st.button("⬅️ Anterior", key=f"prev_{nombre_tabla}", disabled=(pagina_actual <= 0)):
            st.session_state[estado_key] = max(0, pagina_actual - 1)
            st.session_state.pop(f"cursor_{nombre_tabla}", None)
            st.rerun()
            
    with col_centro:
        st.markdown(f"<div style='text-align:center;padding-top:5px;'>Página <b>{pagina_actual+1}</b> de {total_paginas} (Total: {total_registros} regs)</div>", unsafe_allow_html=True)
        
    with col_der:
        if st.button("Siguiente ➡️", key=f"next_{nombre_tabla}", disabled=((pagina_actual + 1) >= total_paginas)):
            st.session_state[estado_key] = min(total_paginas - 1, pagina_actual + 1)
            if not df_pagina.empty:
                last_fecha = df_pagina.iloc[-1].get("FECHA")
                last_id = df_pagina.iloc[-1].get("ID")
                if pd.notna(last_fecha) and pd.notna(last_id):
                    st.session_state[f"cursor_{nombre_tabla}"] = (last_fecha, int(last_id))
            st.rerun()

    # Manejo de la tabla interactiva
    if nombre_tabla in ["insumos", "gastos"]:
        # Se elimina "TIPO" de la lista de columnas editables
        columnas_editables = ["FECHA", "FORMA PAGO", "UNIDAD", "COSTO", "MARCA", "RECURRENCIA"]
        todas_las_columnas = df_pagina.columns.tolist()
        columnas_deshabilitadas = [c for c in todas_las_columnas if c not in columnas_editables]
        editor_key = f"editor_{nombre_tabla}_{pagina_actual}"
        
        marcas_opciones = [m for m in obtener_marcas_activas() if m != "TODAS LAS MARCAS"]
        recurrencias_opciones = obtener_recurrencias_activas()
        
        edited_df = st.data_editor(df_pagina, use_container_width=True, hide_index=True, disabled=columnas_deshabilitadas, key=editor_key, column_config={
            "ID": None,
            "FECHA": st.column_config.DateColumn("FECHA", format="YYYY-MM-DD", width="small"),
            "FORMA PAGO": st.column_config.SelectboxColumn("FORMA PAGO", options=["EFECTIVO", "TARJETA", "TRANSFERENCIA"]),
            "UNIDAD": st.column_config.NumberColumn("UNIDAD", format="%.2f", step=0.01),
            "COSTO": st.column_config.NumberColumn("COSTO", format="$%.2f", step=0.01),
            "TOTAL": st.column_config.NumberColumn("TOTAL", format="$%.2f", step=0.01),
            "MARCA": st.column_config.SelectboxColumn("MARCA", options=marcas_opciones),
            "RECURRENCIA": st.column_config.SelectboxColumn("RECURRENCIA", options=recurrencias_opciones)
        })
        
        cambios_detectados = st.session_state.get(editor_key, {}).get("edited_rows", {})
        
        # Validación amigable en frontend: detectar valores en 0 proyectados
        filas_invalidas = []
        for row_idx, cols_changed in cambios_detectados.items():
            try:
                idx = int(row_idx)
                
                # 1. Obtener valores editados o caer en los originales si no se editaron
                val_u = cols_changed.get("UNIDAD", df_pagina.iloc[idx].get("UNIDAD", 0))
                val_c = cols_changed.get("COSTO", df_pagina.iloc[idx].get("COSTO", 0))
                
                u = Decimal(str(val_u)) if pd.notna(val_u) else Decimal("0")
                c = Decimal(str(val_c)) if pd.notna(val_c) else Decimal("0")
                
                # 2. Calcular el total tal como lo hará el backend
                t = u * c

                # 3. Revisar si alguna de las 3 variables resultó en 0
                for nombre_campo, valor_campo in [("UNIDAD", u), ("COSTO", c), ("TOTAL", t)]:
                    if valor_campo == Decimal("0"):
                        row_id = df_pagina.iloc[idx].get("ID", f"índice {row_idx}")
                        filas_invalidas.append(f"ID {row_id}: {nombre_campo} resulta en 0")
                        
            except (InvalidOperation, TypeError, ValueError):
                row_id = df_pagina.iloc[int(row_idx)].get("ID", f"índice {row_idx}") if str(row_idx).isdigit() else row_idx
                filas_invalidas.append(f"ID {row_id}: contiene valores no numéricos inválidos.")
        
        guardar_key = f"guardar_{nombre_tabla}_{pagina_actual}"
        estado_guardando = f"guardando_{nombre_tabla}"
        if estado_guardando not in st.session_state:
            st.session_state[estado_guardando] = False

        if filas_invalidas:
            # Mensaje amigable y claro
            st.error("No se pueden guardar correcciones con valores en 0 en UNIDAD, COSTO o TOTAL. Corrige las siguientes filas antes de guardar:")
            for msg in filas_invalidas:
                st.caption(f"• {msg}")
            guardar_disabled = True
        else:
            guardar_disabled = False

        if cambios_detectados:
            if st.button(f"💾 Guardar {len(cambios_detectados)} corrección(es) en {nombre_tabla.upper()}", key=guardar_key, disabled=st.session_state[estado_guardando] or guardar_disabled):
                st.session_state[estado_guardando] = True
                try:
                    with st.spinner("Guardando correcciones..."):
                        guardar_correcciones_db_batch(nombre_tabla, cambios_detectados, df_pagina)
                        st.success("✅ Correcciones guardadas correctamente.")
                except ValueError as ve:
                    # Mensaje amigable si la validación del servidor falla
                    logger.exception("Validación fallida al guardar correcciones")
                    st.error(f"❌ No se guardaron las correcciones: {ve}")
                except Exception as e:
                    logger.exception("Error guardando correcciones")
                    st.error("❌ Ocurrió un error al guardar. Revisa los datos e inténtalo de nuevo.")
                finally:
                    st.session_state[estado_guardando] = False
                    
        if st.session_state.get("refresh_flag"):
            st.session_state["refresh_flag"] = False
            st.rerun()
            
    else:
        st.dataframe(df_pagina, use_container_width=True, hide_index=True, column_config={
            "ID": None,
            "FECHA": st.column_config.DateColumn("FECHA", format="YYYY-MM-DD", width="small"),
            "Cantidad": st.column_config.NumberColumn("Cantidad", format="%.2f"),
            "TOTAL": st.column_config.NumberColumn("TOTAL", format="$%.2f")
        })

def mostrar_pestana_resumen():
    st.header("📋 Resumen Diario de Capturas")
    
    usuario_actual = str(st.session_state.get("usuario_actual", "ANÓNIMO")).strip().upper()
    es_admin = st.session_state.get("es_admin", False)
    
    if es_admin:
        usuarios_db = obtener_usuarios_activos()
        opciones_usuarios = ["TODOS"] + usuarios_db
        idx_defecto = opciones_usuarios.index(usuario_actual) if usuario_actual in opciones_usuarios else 0
        
        st.info("👑 Modo Administrador:")
        usuario_conectado = st.selectbox("👤 Visualizar actividad del usuario:", opciones_usuarios, index=idx_defecto)
    else:
        usuario_conectado = usuario_actual
        st.info(f"👤 Mostrando actividad del usuario: **{usuario_conectado}**")
    
    col_filtro1, col_filtro2, col_filtro3 = st.columns(3)
    with col_filtro1:
        tipo_filtro_sel = st.selectbox("1. Criterio de búsqueda:", ["Fecha Captura", "Fecha Sistema"])
    with col_filtro2:
        fecha_actual_dia = datetime.now().date()
        fechas_seleccionadas = st.date_input("2. Selecciona Rango de Fechas:", value=(fecha_actual_dia, fecha_actual_dia))
    with col_filtro3:
        lista_marcas_db = obtener_marcas_activas()
        marca_sel = st.selectbox("3. Selecciona Marca:", lista_marcas_db)
        
    if isinstance(fechas_seleccionadas, tuple) and len(fechas_seleccionadas) == 2:
        fecha_inicio, fecha_fin = fechas_seleccionadas
    else:
        fecha_inicio = fecha_fin = fechas_seleccionadas[0] if isinstance(fechas_seleccionadas, tuple) else fechas_seleccionadas
        
    firma_filtros = f"{fecha_inicio}_{fecha_fin}_{tipo_filtro_sel}_{marca_sel}_{usuario_conectado}"
    if st.session_state.get("firma_filtros_anterior") != firma_filtros:
        for t in ["ventas", "insumos", "gastos"]:
            st.session_state[f"pagina_{t}"] = 0
            st.session_state.pop(f"cursor_{t}", None)
        st.session_state.firma_filtros_anterior = firma_filtros
        
    data_resumen = obtener_resumen_usuario_rango_cached(usuario_conectado, fecha_inicio, fecha_fin, tipo_filtro_sel, marca_sel)
    totales, counts = data_resumen.get("totales", {}), data_resumen.get("counts", {})
    pagos, meta = data_resumen.get("desglose_pagos", {}), data_resumen.get("detalles_meta", {})
    
    st.markdown("---")
    st.subheader("Totales del Periodo")
    col_v, col_i, col_g = st.columns(3)
    
    with col_v:
        col_v.metric(label="🛒 TOTAL VENTAS INYECTADAS", value=f"${totales.get('ventas', 0):,.2f}")
    with col_i:
        col_i.metric(label="📦 TOTAL INSUMOS REGISTRADOS", value=f"${totales.get('insumos', 0):,.2f}")
    with col_g:
        col_g.metric(label="💸 TOTAL GASTOS REGISTRADOS", value=f"${totales.get('gastos', 0):,.2f}")
        
    col_sub_v, col_sub_i, col_sub_g = st.columns(3)
    with col_sub_v:
        with st.container(border=True):
            with st.expander("Filtrar producto específico"):
                col_date_expr = FILTRO_COLUMNAS[tipo_filtro_sel]
                condicion_marca_prod = ""
                condicion_usuario_prod = ""
                params_prod = {"f_ini": fecha_inicio, "f_fin": fecha_fin}
                
                if usuario_conectado != "TODOS":
                    condicion_usuario_prod = ' AND "USUARIO" = :u '
                    params_prod["u"] = usuario_conectado
                    
                if marca_sel != "TODAS LAS MARCAS":
                    condicion_marca_prod = ' AND UPPER("MARCA") = :m '
                    params_prod["m"] = marca_sel
                    
                q_prods = text(f'SELECT DISTINCT "Producto" FROM ventas WHERE {col_date_expr} BETWEEN :f_ini AND :f_fin {condicion_usuario_prod} {condicion_marca_prod} ORDER BY "Producto" ASC')
                try:
                    with ENGINE_GLOBAL.connect() as conn:
                        df_prods = pd.read_sql(q_prods, conn, params=params_prod)
                    lista_productos = df_prods["Producto"].dropna().tolist() if not df_prods.empty else []
                except SQLAlchemyError:
                    logger.exception("Error obteniendo productos")
                    lista_productos = []
                    
                if lista_productos:
                    prod_seleccionado = st.selectbox("Selecciona Producto:", lista_productos, key="sb_filtro_prod_ventas")
                    params_prod["prod"] = prod_seleccionado
                    q_det_prod = text(f'SELECT COALESCE(SUM("Cantidad"),0) as total_cant, COALESCE(SUM("TOTAL"),0) as total_monto FROM ventas WHERE {col_date_expr} BETWEEN :f_ini AND :f_fin AND "Producto" = :prod {condicion_usuario_prod} {condicion_marca_prod}')
                    with ENGINE_GLOBAL.connect() as conn:
                        res_prod = conn.execute(q_det_prod, params_prod).fetchone()
                    cant_prod = float(res_prod[0]) if res_prod else 0.0
                    monto_prod = float(res_prod[1]) if res_prod else 0.0
                    st.markdown(f"📦 Cantidad total: **{cant_prod:,.2f}**")
                    st.markdown(f"💵 Total general: **${monto_prod:,.2f}**")
                else:
                    st.caption("No hay productos registrados en este rango/marca.")
            
            # --- NUEVO BLOQUE: Desglose por Sucursal ---
            st.markdown("<br>**Desglose por Sucursal:**", unsafe_allow_html=True)
            ventas_sucursal = obtener_ventas_por_sucursal(
                usuario_conectado, fecha_inicio, fecha_fin, tipo_filtro_sel, marca_sel
            )
            
            if ventas_sucursal:
                for suc, tot in ventas_sucursal.items():
                    st.caption(f"🏬 {suc}: **${tot:,.2f}**")
            else:
                st.caption("No hay datos de sucursales en este periodo.")
                    
    for col, categoria in zip([col_sub_i, col_sub_g], ["insumos", "gastos"]):
        with col:
            with st.container(border=True):
                st.markdown(f"**Desglose de {categoria.capitalize()}:**")
                st.caption(f"💵 Efectivo: **${pagos.get(categoria, {}).get('EFECTIVO', 0.0):,.2f}**")
                st.caption(f"💳 Tarjeta: **${pagos.get(categoria, {}).get('TARJETA', 0.0):,.2f}**")
                st.caption(f"🏦 Transferencia: **${pagos.get(categoria, {}).get('TRANSFERENCIA', 0.0):,.2f}**")
                
    st.markdown("---")
    st.subheader("Desglose Capturado")
    tab_v, tab_i, tab_g = st.tabs(["VENTAS", "INSUMOS", "GASTOS"])
    col_date_expr = FILTRO_COLUMNAS[tipo_filtro_sel]
    
    if "ventas" in meta:
        with tab_v:
            renderizar_tabla_paginada("ventas", counts, meta, usuario_conectado, col_date_expr, fecha_inicio, fecha_fin, marca_sel)
    if "insumos" in meta:
        with tab_i:
            renderizar_tabla_paginada("insumos", counts, meta, usuario_conectado, col_date_expr, fecha_inicio, fecha_fin, marca_sel)
    if "gastos" in meta:
        with tab_g:
            renderizar_tabla_paginada("gastos", counts, meta, usuario_conectado, col_date_expr, fecha_inicio, fecha_fin, marca_sel)

if __name__ == "__main__":
    mostrar_pestana_resumen()
