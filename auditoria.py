import logging
import re
import json
from typing import Dict, Any, List, Tuple, Optional, Iterable
from decimal import Decimal, InvalidOperation, getcontext
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
from sqlalchemy import (
    text, MetaData, Table, select, column, and_, or_, desc, update, bindparam, func, literal_column
)
from sqlalchemy.exc import SQLAlchemyError
from db_utils import ENGINE_GLOBAL
import streamlit as st

# -------------------------
# Configuración y Constantes
# -------------------------
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
st.set_page_config(layout="wide")

FILTRO_COLUMNAS = {
    "Fecha Captura": '"FECHA_CAPTURA"',
    "Fecha Sistema": '"FECHA"'
}

TABLAS_PERMITIDAS = {"ventas", "insumos", "gastos"}

META_COLUMNAS = {
    "ventas": ["id", "Número de Venta", "FECHA", "Producto", "Cantidad", "TOTAL", "SUCURSAL", "MARCA"],
    "insumos": ["id", "FECHA", "INSUMO", "TIPO", "PROVEEDOR", "FORMA PAGO", "UNIDAD", "COSTO", "TOTAL", "MARCA", "CORREGIDO"],
    "gastos": ["id", "FECHA", "GASTO DE", "TIPO", "CATEGORÍA", "PROVEEDOR", "FORMA PAGO", "UNIDAD", "COSTO", "TOTAL", "RECURRENCIA", "MARCA", "CORREGIDO"]
}

_VALID_COL_RE = re.compile(r'^[A-Z0-9 _]+$')
TTL_AUDITORIA = 60 * 30
MAX_DOWNLOAD_ROWS = 100_000

getcontext().prec = 12

# -------------------------
# Utilidades de Validación
# -------------------------
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

def _prepare_cols_set(cambios: Dict[str, Dict[str, Any]], allowed_cols: Iterable[str]) -> List[str]:
    validated = set()
    allowed_set = {c.upper() for c in allowed_cols}
    for cols_changed in cambios.values():
        for raw_col in cols_changed.keys():
            try:
                col_up = str(raw_col).strip().upper()
                if not _VALID_COL_RE.match(col_up):
                    logger.warning("Columna ignorada por formato inválido: %s", raw_col)
                    continue
                if col_up == "ID":
                    continue
                if col_up not in allowed_set:
                    logger.warning("Columna no permitida: %s", col_up)
                    continue
                validated.add(col_up)
            except Exception:
                logger.exception("Error validando columna: %s", raw_col)
    return sorted(validated)

# -------------------------
# Normalización de Valores
# -------------------------
def _normalize_value(col: str, val: Any) -> Any:
    if val is None:
        return None
    cu = col.upper()
    try:
        if cu in {"COSTO", "TOTAL", "UNIDAD", "CANTIDAD"}:
            return Decimal(str(val))
        if cu == "FECHA":
            if isinstance(val, datetime):
                return val.date()
            if isinstance(val, date):
                return val
            return pd.to_datetime(val, errors="raise").date()
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Valor inválido para {col}: {val}") from e
    return val

def normalize_ts_series(s: pd.Series, target_tz: str = "America/Mexico_City") -> pd.Series:
    s = pd.to_datetime(s, errors="coerce")
    if s.dt.tz is None:
        s = s.dt.tz_localize("UTC", ambiguous="NaT", nonexistent="shift_forward")
    s = s.dt.tz_convert(target_tz).dt.tz_localize(None)
    return s

def safe_download_csv(df: pd.DataFrame, filename: str, max_rows: int = MAX_DOWNLOAD_ROWS) -> bytes:
    if len(df) > max_rows:
        st.warning(f"El conjunto supera {max_rows:,} filas. Se descargará un extracto de {max_rows:,} filas.")
        df = df.head(max_rows)
    return df.to_csv(index=False).encode("utf-8")

