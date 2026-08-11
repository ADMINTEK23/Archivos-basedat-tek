import streamlit as st
import pandas as pd
import numpy as np
import datetime
from sqlalchemy import text
from threading import Thread
from db_utils import obtener_engine_maestro, cargar_datos_optimizados, cargar_catalogos_historicos

# --- CSS PERSONALIZADO AVANZADO PARA FORZAR ESTIRAMIENTO LATERAL ABSOLUTO ---
st.markdown(
    """
    <style>
    /* Elimina márgenes de la página web */
    .block-container { padding-left: 0.5rem !important; padding-right: 0.5rem !important; max-width: 99% !important; }
    .stDataFrame, .stDataEditor { width: 100% !important; }
    
    /* Rompe los límites de ancho ocultos dentro de los componentes internos de las tablas de Streamlit */
    [data-testid="stDataEditor"] > div { width: 100% !important; max-width: 100% !important; }
    [data-testid="stDataEditor"] [data-testid="stTable"] { width: 100% !important; table-layout: fixed !important; }
    
    .stColumn { padding: 0 0.25rem !important; }
    </style>
    """,
    unsafe_allow_html=True
)

# ==========================================
# 1. FUNCIONES AUXILIARES (DRY)
# ==========================================

def obtener_opciones_unicas(df, columna, valor_por_defecto=None, descendente=False):
    if df is not None and not df.empty and columna in df.columns:
        valores = list(df[columna].dropna().unique())
        try:
            meses_map = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12}
            valores_ordenados = sorted(
                valores,
                key=lambda x: datetime.datetime.strptime(f"{x.split('-')[0].strip()}-{meses_map[x.split('-')[1].strip()]}", "%Y-%m"),
                reverse=descendente
            )
            return valores_ordenados
        except Exception:
            return sorted(valores, reverse=descendente)
    return [valor_por_defecto] if valor_por_defecto else []

def mostrar_ticket(datos_dict):
    df_ticket = pd.DataFrame({"Concepto Contable": list(datos_dict.keys()), "Detalle del Registro": list(datos_dict.values())})
    st.dataframe(df_ticket, use_container_width=True, hide_index=True)

def normalizar(valor):
    return str(valor).strip().upper() if pd.notna(valor) and valor != "" else ""

def _clear_cache_background(years):
    """Limpia la caché en segundo plano para no bloquear la UI."""
    try:
        for y in years: 
            cargar_datos_optimizados.clear(y)
        cargar_catalogos_historicos.clear() 
    except Exception:
        pass

def obtener_dia_semana(fecha_obj):
    dias = ["LUNES","MARTES","MIÉRCOLES","JUEVES","VIERNES","SÁBADO","DOMINGO"]
    if isinstance(fecha_obj, pd.Timestamp) or isinstance(fecha_obj, datetime.datetime) or isinstance(fecha_obj, datetime.date):
        return dias[fecha_obj.weekday()]
    return ""

def calcular_turno_venta(hora_obj):
    hora_num = (hora_obj.hour * 3600 + hora_obj.minute * 60 + hora_obj.second) / 86400.0
    if hora_num < 0.48: 
        return "MAÑANA", hora_num
    elif hora_num < 0.56: 
        return "MEDIO DÍA", hora_num
    elif hora_num < 0.83: 
        return "TARDE", hora_num
    return "NOCHE", hora_num


# ==========================================
# 2. MÓDULOS DE INTERFAZ (DESACOPLADOS)
# ==========================================

