import logging
from datetime import date, timedelta
import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import text
from db_utils import ENGINE_GLOBAL

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
TTL_AUDITORIA = 60 * 30

@st.cache_data(ttl=TTL_AUDITORIA)
def obtener_metricas_auditoria_usuarios(fecha_inicio: date, fecha_fin: date) -> pd.DataFrame:
    if fecha_fin < fecha_inicio:
        raise ValueError("fecha_fin debe ser >= fecha_inicio")
    fecha_fin_exclusiva = fecha_fin + timedelta(days=1)
    
    query = text("""
        SELECT 'Ventas' AS modulo, "USUARIO", "FECHA_CAPTURA", "TOTAL" FROM ventas WHERE "FECHA_CAPTURA" >= :inicio AND "FECHA_CAPTURA" < :fin_exclusiva
        UNION ALL
        SELECT 'Gastos' AS modulo, "USUARIO", "FECHA_CAPTURA", "TOTAL" FROM gastos WHERE "FECHA_CAPTURA" >= :inicio AND "FECHA_CAPTURA" < :fin_exclusiva
        UNION ALL
        SELECT 'Insumos' AS modulo, "USUARIO", "FECHA_CAPTURA", "TOTAL" FROM insumos WHERE "FECHA_CAPTURA" >= :inicio AND "FECHA_CAPTURA" < :fin_exclusiva
    """)
    
    try:
        with ENGINE_GLOBAL.connect() as conn:
            df = pd.read_sql(query, conn, params={"inicio": fecha_inicio, "fin_exclusiva": fecha_fin_exclusiva})
            
        if df is None or df.empty:
            return pd.DataFrame(columns=['MODULO', 'USUARIO', 'FECHA_CAPTURA', 'TOTAL'])
            
        df.rename(columns={'modulo': 'MODULO'}, inplace=True)
        df.columns = df.columns.str.upper()
        df['FECHA_CAPTURA'] = pd.to_datetime(df['FECHA_CAPTURA'], errors='coerce')
        df['USUARIO'] = df['USUARIO'].astype(str).str.strip().str.upper()
        df['TOTAL'] = pd.to_numeric(df['TOTAL'], errors='coerce').fillna(0.0).astype('float64')
        return df
        
    except Exception:
        logger.exception("Error en obtener_metricas_auditoria_usuarios")
        return pd.DataFrame(columns=['MODULO', 'USUARIO', 'FECHA_CAPTURA', 'TOTAL'])