# -------------------------
# DB: Creación y Utilidades
# -------------------------
def create_audit_table() -> None:
    ddl = text("""
    CREATE TABLE IF NOT EXISTS auditoria_cambios (
      audit_id BIGSERIAL PRIMARY KEY,
      tabla TEXT NOT NULL, 
      row_id BIGINT NOT NULL, 
      usuario TEXT,
      campo TEXT, 
      old_value TEXT, 
      new_value TEXT, 
      accion TEXT NOT NULL,
      contexto JSONB, 
      ts TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_auditoria_tabla_row ON auditoria_cambios(tabla, row_id);
    CREATE INDEX IF NOT EXISTS idx_auditoria_usuario_ts ON auditoria_cambios(usuario, ts);
    """)
    try:
        with ENGINE_GLOBAL.begin() as conn:
            conn.execute(ddl)
        logger.info("Tabla de auditoría verificada/creada.")
    except SQLAlchemyError:
        logger.exception("No se pudo crear tabla de auditoría")

def _clear_caches_for_tables(tables: Optional[Iterable[str]] = None) -> None:
    cache_funcs = {
        "auditoria": obtener_metricas_auditoria_usuarios,
        "marcas": obtener_marcas_activas,
        "resumen": obtener_resumen_usuario_rango_cached,
        "banderas": obtener_banderas_rojas
    }
    if tables is None:
        targets = cache_funcs.values()
    else:
        targets = []
        for t in tables:
            if t == "ventas":
                targets.append(obtener_marcas_activas)
        if not targets:
            targets = cache_funcs.values()

    for f in set(targets):
        try:
            f.clear()
        except Exception as e:
            logger.debug("No se pudo limpiar cache de %s: %s", getattr(f, "__name__", str(f)), e)

# -------------------------
# Consultas Cacheadas (Solo Lectura)
# -------------------------
@st.cache_data(ttl=TTL_AUDITORIA, show_spinner=False)
def obtener_metricas_auditoria_usuarios(fecha_inicio: date, fecha_fin: date) -> pd.DataFrame:
    if fecha_fin < fecha_inicio:
        raise ValueError("fecha_fin debe ser >= fecha_inicio")
    fin_ex = fecha_fin + timedelta(days=1)
    q = text("""
    SELECT modulo, usuario, date_trunc('day', fecha_cap) AS dia,
           COUNT(*) AS ops, SUM(COALESCE(total,0)) AS total_sum, SUM(COALESCE(corregido,0)) AS total_corregido,
           MIN(fecha_cap) AS first_ts, MAX(fecha_cap) AS last_ts
    FROM (
      SELECT 'ventas' AS modulo, "USUARIO" AS usuario, "FECHA_CAPTURA" AS fecha_cap, "TOTAL"::numeric AS total, 0 AS corregido FROM ventas WHERE "FECHA_CAPTURA" >= :inicio AND "FECHA_CAPTURA" < :fin_ex
      UNION ALL 
      SELECT 'gastos', "USUARIO", "FECHA_CAPTURA", "TOTAL"::numeric, COALESCE("CORREGIDO",0) FROM gastos WHERE "FECHA_CAPTURA" >= :inicio AND "FECHA_CAPTURA" < :fin_ex
      UNION ALL 
      SELECT 'insumos', "USUARIO", "FECHA_CAPTURA", "TOTAL"::numeric, COALESCE("CORREGIDO",0) FROM insumos WHERE "FECHA_CAPTURA" >= :inicio AND "FECHA_CAPTURA" < :fin_ex
    ) t 
    GROUP BY modulo, usuario, date_trunc('day', fecha_cap) 
    ORDER BY dia, usuario
    """)
    try:
        with ENGINE_GLOBAL.connect() as conn:
            df = pd.read_sql(q, conn, params={"inicio": fecha_inicio, "fin_ex": fin_ex})
        if df.empty:
            return df
        df.columns = df.columns.str.upper()
        df['USUARIO'] = df['USUARIO'].astype(str).str.strip().str.upper()
        df['TOTAL_SUM'] = pd.to_numeric(df['TOTAL_SUM'], errors='coerce').fillna(0.0)
        df['TOTAL_CORREGIDO'] = pd.to_numeric(df['TOTAL_CORREGIDO'], errors='coerce').fillna(0).astype(int)
        df['DIA'] = pd.to_datetime(df['DIA']).dt.date
        df['FIRST_TS'] = pd.to_datetime(df['FIRST_TS'], errors='coerce')
        df['LAST_TS'] = pd.to_datetime(df['LAST_TS'], errors='coerce')
        return df
    except SQLAlchemyError:
        logger.exception("Error en obtener_metricas_auditoria_usuarios")
        return pd.DataFrame()

