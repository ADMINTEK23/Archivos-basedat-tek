import os
import logging
from decimal import Decimal, ROUND_HALF_UP, getcontext
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Any
from uuid import uuid4
from datetime import datetime
import pytz

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError, DBAPIError

# ---------------------------
# Configuración global
# ---------------------------
getcontext().prec = 28
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("cortedia")

# ---------------------------
# Utilidades Decimal / Formato / Opciones
# ---------------------------
def to_decimal(value) -> Decimal:
    """Convierte el valor a Decimal con 2 decimales de precisión para cálculos financieros."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def display_2_dec(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"

def _param_decimal(d: Decimal) -> str:
    """Convierte Decimal a string para persistir en Postgres NUMERIC/DECIMAL."""
    return format(d, 'f')

def normalizar(valor):
    return str(valor).strip().upper() if valor else ""

def obtener_opciones(df: pd.DataFrame, columna: str, defecto: Optional[str] = None) -> List[str]:
    if df is not None and not df.empty and columna in df.columns:
        return sorted(list(df[columna].dropna().unique()))
    return [defecto] if defecto else []

def obtener_opciones_frecuentes(df: pd.DataFrame, columna: str, marca: Optional[str] = None, defecto: Optional[str] = None) -> List[str]:
    """
    Ordena las opciones por frecuencia de uso en la marca seleccionada 
    para priorizar los insumos más comunes en la parte superior de la lista.
    """
    if df is not None and not df.empty and columna in df.columns:
        df_target = df
        if marca and 'MARCA' in df.columns:
            df_marca = df[df['MARCA'] == marca]
            if not df_marca.empty:
                df_target = df_marca
        frecuentes = df_target[columna].value_counts().index.tolist()
        return frecuentes
    return [defecto] if defecto else []

# ---------------------------
# DTO & Domain Validation
# ---------------------------
@dataclass(frozen=True, slots=True)
class TraspasoPayload:
    fecha: str
    insumo_o_gasto: str
    tipo: Optional[str]
    proveedor: str
    unidad: Decimal
    costo_unitario: Decimal
    total: Decimal
    dia: str
    usuario: str
    marca: str
    forma_pago: str
    referencia: str
    marca_destino_ref: Optional[str] = None
    recurrencia: Optional[str] = None
    categoria: Optional[str] = None

    def validate(self):
        if not self.insumo_o_gasto or self.insumo_o_gasto == "-- Seleccionar --":
            raise ValueError("Debe seleccionar un insumo o concepto válido.")
        if self.unidad == Decimal("0.00"):
            raise ValueError("La cantidad a traspasar no puede ser cero.")
        if self.costo_unitario < Decimal("0.00"):
            raise ValueError("El costo unitario no puede ser negativo.")
        if self.marca_destino_ref and self.marca == self.marca_destino_ref:
            raise ValueError("La sucursal de origen y destino no pueden ser la misma.")

# ---------------------------
# DB: Engine y carga de catálogos
# ---------------------------
@st.cache_resource
def obtener_engine_maestro() -> Engine:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("La variable de entorno DATABASE_URL no está configurada.")
    return create_engine(db_url, pool_size=5, max_overflow=10, pool_timeout=30, pool_recycle=1800, future=True)

@st.cache_data(ttl=600)
def cargar_catalogos_filtrados(marca_filter: Optional[str] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Carga catálogos de insumos y gastos.
    Si se pasa marca_filter, aplica WHERE para reducir volumen.
    """
    try:
        engine = obtener_engine_maestro()
        with engine.connect() as conn:
            q_insumos = "SELECT FECHA, INSUMO, TIPO, PROVEEDOR, COSTO, MARCA FROM insumos"
            q_gastos = "SELECT FECHA, \"GASTO DE\" as GASTO_DE, TIPO, CATEGORÍA, PROVEEDOR, COSTO, MARCA FROM gastos"
            if marca_filter:
                q_insumos += " WHERE MARCA = :marca"
                q_gastos += " WHERE MARCA = :marca"
                df_insumos = pd.read_sql(text(q_insumos), conn, params={"marca": marca_filter})
                df_gastos = pd.read_sql(text(q_gastos), conn, params={"marca": marca_filter})
            else:
                df_insumos = pd.read_sql(text(q_insumos), conn)
                df_gastos = pd.read_sql(text(q_gastos), conn)
            if "GASTO_DE" in df_gastos.columns:
                df_gastos.rename(columns={"GASTO_DE": "GASTO DE"}, inplace=True)
            return df_insumos, df_gastos
    except Exception:
        logger.exception("Error cargando catálogos")
        return pd.DataFrame(), pd.DataFrame()

