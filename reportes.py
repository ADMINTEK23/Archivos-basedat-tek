import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from sqlalchemy import text
# Importamos el ENGINE_GLOBAL maestro y la nueva función optimizada de db_utils
from db_utils import ENGINE_GLOBAL, cargar_datos_por_mes

# =====================================================================
# 📊 OPTIMIZACIONES DE RED: Generación Dinámica de Períodos
# =====================================================================

def generar_periodos_disponibles():
    """Genera la lista combinada ['AGO 2026', 'JUL 2026', ...] hasta el inicio del negocio"""
    meses_lista = ['ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN', 'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC']
    anio_actual = datetime.now().year
    mes_actual = datetime.now().month
    
    opciones = []
    # Generamos desde el año actual hacia atrás (ej. hasta 2024)
    for anio in range(anio_actual, 2023, -1):
        # Si es el año actual, solo mostramos hasta el mes presente para no saturar con meses vacíos
        limite_mes = mes_actual if anio == anio_actual else 12
        for m_idx in range(limite_mes - 1, -1, -1):
            opciones.append(f"{meses_lista[m_idx]} {anio}")
            
    return opciones

@st.cache_data(ttl=28800)
def obtener_ventas_historicas_resumidas(anio: int, mes_nombre: str):
    """Consulta resumida YoY usando optimización por rangos de fecha."""
    meses_num_dic = {'ENE':1, 'FEB':2, 'MAR':3, 'ABR':4, 'MAY':5, 'JUN':6, 'JUL':7, 'AGO':8, 'SEP':9, 'OCT':10, 'NOV':11, 'DIC':12}
    m_num = meses_num_dic.get(mes_nombre, 1)
    
    fecha_inicio = f"{anio}-{m_num:02d}-01"
    
    query_yoy = text("""
        SELECT 
            COALESCE(NULLIF(UPPER(TRIM(BOTH FROM "SUCURSAL")), ''), 'SIN SUCURSAL') AS SUC,
            COALESCE(NULLIF(UPPER(TRIM(BOTH FROM "MARCA")), ''), 'SIN MARCA') AS MRC,
            COALESCE(SUM("TOTAL"), 0) AS AS_TOTAL
        FROM ventas 
        WHERE "FECHA" >= :inicio AND "FECHA" < (CAST(:inicio AS DATE) + INTERVAL '1 month')
        GROUP BY "SUCURSAL", "MARCA"
    """)
    
    try:
        with ENGINE_GLOBAL.connect() as conn:
            df_hist = pd.read_sql(query_yoy, conn, params={"inicio": fecha_inicio})
        
        if not df_hist.empty:
            df_hist.columns = df_hist.columns.str.upper()
            df_hist['SUC'] = df_hist['SUC'].astype(str).str.strip().str.upper()
            df_hist['MRC'] = df_hist['MRC'].astype(str).str.strip().str.upper()
        return df_hist
    except Exception as e:
        st.error(f"❌ Error crítico en consulta SQL YoY: {e}")
        return pd.DataFrame()


# =====================================================================
# 📅 CONTROLADOR DE LA PESTAÑA DE REPORTES
# =====================================================================

