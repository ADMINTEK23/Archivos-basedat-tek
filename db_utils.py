import os
import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text

# OBTENER URL DE LOS SECRETOS SEGUROS
DATABASE_URL = os.getenv("DATABASE_URL") or st.secrets.get("DATABASE_URL")

# 1. OPTIMIZACIÓN: Caching de recursos con verificación de estado activa
@st.cache_resource
def obtener_engine_maestro():
    """Crea y mantiene el pool de conexiones a la base de datos de forma global y segura."""
    return create_engine(
        DATABASE_URL, 
        pool_size=5, 
        max_overflow=10,
        pool_pre_ping=True  # 🔥 REQUISITO: Evita reusar conexiones caídas o muertas
    )

ENGINE_GLOBAL = obtener_engine_maestro()

# =====================================================================
# 🛠️ FUNCIÓN COMPARTIDA DE ENCAPSULACIÓN
# =====================================================================
def _normalizar_dataframe_financiero(df, anio_fijo=None, mes_fijo=None, mes_numero_fijo=None):
    """
    Estandariza columnas a mayúsculas y genera las 4 columnas virtuales
    de control de forma simétrica para cualquier DataFrame.
    """
    if df.empty:
        return df

    # 1. Forzar nombres de columnas a mayúsculas estrictas
    df.columns = df.columns.str.upper()
    
    # 2. Esquema simétrico de control financiero
    if 'FECHA' in df.columns:
        if anio_fijo and mes_fijo and mes_numero_fijo:
            df['AÑO'] = anio_fijo
            df['MES_NUM'] = mes_numero_fijo
            df['MES'] = mes_fijo
            df['AÑO_MES'] = f"{anio_fijo} - {mes_fijo}"
        else:
            meses_dic = {1:'ENE', 2:'FEB', 3:'MAR', 4:'ABR', 5:'MAY', 6:'JUN', 
                         7:'JUL', 8:'AGO', 9:'SEP', 10:'OCT', 11:'NOV', 12:'DIC'}
            df['AÑO'] = df['FECHA'].dt.year
            df['MES_NUM'] = df['FECHA'].dt.month
            df['MES'] = df['MES_NUM'].map(meses_dic)
            df['AÑO_MES'] = df['AÑO'].astype(str) + " - " + df['MES']
            
    return df