def invalidar_cache_catalogos():
    """
    Invalidación global de cache. Streamlit no ofrece invalidación por clave pública,
    por lo que usamos clear() y recomendamos filtrar en las consultas para reducir impacto.
    """
    try:
        st.cache_data.clear()
        logger.info("Cache de catálogos invalidada.")
    except Exception:
        logger.exception("No se pudo invalidar cache de catálogos.")

# ---------------------------
# Persistencia en Lote (Batch Insert) con conversión segura
# ---------------------------
def crear_traspaso_insumo(engine: Engine, payload_out: TraspasoPayload, payload_in: TraspasoPayload):
    query = text('''
        INSERT INTO insumos 
        ("FECHA", "INSUMO", "TIPO", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "DÍA", "USUARIO", "MARCA", "FORMA PAGO", "REFERENCIA") 
        VALUES 
        (:f, :ins, :t, :p, :u, :c, :tot, :d, :user, :m, :fp, :ref)
    ''')
    params_batch = [
        {
            "f": p.fecha, "ins": p.insumo_o_gasto, "t": p.tipo, "p": p.proveedor,
            "u": _param_decimal(p.unidad), "c": _param_decimal(p.costo_unitario),
            "tot": _param_decimal(p.total), "d": p.dia, "user": p.usuario,
            "m": p.marca, "fp": p.forma_pago, "ref": p.referencia
        }
        for p in [payload_out, payload_in]
    ]
    try:
        with engine.begin() as conn:
            conn.execute(query, params_batch)
    except Exception:
        logger.exception("Error al insertar traspaso insumo")
        raise

def crear_traspaso_gasto(engine: Engine, payload_out: TraspasoPayload, payload_in: TraspasoPayload):
    query = text('''
        INSERT INTO gastos 
        ("FECHA", "GASTO DE", "TIPO", "CATEGORÍA", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "DÍA", "RECURRENCIA", "USUARIO", "MARCA", "FORMA PAGO", "REFERENCIA") 
        VALUES 
        (:f, :g, :t, :cat, :p, :u, :co, :tot, :d, :rec, :user, :m, :fp, :ref)
    ''')
    params_batch = [
        {
            "f": p.fecha, "g": p.insumo_o_gasto, "t": p.tipo, "cat": p.categoria, "p": p.proveedor,
            "u": _param_decimal(p.unidad), "co": _param_decimal(p.costo_unitario),
            "tot": _param_decimal(p.total), "d": p.dia, "rec": p.recurrencia,
            "user": p.usuario, "m": p.marca, "fp": p.forma_pago, "ref": p.referencia
        }
        for p in [payload_out, payload_in]
    ]
    try:
        with engine.begin() as conn:
            conn.execute(query, params_batch)
    except Exception:
        logger.exception("Error al insertar traspaso gasto")
        raise

# ---------------------------
# UI: Historial con paginación simple
# ---------------------------
def mostrar_historial(df: pd.DataFrame, filtro_col: str, filtro_val: str, page: int = 0, page_size: int = 10):
    if df is None or df.empty:
        st.info("Sin registros previos.")
        return
    df_f = df[df[filtro_col] == filtro_val] if filtro_col in df.columns else df
    if df_f.empty:
        st.info("Sin registros previos para la selección.")
        return

    df_hist = df_f.sort_values(by='FECHA', ascending=False).drop_duplicates(subset=['COSTO'], keep='first')
    cols = [c for c in ['FECHA', filtro_col, 'TIPO', 'PROVEEDOR', 'COSTO'] if c in df_hist.columns]

    start = page * page_size
    end = start + page_size
    st.dataframe(df_hist[cols].iloc[start:end], hide_index=True, use_container_width=True)
    col1, col2, col3 = st.columns([1, 1, 6])
    with col1:
        if st.button("Anterior", key=f"hist_prev_{filtro_val}_{page}"):
            st.session_state[f"hist_page_{filtro_val}"] = max(0, page - 1)
    with col2:
        if st.button("Siguiente", key=f"hist_next_{filtro_val}_{page}"):
            st.session_state[f"hist_page_{filtro_val}"] = page + 1
    with col3:
        st.write(f"Mostrando {start + 1} - {min(end, len(df_hist))} de {len(df_hist)}")