def mostrar_pestana_reportes():
    try:
        st.sidebar.header("📅 Filtros Generales")
        
        if st.sidebar.button("🔄 Sincronizar Datos Completo", use_container_width=True):
            cargar_datos_por_mes.clear()
            obtener_ventas_historicas_resumidas.clear()
            st.success("¡Caché de consultas limpiada con éxito!")
            st.rerun()
        
        # 🌟 NUEVO SELECTOR UNIFICADO: Reemplaza a los selectores separados de Año y Mes
        lista_periodos = generar_periodos_disponibles()
        periodo_sel = st.sidebar.selectbox("Selecciona el Período", options=lista_periodos, index=0)
        
        # Extraemos mes y año del texto unificado (Ej: "ENE 2026" -> mes_sel="ENE", anio_sel=2026)
        mes_sel, anio_sel = periodo_sel.split(" ")
        anio_sel = int(anio_sel)
        anio_anterior = anio_sel - 1

        st.session_state.setdefault("version_tabla_insumos", 0)
        st.session_state.setdefault("version_tabla_gastos", 0)
        st.session_state.setdefault("version_tabla_ventas", 0)
        
        version_global = (st.session_state.version_tabla_insumos + 
                          st.session_state.version_tabla_gastos + 
                          st.session_state.version_tabla_ventas)

        df_ventas, df_insumos, df_gastos = cargar_datos_por_mes(anio_sel, mes_sel, version_global)
        
        if df_ventas.empty and df_insumos.empty and df_gastos.empty:
            st.warning(f"⚠️ No se encontraron transacciones operativas para el periodo {mes_sel} - {anio_sel}.")
            return

        todas_las_marcas = set()
        for df in (df_ventas, df_insumos, df_gastos):
            if not df.empty and 'MARCA' in df.columns:
                todas_las_marcas.update(df['MARCA'].dropna().unique())
                
        marca_sel = st.sidebar.selectbox("🏷️ Selecciona Marca", ["Todas"] + sorted(list(todas_las_marcas)))

        if not df_ventas.empty and 'MARCA' in df_ventas.columns:
            df_v_filtrado = df_ventas[df_ventas['MARCA'] == marca_sel] if marca_sel != "Todas" else df_ventas.copy()
        else:
            df_v_filtrado = pd.DataFrame()

        if not df_v_filtrado.empty and 'SUCURSAL' in df_v_filtrado.columns:
            sucursales = ["Todas"] + sorted(list(df_v_filtrado['SUCURSAL'].dropna().unique()))
        else:
            sucursales = ["Todas"]

        suc_sel = st.sidebar.selectbox("📍 Selecciona Sucursal", sucursales)

        if suc_sel != "Todas" and not df_v_filtrado.empty and 'SUCURSAL' in df_v_filtrado.columns:
            df_v_filtrado = df_v_filtrado[df_v_filtrado['SUCURSAL'] == suc_sel]

        if not df_gastos.empty and 'MARCA' in df_gastos.columns:
            df_g_filtrado = df_gastos[df_gastos['MARCA'] == marca_sel] if marca_sel != "Todas" else df_gastos.copy()
        else:
            df_g_filtrado = pd.DataFrame()

        if not df_insumos.empty and 'MARCA' in df_insumos.columns:
            df_i_filtrado = df_insumos[df_insumos['MARCA'] == marca_sel] if marca_sel != "Todas" else df_insumos.copy()
        else:
            df_i_filtrado = pd.DataFrame()


        total_ventas = float(df_v_filtrado['TOTAL'].sum()) if not df_v_filtrado.empty else 0.0
        total_gastos = float(df_g_filtrado['TOTAL'].sum()) if not df_g_filtrado.empty else 0.0
        total_insumos = float(df_i_filtrado['TOTAL'].sum()) if not df_i_filtrado.empty else 0.0

        utilidad_neta = total_ventas - (total_insumos + total_gastos)
        denominador = total_ventas if total_ventas > 0 else 1.0

        pct_utilidad = (utilidad_neta / denominador) * 100
        pct_gastos = (total_gastos / denominador) * 100
        pct_insumos = (total_insumos / denominador) * 100

        u_dec, g_dec, i_dec = pct_utilidad / 100, pct_gastos / 100, pct_insumos / 100
        status_utilidad = "EXCESIVA" if u_dec > 0.25 else "EXCELENTE" if u_dec > 0.18 else "SALUDABLE" if u_dec > 0.12 else "REGULAR" if u_dec > 0.08 else "DE RIESGO"
        status_gastos = "DE RIESGO" if g_dec > 0.55 else "REGULAR" if g_dec > 0.50 else "SALUDABLE" if g_dec > 0.47 else "EXCELENTE"
        status_insumos = "DE RIESGO" if i_dec > 0.41 else "REGULAR" if i_dec > 0.38 else "SALUDABLE" if i_dec > 0.35 else "EXCELENTE"

        # =====================================================================
        # 🎨 CAPA VISUAL SUPERIOR
        # =====================================================================
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
            
            v_bokoba = float(df_v_filtrado[df_v_filtrado['SUCURSAL'] == 'BOKOBA']['TOTAL'].sum()) if 'SUCURSAL' in df_v_filtrado.columns and not df_v_filtrado.empty else 0.0
            v_sotuta = float(df_v_filtrado[df_v_filtrado['SUCURSAL'] == 'SOTUTA']['TOTAL'].sum()) if 'SUCURSAL' in df_v_filtrado.columns and not df_v_filtrado.empty else 0.0
            
            df_barras = pd.DataFrame({
                'Métrica': ['CONSOLIDADA', 'BOKOBA', 'SOTUTA', 'INSUMOS', 'GASTOS', 'UTILIDAD'], 
                'Monto': [total_ventas, v_bokoba, v_sotuta, total_insumos, total_gastos, utilidad_neta]
            })
            fig_bar = px.bar(
                df_barras, x='Monto', y='Métrica', orientation='h', text_auto=',.2f', color='Métrica', 
                color_discrete_map={'CONSOLIDADA': '#3B75AF', 'BOKOBA': '#A5C8E1', 'SOTUTA': '#4F94CD', 'INSUMOS': '#FFC000', 'GASTOS': '#70AD47', 'UTILIDAD': '#7030A0'}
            )
            fig_bar.update_layout(
                showlegend=False, 
                yaxis={'categoryorder':'array', 'categoryarray':['UTILIDAD', 'GASTOS', 'INSUMOS', 'SOTUTA', 'BOKOBA', 'CONSOLIDADA'], 'title': None}, 
                xaxis={'title': None, 'showticklabels': False}
            )
            st.plotly_chart(fig_bar, use_container_width=True, key="barras_reportes")

        # =====================================================================
        # 📈 SECCIÓN YoY
        # =====================================================================
        st.markdown("---")
        st.markdown(f"<h3 style='text-align: center;'>COMPARACIÓN DE RENDIMIENTO INTERANUAL: {mes_sel} {anio_anterior} vs {mes_sel} {anio_sel}</h3>", unsafe_allow_html=True)
        
        df_historico_raw = obtener_ventas_historicas_resumidas(anio_anterior, mes_sel)
        
        marca_filtro = str(marca_sel).strip().upper()
        suc_filtro = str(suc_sel).strip().upper()
        
        df_ant_filtrado = df_historico_raw.copy() if not df_historico_raw.empty else pd.DataFrame()
        if not df_ant_filtrado.empty and marca_filtro != "TODAS":
            df_ant_filtrado = df_ant_filtrado[df_ant_filtrado['MRC'] == marca_filtro]
            
        tarjetas_render = []
        
        if suc_filtro == "TODAS":
            sucursales_activas = []
            if not df_v_filtrado.empty and 'SUCURSAL' in df_v_filtrado.columns:
                sucursales_activas = sorted(list(df_v_filtrado['SUCURSAL'].dropna().unique()))
            if not sucursales_activas and not df_ant_filtrado.empty:
                sucursales_activas = sorted(list(df_ant_filtrado['SUC'].dropna().unique()))
                
            for s in sucursales_activas:
                s_norm = str(s).strip().upper()
                monto_act_suc = float(df_v_filtrado[df_v_filtrado['SUCURSAL'] == s]['TOTAL'].sum()) if not df_v_filtrado.empty else 0.0
                monto_ant_suc = float(df_ant_filtrado[df_ant_filtrado['SUC'] == s_norm]['AS_TOTAL'].sum()) if not df_ant_filtrado.empty else 0.0
                tarjetas_render.append((f"📍 {s_norm}", monto_act_suc, monto_ant_suc))
        else:
            monto_act_suc = float(df_v_filtrado['TOTAL'].sum()) if not df_v_filtrado.empty else 0.0
            monto_ant_suc = float(df_ant_filtrado[df_ant_filtrado['SUC'] == suc_filtro]['AS_TOTAL'].sum()) if not df_ant_filtrado.empty else 0.0
            tarjetas_render.append((f"📍 SUCURSAL: {suc_filtro}", monto_act_suc, monto_ant_suc))

        total_act_global = float(df_v_filtrado['TOTAL'].sum()) if not df_v_filtrado.empty else 0.0
        total_ant_global = float(df_ant_filtrado['AS_TOTAL'].sum()) if not df_ant_filtrado.empty else 0.0
        
        if suc_filtro == "TODAS":
            tarjetas_render.append(("🏢 CONSOLIDADO TOTAL", total_act_global, total_ant_global))

        num_tarjetas = len(tarjetas_render)
        if num_tarjetas > 0:
            columnas_dinamicas = st.columns(num_tarjetas)
            for idx, (lbl_titulo, val_hoy, val_ayer) in enumerate(tarjetas_render):
                with columnas_dinamicas[idx]:
                    fig_dinamica = go.Figure(go.Indicator(
                        mode = "number+delta", value = val_hoy,
                        delta = {'reference': val_ayer, 'relative': True, 'valueformat': '.2%'},
                        title = {"text": f"<span style='font-size:1.1em;color:#444;font-weight:bold;'>{lbl_titulo}</span><br><span style='font-size:0.8em;color:gray;'>Año anterior: ${val_ayer:,.2f}</span>"},
                        number = {'prefix': "$", 'valueformat': ',.2f'}
                    ))
                    fig_dinamica.update_layout(height=240, margin=dict(t=40, b=10, l=10, r=10))
                    st.plotly_chart(fig_dinamica, use_container_width=True, key=f"yoy_dinamico_matricial_{idx}")
        else:
            st.caption("No hay datos comparativos interanuales disponibles.")


        # =====================================================================
        # 🔐 PANEL DE AUDITORÍA Y BORRADO QUIRÚRGICO (SOLO ADMIN)
        # =====================================================================
        if st.session_state.get("rol_actual") == "admin":
            st.markdown("---")
            st.markdown("### 🔍 Panel de Auditoría de Registros")
            st.info("💡 *Cómo eliminar un registro:* Identifica el número en la columna *ID* del registro erróneo, escríbelo en el formulario de abajo y confirma la eliminación.")

            if "confirmar_baja_id" not in st.session_state:
                st.session_state["confirmar_baja_id"] = False
                st.session_state["id_objetivo"] = None
                st.session_state["tabla_objetivo"] = ""

            if st.session_state["confirmar_baja_id"]:
                st.warning(f"⚠️ *¿Confirmas la eliminación definitiva?* Estás a punto de borrar el *ID: {st.session_state['id_objetivo']}* de la tabla *{st.session_state['tabla_objetivo'].upper()}*.")
                col_btn1, col_conf2 = st.columns(2)
                
                with col_btn1:
                    if st.button("🚨 SÍ, ELIMINAR DE SUPABASE", type="primary", use_container_width=True):
                        tabla_sanitizada = "".join([c for c in st.session_state['tabla_objetivo'] if c.isalnum() or c == "_"])
                        query_baja = text(f"DELETE FROM {tabla_sanitizada} WHERE id = :id_target")
                        
                        with ENGINE_GLOBAL.begin() as conn:
                            conn.execute(query_baja, {"id_target": st.session_state["id_objetivo"]})
                        st.success(f"✨ ¡El registro con ID {st.session_state['id_objetivo']} ha sido eliminado!")
                        
                        # 🔥 CORRECCIÓN CRÍTICA DE CACHÉ EN STREAMLIT (.clear() sin argumentos)
                        cargar_datos_por_mes.clear()
                        obtener_ventas_historicas_resumidas.clear()
                        
                        st.session_state["confirmar_baja_id"] = False
                        st.session_state["id_objetivo"] = None
                        st.session_state["tabla_objetivo"] = ""
                        st.rerun()
                        
                with col_conf2:
                    if st.button("CANCELAR OPERACIÓN", use_container_width=True):
                        st.session_state["confirmar_baja_id"] = False
                        st.session_state["id_objetivo"] = None
                        st.session_state["tabla_objetivo"] = ""
                        st.rerun()
                st.markdown("---")

            tab_v, tab_i, tab_g = st.tabs(["Ventas del Mes", "Insumos del Mes", "Gastos del Mes"])
            
            with tab_v:
                if not df_v_filtrado.empty:
                    cols_v = [c for c in ['ID', 'FECHA', 'SUCURSAL', 'PRODUCTO', 'TOTAL', 'USUARIO', 'FECHA_CAPTURA'] if c in df_v_filtrado.columns]
                    st.dataframe(df_v_filtrado[cols_v], use_container_width=True, hide_index=True)
                else: 
                    st.caption("No hay registros de ventas.")

            with tab_i:
                if not df_i_filtrado.empty:
                    cols_i = [c for c in ['ID', 'FECHA', 'INSUMO', 'PROVEEDOR', 'TOTAL', 'USUARIO', 'FECHA_CAPTURA'] if c in df_i_filtrado.columns]
                    st.dataframe(df_i_filtrado[cols_i], use_container_width=True, hide_index=True)
                else: 
                    st.caption("No hay registros de insumos.")

            with tab_g:
                if not df_g_filtrado.empty:
                    cols_g = [c for c in ['ID', 'FECHA', 'GASTO DE', 'CATEGORÍA', 'TOTAL', 'USUARIO', 'FECHA_CAPTURA'] if c in df_g_filtrado.columns]
                    st.dataframe(df_g_filtrado[cols_g], use_container_width=True, hide_index=True)
                else: 
                    st.caption("No hay registros de gastos.")

            st.markdown("#### 🗑️ Módulo de Eliminación de Errores")
            with st.form("formulario_borrado_id", clear_on_submit=True):
                col_input1, col_input2 = st.columns(2)
                with col_input1:
                    tabla_seleccionada = st.selectbox("1. Selecciona la tabla de origen:", ["ventas", "insumos", "gastos"])
                with col_input2:
                    id_ingresado = st.number_input("2. Ingresa el número de ID a eliminar:", min_value=1, step=1, value=1)
                
                if st.form_submit_button("🚨 SOLICITAR BAJA DE REGISTRO", use_container_width=True):
                    st.session_state["confirmar_baja_id"] = True
                    st.session_state["id_objetivo"] = int(id_ingresado)
                    st.session_state["tabla_objetivo"] = tabla_seleccionada
                    st.rerun()
                
    except Exception as e:
        st.error(f"Error de visualización en reportes: {e}")
