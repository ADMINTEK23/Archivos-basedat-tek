import streamlit as st
import pandas as pd
import time
from datetime import datetime
from sqlalchemy import text
from db_utils import obtener_engine_maestro, cargar_datos_optimizados

def normalizar(valor):
    return str(valor).strip().upper() if valor else ""

def obtener_opciones(df, columna, defecto=None):
    if df is not None and not df.empty and columna in df.columns:
        return sorted(list(df[columna].dropna().unique()))
    return [defecto] if defecto else []

def mostrar_modulo_traspasos():
    st.header("🔄 Sistema de Traspasos/Préstamos")
    st.markdown("Transfiere entre sucursales. El sistema generará automáticamente la salida y la entrada.")
    
    operador_actual = normalizar(st.session_state.get("usuario_actual", "ANÓNIMO"))
    engine = obtener_engine_maestro()
    anio_actual = datetime.now().year
    
    # Cargar catálogos
    _, df_insumos, df_gastos = cargar_datos_optimizados(anio_actual)
    
    tipo_traspaso = st.radio("¿Qué deseas traspasar?", ["Insumos", "Gastos"], horizontal=True)
    st.markdown("---")
    
    # --- 1. ORIGEN Y DESTINO ---
    col_o, col_d = st.columns(2)
    # Obtenemos las marcas de ambas bases para tener el catálogo completo
    marcas_existentes = sorted(list(set(obtener_opciones(df_insumos, 'MARCA') + obtener_opciones(df_gastos, 'MARCA'))))
    
    with col_o:
        marca_origen = st.selectbox("MARCA ORIGEN", marcas_existentes, key="marca_out")
    with col_d:
        marca_destino = st.selectbox("MARCA DESTINO", marcas_existentes, index=1 if len(marcas_existentes)>1 else 0, key="marca_in")
        
    if marca_origen == marca_destino:
        st.warning("⚠️ La sucursal de origen y destino son la misma. Por favor, selecciona sucursales distintas.")
        return

    st.markdown("---")
    
    # --- 2. FECHA DEL MOVIMIENTO ---
    fecha_traspaso = st.date_input("🗓️ Fecha del Traspaso", datetime.now())
    dia_semana = ["LUNES","MARTES","MIÉRCOLES","JUEVES","VIERNES","SÁBADO","DOMINGO"][fecha_traspaso.weekday()]

    # ==========================================
    # LÓGICA PARA INSUMOS
    # ==========================================
    if tipo_traspaso == "Insumos":
        st.subheader("📦 Detalles del Insumo")
        ins_lista = obtener_opciones(df_insumos, 'INSUMO')
        ins_sel = st.selectbox("Selecciona el Insumo:", ["-- Seleccionar --"] + ins_lista)
        
        if ins_sel != "-- Seleccionar --":
            df_filtro = df_insumos[df_insumos['INSUMO'] == ins_sel]
            tipos_lista = obtener_opciones(df_filtro, 'TIPO')
            tipo_sel = st.selectbox("Tipo / Presentación:", tipos_lista)
            
            # Buscar último costo registrado para sugerirlo
            ultimo_costo = 0.0
            if not df_filtro.empty:
                ultimo_costo = float(df_filtro.sort_values(by='FECHA', ascending=False)['COSTO'].iloc[0])
            
            col1, col2 = st.columns(2)
            cantidad = col1.number_input("Cantidad a traspasar (UNIDAD):", min_value=0.01, value=1.00, step=0.01)
            costo_unit = col2.number_input("Costo Unitario ($):", min_value=0.0, value=ultimo_costo, step=1.0)
            
            if st.button("🚀 EJECUTAR TRASPASO DE INSUMO", use_container_width=True, type="primary"):
                total_traspaso = cantidad * costo_unit
                proveedor_texto = f"{marca_origen} A {marca_destino}"
                forma_pago_traspaso = "TRASPASO"
                
                query_ins = text('''
                    INSERT INTO insumos 
                    ("FECHA", "INSUMO", "TIPO", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "DÍA", "USUARIO", "MARCA", "FORMA PAGO") 
                    VALUES 
                    (:f, :ins, :t, :p, :u, :c, :tot, :d, :user, :m, :fp)
                ''')
                
                try:
                    with engine.begin() as conn:
                        # 1. Movimiento de Salida (Negativo)
                        conn.execute(query_ins, {
                            "f": str(fecha_traspaso), "ins": ins_sel, "t": tipo_sel, "p": proveedor_texto,
                            "u": -cantidad, "c": costo_unit, "tot": -total_traspaso, "d": dia_semana,
                            "user": operador_actual, "m": marca_origen, "fp": forma_pago_traspaso
                        })
                        
                        # 2. Movimiento de Entrada (Positivo)
                        conn.execute(query_ins, {
                            "f": str(fecha_traspaso), "ins": ins_sel, "t": tipo_sel, "p": proveedor_texto,
                            "u": cantidad, "c": costo_unit, "tot": total_traspaso, "d": dia_semana,
                            "user": operador_actual, "m": marca_destino, "fp": forma_pago_traspaso
                        })
                        
                    st.success(f"✅ Traspaso exitoso: {cantidad} de {ins_sel} de {marca_origen} hacia {marca_destino}.")
                    cargar_datos_optimizados.clear(fecha_traspaso.year)
                    time.sleep(1.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"Error en la base de datos: {e}")

    # ==========================================
    # LÓGICA PARA GASTOS
    # ==========================================
    elif tipo_traspaso == "Gastos":
        st.subheader("💸 Detalles del Gasto")
        gasto_lista = obtener_opciones(df_gastos, 'GASTO DE')
        gasto_sel = st.selectbox("Selecciona el Concepto de Gasto:", ["-- Seleccionar --"] + gasto_lista)
        
        if gasto_sel != "-- Seleccionar --":
            df_filtro = df_gastos[df_gastos['GASTO DE'] == gasto_sel]
            tipo_sel = st.selectbox("Tipo:", obtener_opciones(df_filtro, 'TIPO', "OPERATIVO"))
            cat_sel = st.selectbox("Categoría:", obtener_opciones(df_filtro, 'CATEGORÍA'))
            rec_sel = st.selectbox("Recurrencia:", obtener_opciones(df_filtro, 'RECURRENCIA', "VARIABLE"))
            
            ultimo_costo = 0.0
            if not df_filtro.empty:
                ultimo_costo = float(df_filtro.sort_values(by='FECHA', ascending=False)['COSTO'].iloc[0])
            
            col1, col2 = st.columns(2)
            cantidad = col1.number_input("Cantidad a traspasar (UNIDAD):", min_value=0.01, value=1.00, step=0.01)
            costo_unit = col2.number_input("Costo Unitario ($):", min_value=0.0, value=ultimo_costo, step=1.0)
            
            if st.button("🚀 EJECUTAR TRASPASO DE GASTO", use_container_width=True, type="primary"):
                total_traspaso = cantidad * costo_unit
                proveedor_texto = f"{marca_origen} A {marca_destino}"
                forma_pago_traspaso = "TRASPASO"
                
                query_gas = text('''
                    INSERT INTO gastos 
                    ("FECHA", "GASTO DE", "TIPO", "CATEGORÍA", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "DÍA", "RECURRENCIA", "USUARIO", "MARCA", "FORMA PAGO") 
                    VALUES 
                    (:f, :g, :t, :c, :p, :u, :co, :tot, :d, :rec, :user, :m, :fp)
                ''')
                
                try:
                    with engine.begin() as conn:
                        # 1. Movimiento de Salida (Negativo)
                        conn.execute(query_gas, {
                            "f": str(fecha_traspaso), "g": gasto_sel, "t": tipo_sel, "c": cat_sel, "p": proveedor_texto,
                            "u": -cantidad, "co": costo_unit, "tot": -total_traspaso, "d": dia_semana, "rec": rec_sel,
                            "user": operador_actual, "m": marca_origen, "fp": forma_pago_traspaso
                        })
                        
                        # 2. Movimiento de Entrada (Positivo)
                        conn.execute(query_gas, {
                            "f": str(fecha_traspaso), "g": gasto_sel, "t": tipo_sel, "c": cat_sel, "p": proveedor_texto,
                            "u": cantidad, "co": costo_unit, "tot": total_traspaso, "d": dia_semana, "rec": rec_sel,
                            "user": operador_actual, "m": marca_destino, "fp": forma_pago_traspaso
                        })
                        
                    st.success(f"✅ Traspaso exitoso: {cantidad} de {gasto_sel} de {marca_origen} hacia {marca_destino}.")
                    cargar_datos_optimizados.clear(fecha_traspaso.year)
                    time.sleep(1.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"Error en la base de datos: {e}")
