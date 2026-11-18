"""
Se corre una vez al día, temprano (ej. 7:00 AM), con el programador de tareas del hosting.
Le manda al barbero el resumen del día y programa los recordatorios pendientes.
"""
from app import app, resumen_diario

with app.app_context():
    n = resumen_diario()
    print(f"Resumen enviado: {n} cita(s) hoy")
