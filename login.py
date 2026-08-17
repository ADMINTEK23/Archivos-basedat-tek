import streamlit as st
import bcrypt
from sqlalchemy import text
from db_utils import obtener_engine_maestro

# 1. CONSTANTE GLOBAL: Evitamos crear este diccionario en cada recarga de página
PERMISOS = {
    "admin": ["crear", "editar", "eliminar", "ver"],
    "viewer": ["ver"],
    "operator": ["crear", "ver"]
}

def verificar_y_actualizar_password(usuario, password_plano):
    """Busca al usuario en Supabase y valida sus credenciales de forma directa con bcrypt."""
    engine_local = obtener_engine_maestro()
    
    query = text("SELECT id, usuario, password_hash, rol FROM usuarios WHERE usuario = :user")
    try:
        with engine_local.connect() as conn:
            resultado = conn.execute(query, {"user": usuario}).fetchone()
        
        if resultado:
            # 2. DESEMPAQUETADO LIMPIO: Python asigna los valores automáticamente
            db_id, db_usuario, db_hash, db_rol = resultado 
            
            # --- LÓGICA DE MIGRACIÓN AUTOMÁTICA ---
            if not db_hash.startswith("$2b$"):
                if db_hash == password_plano:
                    salt = bcrypt.gensalt()
                    nuevo_hash = bcrypt.hashpw(password_plano.encode('utf-8'), salt).decode('utf-8')
                    update_query = text("UPDATE usuarios SET password_hash = :nh WHERE id = :id")
                    with engine_local.begin() as conn:
                        conn.execute(update_query, {"nh": nuevo_hash, "id": db_id})
                    return True, db_rol
                return False, None
            
            # --- VERIFICACIÓN CRIPTOGRÁFICA ---
            if bcrypt.checkpw(password_plano.encode('utf-8'), db_hash.encode('utf-8')):
                return True, db_rol
                
        return False, None
    except Exception as e:
        st.error(f"Error de seguridad en la base de datos de usuarios: {e}")
        return False, None

def ejecutar_login():
    """Inicializa el estado de la sesión y dibuja el formulario de control de acceso."""
    
    # 3. ACTUALIZACIÓN MASIVA DEL ESTADO
    if "autenticado" not in st.session_state:
        st.session_state.update({
            "autenticado": False,
            "usuario_actual": "",
            "rol_actual": "",
            "permisos_actuales": []
        })

    if not st.session_state["autenticado"]:
        st.title("CONTINÚA CON:")
        with st.form("formulario_login"):
            usuario_input = st.text_input("Usuario")
            password_input = st.text_input("Contraseña", type="password")
            boton_login = st.form_submit_button("Ingresar")
            
            if boton_login:
                es_valido, rol_usuario = verificar_y_actualizar_password(usuario_input, password_input)
                
                if es_valido:
                    st.session_state.update({
                        "autenticado": True,
                        "usuario_actual": usuario_input,
                        "rol_actual": rol_usuario,
                        "permisos_actuales": PERMISOS.get(rol_usuario, ["ver"])
                    })
                    st.rerun()
                else:
                    st.error("Usuario o contraseña incorrectos")
        st.stop()

    # Barra lateral informativa de cierre de sesión
    st.sidebar.markdown(f"👤 *Usuario:* {st.session_state['usuario_actual']} ({st.session_state['rol_actual'].upper()})")
    if st.sidebar.button("Cerrar Sesión"):
        # 4. LIMPIEZA EFICIENTE: Eliminamos las llaves en lugar de vaciarlas, previniendo errores de estado residual
        for key in ["autenticado", "usuario_actual", "rol_actual", "permisos_actuales"]:
            st.session_state.pop(key, None)
        st.rerun()