# ---------------------------
# Interfaz principal (flujo en dos pasos, sin rerun forzado)
# ---------------------------
def mostrar_modulo_traspasos():
    st.header("🔄 Sistema de Traspasos/Préstamos cortedia")
    st.markdown("Formulario en dos pasos: completa los datos, revisa el resumen y confirma. Usa 'Actualizar catálogos' para recargar datos tras cambios.")

    # Inicialización de estado
    st.session_state.setdefault("pending_traspaso", None)
    st.session_state.setdefault("form_version", 0)
    st.session_state.setdefault("session_traspasos_counter", 0)
    st.session_state.setdefault("log_sesion", [])

    operador_actual = normalizar(st.session_state.get("usuario_actual", "ANÓNIMO"))
    tz_cdmx = pytz.timezone("America/Mexico_City")
    hoy = datetime.now(tz_cdmx).date()

    # Cargar catálogos
    df_insumos, df_gastos = cargar_catalogos_filtrados()

    tipo_traspaso = st.radio("¿Qué deseas traspasar?", ["Insumos", "Gastos"], horizontal=True)
    st.markdown("---")

    # Marcas y fecha persistentes
    marcas_ins = obtener_opciones(df_insumos, 'MARCA')
    marcas_gas = obtener_opciones(df_gastos, 'MARCA')
    marcas_existentes = sorted(list(set(marcas_ins + marcas_gas)))
    if not marcas_existentes:
        st.warning("No hay marcas/sucursales disponibles en los catálogos.")
        return

    col_o, col_d, col_f = st.columns([2, 2, 2])
    with col_o:
        marca_origen = st.selectbox("MARCA ORIGEN", marcas_existentes, key="persistent_marca_origen")
    with col_d:
        default_idx = 1 if len(marcas_existentes) > 1 else 0
        marca_destino = st.selectbox("MARCA DESTINO", marcas_existentes, index=default_idx, key="persistent_marca_destino")
    with col_f:
        fecha_traspaso = st.date_input("🗓️ Fecha del Traspaso", hoy, key="persistent_fecha_traspaso")

    if marca_origen == marca_destino:
        st.warning("⚠️ La sucursal de origen y destino no pueden ser la misma.")
        return

    dia_semana = ["LUNES","MARTES","MIÉRCOLES","JUEVES","VIERNES","SÁBADO","DOMINGO"][fecha_traspaso.weekday()]

    try:
        engine = obtener_engine_maestro()
    except Exception:
        st.error("No se pudo conectar a la base de datos. Revisa DATABASE_URL.")
        logger.exception("Error obteniendo engine")
        return

    v_key = st.session_state["form_version"]

    # Paso 1: formulario
    st.subheader("Paso 1 — Datos del movimiento")
    with st.form(key=f"traspaso_form_{v_key}"):
        cat_sel = None
        rec_sel = None

        if tipo_traspaso == "Insumos":
            ins_lista = obtener_opciones_frecuentes(df_insumos, 'INSUMO', marca=marca_origen)
            ins_sel = st.selectbox("Selecciona el Insumo (ordenado por frecuencia):", ["-- Seleccionar --"] + ins_lista, key=f"ins_sel_{v_key}")
            tipo_sel = None
            ultimo_costo = Decimal("0.00")
            if ins_sel != "-- Seleccionar --":
                df_filtro = df_insumos[df_insumos['INSUMO'] == ins_sel]
                tipo_sel = st.selectbox("Tipo / Presentación:", obtener_opciones(df_filtro, 'TIPO'), key=f"tipo_sel_{v_key}")
                if not df_filtro.empty and 'COSTO' in df_filtro.columns:
                    ultimo_costo = to_decimal(df_filtro.sort_values(by='FECHA', ascending=False)['COSTO'].iloc[0])
        else:
            gasto_lista = obtener_opciones_frecuentes(df_gastos, 'GASTO DE', marca=marca_origen)
            ins_sel = st.selectbox("Selecciona el Concepto (ordenado por frecuencia):", ["-- Seleccionar --"] + gasto_lista, key=f"ins_sel_{v_key}")
            tipo_sel = None
            ultimo_costo = Decimal("0.00")
            if ins_sel != "-- Seleccionar --":
                df_filtro = df_gastos[df_gastos['GASTO DE'] == ins_sel]
                tipo_sel = st.selectbox("Tipo:", obtener_opciones(df_filtro, 'TIPO', "OPERATIVO"), key=f"tipo_sel_{v_key}")
                cat_sel = st.selectbox("Categoría:", obtener_opciones(df_filtro, 'CATEGORÍA'), key=f"cat_sel_{v_key}")
                rec_sel = st.selectbox("Recurrencia:", obtener_opciones(df_filtro, 'RECURRENCIA', "VARIABLE"), key=f"rec_sel_{v_key}")
                if not df_filtro.empty and 'COSTO' in df_filtro.columns:
                    ultimo_costo = to_decimal(df_filtro.sort_values(by='FECHA', ascending=False)['COSTO'].iloc[0])

        col1, col2 = st.columns(2)
        with col1:
            cantidad = st.number_input("Cantidad a traspasar (UNIDAD):", min_value=0.0, value=1.00, format="%.2f", key=f"cant_{v_key}")
        with col2:
            costo_unit = st.number_input("Costo Unitario ($):", min_value=0.0, value=float(ultimo_costo), format="%.2f", key=f"costo_{v_key}")

        next_step = st.form_submit_button("Siguiente — Revisar resumen")

    # Empaquetado y Paso 2
    if next_step:
        cantidad_d = to_decimal(cantidad)
        costo_d = to_decimal(costo_unit)
        total = (cantidad_d * costo_d).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        referencia = str(uuid4())
        proveedor_texto = f"{marca_origen} A {marca_destino}"
        forma_pago = "TRASPASO"

        payload_out = TraspasoPayload(
            fecha=str(fecha_traspaso),
            insumo_o_gasto=ins_sel,
            tipo=tipo_sel,
            proveedor=proveedor_texto,
            unidad=-cantidad_d,
            costo_unitario=costo_d,
            total=-total,
            dia=dia_semana,
            usuario=operador_actual,
            marca=marca_origen,
            forma_pago=forma_pago,
            referencia=referencia,
            marca_destino_ref=marca_destino,
            recurrencia=rec_sel,
            categoria=cat_sel
        )
        payload_in = TraspasoPayload(
            fecha=str(fecha_traspaso),
            insumo_o_gasto=ins_sel,
            tipo=tipo_sel,
            proveedor=proveedor_texto,
            unidad=cantidad_d,
            costo_unitario=costo_d,
            total=total,
            dia=dia_semana,
            usuario=operador_actual,
            marca=marca_destino,
            forma_pago=forma_pago,
            referencia=referencia,
            marca_destino_ref=marca_origen,
            recurrencia=rec_sel,
            categoria=cat_sel
        )

        try:
            payload_out.validate()
            payload_in.validate()
            st.session_state["pending_traspaso"] = {"out": payload_out, "in": payload_in, "tipo": tipo_traspaso}
        except ValueError as ve:
            st.error(f"Validación de datos: {ve}")
            st.session_state["pending_traspaso"] = None

    # Resumen y confirmación
    if st.session_state.get("pending_traspaso"):
        pending = st.session_state["pending_traspaso"]
        p_out: TraspasoPayload = pending["out"]
        p_in: TraspasoPayload = pending["in"]

        st.subheader("Paso 2 — Resumen y confirmación")
        resumen = {
            "Referencia": p_out.referencia,
            "Fecha": p_out.fecha,
            "Usuario": p_out.usuario,
            "Origen": p_out.marca,
            "Destino": p_in.marca,
            "Concepto": p_out.insumo_o_gasto,
            "Tipo": p_out.tipo or "-",
            "Cantidad": display_2_dec(abs(p_in.unidad)),
            "Costo unitario": display_2_dec(p_in.costo_unitario),
            "Total": display_2_dec(p_in.total)
        }
        st.table(pd.DataFrame([resumen]).T.rename(columns={0: "Valor"}))

        col_confirm, col_cancel = st.columns([1, 1])
        with col_confirm:
            if st.button("Confirmar traspaso", type="primary"):
                try:
                    p_out.validate()
                    p_in.validate()
                    with st.spinner("Ejecutando traspaso en la base de datos..."):
                        if pending["tipo"] == "Insumos":
                            crear_traspaso_insumo(engine, p_out, p_in)
                        else:
                            crear_traspaso_gasto(engine, p_out, p_in)
                        invalidar_cache_catalogos()

                    logger.info("Traspaso ejecutado con éxito", extra={"ref": p_out.referencia, "user": p_out.usuario})
                    st.success(f"✅ Traspaso exitoso. Referencia: {p_out.referencia}")

                    # Bitácora de sesión
                    log_entry = {
                        "Hora": datetime.now(tz_cdmx).strftime("%H:%M:%S"),
                        "Concepto": p_out.insumo_o_gasto,
                        "Cantidad": display_2_dec(abs(p_in.unidad)),
                        "Total ($)": display_2_dec(p_in.total),
                        "Origen": p_out.marca,
                        "Destino": p_in.marca,
                        "Ref": p_out.referencia[:8]
                    }
                    st.session_state["log_sesion"].insert(0, log_entry)

                    # Reset controlado
                    st.session_state["pending_traspaso"] = None
                    st.session_state["session_traspasos_counter"] += 1
                    st.session_state["form_version"] += 1

                except IntegrityError:
                    logger.exception("IntegrityError al confirmar traspaso")
                    st.error("Error de integridad en la DB. Revisa los datos.")
                except OperationalError:
                    logger.exception("OperationalError al confirmar traspaso")
                    st.error("Error de conexión con la base de datos.")
                except DBAPIError:
                    logger.exception("DBAPIError al confirmar traspaso")
                    st.error("Error en la ejecución SQL de la base de datos.")
                except ValueError as ve:
                    st.error(f"Validación de backend: {ve}")
                except Exception:
                    logger.exception("Error inesperado al confirmar traspaso")
                    st.error("Ocurrió un error inesperado al registrar el traspaso.")

        with col_cancel:
            if st.button("Cancelar traspaso"):
                st.session_state["pending_traspaso"] = None
                st.info("Traspaso cancelado.")

    # Bitácora de sesión
    if st.session_state["log_sesion"]:
        st.markdown("---")
        with st.expander("📋 Bitácora de Traspasos Realizados en esta Sesión", expanded=False):
            st.dataframe(pd.DataFrame(st.session_state["log_sesion"]), use_container_width=True, hide_index=True)

    # Actualizar catálogos manualmente
    st.markdown("---")
    st.write("Si acabas de ejecutar un traspaso y no ves los cambios, pulsa actualizar catálogos.")
    if st.button("Actualizar catálogos"):
        invalidar_cache_catalogos()
        st.success("Catálogos marcados para recarga. Refresca la página si es necesario.")

    # Historial del concepto
    st.markdown("---")
    st.subheader("Historial del concepto")
    if 'ins_sel' in locals() and ins_sel != "-- Seleccionar --":
        page = st.session_state.get(f"hist_page_{ins_sel}", 0)
        target_df = df_insumos if tipo_traspaso == "Insumos" else df_gastos
        target_col = 'INSUMO' if tipo_traspaso == "Insumos" else 'GASTO DE'
        mostrar_historial(target_df, target_col, ins_sel, page=page)
    else:
        st.info("Selecciona un insumo/concepto arriba para ver su historial.")

# ---------------------------
# Punto de entrada
# ---------------------------
if __name__ == "__main__":
    st.set_page_config(page_title="Traspasos cortedia", layout="wide")
    mostrar_modulo_traspasos()