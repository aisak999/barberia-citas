# ============================================================
#  DATOS DE LA BARBERÍA  —  cambia esto con los datos reales
# ============================================================

NOMBRE = "Mi Barbería"
BARBERO = "Nombre del barbero"
DIRECCION = "Calle Principal #10, Santo Domingo"

# Número de WhatsApp del barbero: código de país + número, solo dígitos.
# República Dominicana = 1  →  809-555-1234 se escribe "18095551234"
WHATSAPP = "18095551234"

# Servicios: la clave (izquierda) es interna, no la ve el cliente.
SERVICIOS = {
    "corte":       {"nombre": "Corte de pelo",        "minutos": 30, "precio": 300},
    "corte_barba": {"nombre": "Corte + barba",        "minutos": 45, "precio": 450},
    "barba":       {"nombre": "Barba",                "minutos": 20, "precio": 200},
    "cerquillo":   {"nombre": "Cerquillo / retoque",  "minutos": 15, "precio": 150},
    "nino":        {"nombre": "Corte de niño",        "minutos": 30, "precio": 250},
}

# Horario por día de la semana: 0 = lunes ... 6 = domingo.
# None = cerrado ese día. Formato 24 horas.
HORARIO = {
    0: ("09:00", "19:00"),
    1: ("09:00", "19:00"),
    2: ("09:00", "19:00"),
    3: ("09:00", "19:00"),
    4: ("09:00", "20:00"),
    5: ("08:00", "20:00"),
    6: None,
}

INTERVALO = 30             # cada cuántos minutos se ofrece una hora (9:00, 9:30, 10:00...)
DIAS_ADELANTE = 14         # cuántos días hacia adelante se puede reservar
RECORDAR_ANTES_MIN = 60    # minutos antes de la cita para avisarle al barbero