def renderizar_masivo_insumos(df_insumos, operador, engine):
    st.subheader("Selección de Insumos")
    col_f1, col_f2 = st.columns(2)
    with col_f1: 
        fecha_ingreso = st.date_input("Seleccionar Fecha de Ingreso:", datetime.datetime.now(), key="fecha_ins_masiva")
    lista_meses_hist = obtener_opciones_unicas(df_insumos, 'AÑO_MES', descendente=True)
    with col_f2: 
        mes_hist_sel = st.selectbox("Filtrar Catálogo por Mes Histórico:", ["-- Todos los Meses --"] + lista_meses_hist, key="mes_hist_ins")
    
    if mes_hist_sel != "-- Todos los Meses --":
        filtro_mes = df_insumos['AÑO_MES'] == mes_hist_sel
    else:
        filtro_mes = pd.Series(True, index=df_insumos.index)

    df_prov_filtrados = df_insumos[filtro_mes]
    prov_sel = st.selectbox("PROVEEDOR", ["-- Seleccionar Proveedor --"] + obtener_opciones_unicas(df_prov_filtrados, 'PROVEEDOR'), key="prov_ins_sel")
    
    df_catalogo_ins = df_insumos[filtro_mes]
    if prov_sel != "-- Seleccionar Proveedor --": 
        df_catalogo_ins = df_catalogo_ins[df_catalogo_ins['PROVEEDOR'] == prov_sel]
    
    if not df_catalogo_ins.empty:
        df_catalogo_ins = df_catalogo_ins.sort_values(by='FECHA', ascending=False).drop_duplicates(subset=['INSUMO', 'TIPO'])
        
        df_editor_ins = pd.DataFrame({
            "Ingresar": False, 
            "Insumo": df_catalogo_ins['INSUMO'], 
            "Tipo": df_catalogo_ins['TIPO'], 
            "Marca": df_catalogo_ins['MARCA'].fillna("General"), 
            "Forma Pago": "EFECTIVO",  
            "Unidad": 1.0, 
            "Costo Unitario": df_catalogo_ins['COSTO'].fillna(0.0)
        }).reset_index(drop=True)
        
        key_dinamica_ins = f"editor_insumos_masivo_v{st.session_state.get('version_tabla_insumos', 0)}"
        
        datos_editados_ins = st.data_editor(
            df_editor_ins, 
            column_config={
                "Ingresar": st.column_config.CheckboxColumn("✔️", help="Marca para guardar", default=False, width=40), 
                "Insumo": st.column_config.TextColumn("INSUMO", disabled=True, width="small"), 
                "Tipo": st.column_config.TextColumn("TIPO", disabled=True, width="small"), 
                "Marca": st.column_config.SelectboxColumn("MARCA", options=obtener_opciones_unicas(df_insumos, 'MARCA', "General"), required=True, width="small"), 
                "Forma Pago": st.column_config.SelectboxColumn("FORMA PAGO", options=["EFECTIVO", "TARJETA", "TRANSFERENCIA","NO ESPECIFICADO"], required=True, width="small"),
                "Unidad": st.column_config.NumberColumn("UNIDAD", min_value=0.0, step=0.01, format="%.2f", required=True, width=70), 
                "Costo Unitario": st.column_config.NumberColumn("COSTO", min_value=0.0, format="$%.2f", required=True, width=80)
            }, 
            column_order=["Ingresar", "Insumo", "Tipo", "Unidad", "Costo Unitario", "Marca", "Forma Pago"],
            num_rows="fixed", 
            hide_index=True, 
            use_container_width=True,
            height=600, 
            key=key_dinamica_ins
        )
        
        if st.button("💾 GUARDAR SELECCIÓN DE INSUMOS", type="primary", use_container_width=True):
            filas = datos_editados_ins[datos_editados_ins["Ingresar"] == True]
            if prov_sel == "-- Seleccionar Proveedor --": 
                st.error("❌ Selecciona un Proveedor.")
            elif filas.empty: 
                st.warning("⚠️ No has seleccionado ningún insumo.")
            else:
                dia_str = obtener_dia_semana(fecha_ingreso)
                fecha_cap = datetime.datetime.now()
                lote = [
                    {
                        "f": str(fecha_ingreso), "ins": normalizar(r["Insumo"]), "t": normalizar(r["Tipo"]), 
                        "p": normalizar(prov_sel), "u": float(r["Unidad"]), "c": float(r["Costo Unitario"]), 
                        "m": normalizar(r["Marca"]), "fp": normalizar(r["Forma Pago"]), 
                        "tot": float(r["Unidad"]) * float(r["Costo Unitario"]), "d": dia_str, 
                        "user": operador, "fcap": fecha_cap
                    }
                    for _, r in filas.iterrows()
                ]
                
                query = text('INSERT INTO insumos ("FECHA", "INSUMO", "TIPO", "PROVEEDOR", "UNIDAD", "COSTO", "MARCA", "FORMA PAGO", "TOTAL", "DÍA", "USUARIO", "FECHA_CAPTURA") VALUES (:f, :ins, :t, :p, :u, :c, :m, :fp, :tot, :d, :user, :fcap)')
                try:
                    with st.spinner("Guardando insumos masivos en la base de datos..."):
                        with engine.begin() as conn: 
                            conn.execute(query, lote)
                    
                    st.success(f"✨ ¡Se inyectaron {len(lote)} insumos masivos!")
                    Thread(target=_clear_cache_background, args=([fecha_ingreso.year],), daemon=True).start()
                    st.session_state.version_tabla_insumos = st.session_state.get('version_tabla_insumos', 0) + 1
                    st.rerun()
                except Exception as e: 
                    st.error(f"Error masivo: {e}")
    else: 
        st.info("💡 Selecciona filtros para desplegar el catálogo.")