def mostrar_pestana_auditoria_usuarios():
    st.sidebar.header("👤 Filtros de Auditoría")
    debug_mode = st.sidebar.checkbox("Mostrar debug (df_logs)", value=False)
    
    hoy = date.today()
    hace_un_mes = hoy - timedelta(days=30)
    fechas_sel = st.sidebar.date_input("Rango de Fechas", value=(hace_un_mes, hoy), max_value=hoy)
    
    if not (isinstance(fechas_sel, tuple) and len(fechas_sel) == 2):
        st.info("💡 Selecciona la fecha inicial y final en el menú lateral para comenzar.")
        return
        
    f_inicio, f_fin = fechas_sel
    if f_fin < f_inicio:
        st.error("❌ La fecha final debe ser mayor o igual a la inicial.")
        return
        
    if st.sidebar.button("🔄 Sincronizar Logs", use_container_width=True, key="sync_auditoria"):
        try:
            obtener_metricas_auditoria_usuarios.clear()
        except Exception:
            logger.debug("No se pudo limpiar cache")
        st.success("¡Datos actualizados!")
        st.rerun()
        
    df_logs = obtener_metricas_auditoria_usuarios(f_inicio, f_fin)
    
    if debug_mode:
        st.write("DEBUG:", None if df_logs is None else {"shape": df_logs.shape, "cols": df_logs.columns.tolist()})
        st.dataframe(df_logs.head(10) if df_logs is not None else None)
        
    if df_logs is None or df_logs.empty:
        st.warning(f"⚠️ No se encontraron registros entre {f_inicio} y {f_fin}.")
        return
        
    if 'FECHA_CAPTURA' not in df_logs.columns or 'USUARIO' not in df_logs.columns:
        st.error("Columnas estructurales faltantes. Revisa la consulta SQL.")
        return
        
    usuarios_disponibles = sorted(df_logs['USUARIO'].unique())
    usuario_sel = st.sidebar.selectbox("Selecciona Usuario", ["Todos"] + usuarios_disponibles, index=0)
    df_filtrado = df_logs.copy() if usuario_sel == "Todos" else df_logs[df_logs['USUARIO'] == usuario_sel].copy()
    
    if df_filtrado.empty:
        st.info(f"El usuario {usuario_sel} no registra actividad.")
        return
        
    df_filtrado = df_filtrado.sort_values(['USUARIO', 'FECHA_CAPTURA'])
    
    # 💡 CÁLCULO DE DIFERENCIAS EN SEGUNDOS Y MINUTOS
    df_filtrado['DIFERENCIA_SEGUNDOS'] = df_filtrado.groupby('USUARIO', observed=False)['FECHA_CAPTURA'].diff().dt.total_seconds()
    df_filtrado['DIFERENCIA_MINUTOS'] = df_filtrado['DIFERENCIA_SEGUNDOS'] / 60.0
    
    # Cálculos generales
    capturas_continuas = df_filtrado[df_filtrado['DIFERENCIA_MINUTOS'] <= 30]
    tiempo_promedio = capturas_continuas['DIFERENCIA_MINUTOS'].mean()
    df_filtrado['FECHA_DIA'] = df_filtrado['FECHA_CAPTURA'].dt.date
    df_filtrado['HORA_EXACTA'] = df_filtrado['FECHA_CAPTURA'].dt.hour + (df_filtrado['FECHA_CAPTURA'].dt.minute / 60.0)
    
    jornadas = df_filtrado.groupby(['USUARIO', 'FECHA_DIA'], observed=False)['FECHA_CAPTURA'].agg(['min', 'max'])
    jornadas['HORAS_ACTIVAS'] = (jornadas['max'] - jornadas['min']).dt.total_seconds() / 3600.0
    promedio_horas = jornadas[jornadas['HORAS_ACTIVAS'] > 0]['HORAS_ACTIVAS'].mean()
    
    st.title("📊 Cuadro de Mando: Auditoría de Usuarios")
    st.caption(f"Rango: **{f_inicio}** al **{f_fin}** | Filtro: **{usuario_sel}**")
    
    # 💡 BANDERAS ROJAS ACTUALIZADAS (Menos de 20 segundos y más de 4 minutos)
    capturas_madrugada = df_filtrado[df_filtrado['FECHA_CAPTURA'].dt.hour < 6]
    capturas_muy_rapidas = df_filtrado[(df_filtrado['DIFERENCIA_SEGUNDOS'] >= 0) & (df_filtrado['DIFERENCIA_SEGUNDOS'] < 20)]
    capturas_pausadas = df_filtrado[df_filtrado['DIFERENCIA_MINUTOS'] > 4.0]
    
    if not capturas_madrugada.empty or not capturas_muy_rapidas.empty or not capturas_pausadas.empty:
        alertas = []
        if not capturas_madrugada.empty:
            alertas.append(f"**{len(capturas_madrugada)}** registros capturados de madrugada (antes de las 6:00 AM).")
        if not capturas_muy_rapidas.empty:
            alertas.append(f"**{len(capturas_muy_rapidas)}** operaciones consecutivas ultra-rápidas (menos de 20 segundos de diferencia).")
        if not capturas_pausadas.empty:
            alertas.append(f"**{len(capturas_pausadas)}** pausas prolongadas entre capturas consecutivas (más de 4 minutos de diferencia).")
        
        st.warning("⚠️ **Banderas Rojas Detectadas en la Operación:**\n" + "\n".join([f"- {a}" for a in alertas]))

    # Tarjetas Métricas
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Operaciones", f"{len(df_filtrado):,}", help="Cantidad de registros procesados.")
    m2.metric("Volumen Financiero", f"${df_filtrado['TOTAL'].sum():,.2f}", help="Suma monetaria total.")
    txt_prom = f"{tiempo_promedio:.1f} min" if pd.notnull(tiempo_promedio) else "N/A"
    m3.metric("Frecuencia Promedio", txt_prom, help="Intervalo medio entre cargas continuas (≤ 30 min).")
    txt_hrs = f"{promedio_horas:.1f} hrs" if pd.notnull(promedio_horas) else "N/A"
    m4.metric("Conexión Diaria Prom.", txt_hrs, help="Duración promedio de la jornada por día activo.")
    
    st.divider()
    
    # Fila de Gráficos Superiores
    g1, g2 = st.columns(2)
    with g1:
        with st.container(border=True):
            st.subheader("Distribución por Módulo")
            df_modulo = df_filtrado.groupby('MODULO', observed=False)['TOTAL'].sum().reset_index()
            if not df_modulo.empty and df_modulo['TOTAL'].sum() > 0:
                fig_pie = px.pie(df_modulo, names='MODULO', values='TOTAL', hole=0.4, color_discrete_sequence=px.colors.qualitative.Safe)
                fig_pie.update_layout(height=280, margin=dict(t=10, b=10, l=10, r=10), legend=dict(orientation="h", y=-0.1))
                st.plotly_chart(fig_pie, use_container_width=True)
            else:
                st.info("No hay importes para graficar.")
    with g2:
        with st.container(border=True):
            st.subheader("Volumen de Actividad Diaria")
            df_tendencia = df_filtrado.groupby(['FECHA_DIA', 'MODULO'], observed=False).size().reset_index(name='OPERACIONES')
            if not df_tendencia.empty:
                fig_line = px.line(df_tendencia, x='FECHA_DIA', y='OPERACIONES', color='MODULO', markers=True, color_discrete_sequence=px.colors.qualitative.Safe)
                fig_line.update_layout(height=280, margin=dict(t=10, b=10, l=10, r=10), xaxis_title=None, legend=dict(orientation="h", y=-0.1))
                st.plotly_chart(fig_line, use_container_width=True)
            else:
                st.info("No hay actividad diaria para graficar.")
                
    # Gráfico de Dispersión con corrección de valores absolutos para evitar errores en Plotly
    with st.container(border=True):
        st.subheader("⏱️ Mapa Temporal de Capturas (Patrones de horario)")
        if not df_filtrado.empty:
            df_filtrado['TOTAL_ABS'] = df_filtrado['TOTAL'].abs()

            fig_scatter = px.scatter(
                df_filtrado, 
                x='FECHA_CAPTURA', 
                y='HORA_EXACTA', 
                color='MODULO', 
                size='TOTAL_ABS', 
                hover_data=['USUARIO', 'TOTAL'],
                color_discrete_sequence=px.colors.qualitative.Safe,
                labels={"HORA_EXACTA": "Hora del Día (0h - 24h)", "FECHA_CAPTURA": "Fecha"}
            )
            fig_scatter.update_layout(
                height=300, 
                margin=dict(t=10, b=10, l=10, r=10), 
                yaxis=dict(range=[-1, 25], tick0=0, dtick=2)
            )
            st.plotly_chart(fig_scatter, use_container_width=True)
        else:
            st.info("No hay datos para graficar la dispersión.")
            
    st.divider()
    
    # Tabla con Botón de Exportación CSV
    col_title, col_btn = st.columns([3, 1])
    with col_title:
        st.markdown("#### 📋 Últimos Movimientos Registrados")
        
    columnas_vista = ['FECHA_CAPTURA', 'USUARIO', 'MODULO', 'TOTAL']
    cols_present = [c for c in columnas_vista if c in df_filtrado.columns]
    
    if cols_present:
        df_tabla = df_filtrado[cols_present].sort_values('FECHA_CAPTURA', ascending=False).head(100)
        
        with col_btn:
            csv = df_tabla.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Descargar CSV",
                data=csv,
                file_name=f"auditoria_{usuario_sel}_{f_inicio}_al_{f_fin}.csv",
                mime="text/csv",
                use_container_width=True
            )
            
        st.dataframe(df_tabla, use_container_width=True, hide_index=True, column_config={
            "FECHA_CAPTURA": st.column_config.DatetimeColumn("Fecha y Hora", format="DD/MM/YYYY HH:mm"),
            "USUARIO": st.column_config.TextColumn("Usuario"),
            "MODULO": st.column_config.TextColumn("Módulo"),
            "TOTAL": st.column_config.NumberColumn("Monto Total", format="$%,.2f")
        })
    else:
        st.warning("Columnas insuficientes para mostrar la tabla.")

if __name__ == "__main__":
    mostrar_pestana_auditoria_usuarios()
