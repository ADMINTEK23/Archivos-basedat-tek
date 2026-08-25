import streamlit as st
import importlib
from login import ejecutar_login

# 1. Módulo de Seguridad y Login Prioritario Directo
ejecutar_login()

if st.session_state.get("autenticado", False):
    rol = st.session_state.get("rol_actual", "")

    # --- INICIALIZAR CONTADORES DE TABLAS MASIVAS ---
    st.session_state.setdefault("version_tabla_insumos", 0)
    st.session_state.setdefault("version_tabla_gastos", 0)

    # 2. Configurar las opciones visibles según el rol
    lista_opciones = []
    if rol == "admin":
        lista_opciones = [
            "📊 Gráficos y Reportes", 
            "📝 Capturar Transacciones", 
            "📋 Resumen de Capturas", 
            "✂️ Traspasos",
            "👤 Auditoría de Usuarios",
            "🔄 Recurrencia"
        ]
    elif rol == "viewer":
        lista_opciones = ["📊 Gráficos y Reportes", "📋 Resumen de Capturas"]
    elif rol == "operator":
        lista_opciones = ["📝 Capturar Transacciones", "📋 Resumen de Capturas", "✂️ Traspasos"]
    else:
        st.warning("⚠️ Tu usuario no cuenta con un rol válido asignado. Contacta soporte.")
        st.stop()

    if lista_opciones:
        # MENÚ LATERAL
        vista_actual = st.sidebar.radio("Navegación", lista_opciones)
        
        # --- MAPEO DE FUNCIONES CON IMPORTLIB ---
        if vista_actual == "📊 Gráficos y Reportes":
            importlib.import_module("reportes").mostrar_pestana_reportes()
            
        elif vista_actual == "📝 Capturar Transacciones":
            importlib.import_module("captura").mostrar_pestana_captura()
            
        elif vista_actual == "📋 Resumen de Capturas":
            importlib.import_module("resumen").mostrar_pestana_resumen()
            
        elif vista_actual == "✂️ Traspasos":
            importlib.import_module("cortedia").mostrar_modulo_traspasos()

        # 💡 CORRECCIÓN: Ahora apunta al nuevo archivo auditoria.py
        elif vista_actual == "👤 Auditoría de Usuarios":
            importlib.import_module("auditoria").mostrar_pestana_auditoria_usuarios()

        # Nuevo: Ahora apunta al nuevo archivo recurrencia.py
        elif vista_actual == "🔄 Recurrencia":
            importlib.import_module("recurrencia").mostrar_pestana_recurrencia()

else:
    st.stop()