def renderizar_masivo_gastos(df_gastos, operador, engine):
    st.subheader("Selección de Gastos")
    col_fecha, col_mes = st.columns(2)
    with col_fecha: 
        fecha_ingreso = st.date_input("Seleccionar Fecha de Ingreso:", datetime.datetime.now(), key="fecha_gas_masiva")
    lista_meses_hist = obtener_opciones_unicas(df_gastos, 'AÑO_MES', descendente=True)
    with col_mes: 
        mes_hist_sel = st.selectbox("Filtrar por Mes Histórico:", ["-- Todos los Meses --"] + lista_meses_hist, key="mes_hist_gas")
           
    if mes_hist_sel != "-- Todos los Meses --":
        filtro_mes = df_gastos['AÑO_MES'] == mes_hist_sel
    else:
        filtro_mes = pd.Series(True, index=df_gastos.index)

    df_opciones = df_gastos[filtro_mes]
    
    col_c, col_p = st.columns(2)
    cat_sel = col_c.selectbox("CATEGORÍA", ["-- Seleccionar Categoría --"] + obtener_opciones_unicas(df_opciones, 'CATEGORÍA'))
    prov_sel = col_p.selectbox("PROVEEDOR", ["-- Seleccionar Proveedor --"] + obtener_opciones_unicas(df_opciones, 'PROVEEDOR'))

    df_catalogo = df_gastos[filtro_mes]
    if cat_sel != "-- Seleccionar Categoría --": 
        df_catalogo = df_catalogo[df_catalogo['CATEGORÍA'] == cat_sel]
    if prov_sel != "-- Seleccionar Proveedor --": 
        df_catalogo = df_catalogo[df_catalogo['PROVEEDOR'] == prov_sel]
    
    if not df_catalogo.empty:
        df_catalogo = df_catalogo.sort_values(by='FECHA', ascending=False).drop_duplicates(subset=['GASTO DE', 'CATEGORÍA'])
        
        df_editor = pd.DataFrame({
            "Ingresar": False, 
            "Gasto De": df_catalogo['GASTO DE'], 
            "Tipo": df_catalogo['TIPO'].fillna(""), 
            "Categoría": df_catalogo['CATEGORÍA'], 
            "Recurrencia": df_catalogo['RECURRENCIA'].fillna("MENSUAL"), 
            "Marca": df_catalogo['MARCA'].fillna("General"), 
            "Forma Pago": "EFECTIVO",  
            "Cantidad": 1.0, 
            "Costo Unitario": df_catalogo['COSTO'].fillna(0.0)
        }).reset_index(drop=True)
        
        key_dinamica_gas = f"editor_gastos_masivo_v{st.session_state.get('version_tabla_gastos', 0)}"
        
        datos_editados = st.data_editor(
            df_editor, 
            column_config={
                "Ingresar": st.column_config.CheckboxColumn("✔️", default=False, width=40), 
                "Gasto De": st.column_config.TextColumn("GASTO DE", disabled=True, width="small"), 
                "Tipo": st.column_config.TextColumn("TIPO", disabled=True, width="small"),
                "Categoría": st.column_config.TextColumn("CATEGORÍA", disabled=True, width="small"), 
                "Recurrencia": st.column_config.SelectboxColumn("RECURRENCIA", options=obtener_opciones_unicas(df_gastos, 'RECURRENCIA', "MENSUAL"), width="small"), 
                "Marca": st.column_config.SelectboxColumn("MARCA", options=obtener_opciones_unicas(df_gastos, 'MARCA', "General"), width="small"), 
                "Forma Pago": st.column_config.SelectboxColumn("FORMA PAGO", options=["EFECTIVO", "TARJETA", "TRANSFERENCIA","NO ESPECIFICADO"], required=True, width="small"),
                "Cantidad": st.column_config.NumberColumn("UNIDAD", min_value=0.0, step=0.01, format="%.2f", required=True, width=70), 
                "Costo Unitario": st.column_config.NumberColumn("COSTO", min_value=0.0, width=80)
            }, 
            column_order=["Ingresar", "Gasto De", "Tipo", "Categoría", "Recurrencia", "Cantidad", "Costo Unitario", "Marca", "Forma Pago"],
            hide_index=True, 
            use_container_width=True, 
            height=600,
            key=key_dinamica_gas
        )
        
        if st.button("💾 GUARDAR SELECCIÓN DE GASTOS", type="primary", use_container_width=True):
            filas = datos_editados[datos_editados["Ingresar"] == True]
            if cat_sel == "-- Seleccionar Categoría --" or prov_sel == "-- Seleccionar Proveedor --": 
                st.error("❌ Filtros vacíos.")
            elif filas.empty: 
                st.warning("⚠️ No hay selección.")
            else:
                dia_str = obtener_dia_semana(fecha_ingreso)
                fecha_cap = datetime.datetime.now()
                lote = [
                    {
                        "f": str(fecha_ingreso), "g": normalizar(r["Gasto De"]), "t": normalizar(r["Tipo"]), 
                        "c": normalizar(r["Categoría"]), "p": normalizar(prov_sel), "u": float(r["Cantidad"]), 
                        "co": float(r["Costo Unitario"]), "tot": float(r["Cantidad"]) * float(r["Costo Unitario"]), 
                        "d": dia_str, "rec": normalizar(r["Recurrencia"]), "user": operador, 
                        "m": normalizar(r["Marca"]), "fp": normalizar(r["Forma Pago"]), "fcap": fecha_cap
                    }
                    for _, r in filas.iterrows()
                ]
                query = text('INSERT INTO gastos ("FECHA", "GASTO DE", "TIPO", "CATEGORÍA", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "DÍA", "RECURRENCIA", "USUARIO", "MARCA", "FORMA PAGO", "FECHA_CAPTURA") VALUES (:f, :g, :t, :c, :p, :u, :co, :tot, :d, :rec, :user, :m, :fp, :fcap)')
                
                try:
                    with st.spinner("Guardando lote de gastos..."):
                        with engine.begin() as conn: 
                            conn.execute(query, lote)
                    st.success("✨ ¡Lote de gastos guardado!")
                    Thread(target=_clear_cache_background, args=([fecha_ingreso.year],), daemon=True).start()
                    st.session_state.version_tabla_gastos = st.session_state.get('version_tabla_gastos', 0) + 1
                    st.rerun()
                except Exception as e: 
                    st.error(f"Error masivo: {e}")
    else: 
        st.info("💡 Selecciona filtros.")