@st.cache_data(ttl=TTL_AUDITORIA, show_spinner=False)
def obtener_banderas_rojas(fecha_inicio: date, fecha_fin: date, usuario: Optional[str] = None) -> Dict[str, int]:
    if fecha_fin < fecha_inicio:
        return {"madrugada": 0, "ultrarrapidas": 0, "pausas": 0}
    fin_ex = fecha_fin + timedelta(days=1)
    
    user_cond = ' AND "USUARIO" = :u ' if usuario and usuario != "Todos" else ""
    params = {"inicio": fecha_inicio, "fin_ex": fin_ex}
    if user_cond:
        params["u"] = str(usuario).strip().upper()

    q = text(f"""
    SELECT "USUARIO", "FECHA_CAPTURA" AS ts
    FROM (
      SELECT "USUARIO", "FECHA_CAPTURA" FROM ventas WHERE "FECHA_CAPTURA" >= :inicio AND "FECHA_CAPTURA" < :fin_ex {user_cond}
      UNION ALL
      SELECT "USUARIO", "FECHA_CAPTURA" FROM gastos WHERE "FECHA_CAPTURA" >= :inicio AND "FECHA_CAPTURA" < :fin_ex {user_cond}
      UNION ALL
      SELECT "USUARIO", "FECHA_CAPTURA" FROM insumos WHERE "FECHA_CAPTURA" >= :inicio AND "FECHA_CAPTURA" < :fin_ex {user_cond}
    ) t
    WHERE "FECHA_CAPTURA" IS NOT NULL
    ORDER BY "USUARIO", "FECHA_CAPTURA" ASC
    """)
    try:
        with ENGINE_GLOBAL.connect() as conn:
            df = pd.read_sql(q, conn, params=params)
        
        if df.empty:
            return {"madrugada": 0, "ultrarrapidas": 0, "pausas": 0}

        df['ts'] = normalize_ts_series(df['ts'])
        
        # 1. Registros de madrugada (< 6:00 AM)
        madrugada_cnt = int((df['ts'].dt.hour < 6).sum())
        
        # 2 y 3. Tiempos entre operaciones consecutivas por usuario
        df['diff_sec'] = df.groupby('USUARIO')['ts'].diff().dt.total_seconds()
        ultrarrapidas_cnt = int(((df['diff_sec'] > 0) & (df['diff_sec'] < 20)).sum())
        pausas_cnt = int((df['diff_sec'] > 240).sum())
        
        return {
            "madrugada": madrugada_cnt,
            "ultrarrapidas": ultrarrapidas_cnt,
            "pausas": pausas_cnt
        }
    except SQLAlchemyError:
        logger.exception("Error en obtener_banderas_rojas")
        return {"madrugada": 0, "ultrarrapidas": 0, "pausas": 0}

@st.cache_data(ttl=300, show_spinner=False)
def obtener_marcas_activas() -> List[str]:
    q = text('''SELECT DISTINCT UPPER(TRIM("MARCA")) AS m FROM ventas WHERE "MARCA" IS NOT NULL
                UNION SELECT DISTINCT UPPER(TRIM("MARCA")) FROM insumos WHERE "MARCA" IS NOT NULL
                UNION SELECT DISTINCT UPPER(TRIM("MARCA")) FROM gastos WHERE "MARCA" IS NOT NULL''')
    try:
        with ENGINE_GLOBAL.connect() as conn:
            res = conn.execute(q).fetchall()
            return ["TODAS LAS MARCAS"] + sorted([r[0] for r in res if r[0]])
    except SQLAlchemyError:
        logger.exception("Error al obtener marcas")
        return ["TODAS LAS MARCAS"]

