import streamlit as st
import pandas as pd
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import text
from db_utils import obtener_engine_maestro, cargar_datos_optimizados

def normalizar(valor):
    return str(valor).strip().upper() if valor else ""

def obtener_opciones(df, columna, defecto=None):
    if df is not None and not df.empty and columna in df.columns:
        return sorted(list(df[columna].dropna().unique()))
    return [defecto] if defecto else []

def obtener_opciones_frecuentes(df, columna, marca=None, defecto=None):
    if df is not None and not df.empty and columna in df.columns:
        df_target = df
        if marca and 'MARCA' in df.columns:
            df_marca = df[df['MARCA'] == marca]
            if not df_marca.empty:
                df_target = df_marca
        frecuentes = df_target[columna].value_counts().index.tolist()
        return frecuentes
    return [defecto] if defecto else []

def mostrar_historial(df, filtro_col, filtro_val):
    if df is None or df.empty:
        st.info("Sin registros previos.")
        return
    df_f = df[df[filtro_col] == filtro_val] if filtro_col in df.columns else df
    if df_f.empty:
        st.info("Sin registros previos para la selección.")
        return

    df_hist = df_f.sort_values(by='FECHA', ascending=False).drop_duplicates(subset=['COSTO'], keep='first')
    cols = [c for c in ['FECHA', filtro_col, 'TIPO', 'PROVEEDOR', 'COSTO'] if c in df_hist.columns]
    
    st.dataframe(df_hist[cols].head(10), hide_index=True, use_container_width=True)
    st.caption("Mostrando los últimos 10 precios.")