def renderizar_carga_excel(operador, engine):
    st.subheader("📂 Subir Excel Ventas - Insumos - Gastos")
    
    # EXCEL VENTAS
    st.markdown("#### 🛒 Ventas")
    archivo_ventas = st.file_uploader("Sube Excel de Ventas (.xlsx)", type=["xlsx"], key="xl_ventas")
    if archivo_ventas:
        try:
            df_v = pd.read_excel(archivo_ventas).fillna(0)
            
            columnas_requeridas = [
                "Número de Venta", "FECHA", "Hora", "Producto", "Precio Unitario", 
                "Sub total", "Cantidad", "Descuento", "TOTAL", "% descuento", 
                "Estado", "Sistema", "Vendedor", "Cliente", "Metodo de pago", 
                "HORA NÚMERO", "TURNO", "CATEGORÍA", "SUCURSAL", "MARCA"
            ]
            
            if not set(columnas_requeridas).issubset(df_v.columns):
                st.error("❌ Faltan columnas. Revisa que el Excel tenga las 20 columnas con los nombres exactos.")
            else:
                df_v['FECHA_DT'] = pd.to_datetime(df_v['FECHA'], errors='coerce')
                fechas_det = df_v['FECHA_DT'].dt.year.dropna().unique().tolist()
                
                df_v = df_v.rename(columns={
                    "Número de Venta": "num", "Hora": "hora", "Producto": "prod", "Precio Unitario": "pu",
                    "Sub total": "sub", "Cantidad": "cant", "Descuento": "desc", "TOTAL": "tot", "% descuento": "pdesc",
                    "Estado": "est", "Sistema": "sis", "Vendedor": "vend", "Cliente": "cli", "Metodo de pago": "mp",
                    "HORA NÚMERO": "hnum", "TURNO": "tur", "CATEGORÍA": "cat", "SUCURSAL": "suc", "MARCA": "mrc"
                })
                
                columnas_str = ["num", "prod", "est", "sis", "vend", "cli", "mp", "tur", "cat", "suc", "mrc"]
                for col in columnas_str:
                    df_v[col] = df_v[col].astype(str).str.strip().str.upper()

                df_v["hora"] = df_v["hora"].apply(lambda x: str(x) if x != 0 else None)
                
                df_v['f'] = df_v['FECHA_DT'].dt.strftime('%Y-%m-%d')
                df_v['user'] = operador
                df_v['fcap'] = datetime.datetime.now()
                
                st.dataframe(df_v.head(5), use_container_width=True)
                
                columnas_db = ["num", "f", "hora", "prod", "pu", "sub", "cant", "desc", "tot", "pdesc", "est", "sis", "vend", "cli", "mp", "hnum", "tur", "cat", "suc", "mrc", "user", "fcap"]
                
                if st.button("🚀 Inyectar Ventas en Supabase", key="btn_v"):
                    lote_ventas = df_v[columnas_db].to_dict(orient="records")
                    query = text('''INSERT INTO ventas ("Número de Venta", "FECHA", "Hora", "Producto", "Precio Unitario", "Sub total", "Cantidad", "Descuento", "TOTAL", "% descuento", "Estado", "Sistema", "Vendedor", "Cliente", "Metodo de pago", "HORA NÚMERO", "TURNO", "CATEGORÍA", "SUCURSAL", "MARCA", "USUARIO", "FECHA_CAPTURA") 
                                    VALUES (:num, :f, :hora, :prod, :pu, :sub, :cant, :desc, :tot, :pdesc, :est, :sis, :vend, :cli, :mp, :hnum, :tur, :cat, :suc, :mrc, :user, :fcap)''')
                    try:
                        with st.spinner("Inyectando..."):
                            with engine.begin() as conn: 
                                conn.execute(query, lote_ventas)
                        st.success("✨ ¡Excel de ventas inyectado correctamente!")
                        Thread(target=_clear_cache_background, args=(fechas_det,), daemon=True).start()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al inyectar ventas: {e}")
        except Exception as e: 
            st.error(f"Error procesando Excel: {e}")

    st.markdown("---")
    
    # EXCEL INSUMOS
    st.markdown("#### 📦 Insumos")
    archivo_insumos = st.file_uploader("Sube Excel de Insumos (.xlsx)", type=["xlsx"], key="xl_insumos")
    if archivo_insumos:
        try:
            # CORREGIDO: Se usa .fillna(0) para proteger columnas numéricas float8
            df_i = pd.read_excel(archivo_insumos).fillna(0)
            
            # CORREGIDO: "FORMA DE PAGO" cambiado por "FORMA PAGO" para coincidir con la base de datos
            columnas_requeridas_ins = ["FECHA", "INSUMO", "TIPO", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "MARCA", "FORMA PAGO"]
            if not set(columnas_requeridas_ins).issubset(df_i.columns): 
                st.error(f"❌ Faltan columnas en Insumos. Asegúrate de tener exactamente: {', '.join(columnas_requeridas_ins)}")
            else:
                df_i['FECHA_DT'] = pd.to_datetime(df_i['FECHA'], errors='coerce')
                fechas_det = df_i['FECHA_DT'].dt.year.dropna().unique().tolist()
                
                df_i['d'] = df_i['FECHA_DT'].apply(obtener_dia_semana)
                df_i['f'] = df_i['FECHA_DT'].dt.strftime('%Y-%m-%d')
                df_i['user'] = operador
                df_i['fcap'] = datetime.datetime.now()
                
                df_i = df_i.rename(columns={
                    "INSUMO": "ins", "TIPO": "t", "PROVEEDOR": "p", "UNIDAD": "u", 
                    "COSTO": "c", "TOTAL": "tot", "MARCA": "m", "FORMA PAGO": "fp"
                })
                
                cols_str = ["ins", "t", "p", "m", "fp"]
                for col in cols_str:
                    df_i[col] = df_i[col].astype(str).str.strip().str.upper()
                
                st.dataframe(df_i.head(5), use_container_width=True)
                
                if st.button("🚀 Inyectar Insumos en Supabase", key="btn_i"):
                    columnas_db = ["f", "ins", "t", "p", "u", "c", "tot", "m", "fp", "d", "user", "fcap"]
                    lote_ins = df_i[columnas_db].to_dict(orient="records")
                    query = text('INSERT INTO insumos ("FECHA", "INSUMO", "TIPO", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "MARCA", "FORMA PAGO", "DÍA", "USUARIO", "FECHA_CAPTURA") VALUES (:f, :ins, :t, :p, :u, :c, :tot, :m, :fp, :d, :user, :fcap)')
                    try:
                        with st.spinner("Inyectando insumos desde Excel..."):
                            with engine.begin() as conn: 
                                conn.execute(query, lote_ins)
                        st.success("✅ ¡Excel de insumos cargado con sus campos automáticos!")
                        Thread(target=_clear_cache_background, args=(fechas_det,), daemon=True).start()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
        except Exception as e: 
            st.error(f"Error procesando Excel: {e}")

    st.markdown("---")
    
    # EXCEL GASTOS
    st.markdown("#### 💸 Gastos")
    archivo_gastos = st.file_uploader("Sube Excel de Gastos (.xlsx)", type=["xlsx"], key="xl_gastos")
    if archivo_gastos:
        try:
            # CORREGIDO: Se usa .fillna(0) para proteger columnas numéricas float8
            df_g = pd.read_excel(archivo_gastos).fillna(0)
            
            # CORREGIDO: "FORMA DE PAGO" cambiado por "FORMA PAGO" para coincidir con la base de datos
            columnas_requeridas_gas = ["FECHA", "GASTO DE", "TIPO", "CATEGORÍA", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "RECURRENCIA", "MARCA", "FORMA PAGO"]
            if not set(columnas_requeridas_gas).issubset(df_g.columns): 
                st.error(f"❌ Faltan columnas en Gastos. Asegúrate de tener exactamente: {', '.join(columnas_requeridas_gas)}")
            else:
                df_g['FECHA_DT'] = pd.to_datetime(df_g['FECHA'], errors='coerce')
                fechas_det = df_g['FECHA_DT'].dt.year.dropna().unique().tolist()
                
                df_g['d'] = df_g['FECHA_DT'].apply(obtener_dia_semana)
                df_g['f'] = df_g['FECHA_DT'].dt.strftime('%Y-%m-%d')
                df_g['user'] = operador
                df_g['fcap'] = datetime.datetime.now()
                
                df_g = df_g.rename(columns={
                    "GASTO DE": "g", "TIPO": "t", "CATEGORÍA": "c", "PROVEEDOR": "p", 
                    "UNIDAD": "u", "COSTO": "co", "TOTAL": "tot", "RECURRENCIA": "rec", 
                    "MARCA": "m", "FORMA PAGO": "fp"
                })
                
                cols_str = ["g", "t", "c", "p", "rec", "m", "fp"]
                for col in cols_str:
                    df_g[col] = df_g[col].astype(str).str.strip().str.upper()

                st.dataframe(df_g.head(5), use_container_width=True)
                
                if st.button("🚀 Inyectar Gastos en Supabase", key="btn_g"):
                    columnas_db = ["f", "g", "t", "c", "p", "u", "co", "tot", "rec", "m", "fp", "d", "user", "fcap"]
                    lote_gas = df_g[columnas_db].to_dict(orient="records")
                    query = text('INSERT INTO gastos ("FECHA", "GASTO DE", "TIPO", "CATEGORÍA", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "RECURRENCIA", "MARCA", "FORMA PAGO", "DÍA", "USUARIO", "FECHA_CAPTURA") VALUES (:f, :g, :t, :c, :p, :u, :co, :tot, :rec, :m, :fp, :d, :user, :fcap)')
                    try:
                        with st.spinner("Inyectando gastos desde Excel..."):
                            with engine.begin() as conn: 
                                conn.execute(query, lote_gas)
                        st.success("✅ ¡Excel de gastos cargado con sus campos automáticos!")
                        Thread(target=_clear_cache_background, args=(fechas_det,), daemon=True).start()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
        except Exception as e: 
            st.error(f"Error procesando Excel: {e}")