# =====================================================================
# 🔥 FUNCIÓN OPTIMIZADA: SEGMENTACIÓN POR MES
# =====================================================================
@st.cache_data(ttl=28800)
def cargar_datos_por_mes(anio, mes, version_cache=0):
    """Filtra directamente en PostgreSQL usando rangos indexados en lugar de EXTRACT"""
    meses_num_dic = {
        'ENE': 1, 'FEB': 2, 'MAR': 3, 'ABR': 4, 'MAY': 5, 'JUN': 6, 
        'JUL': 7, 'AGO': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DIC': 12
    }
    mes_numero = meses_num_dic.get(mes, 1)
    
    # Construimos de forma limpia la fecha de inicio del mes seleccionado
    fecha_inicio = f"{anio}-{mes_numero:02d}-01"
    
    # 🚀 OPTIMIZACIÓN CRÍTICA: Reemplazamos EXTRACT por rangos lógicos indexados (>= y <)
    query_v = text("""
        SELECT id, "FECHA", "SUCURSAL", "MARCA", "TOTAL", "Producto", "Vendedor", "Cliente", "USUARIO", "FECHA_CAPTURA"
        FROM ventas 
        WHERE "FECHA" >= :inicio AND "FECHA" < (CAST(:inicio AS DATE) + INTERVAL '1 month')
    """)
    query_i = text("""
        SELECT id, "FECHA", "PROVEEDOR", "MARCA", "INSUMO", "TIPO", "COSTO", "UNIDAD", "TOTAL", "USUARIO", "FECHA_CAPTURA"
        FROM insumos 
        WHERE "FECHA" >= :inicio AND "FECHA" < (CAST(:inicio AS DATE) + INTERVAL '1 month')
    """)
    query_g = text("""
        SELECT id, "FECHA", "CATEGORÍA", "PROVEEDOR", "MARCA", "GASTO DE", "TIPO", "RECURRENCIA", "COSTO", "UNIDAD", "TOTAL", "USUARIO", "FECHA_CAPTURA"
        FROM gastos 
        WHERE "FECHA" >= :inicio AND "FECHA" < (CAST(:inicio AS DATE) + INTERVAL '1 month')
    """)

    
    try:
        with ENGINE_GLOBAL.connect() as conn:
            # Enviamos el parámetro forzado como string para evitar conflictos de tipo en el CAST
            parametros = {"inicio": str(fecha_inicio)}
            
            ventas = pd.read_sql(query_v, conn, params=parametros, parse_dates=["FECHA"])
            insumos = pd.read_sql(query_i, conn, params=parametros, parse_dates=["FECHA"])
            gastos = pd.read_sql(query_g, conn, params=parametros, parse_dates=["FECHA"])
            
        ventas = _normalizar_dataframe_financiero(ventas, anio, mes, mes_numero)
        insumos = _normalizar_dataframe_financiero(insumos, anio, mes, mes_numero)
        gastos = _normalizar_dataframe_financiero(gastos, anio, mes, mes_numero)
                    
        return ventas, insumos, gastos
    except Exception as e:
        st.error(f"Error en carga segmentada mensual simétrica: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


# =====================================================================
# 🔄 FUNCIÓN COMPATIBLE: HISTÓRICO POR AÑO COMPLETO
# =====================================================================
@st.cache_data(ttl=28800)  
def cargar_datos_optimizados(anio_consultar, version_cache=0):
    """Consulta el año completo a través de límites fijos indexados para evitar bloqueos"""
    
    # Definimos los límites fijos del año
    fecha_inicio = f"{anio_consultar}-01-01"
    fecha_fin = f"{anio_consultar}-12-31"
    
    # 🚀 OPTIMIZACIÓN CRÍTICA: Reemplazamos EXTRACT por BETWEEN
    query_ventas = text("""
        SELECT id, "FECHA", "SUCURSAL", "MARCA", "TOTAL", "Producto", "Vendedor", "Cliente", "USUARIO", "FECHA_CAPTURA"
        FROM ventas WHERE "FECHA" BETWEEN :inicio AND :fin
    """)
    query_insumos = text("""
        SELECT id, "FECHA", "PROVEEDOR", "MARCA", "INSUMO", "TIPO", "COSTO", "UNIDAD", "TOTAL", "USUARIO", "FECHA_CAPTURA"
        FROM insumos WHERE "FECHA" BETWEEN :inicio AND :fin
    """)
    query_gastos = text("""
        SELECT id, "FECHA", "CATEGORÍA", "PROVEEDOR", "MARCA", "GASTO DE", "TIPO", "RECURRENCIA", "COSTO", "UNIDAD", "TOTAL", "USUARIO", "FECHA_CAPTURA"
        FROM gastos WHERE "FECHA" BETWEEN :inicio AND :fin
    """)
    
    try:
        with ENGINE_GLOBAL.connect() as conn:
            ventas = pd.read_sql(query_ventas, conn, params={"inicio": fecha_inicio, "fin": fecha_fin}, parse_dates=["FECHA"])
            insumos = pd.read_sql(query_insumos, conn, params={"inicio": fecha_inicio, "fin": fecha_fin}, parse_dates=["FECHA"])
            gastos = pd.read_sql(query_gastos, conn, params={"inicio": fecha_inicio, "fin": fecha_fin}, parse_dates=["FECHA"])
        
        ventas = _normalizar_dataframe_financiero(ventas)
        insumos = _normalizar_dataframe_financiero(insumos)
        gastos = _normalizar_dataframe_financiero(gastos)
                    
        return ventas, insumos, gastos
    except Exception as e:
        st.error(f"Error en consulta SQL por año completo: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


# =====================================================================
# ⚡ FUNCIÓN NUEVA: CATÁLOGOS LIGEROS PARA MENÚS DESPLEGABLES (HISTÓRICO COMPLETO)
# =====================================================================
@st.cache_data(ttl=28800)
def cargar_catalogos_historicos(version_cache=0):
    """
    Descarga ÚNICAMENTE las combinaciones únicas de toda la historia.
    Usa DATE_TRUNC para agrupar por mes y DISTINCT para eliminar duplicados,
    reduciendo el tráfico de red y el uso de memoria drásticamente.
    """
    # Ventas: Solo necesitamos las categorías de texto
    query_ventas = text("""
        SELECT DISTINCT "MARCA", "SUCURSAL", "Número de Venta", "Producto", 
                        "CATEGORÍA", "Estado", "Sistema", "Vendedor", "Cliente", "Metodo de pago"
        FROM ventas
    """)
    
    # Insumos: Agrupamos por mes para mantener la compatibilidad del menú "AÑO_MES" y traemos el COSTO
    query_insumos = text("""
        SELECT DISTINCT DATE_TRUNC('month', "FECHA") AS "FECHA", 
                        "PROVEEDOR", "MARCA", "INSUMO", "TIPO", "COSTO"
        FROM insumos
    """)
    
    # Gastos: Agrupamos por mes y traemos las categorías correspondientes
    query_gastos = text("""
        SELECT DISTINCT DATE_TRUNC('month', "FECHA") AS "FECHA", 
                        "CATEGORÍA", "PROVEEDOR", "MARCA", "GASTO DE", "TIPO", "RECURRENCIA", "COSTO"
        FROM gastos
    """)
    
    try:
        with ENGINE_GLOBAL.connect() as conn:
            cat_ventas = pd.read_sql(query_ventas, conn) 
            cat_insumos = pd.read_sql(query_insumos, conn, parse_dates=["FECHA"])
            cat_gastos = pd.read_sql(query_gastos, conn, parse_dates=["FECHA"])
        
        # Reutilizamos tu normalizador para que genere la columna AÑO_MES automáticamente
        cat_ventas = _normalizar_dataframe_financiero(cat_ventas)
        cat_insumos = _normalizar_dataframe_financiero(cat_insumos)
        cat_gastos = _normalizar_dataframe_financiero(cat_gastos)
                    
        return cat_ventas, cat_insumos, cat_gastos
    except Exception as e:
        st.error(f"Error cargando catálogos históricos: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