@st.cache_data(ttl=300, show_spinner=False)
def obtener_resumen_usuario_rango_cached(usuario: str, fecha_inicio: date, fecha_fin: date, tipo_filtro: str, marca_filtro: str) -> Dict[str, Any]:
    usuario_u = str(usuario).strip().upper()
    if tipo_filtro not in FILTRO_COLUMNAS:
        raise ValueError("Filtro no válido")
    col_date_expr = FILTRO_COLUMNAS[tipo_filtro]
    marca_cond = ' AND UPPER("MARCA") = :m ' if marca_filtro != "TODAS LAS MARCAS" else ""
    
    params = {"u": usuario_u, "f_ini": fecha_inicio, "f_fin": fecha_fin}
    if marca_cond:
        params["m"] = marca_filtro
        
    q = text(f"""
        SELECT concepto, forma_pago, COALESCE(SUM(total),0) AS total, COUNT(*) AS cnt FROM (
          SELECT 'ventas' AS concepto, "TOTAL"::numeric AS total, NULL AS forma_pago, {col_date_expr} AS fecha, "USUARIO", "MARCA" AS marca_campo FROM ventas WHERE "USUARIO" = :u AND {col_date_expr} BETWEEN :f_ini AND :f_fin {marca_cond}
          UNION ALL 
          SELECT 'insumos', "TOTAL"::numeric, UPPER("FORMA PAGO"), {col_date_expr}, "USUARIO", "MARCA" FROM insumos WHERE "USUARIO" = :u AND {col_date_expr} BETWEEN :f_ini AND :f_fin {marca_cond}
          UNION ALL 
          SELECT 'gastos', "TOTAL"::numeric, UPPER("FORMA PAGO"), {col_date_expr}, "USUARIO", "MARCA" FROM gastos WHERE "USUARIO" = :u AND {col_date_expr} BETWEEN :f_ini AND :f_fin {marca_cond}
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
        return {"totales": totales, "counts": counts, "desglose_pagos": desglose_pagos, "detalles_meta": {k: {"cols": v} for k, v in META_COLUMNAS.items()}}
    except SQLAlchemyError:
        logger.exception("Error en BD al obtener resumen")
        return {"totales": {}, "counts": {}, "desglose_pagos": {}, "detalles_meta": {}}

# -------------------------
# Paginación y Consulta por Pagina
# -------------------------
def fetch_page(
    table_name: str, select_cols: List[str], usuario: str, col_date_expr: str,
    fecha_inicio: date, fecha_fin: date, marca_filtro: str, limit: int = 50,
    offset: int = 0, cursor: Optional[Tuple[Optional[date], Optional[int]]] = None
) -> pd.DataFrame:
    t_name = validar_tabla(table_name)
    cols_allowed = columnas_permitidas_para_tabla(t_name)
    for c in select_cols:
        if c.upper() not in cols_allowed:
            raise ValueError(f"Columna no permitida: {c}")

    meta = MetaData()
    try:
        tbl = Table(t_name, meta, autoload_with=ENGINE_GLOBAL)
    except Exception:
        tbl = Table(t_name, meta, *[column(c) for c in set([c.upper() for c in select_cols] + ["USUARIO", "MARCA", "FECHA", "FECHA_CAPTURA", "ID"])])

    sel_cols = [tbl.c[c] if c in tbl.c else literal_column(f'"{c}"') for c in [c.upper() for c in select_cols]]
    stmt = select(*sel_cols).select_from(tbl).where(tbl.c.USUARIO == str(usuario).strip().upper())

    if col_date_expr not in FILTRO_COLUMNAS.values():
        raise ValueError("Expresión de fecha no permitida")

    if '"FECHA_CAPTURA"' in col_date_expr or '"FECHA"' in col_date_expr:
        stmt = stmt.where(literal_column(col_date_expr).between(fecha_inicio, fecha_fin))
    else:
        if 'FECHA' in tbl.c:
            stmt = stmt.where(tbl.c.FECHA.between(fecha_inicio, fecha_fin))
        else:
            stmt = stmt.where(literal_column(col_date_expr).between(fecha_inicio, fecha_fin))

    if marca_filtro != "TODAS LAS MARCAS":
        if 'MARCA' in tbl.c:
            stmt = stmt.where(func.upper(tbl.c.MARCA) == marca_filtro)
        else:
            stmt = stmt.where(literal_column('UPPER("MARCA")') == marca_filtro)

    if "FECHA_CAPTURA" in col_date_expr and "FECHA_CAPTURA" in tbl.c:
        col_sort = tbl.c.FECHA_CAPTURA
    elif "FECHA" in tbl.c:
        col_sort = tbl.c.FECHA
    else:
        col_sort = literal_column(col_date_expr)

    if cursor and cursor[0] and cursor[1]:
        stmt = stmt.where(or_(col_sort < cursor[0], and_(col_sort == cursor[0], tbl.c.ID < cursor[1]))).order_by(desc(col_sort), desc(tbl.c.ID)).limit(limit)
    else:
        stmt = stmt.order_by(desc(col_sort), desc(tbl.c.ID)).limit(limit).offset(offset)

    try:
        with ENGINE_GLOBAL.connect() as conn:
            df = pd.read_sql(stmt, conn)
        if not df.empty:
            df.columns = df.columns.str.upper()
            if "FECHA" in df.columns:
                df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce").dt.date
            if "MARCA" in df.columns:
                df["MARCA"] = df["MARCA"].astype(str).str.strip().str.upper().replace({"": None, "NAN": None})
        return df
    except SQLAlchemyError:
        logger.exception("Error en fetch_page para %s", t_name)
        return pd.DataFrame(columns=[c.upper() for c in select_cols])

# -------------------------
# Servicio: Guardar Correcciones (Desacoplado de UI)
# -------------------------
def guardar_correcciones_db_batch(
    nombre_tabla: str, cambios: Dict[str, Dict[str, Any]], df_original: pd.DataFrame,
    usuario_actual: Optional[str] = None, contexto: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    result = {"aplicadas": 0, "audit_inserted": 0, "errors": []}
    if not cambios:
        return result

    t = validar_tabla(nombre_tabla)
    allowed_cols = columnas_permitidas_para_tabla(t)
    cols = _prepare_cols_set(cambios, allowed_cols)
    if not cols:
        return result

    audit_rows = []
    update_params = []
    ts_now = datetime.now(timezone.utc)
    ctx_json = json.dumps(contexto) if contexto else None

    for row_idx, cols_changed in cambios.items():
        try:
            idx = int(row_idx)
            if idx < 0 or idx >= len(df_original):
                raise IndexError("Índice fuera de rango")
            row_id = int(df_original.iloc[idx]["ID"])
        except Exception as e:
            logger.exception("ID inválido para row_idx=%s: %s", row_idx, e)
            result["errors"].append({"row_idx": row_idx, "error": str(e)})
            continue

        payload = {'p_id': row_id}
        any_change = False
        for c in cols:
            raw = cols_changed.get(c)
            try:
                nv = _normalize_value(c, raw)
            except ValueError as e:
                logger.warning("Valor inválido para fila %s columna %s: %s", row_idx, c, e)
                result["errors"].append({"row_idx": row_idx, "col": c, "error": str(e)})
                nv = None

            payload[f'p_{c}'] = nv

            old_val = None
            if c in df_original.columns and pd.notna(df_original.iloc[idx].get(c)):
                old_val = str(df_original.iloc[idx].get(c))
            new_val = None if nv is None else str(nv)

            if old_val != new_val:
                any_change = True
                audit_rows.append({
                    "tabla": t,
                    "row_id": row_id,
                    "usuario": usuario_actual,
                    "campo": c,
                    "old_value": old_val,
                    "new_value": new_val,
                    "accion": "UPDATE",
                    "contexto": ctx_json,
                    "ts": ts_now
                })
        if any_change:
            update_params.append(payload)

    if not update_params:
        return result

    meta = MetaData()
    tbl = Table(t, meta, column('ID'), column('CORREGIDO'), *[column(c) for c in cols])

    values_dict = {c: bindparam(f'p_{c}') for c in cols}
    values_dict['CORREGIDO'] = func.coalesce(tbl.c.CORREGIDO, 0) + 1

    stmt = update(tbl).where(tbl.c.ID == bindparam('p_id')).values(**values_dict)
    q_audit = text('INSERT INTO auditoria_cambios(tabla,row_id,usuario,campo,old_value,new_value,accion,contexto,ts) VALUES (:tabla,:row_id,:usuario,:campo,:old_value,:new_value,:accion,:contexto,:ts)')

    try:
        with ENGINE_GLOBAL.begin() as conn:
            if audit_rows:
                conn.execute(q_audit, audit_rows)
                result["audit_inserted"] = len(audit_rows)
            conn.execute(stmt, update_params)
            result["aplicadas"] = len(update_params)
    except SQLAlchemyError as e:
        logger.exception("Error aplicando correcciones en BD: %s", e)
        result["errors"].append({"db": str(e)})
        raise

    _clear_caches_for_tables([t])
    return result

# -------------------------
# UI: Componente Streamlit
# -------------------------
@st.fragment
def mostrar_pestana_auditoria_usuarios():
    st.sidebar.header("👤 Filtros de Auditoría")
    hoy = date.today()
    fechas_sel = st.sidebar.date_input("Rango de Fechas", value=(hoy - timedelta(days=30), hoy), max_value=hoy)

    if not (isinstance(fechas_sel, tuple) and len(fechas_sel) == 2):
        st.info("💡 Selecciona la fecha inicial y final en el menú lateral.")
        return

    f_inicio, f_fin = fechas_sel
    if f_fin < f_inicio:
        st.error("❌ La fecha final debe ser mayor o igual a la inicial.")
        return

    st.session_state.setdefault("auditoria_syncing", False)
    if st.sidebar.button("🔄 Sincronizar Logs", use_container_width=True, key="sync_auditoria", disabled=st.session_state["auditoria_syncing"]):
        st.session_state["auditoria_syncing"] = True
        try:
            with st.spinner("Sincronizando..."):
                _clear_caches_for_tables(None)
                st.success("¡Datos actualizados!")
        finally:
            st.session_state["auditoria_syncing"] = False

    with st.spinner("Cargando métricas de auditoría..."):
        df_logs = obtener_metricas_auditoria_usuarios(f_inicio, f_fin)

    if df_logs is None or df_logs.empty:
        st.warning(f"⚠️ No se encontraron registros entre {f_inicio} y {f_fin}.")
        return

    for ts_col in ["FIRST_TS", "LAST_TS"]:
        if ts_col in df_logs.columns:
            df_logs[ts_col] = normalize_ts_series(df_logs[ts_col])

    usuarios_disponibles = sorted(df_logs['USUARIO'].dropna().unique())
    usuario_sel = st.sidebar.selectbox("Selecciona Usuario", ["Todos"] + usuarios_disponibles, index=0)
    df_filtrado = df_logs if usuario_sel == "Todos" else df_logs[df_logs['USUARIO'] == usuario_sel].copy()

    if df_filtrado.empty:
        st.info(f"El usuario {usuario_sel} no registra actividad.")
        return

    st.title("📊 Auditoría de Usuarios")
    st.caption(f"Rango: **{f_inicio}** al **{f_fin}** | Filtro: **{usuario_sel}**")

    # Banderas Rojas Detectadas
    banderas = obtener_banderas_rojas(f_inicio, f_fin, usuario_sel)
    if any(banderas.values()):
        st.warning(
            f"**⚠️ Banderas Rojas Detectadas en la Operación:**\n\n"
            f"* **{banderas['madrugada']:,}** registros capturados de madrugada (antes de las 6:00 AM).\n"
            f"* **{banderas['ultrarrapidas']:,}** operaciones consecutivas ultra-rápidas (menos de 20 segundos de diferencia).\n"
            f"* **{banderas['pausas']:,}** pausas prolongadas entre capturas consecutivas (más de 4 minutos de diferencia)."
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Operaciones", f"{int(df_filtrado['OPS'].sum()):,}")
    c2.metric("Monto capturado", f"${float(df_filtrado['TOTAL_SUM'].sum()):,.2f}")
    c3.metric("Correcciones", f"{int(df_filtrado['TOTAL_CORREGIDO'].sum()):,}")

    jornadas = df_filtrado.groupby(['USUARIO', 'DIA'], observed=False).agg(first_ts=('FIRST_TS', 'min'), last_ts=('LAST_TS', 'max'))
    jornadas['HORAS_ACTIVAS'] = (pd.to_datetime(jornadas['last_ts']) - pd.to_datetime(jornadas['first_ts'])).dt.total_seconds() / 3600.0
    prom_hrs = jornadas[jornadas['HORAS_ACTIVAS'] > 0]['HORAS_ACTIVAS'].mean()
    c4.metric("Conexión Diaria Prom.", f"{prom_hrs:.1f} hrs" if pd.notnull(prom_hrs) else "N/A")

    st.divider()
    st.subheader("📝 Informe de Correcciones")
    tot_corr = int(df_filtrado['TOTAL_CORREGIDO'].sum())
    
    if tot_corr > 0:
        grp_col = 'USUARIO' if usuario_sel == "Todos" else 'MODULO'
        df_corr = df_filtrado.groupby(grp_col, observed=False)['TOTAL_CORREGIDO'].sum().reset_index()
        st.dataframe(df_corr[df_corr['TOTAL_CORREGIDO'] > 0].sort_values('TOTAL_CORREGIDO', ascending=False), use_container_width=True)
    else:
        st.info("No se registran correcciones en el periodo seleccionado.")

    st.divider()
    st.subheader("Visualizaciones")
    g1, g2 = st.columns(2)
    
    with g1:
        st.markdown("**Distribución tipo de captura**")
        df_mod = df_filtrado.groupby('MODULO', observed=False)['TOTAL_SUM'].sum().reset_index()
        if not df_mod.empty and df_mod['TOTAL_SUM'].sum() > 0:
            fig_pie = px.pie(df_mod, names='MODULO', values='TOTAL_SUM', hole=0.4, color_discrete_sequence=px.colors.qualitative.Safe)
            fig_pie.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10), legend=dict(orientation="h", y=-0.1))
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("Sin importes para graficar.")
            
    with g2:
        st.markdown("**Volumen de Actividad Diaria**")
        df_tend = df_filtrado.groupby(['DIA', 'MODULO'], observed=False).size().reset_index(name='OPERACIONES')
        if not df_tend.empty:
            fig_line = px.line(df_tend, x='DIA', y='OPERACIONES', color='MODULO', markers=True, color_discrete_sequence=px.colors.qualitative.Safe)
            fig_line.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10), xaxis_title=None, legend=dict(orientation="h", y=-0.1))
            st.plotly_chart(fig_line, use_container_width=True)
        else:
            st.info("Sin actividad diaria para graficar.")

    st.divider()
    st.subheader("📋 Últimos Movimientos Capturados")
    df_tabla = df_filtrado.sort_values('FIRST_TS', ascending=False).head(200)
    cols_show = [c for c in ['FIRST_TS', 'USUARIO', 'MODULO', 'TOTAL_SUM', 'TOTAL_CORREGIDO'] if c in df_tabla.columns]

    if cols_show:
        csv = safe_download_csv(df_filtrado.sort_values('FIRST_TS', ascending=False)[cols_show], f"auditoria_{usuario_sel}_{f_inicio}_al_{f_fin}.csv")
        st.download_button("Descargar Excel", data=csv, file_name=f"auditoria_{usuario_sel}_{f_inicio}_al_{f_fin}.csv", mime="text/csv", use_container_width=True)
        st.dataframe(df_tabla[cols_show], use_container_width=True, hide_index=True)

# -------------------------
# Punto de Entrada Principal
# -------------------------
if __name__ == "__main__":
    create_audit_table()
    mostrar_pestana_auditoria_usuarios()