def renderizar_venta_individual(df_ventas, operador, engine):
    st.subheader("🛍️ Captura de Venta Individual")
    
    col_f, col_h = st.columns(2)
    with col_f: 
        f_v = st.date_input("🗓️ FECHA DE VENTA", datetime.datetime.now(), key="fecha_ven_ind")
    with col_h: 
        hora_v = st.time_input("⏰ HORA", datetime.datetime.now().time(), key="hora_ven_ind")
        
    st.markdown("---")
    
    col_m, col_s = st.columns(2)
    with col_m:
        marca_sel = st.selectbox("1. Selecciona la MARCA:", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_ventas, 'MARCA'), key="sb_marca_v")
    
    df_v_m = df_ventas[df_ventas['MARCA'] == marca_sel] if marca_sel != "➕ AGREGAR NUEVO..." else df_ventas
    with col_s:
        sucursal_sel = st.selectbox("2. Selecciona la SUCURSAL:", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_v_m, 'SUCURSAL'), key="sb_suc_v")

    df_v_s = df_v_m[df_v_m['SUCURSAL'] == sucursal_sel] if sucursal_sel != "➕ AGREGAR NUEVO..." else df_v_m

    with st.form("form_ventas_individual_cascada", clear_on_submit=True):
        st.markdown("### 📋 Detalles de la Operación")
        col_c1, col_c2, col_c3, col_c4 = st.columns(4)
        num_venta_sel = col_c1.selectbox("Número de Venta", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_v_s, 'Número de Venta'))
        prod_sel = col_c2.selectbox("Producto", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_v_s, 'Producto'))
        cat_sel = col_c3.selectbox("Categoría", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_v_s, 'CATEGORÍA'))
        est_sel = col_c4.selectbox("Estado", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_v_s, 'Estado', "Venta"))
        
        col_c5, col_c6, col_c7, col_c8 = st.columns(4)
        sis_sel = col_c5.selectbox("Sistema", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_v_s, 'Sistema', "LOYVERSE"))
        ven_sel = col_c6.selectbox("Vendedor", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_v_s, 'Vendedor'))
        cli_sel = col_c7.selectbox("Cliente", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_v_s, 'Cliente', "PÚBLICO EN GENERAL"))
        met_sel = col_c8.selectbox("Método de Pago", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_v_s, 'Metodo de pago', "EFECTIVO"))
        
        with st.expander("🆕 Despliega aquí para escribir nuevos registros"):
            st.caption("Llena estos campos SOLO si seleccionaste '➕ AGREGAR NUEVO...' en los menús de arriba.")
            col_n1, col_n2, col_n3 = st.columns(3)
            marca_nueva = col_n1.text_input("Nueva Marca:")
            sucursal_nueva = col_n2.text_input("Nueva Sucursal:")
            num_venta_nuevo = col_n3.text_input("Nuevo Número de Venta:")
            
            col_n4, col_n5, col_n6 = st.columns(3)
            prod_nuevo = col_n4.text_input("Nuevo Producto:")
            cat_nueva = col_n5.text_input("Nueva Categoría:")
            est_nuevo = col_n6.text_input("Nuevo Estado:")
            
            col_n7, col_n8, col_n9, col_n10 = st.columns(4)
            sis_nuevo = col_n7.text_input("Nuevo Sistema:")
            ven_nuevo = col_n8.text_input("Nuevo Vendedor:")
            cli_nuevo = col_n9.text_input("Nuevo Cliente:")
            met_nuevo = col_n10.text_input("Nuevo Método de Pago:")
            
        st.markdown("### 💵 Importes Financieros")
        col_num1, col_num2, col_num3, col_num4, col_num5, col_num6 = st.columns(6)
        pu = col_num1.number_input("Precio Unitario", min_value=0.0, format="%.2f")
        subt = col_num2.number_input("Sub total", min_value=0.0, format="%.2f")
        cant = col_num3.number_input("Cantidad", min_value=0.0, value=1.0, format="%.2f")
        desc = col_num4.number_input("Descuento", min_value=0.0, format="%.2f")
        tot = col_num5.number_input("TOTAL", min_value=0.0, format="%.2f")
        pdesc = col_num6.number_input("% descuento", min_value=0.0, format="%.2f")

        if st.form_submit_button("💾 GUARDAR VENTA MANUAL", use_container_width=True):
            m_fin = normalizar(marca_nueva if marca_sel == "➕ AGREGAR NUEVO..." else marca_sel)
            s_fin = normalizar(sucursal_nueva if sucursal_sel == "➕ AGREGAR NUEVO..." else sucursal_sel)
            nv_fin = normalizar(num_venta_nuevo if num_venta_sel == "➕ AGREGAR NUEVO..." else num_venta_sel)
            p_fin = normalizar(prod_nuevo if prod_sel == "➕ AGREGAR NUEVO..." else prod_sel)
            c_fin = normalizar(cat_nueva if cat_sel == "➕ AGREGAR NUEVO..." else cat_sel)
            e_fin = normalizar(est_nuevo if est_sel == "➕ AGREGAR NUEVO..." else est_sel)
            sys_fin = normalizar(sis_nuevo if sis_sel == "➕ AGREGAR NUEVO..." else sis_sel)
            v_fin = normalizar(ven_nuevo if ven_sel == "➕ AGREGAR NUEVO..." else ven_sel)
            cli_fin = normalizar(cli_nuevo if cli_sel == "➕ AGREGAR NUEVO..." else cli_sel)
            met_fin = normalizar(met_nuevo if met_sel == "➕ AGREGAR NUEVO..." else met_sel)

            if not m_fin or not s_fin or not p_fin:
                st.error("❌ Faltan campos obligatorios. Asegúrate de definir Marca, Sucursal y Producto.")
            else:
                hora_str = hora_v.strftime("%H:%M:%S")
                turno_fin, hora_num = calcular_turno_venta(hora_v)
                fecha_captura = datetime.datetime.now()
                
                query_insert_venta = text('''
                    INSERT INTO ventas (
                        "Número de Venta", "FECHA", "Hora", "Producto", "Precio Unitario", 
                        "Sub total", "Cantidad", "Descuento", "TOTAL", "% descuento", 
                        "Estado", "Sistema", "Vendedor", "Cliente", "Metodo de pago", 
                        "HORA NÚMERO", "TURNO", "CATEGORÍA", "SUCURSAL", "MARCA", "USUARIO", "FECHA_CAPTURA"
                    ) VALUES (
                        :nv, :f, :h, :prod, :pu, :sub, :cant, :desc, :tot, :pdesc,
                        :est, :sis, :ven, :cli, :met, :hnum, :turno, :cat, :suc, :marca, :usu, :fcap
                    )
                ''')
                
                try:
                    with st.spinner("Guardando venta en la base de datos..."):
                        with engine.begin() as conn:
                            conn.execute(query_insert_venta, {
                                "nv": nv_fin, "f": str(f_v), "h": hora_str, "prod": p_fin, 
                                "pu": pu, "sub": subt, "cant": cant, "desc": desc, "tot": tot, 
                                "pdesc": pdesc, "est": e_fin, "sis": sys_fin, "ven": v_fin, 
                                "cli": cli_fin, "met": met_fin, "hnum": hora_num, 
                                "turno": turno_fin, "cat": c_fin, "suc": s_fin, 
                                "marca": m_fin, "usu": operador, "fcap": fecha_captura
                            })
                        
                    st.success("✅ ¡Venta registrada exitosamente en Supabase!")
                    mostrar_ticket({"NÚMERO VENTA": nv_fin, "PRODUCTO": p_fin, "TURNO": turno_fin, "TOTAL VENTA": f"${tot:,.2f}"})
                    Thread(target=_clear_cache_background, args=([f_v.year],), daemon=True).start()
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al insertar la venta manual: {e}")