def mostrar_modulo_traspasos():
    st.header("🔄 Sistema de Traspasos/Préstamos")
    st.markdown("Transfiere entre sucursales.")
    
    st.session_state.setdefault("pending_traspaso", None)
    st.session_state.setdefault("log_sesion", [])
    
    operador_actual = normalizar(st.session_state.get("usuario_actual", "ANÓNIMO"))
    engine = obtener_engine_maestro()
    anio_actual = datetime.now().year
    
    # IMPORTANTE: Aquí regresamos a la forma original de cargar datos de tu base
    _, df_insumos, df_gastos = cargar_datos_optimizados(anio_actual)
    
    tipo_traspaso = st.radio("¿Qué deseas traspasar?", ["Insumos", "Gastos"], horizontal=True)
    st.markdown("---")
    
    marcas_ins = obtener_opciones(df_insumos, 'MARCA')
    marcas_gas = obtener_opciones(df_gastos, 'MARCA')
    marcas_existentes = sorted(list(set(marcas_ins + marcas_gas)))
    
    if not marcas_existentes:
        st.warning("No hay datos suficientes en las tablas de Insumos/Gastos para cargar las sucursales.")
        return

    col_o, col_d, col_f = st.columns([2, 2, 2])
    with col_o:
        marca_origen = st.selectbox("MARCA ORIGEN", marcas_existentes, key="marca_out")
    with col_d:
        marca_destino = st.selectbox("MARCA DESTINO", marcas_existentes, index=1 if len(marcas_existentes)>1 else 0, key="marca_in")
    with col_f:
        fecha_traspaso = st.date_input("🗓️ Fecha del Traspaso", datetime.now())
        
    if marca_origen == marca_destino:
        st.warning("⚠️ La sucursal de origen y destino no pueden ser la misma.")
        return

    dia_semana = ["LUNES","MARTES","MIÉRCOLES","JUEVES","VIERNES","SÁBADO","DOMINGO"][fecha_traspaso.weekday()]

    st.subheader("Paso 1 — Datos del movimiento")
    
    with st.form(key="traspaso_form"):
        concepto_sel = "-- Seleccionar --"
        tipo_sel = None
        cat_sel = None
        rec_sel = None
        ultimo_costo = 0.0

        if tipo_traspaso == "Insumos":
            ins_lista = obtener_opciones_frecuentes(df_insumos, 'INSUMO', marca=marca_origen)
            concepto_sel = st.selectbox("Selecciona el Insumo (ordenado por frecuencia):", ["-- Seleccionar --"] + ins_lista)
            
            if concepto_sel != "-- Seleccionar --":
                df_filtro = df_insumos[df_insumos['INSUMO'] == concepto_sel]
                tipo_sel = st.selectbox("Tipo / Presentación:", obtener_opciones(df_filtro, 'TIPO'))
                if not df_filtro.empty:
                    ultimo_costo = float(df_filtro.sort_values(by='FECHA', ascending=False)['COSTO'].iloc[0])
                    
        else: # Gastos
            gasto_lista = obtener_opciones_frecuentes(df_gastos, 'GASTO DE', marca=marca_origen)
            concepto_sel = st.selectbox("Selecciona el Concepto de Gasto:", ["-- Seleccionar --"] + gasto_lista)
            
            if concepto_sel != "-- Seleccionar --":
                df_filtro = df_gastos[df_gastos['GASTO DE'] == concepto_sel]
                tipo_sel = st.selectbox("Tipo:", obtener_opciones(df_filtro, 'TIPO', "OPERATIVO"))
                cat_sel = st.selectbox("Categoría:", obtener_opciones(df_filtro, 'CATEGORÍA'))
                rec_sel = st.selectbox("Recurrencia:", obtener_opciones(df_filtro, 'RECURRENCIA', "VARIABLE"))
                if not df_filtro.empty:
                    ultimo_costo = float(df_filtro.sort_values(by='FECHA', ascending=False)['COSTO'].iloc[0])

        col1, col2 = st.columns(2)
        cantidad = col1.number_input("Cantidad a traspasar (UNIDAD):", min_value=0.01, value=1.00, step=0.01)
        costo_unit = col2.number_input("Costo Unitario ($):", min_value=0.0, value=ultimo_costo, step=1.0)
        
        btn_siguiente = st.form_submit_button("Siguiente — Revisar resumen")

    if btn_siguiente:
        if concepto_sel == "-- Seleccionar --":
            st.error("Por favor, selecciona un concepto válido antes de continuar.")
        else:
            total_traspaso = cantidad * costo_unit
            st.session_state["pending_traspaso"] = {
                "tipo_traspaso": tipo_traspaso,
                "concepto": concepto_sel,
                "tipo": tipo_sel,
                "cat": cat_sel,
                "rec": rec_sel,
                "cantidad": cantidad,
                "costo_unit": costo_unit,
                "total": total_traspaso,
                "proveedor_texto": f"{marca_origen} A {marca_destino}"
            }

    if st.session_state.get("pending_traspaso"):
        pending = st.session_state["pending_traspaso"]
        st.markdown("---")
        st.subheader("Paso 2 — Resumen y confirmación")
        
        st.info(
            f"**Ruta:** {marca_origen} ➔ {marca_destino}  
"
            f"**Concepto:** {pending['concepto']} ({pending['tipo']})  
"
            f"**Cant:** {pending['cantidad']} | **Costo U:** ${pending['costo_unit']:.2f} | **Total:** ${pending['total']:.2f}"
        )

        col_confirm, col_cancel = st.columns([1, 1])
        with col_confirm:
            if st.button("✅ Confirmar Traspaso", type="primary", use_container_width=True):
                forma_pago_traspaso = "TRASPASO"
                try:
                    with engine.begin() as conn:
                        if pending["tipo_traspaso"] == "Insumos":
                            query_ins = text('''
                                INSERT INTO insumos 
                                ("FECHA", "INSUMO", "TIPO", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "DÍA", "USUARIO", "MARCA", "FORMA PAGO") 
                                VALUES 
                                (:f, :ins, :t, :p, :u, :c, :tot, :d, :user, :m, :fp)
                            ''')
                            conn.execute(query_ins, {
                                "f": str(fecha_traspaso), "ins": pending["concepto"], "t": pending["tipo"], "p": pending["proveedor_texto"],
                                "u": -pending["cantidad"], "c": pending["costo_unit"], "tot": -pending["total"], "d": dia_semana,
                                "user": operador_actual, "m": marca_origen, "fp": forma_pago_traspaso
                            })
                            conn.execute(query_ins, {
                                "f": str(fecha_traspaso), "ins": pending["concepto"], "t": pending["tipo"], "p": pending["proveedor_texto"],
                                "u": pending["cantidad"], "c": pending["costo_unit"], "tot": pending["total"], "d": dia_semana,
                                "user": operador_actual, "m": marca_destino, "fp": forma_pago_traspaso
                            })
                        else: # Gastos
                            query_gas = text('''
                                INSERT INTO gastos 
                                ("FECHA", "GASTO DE", "TIPO", "CATEGORÍA", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "DÍA", "RECURRENCIA", "USUARIO", "MARCA", "FORMA PAGO") 
                                VALUES 
                                (:f, :g, :t, :c, :p, :u, :co, :tot, :d, :rec, :user, :m, :fp)
                            ''')
                            conn.execute(query_gas, {
                                "f": str(fecha_traspaso), "g": pending["concepto"], "t": pending["tipo"], "c": pending["cat"], "p": pending["proveedor_texto"],
                                "u": -pending["cantidad"], "co": pending["costo_unit"], "tot": -pending["total"], "d": dia_semana, "rec": pending["rec"],
                                "user": operador_actual, "m": marca_origen, "fp": forma_pago_traspaso
                            })
                            conn.execute(query_gas, {
                                "f": str(fecha_traspaso), "g": pending["concepto"], "t": pending["tipo"], "c": pending["cat"], "p": pending["proveedor_texto"],
                                "u": pending["cantidad"], "co": pending["costo_unit"], "tot": pending["total"], "d": dia_semana, "rec": pending["rec"],
                                "user": operador_actual, "m": marca_destino, "fp": forma_pago_traspaso
                            })

                    st.success("✅ Traspaso ejecutado correctamente.")
                    st.session_state["log_sesion"].insert(0, {
                        "Hora": datetime.now().strftime("%H:%M:%S"),
                        "Concepto": pending["concepto"],
                        "Cant": pending["cantidad"],
                        "Total": f"${pending['total']:.2f}",
                        "Ruta": f"{marca_origen} -> {marca_destino}"
                    })
                    st.session_state["pending_traspaso"] = None
                    cargar_datos_optimizados.clear(anio_actual)
                    time.sleep(1.5)
                    st.rerun()

                except Exception as e:
                    st.error(f"Error al escribir en base de datos: {e}")

        with col_cancel:
            if st.button("❌ Cancelar", use_container_width=True):
                st.session_state["pending_traspaso"] = None
                st.rerun()

    if st.session_state["log_sesion"]:
        st.markdown("---")
        with st.expander("📋 Traspasos realizados en esta sesión", expanded=False):
            st.dataframe(pd.DataFrame(st.session_state["log_sesion"]), use_container_width=True, hide_index=True)

    st.markdown("---")
    concepto_a_buscar = None
    if st.session_state.get("pending_traspaso"):
        concepto_a_buscar = st.session_state["pending_traspaso"]["concepto"]
    elif 'concepto_sel' in locals() and concepto_sel != "-- Seleccionar --":
        concepto_a_buscar = concepto_sel

    if concepto_a_buscar:
        st.subheader("Historial de costos")
        target_df = df_insumos if tipo_traspaso == "Insumos" else df_gastos
        target_col = 'INSUMO' if tipo_traspaso == "Insumos" else 'GASTO DE'
        mostrar_historial(target_df, target_col, concepto_a_buscar)
    else:
        st.info("💡 Selecciona un insumo o gasto para ver su historial de precios recientes.")

if __name__ == "__main__":
    mostrar_modulo_traspasos()