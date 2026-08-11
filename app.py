import streamlit as st
import importlib
from login import ejecutar_login

# 1. Módulo de Seguridad y Login Prioritario Directo
ejecutar_login()

if st.session_state.get("autenticado", False):
    rol = st.session_state.get("rol_actual", "")

    # --- CORRECCIÓN DEFINITIVA: INICIALIZAR CONTADORES DE TABLAS MASIVAS ---
    # setdefault es más eficiente y limpio para inicializar variables si no existen
    st.session_state.setdefault("version_tabla_insumos", 0)
    st.session_state.setdefault("version_tabla_gastos", 0)

    # 2. Configurar las opciones visibles según el rol
    lista_opciones = []
    if rol == "admin":
        lista_opciones = ["📊 Gráficos y Reportes", "📝 Capturar Transacciones", "📋 Resumen de Capturas", "✂️ Traspasos"]
    elif rol == "viewer":
        lista_opciones = ["📊 Gráficos y Reportes", "📋 Resumen de Capturas"]
    elif rol == "operator":
        lista_opciones = ["📝 Capturar Transacciones", "📋 Resumen de Capturas", "✂️ Traspasos"]
    else:
        st.warning("⚠️ Tu usuario no cuenta con un rol válido asignado. Contacta soporte.")
        st.stop()

    if lista_opciones:
        # MENÚ LATERAL: Esto evita que Streamlit ejecute todas las pantallas a la vez
        vista_actual = st.sidebar.radio("Navegación", lista_opciones)
        
        # --- MAPEO DE FUNCIONES CON IMPORTLIB ---
        # Solo se importa y ejecuta el módulo de la vista que el usuario seleccionó
        if vista_actual == "📊 Gráficos y Reportes":
            importlib.import_module("reportes").mostrar_pestana_reportes()
            
        elif vista_actual == "📝 Capturar Transacciones":
            importlib.import_module("captura").mostrar_pestana_captura()
            
        elif vista_actual == "📋 Resumen de Capturas":
            importlib.import_module("resumen").mostrar_pestana_resumen()
            
        elif vista_actual == "✂️ Traspasos":
            importlib.import_module("cortedia").mostrar_modulo_traspasos()
else:
    st.stop()