def renderizar_insumo_individual(df_insumos, operador, engine):
    st.subheader("Captura de Insumo")
    
    f_i = st.date_input("🗓️ FECHA DE EVENTO", datetime.datetime.now(), key="fecha_ins_ind")
    st.markdown("---")
    
    ins_sel = st.selectbox("1. Selecciona el PRODUCTO / INSUMO:", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_insumos, 'INSUMO'), key="sb_ins_cascada")
    df_i_f1 = df_insumos[df_insumos['INSUMO'] == ins_sel] if ins_sel != "➕ AGREGAR NUEVO..." else df_insumos
    
    tip_sel = st.selectbox("2. Selecciona el TIPO / PRESENTACIÓN:", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_i_f1, 'TIPO'), key="sb_tip_cascada")
    
    if tip_sel != "➕ AGREGAR NUEVO...":
        df_aux_prov = df_i_f1[df_i_f1['TIPO'] == tip_sel]
    else:
        df_aux_prov = df_i_f1
        
    prov_sel = st.selectbox("3. Selecciona el PROVEEDOR:", ["-- Seleccionar Proveedor --", "➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_aux_prov, 'PROVEEDOR'), key="sb_prov_cascada")

    with st.form("form_insumos_individual_cascada_final", clear_on_submit=True):
        
        with st.expander("¿No encuentras tu opción? Despliega aquí para escribir nuevos registros"):
            st.caption("Llena estos campos SOLO si seleccionaste '➕ AGREGAR NUEVO...' en los menús de arriba.")
            ins_nuevo = st.text_input("Nuevo Prodcuto/Insumo:")
            tip_nuevo = st.text_input("Nuevo Tipo/Presentación:")
            prov_nuevo = st.text_input("Nuevo Proveedor:")
        
        col1, col2 = st.columns(2)
        with col1:
            marca_i = st.selectbox("MARCA", options=obtener_opciones_unicas(df_insumos, 'MARCA', "General"))
            cant_i = st.number_input("UNIDAD", min_value=0.0, value=1.0, step=0.01, format="%.2f")
        with col2:
            forma_pago_i = st.selectbox("FORMA DE PAGO", options=["EFECTIVO", "TARJETA", "TRANSFERENCIA", "NO ESPECIFICADO"]) 
            cost_i = st.number_input("COSTO UNITARIO", min_value=0.0, value=0.0)
        
        if st.form_submit_button("💾 GUARDAR INSUMO", use_container_width=True):
            ins_final = normalizar(ins_nuevo if ins_sel == "➕ AGREGAR NUEVO..." else ins_sel)
            tip_final = normalizar(tip_nuevo if tip_sel == "➕ AGREGAR NUEVO..." else tip_sel)
            prov_final = normalizar(prov_nuevo if prov_sel == "➕ AGREGAR NUEVO..." else ("" if prov_sel == "-- Seleccionar Proveedor --" else prov_sel))
            
            if not ins_final or not tip_final or not prov_final or cost_i <= 0: 
                st.error("❌ Hay campos inválidos o el costo es 0.")
            else:
                tot_i = cant_i * cost_i
                dia_i = obtener_dia_semana(f_i)
                fecha_captura = datetime.datetime.now()
                
                try:
                    with st.spinner("Guardando insumo manual en la base de datos..."):
                        with engine.begin() as conn: 
                            conn.execute(text('INSERT INTO insumos ("FECHA", "INSUMO", "TIPO", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "DÍA", "USUARIO", "MARCA", "FORMA PAGO", "FECHA_CAPTURA") VALUES (:f, :ins, :t, :p, :u, :c, :tot, :d, :user, :m, :fp, :fcap)'), 
                                         {"f": str(f_i), "ins": ins_final, "t": tip_final, "p": prov_final, "u": cant_i, "c": cost_i, "tot": tot_i, "d": dia_i, "user": operador, "m": normalizar(marca_i), "fp": forma_pago_i, "fcap": fecha_captura})
                    st.success("✅ ¡Insumo guardado!")
                    mostrar_ticket({"PRODUCTO": ins_final, "MONTO TOTAL": f"${tot_i:,.2f}"})
                    Thread(target=_clear_cache_background, args=([f_i.year],), daemon=True).start()
                    st.rerun()
                except Exception as e: 
                    st.error(f"Error: {e}")

def renderizar_gasto_individual(df_gastos, operador, engine):
    st.subheader("Captura de Gasto")
    
    f_g = st.date_input("🗓️ FECHA DE EVENTO", datetime.datetime.now(), key="fecha_gas_ind")
    st.markdown("---")
    
    gas_de_sel = st.selectbox("1. Selecciona el CONCEPTO:", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_gastos, 'GASTO DE'), key="sb_gas_de_cascada")
    df_g_f = df_gastos[df_gastos['GASTO DE'] == gas_de_sel] if gas_de_sel != "➕ AGREGAR NUEVO..." else df_gastos
    
    tip_g_sel = st.selectbox("2. TIPO DE GASTO:", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_g_f, 'TIPO', "OPERATIVO"))
    cat_g_sel = st.selectbox("3. CATEGORÍA:", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_g_f, 'CATEGORÍA'))
    prov_g_sel = st.selectbox("4. PROVEEDOR:", ["-- Seleccionar Proveedor --", "➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_g_f, 'PROVEEDOR'))
    rec_g_sel = st.selectbox("5. RECURRENCIA:", ["➕ AGREGAR NUEVO..."] + obtener_opciones_unicas(df_g_f, 'RECURRENCIA', "VARIABLE"))

    with st.form("form_gastos_individual_cascada_definitivo", clear_on_submit=True):
        
        with st.expander("¿No encuentras tu opción? Despliega aquí para escribir nuevos registros"):
            st.caption("Llena estos campos SOLO si seleccionaste '➕ AGREGAR NUEVO...' en los menús de arriba.")
            gas_de_nuevo = st.text_input("Nuevo Gasto:")
            tip_g_nuevo = st.text_input("Nuevo Tipo:")
            cat_g_nuevo = st.text_input("Nueva Categoría:")
            prov_g_nuevo = st.text_input("Nuevo Proveedor:")
            rec_g_nuevo = st.text_input("Nueva Recurrencia:")
        
        col1, col2 = st.columns(2)
        with col1:
            marca_g = st.selectbox("MARCA", options=obtener_opciones_unicas(df_gastos, 'MARCA', "General"))
            cant_g = st.number_input("UNIDAD", min_value=0.0, value=1.0, step=0.01, format="%.2f")
        with col2:
            forma_pago_g = st.selectbox("FORMA DE PAGO", options=["EFECTIVO", "TARJETA", "TRANSFERENCIA", "NO ESPECIFICADO"])
            cost_g = st.number_input("COSTO UNITARIO", min_value=0.0, value=0.0)
        
        if st.form_submit_button("💾 GUARDAR GASTO", use_container_width=True):
            gas_final = normalizar(gas_de_nuevo if gas_de_sel == "➕ AGREGAR NUEVO..." else gas_de_sel)
            tip_final = normalizar(tip_g_nuevo if tip_g_sel == "➕ AGREGAR NUEVO..." else tip_g_sel)
            cat_final = normalizar(cat_g_nuevo if cat_g_sel == "➕ AGREGAR NUEVO..." else cat_g_sel)
            rec_final = normalizar(rec_g_nuevo if rec_g_sel == "➕ AGREGAR NUEVO..." else rec_g_sel)
            prov_final = normalizar(prov_g_nuevo if prov_g_sel == "➕ AGREGAR NUEVO..." else ("" if prov_g_sel == "-- Seleccionar Proveedor --" else prov_g_sel))
            
            if not gas_final or not tip_final or not cat_final or not prov_final or not rec_final or cost_g <= 0: 
                st.error("❌ Faltan datos o el costo es 0.")
            else:
                tot_g = cant_g * cost_g
                dia_g = obtener_dia_semana(f_g)
                fecha_captura = datetime.datetime.now()
                try:
                    with st.spinner("Guardando gasto manual en la base de datos..."):
                        with engine.begin() as conn: 
                            conn.execute(text('INSERT INTO gastos ("FECHA", "GASTO DE", "TIPO", "CATEGORÍA", "PROVEEDOR", "UNIDAD", "COSTO", "TOTAL", "DÍA", "RECURRENCIA", "USUARIO", "MARCA", "FORMA PAGO", "FECHA_CAPTURA") VALUES (:f, :g, :t, :c, :p, :u, :co, :tot, :d, :rec, :user, :m, :fp, :fcap)'), 
                                         {"f": str(f_g), "g": gas_final, "t": tip_final, "c": cat_final, "p": prov_final, "u": cant_g, "co": cost_g, "tot": tot_g, "d": dia_g, "rec": rec_final, "user": operador, "m": normalizar(marca_g), "fp": forma_pago_g, "fcap": fecha_captura})
                    st.success("✅ ¡Gasto registrado exitosamente!")
                    mostrar_ticket({"CONCEPTO": gas_final, "TOTAL": f"${tot_g:,.2f}"})
                    Thread(target=_clear_cache_background, args=([f_g.year],), daemon=True).start()
                    st.rerun()
                except Exception as e: st.error(f"Error: {e}")

# ==========================================
# 3. ENRUTADOR PRINCIPAL (CLEAN ARCHITECTURE)
# ==========================================

def mostrar_pestana_captura():
    st.header("📝 Registro de Nuevas Operaciones")
    tipo_registro = st.radio(
        "Método de Captura:", 
        ["Selección de Insumos", "Selección de Gastos", "Cargar Excel", 
         "Venta Individual", "Insumo Individual", "Gasto Individual"], 
        horizontal=True
    )
    st.markdown("---")
    
    operador_actual = normalizar(st.session_state.get("usuario_actual", "ANÓNIMO"))
    engine_global = obtener_engine_maestro()
    df_v, df_i, df_g = cargar_catalogos_historicos(st.session_state.get('version_tabla_insumos',0))

    if tipo_registro == "Selección de Insumos":
        renderizar_masivo_insumos(df_i, operador_actual, engine_global)
    elif tipo_registro == "Selección de Gastos":
        renderizar_masivo_gastos(df_g, operador_actual, engine_global)
    elif tipo_registro == "Cargar Excel":
        renderizar_carga_excel(operador_actual, engine_global)
    elif tipo_registro == "Venta Individual":
        renderizar_venta_individual(df_v, operador_actual, engine_global)
    elif tipo_registro == "Insumo Individual":
        renderizar_insumo_individual(df_i, operador_actual, engine_global)
    elif tipo_registro == "Gasto Individual":
        renderizar_gasto_individual(df_g, operador_actual, engine_global)
    else:
        st.info("💡 Módulo no reconocido o en desarrollo.")
