"""
Sistema de citas para barbería.
Flask + SQLite. Para correrlo:  python app.py   (o doble clic en INICIAR.bat / PROBAR.bat)
"""
import hmac
import json
import os
import secrets
import socket
import sqlite3
import threading
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from functools import wraps

import qrcode
import qrcode.image.svg
from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import config

app = Flask(__name__)
app.secret_key = os.environ.get("BARBERIA_SECRETO", "solo-para-pruebas-cambiar-en-produccion")
app.permanent_session_lifetime = timedelta(days=60)   # el barbero no tiene que entrar la clave cada día
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["TEMPLATES_AUTO_RELOAD"] = True

CARPETA = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("BARBERIA_DB") or os.path.join(CARPETA, "citas.db")
DEMO = os.environ.get("BARBERIA_DEMO") == "1"         # modo prueba: barra arriba + citas de ejemplo
CLAVE_INICIAL = os.environ.get("BARBERIA_CLAVE", "barbero123")
NTFY_TEMA = os.environ.get("NTFY_TEMA", "")            # vacío = sin notificaciones push
URL_PUBLICA = os.environ.get("BARBERIA_URL", "").rstrip("/")

RD = timezone(timedelta(hours=-4))   # República Dominicana (no usa horario de verano)

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
# Horas que aparecen en las listas para elegir (de 6:00 AM a 10:00 PM, cada 30 min)
HORAS_LISTA = [f"{h:02d}:{m:02d}" for h in range(6, 23) for m in (0, 30)][:-1]


def ahora():
    """Hora actual en RD, sin zona (todas las fechas de la base están en hora de RD)."""
    return datetime.now(RD).replace(tzinfo=None)


# ---------------------------------------------------------------- base de datos

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB, isolation_level=None)   # autocommit; las transacciones las abrimos a mano
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def cerrar_db(_=None):
    con = g.pop("db", None)
    if con:
        con.close()


VERSION_DB = 2


def crear_tablas():
    con = sqlite3.connect(DB)
    if con.execute("PRAGMA user_version").fetchone()[0] < VERSION_DB:
        # La versión 1 era solo de prueba (sin datos reales): se borra y se crea la nueva.
        # Cuando ya haya clientes de verdad, aquí se harían cambios con ALTER TABLE, nunca DROP.
        con.executescript("DROP TABLE IF EXISTS citas; DROP TABLE IF EXISTS bloqueos;")
    con.executescript(f"""
        CREATE TABLE IF NOT EXISTS citas (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            token     TEXT UNIQUE NOT NULL,          -- código secreto del link del cliente
            nombre    TEXT NOT NULL,
            telefono  TEXT NOT NULL,
            servicio  TEXT NOT NULL,                 -- nombre del servicio (se guarda el texto por si luego lo borran)
            inicio    TEXT NOT NULL,                 -- 'YYYY-MM-DD HH:MM' en hora de RD
            duracion  INTEGER NOT NULL,
            precio    INTEGER NOT NULL,
            estado    TEXT NOT NULL DEFAULT 'pendiente',   -- pendiente | completada | cancelada
            aviso     INTEGER NOT NULL DEFAULT 0,          -- 1 = ya se programó el recordatorio
            creada    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS servicios (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre  TEXT NOT NULL,
            minutos INTEGER NOT NULL,
            precio  INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS horario (
            dia    INTEGER PRIMARY KEY,              -- 0 = lunes ... 6 = domingo
            abre   TEXT,                             -- NULL = cerrado ese día
            cierra TEXT
        );
        CREATE TABLE IF NOT EXISTS bloqueos (       -- días u horas libres del barbero
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha  TEXT NOT NULL,
            desde  TEXT,                             -- NULL = todo el día
            hasta  TEXT,
            motivo TEXT
        );
        CREATE TABLE IF NOT EXISTS ajustes (        -- nombre, dirección, whatsapp, clave...
            clave TEXT PRIMARY KEY,
            valor TEXT
        );
        PRAGMA user_version = {VERSION_DB};
    """)

    # La primera vez se llenan con los valores de config.py; después el barbero los cambia desde el panel
    if not con.execute("SELECT 1 FROM servicios").fetchone():
        con.executemany("INSERT INTO servicios (nombre, minutos, precio) VALUES (?, ?, ?)",
                        [(s["nombre"], s["minutos"], s["precio"]) for s in config.SERVICIOS.values()])
    if not con.execute("SELECT 1 FROM horario").fetchone():
        con.executemany("INSERT INTO horario VALUES (?, ?, ?)",
                        [(d, *(config.HORARIO[d] or (None, None))) for d in range(7)])
    for clave, valor in [("nombre", config.NOMBRE), ("direccion", config.DIRECCION), ("whatsapp", config.WHATSAPP)]:
        con.execute("INSERT OR IGNORE INTO ajustes VALUES (?, ?)", (clave, valor))
    con.commit()
    con.close()


crear_tablas()


def negocio():
    if "negocio" not in g:
        g.negocio = {r["clave"]: r["valor"] for r in db().execute("SELECT * FROM ajustes")}
    return g.negocio


def lista_servicios():
    return db().execute("SELECT * FROM servicios ORDER BY id").fetchall()


def horario_de(d):
    """(abre, cierra) en minutos para esa fecha, o None si ese día de la semana está cerrado."""
    h = db().execute("SELECT abre, cierra FROM horario WHERE dia = ?", (d.weekday(),)).fetchone()
    return (minutos(h["abre"]), minutos(h["cierra"])) if h and h["abre"] else None


def bloqueos_de(d):
    return db().execute("SELECT * FROM bloqueos WHERE fecha = ?", (d.isoformat(),)).fetchall()


# ---------------------------------------------------------------- utilidades

def minutos(texto_hm):
    h, m = map(int, texto_hm.split(":"))
    return h * 60 + m


def hora12(texto):
    """'2026-10-25 14:30' o '14:30'  →  '2:30 PM'"""
    h, m = map(int, texto[-5:].split(":"))
    return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


def fecha_larga(valor):
    """date o '2026-10-25...'  →  'Domingo 25 de octubre'"""
    d = valor if isinstance(valor, date) else date.fromisoformat(valor[:10])
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def numero_whatsapp(telefono):
    """Deja solo dígitos y le pone el 1 de RD si el número tiene 10 dígitos."""
    d = "".join(c for c in telefono if c.isdigit())
    return "1" + d if len(d) == 10 else d


def link_whatsapp(numero, texto):
    return f"https://wa.me/{numero_whatsapp(numero)}?text={urllib.parse.quote(texto)}"


def dinero(n):
    return f"RD${n:,}"


def entero(texto, defecto=0):
    try:
        return int(texto)
    except (TypeError, ValueError):
        return defecto


app.jinja_env.filters.update(hora12=hora12, fecha_larga=fecha_larga, dinero=dinero)


@app.context_processor
def datos_globales():
    return {"n": negocio(), "demo": DEMO, "DIAS": DIAS, "HORAS_LISTA": HORAS_LISTA}


# ---------------------------------------------------------------- horarios

def dia_abierto(d):
    return horario_de(d) is not None and not any(b["desde"] is None for b in bloqueos_de(d))


def horas_libres(d, duracion):
    """Lista de horas ('HH:MM') en las que cabe un servicio de `duracion` minutos ese día."""
    hoy = ahora()
    if d < hoy.date() or d > hoy.date() + timedelta(days=config.DIAS_ADELANTE) or not dia_abierto(d):
        return []

    abre, cierra = horario_de(d)
    # Lo ocupado = las citas de ese día + las horas que el barbero marcó como libres
    ocupado = [
        (minutos(c["inicio"][11:]), minutos(c["inicio"][11:]) + c["duracion"])
        for c in db().execute(
            "SELECT inicio, duracion FROM citas WHERE estado != 'cancelada' AND substr(inicio, 1, 10) = ?",
            (d.isoformat(),))
    ] + [(minutos(b["desde"]), minutos(b["hasta"])) for b in bloqueos_de(d)]
    # Hoy no se ofrecen horas que ya pasaron (con 15 minutos de margen)
    desde = hoy.hour * 60 + hoy.minute + 15 if d == hoy.date() else 0

    libres = []
    t = abre
    while t + duracion <= cierra:
        choca = any(t < fin and t + duracion > ini for ini, fin in ocupado)
        if t >= desde and not choca:
            libres.append(f"{t // 60:02d}:{t % 60:02d}")
        t += config.INTERVALO
    return libres


def proximos_dias(cuantos=config.DIAS_ADELANTE):
    hoy = ahora().date()
    dias = []
    for i in range(cuantos + 1):
        d = hoy + timedelta(days=i)
        abierto = dia_abierto(d)
        dias.append({
            "fecha": d.isoformat(),
            "semana": "Hoy" if i == 0 else "Mañana" if i == 1 else DIAS[d.weekday()][:3],
            "num": d.day,
            "mes": MESES[d.month - 1][:3],
            "abierto": abierto,
            "lleno": abierto and not horas_libres(d, 10),
            "trabaja": horario_de(d) is not None,
        })
    return dias


# ---------------------------------------------------------------- notificaciones (ntfy.sh)

def avisar(titulo, mensaje, cuando=None):
    """Manda una notificación push al teléfono del barbero usando ntfy.sh (gratis).
    `cuando` = datetime en hora de RD para programarla en el futuro (máximo 3 días).
    Devuelve el hilo para quien necesite esperar a que termine."""
    if not NTFY_TEMA:
        return None
    datos = {"topic": NTFY_TEMA, "title": titulo, "message": mensaje, "tags": ["barber"]}
    if URL_PUBLICA:
        datos["click"] = URL_PUBLICA + "/panel"
    if cuando:
        datos["delay"] = str(int(cuando.replace(tzinfo=RD).timestamp()))

    def enviar():
        try:
            req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(datos).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:   # si falla la notificación, la cita igual queda guardada
            app.logger.warning("No se pudo enviar la notificación: %s", e)

    hilo = threading.Thread(target=enviar, daemon=True)
    hilo.start()
    return hilo


def programar_recordatorio(cita):
    """Programa el aviso 'tu cita es en 1 hora' si cae dentro de los próximos 3 días."""
    inicio = datetime.fromisoformat(cita["inicio"])
    cuando = inicio - timedelta(minutes=config.RECORDAR_ANTES_MIN)
    if cita["aviso"] or not (ahora() < cuando < ahora() + timedelta(days=2, hours=23)):
        return None
    hilo = avisar(f"Cita a las {hora12(cita['inicio'])}",
                  f"{cita['nombre']} viene para {cita['servicio']}. Tel: {cita['telefono']}", cuando)
    if hilo:
        db().execute("UPDATE citas SET aviso = 1 WHERE id = ?", (cita["id"],))
    return hilo


def resumen_diario():
    """Lo corre tarea_diaria.py una vez al día (temprano): manda el resumen del día
    y programa los recordatorios que todavía no se habían podido programar."""
    hoy = ahora().date().isoformat()
    citas_hoy = db().execute(
        "SELECT * FROM citas WHERE estado = 'pendiente' AND substr(inicio, 1, 10) = ? ORDER BY inicio",
        (hoy,)).fetchall()
    hilos = []
    if citas_hoy:
        lista = "\n".join(f"{hora12(c['inicio'])} - {c['nombre']}" for c in citas_hoy)
        hilos.append(avisar(f"Hoy tienes {len(citas_hoy)} cita(s)", lista))
    for c in db().execute("SELECT * FROM citas WHERE estado = 'pendiente' AND aviso = 0 AND inicio >= ?",
                          (ahora().strftime("%Y-%m-%d %H:%M"),)).fetchall():
        hilos.append(programar_recordatorio(c))
    for h in hilos:
        if h:
            h.join()
    return len(citas_hoy)


# ---------------------------------------------------------------- páginas del cliente

@app.get("/")
def inicio():
    return render_template("reservar.html", dias=proximos_dias(), servicios=lista_servicios())


@app.get("/api/horas")
def api_horas():
    servicio = db().execute("SELECT * FROM servicios WHERE id = ?", (entero(request.args.get("servicio")),)).fetchone()
    try:
        d = date.fromisoformat(request.args.get("fecha", ""))
    except ValueError:
        return jsonify([])
    if not servicio:
        return jsonify([])
    return jsonify([{"valor": h, "texto": hora12(h)} for h in horas_libres(d, servicio["minutos"])])


@app.post("/reservar")
def reservar():
    f = request.form
    servicio = db().execute("SELECT * FROM servicios WHERE id = ?", (entero(f.get("servicio")),)).fetchone()
    nombre = " ".join(f.get("nombre", "").split())[:60]
    telefono = "".join(ch for ch in f.get("telefono", "") if ch.isdigit())[:15]
    hora = f.get("hora", "")
    try:
        d = date.fromisoformat(f.get("fecha", ""))
    except ValueError:
        d = None

    if not (servicio and d and hora and nombre and len(telefono) >= 10):
        flash("Faltan datos. Revisa el servicio, el día, la hora, tu nombre y tu WhatsApp.")
        return redirect(url_for("inicio"))

    con = db()
    con.execute("BEGIN IMMEDIATE")   # bloquea la base para que dos personas no tomen la misma hora
    try:
        if hora not in horas_libres(d, servicio["minutos"]):
            con.execute("ROLLBACK")
            flash("Esa hora se acaba de ocupar. Elige otra, por favor.")
            return redirect(url_for("inicio"))
        token = secrets.token_urlsafe(8)
        con.execute(
            "INSERT INTO citas (token, nombre, telefono, servicio, inicio, duracion, precio, creada)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (token, nombre, telefono, servicio["nombre"], f"{d.isoformat()} {hora}", servicio["minutos"],
             servicio["precio"], ahora().isoformat(timespec="seconds")))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    cita = con.execute("SELECT * FROM citas WHERE token = ?", (token,)).fetchone()
    avisar("Nueva cita 💈", f"{nombre} - {servicio['nombre']}\n{fecha_larga(d)}, {hora12(hora)}")
    programar_recordatorio(cita)
    return redirect(url_for("ver_cita", token=token))


def buscar_cita(token):
    cita = db().execute("SELECT * FROM citas WHERE token = ?", (token,)).fetchone()
    if not cita:
        abort(404)
    return cita


def utc(x):
    return (x + timedelta(hours=4)).strftime("%Y%m%dT%H%M%SZ")


@app.get("/cita/<token>")
def ver_cita(token):
    cita = buscar_cita(token)
    inicio = datetime.fromisoformat(cita["inicio"])
    fin = inicio + timedelta(minutes=cita["duracion"])
    google = "https://calendar.google.com/calendar/render?" + urllib.parse.urlencode({
        "action": "TEMPLATE",
        "text": f"{cita['servicio']} - {negocio()['nombre']}",
        "dates": f"{utc(inicio)}/{utc(fin)}",
        "location": negocio()["direccion"],
        "details": f"Tu cita en {negocio()['nombre']}. Ver o cancelar: {request.url}",
    })
    return render_template(
        "cita.html", cita=cita, google=google, futura=inicio > ahora(),
        whatsapp=link_whatsapp(negocio()["whatsapp"], f"Hola, tengo una cita el {fecha_larga(inicio)} a las {hora12(cita['inicio'])} a nombre de {cita['nombre']}."))


@app.get("/cita/<token>/cita.ics")
def calendario_ics(token):
    """Archivo de calendario (sirve en iPhone y en cualquier app de calendario) con alarma 1 hora antes."""
    cita = buscar_cita(token)
    inicio = datetime.fromisoformat(cita["inicio"])
    lineas = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Barberia//Citas//ES",
        "BEGIN:VEVENT",
        f"UID:{cita['token']}@barberia",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{utc(inicio)}",
        f"DTEND:{utc(inicio + timedelta(minutes=cita['duracion']))}",
        f"SUMMARY:{cita['servicio']} - {negocio()['nombre']}",
        f"LOCATION:{negocio()['direccion'].replace(',', chr(92) + ',')}",
        "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-PT1H",
        f"DESCRIPTION:Tu cita en {negocio()['nombre']} es en 1 hora",
        "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return Response("\r\n".join(lineas) + "\r\n", mimetype="text/calendar",
                    headers={"Content-Disposition": "attachment; filename=mi-cita.ics"})


@app.post("/cita/<token>/cancelar")
def cancelar_cita(token):
    cita = buscar_cita(token)
    if cita["estado"] == "pendiente" and datetime.fromisoformat(cita["inicio"]) > ahora():
        db().execute("UPDATE citas SET estado = 'cancelada' WHERE id = ?", (cita["id"],))
        avisar("Cita cancelada ❌", f"{cita['nombre']} canceló su cita del {fecha_larga(cita['inicio'])}, {hora12(cita['inicio'])}")
    return redirect(url_for("ver_cita", token=token))


# ---------------------------------------------------------------- panel del barbero: entrar

def requiere_login(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        if not session.get("barbero"):
            return redirect(url_for("login"))
        return vista(*args, **kwargs)
    return envoltura


def clave_correcta(clave):
    guardada = negocio().get("clave_hash")
    if guardada:                                   # el barbero ya cambió su clave desde Ajustes
        return check_password_hash(guardada, clave)
    return hmac.compare_digest(clave, CLAVE_INICIAL)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if clave_correcta(request.form.get("clave", "")):
            session.permanent = True
            session["barbero"] = True
            return redirect(url_for("panel"))
        flash("Clave incorrecta.")
    return render_template("login.html")


@app.get("/salir")
def salir():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- panel del barbero: citas

@app.get("/panel")
@requiere_login
def panel():
    vista = request.args.get("vista", "hoy")
    hoy = ahora().date()
    datos = {"vista": vista, "hoy": hoy.isoformat(), "stats": estadisticas_hoy(hoy)}

    if vista == "compartir":
        link = link_clientes()
        datos.update(link=link, es_local=es_local(link),
                     mensaje=f"¡Hola! 💈 Gracias por escribir a {negocio()['nombre']}.\n"
                             f"Reserva tu cita aquí en 1 minuto 👇\n{link}")
    elif vista == "reportes":
        datos.update(reporte(request.args.get("periodo", "dia"), request.args.get("fecha"), hoy))
    elif vista == "libres":
        datos["dias"] = proximos_dias(60)
        datos["bloqueos"] = db().execute("SELECT * FROM bloqueos WHERE fecha >= ? ORDER BY fecha, desde",
                                         (hoy.isoformat(),)).fetchall()
    elif vista == "ajustes":
        datos["horario"] = {h["dia"]: h for h in db().execute("SELECT * FROM horario")}
        datos["servicios"] = lista_servicios()
    else:
        rangos = {
            "hoy": (hoy, hoy),
            "manana": (hoy + timedelta(days=1), hoy + timedelta(days=1)),
            "proximas": (hoy, hoy + timedelta(days=90)),
        }
        desde, hasta = rangos.get(vista, rangos["hoy"])
        datos["grupos"] = citas_agrupadas(desde, hasta, hoy)
    return render_template("panel.html", **datos)


def link_clientes():
    """El link que se le manda a los clientes. Si todavía no está publicado en internet y se abrió
    como 'localhost', se cambia por la IP de esta PC en el Wi-Fi, porque 'localhost' en un
    teléfono significa 'este mismo teléfono' y no abre nada."""
    if URL_PUBLICA:
        return URL_PUBLICA
    link = request.url_root.rstrip("/")
    host = urllib.parse.urlparse(link).hostname
    if host in ("localhost", "127.0.0.1"):
        link = link.replace(host, ip_local(), 1)
    return link


def es_local(link):
    """True si el link solo funciona en este Wi-Fi (todavía no está publicado en internet)."""
    host = urllib.parse.urlparse(link).hostname or ""
    return host in ("localhost", "127.0.0.1") or host.startswith(("10.", "192.168.", "172."))


@app.get("/qr.svg")
def qr_svg():
    """QR con el link público de reservas (no es secreto, por eso no pide clave)."""
    svg = qrcode.make(link_clientes(), image_factory=qrcode.image.svg.SvgPathImage, border=2)
    return Response(svg.to_string(encoding="unicode"), mimetype="image/svg+xml",
                    headers={"Content-Disposition": "inline; filename=qr-citas.svg"})


@app.get("/panel/cartel")
@requiere_login
def cartel():
    """Hoja para imprimir y pegar en la barbería: 'Reserva tu cita' + QR."""
    return render_template("cartel.html", link=link_clientes())


@app.get("/manifest.json")
def manifest():
    """Permite instalar el panel en el teléfono como si fuera una app (ícono en la pantalla de inicio)."""
    return jsonify({
        "name": f"{negocio()['nombre']} · Citas",
        "short_name": "Citas",
        "start_url": "/panel",
        "display": "standalone",
        "background_color": "#0f0f0f",
        "theme_color": "#0f0f0f",
        "icons": [
            {"src": url_for("static", filename="icono-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": url_for("static", filename="icono-512.png"), "sizes": "512x512", "type": "image/png"},
        ],
    })


def citas_agrupadas(desde, hasta, hoy):
    """Citas entre dos fechas, agrupadas por día, con el link de recordatorio por WhatsApp listo."""
    grupos = {}
    for c in db().execute(
            "SELECT * FROM citas WHERE estado != 'cancelada' AND substr(inicio, 1, 10) BETWEEN ? AND ?"
            " ORDER BY inicio", (desde.isoformat(), hasta.isoformat())):
        cuando = "hoy" if c["inicio"][:10] == hoy.isoformat() else f"el {fecha_larga(c['inicio'])}"
        texto = (f"Hola {c['nombre'].split()[0]} 👋 Te recuerdo tu cita en {negocio()['nombre']} "
                 f"{cuando} a las {hora12(c['inicio'])} ({c['servicio']}). ¡Te espero! 💈\n"
                 f"Si no puedes venir, cancélala aquí: {link_clientes()}{url_for('ver_cita', token=c['token'])}")
        grupos.setdefault(c["inicio"][:10], []).append({
            **dict(c), "wa": link_whatsapp(c["telefono"], texto),
            "puede_atender": c["inicio"][:10] <= hoy.isoformat(),   # no se cobra una cita que todavía no llega
        })
    return grupos


def estadisticas_hoy(hoy):
    citas = db().execute(
        "SELECT estado, precio FROM citas WHERE estado != 'cancelada' AND substr(inicio, 1, 10) = ?",
        (hoy.isoformat(),)).fetchall()
    return {
        "total": len(citas),
        "pendientes": sum(1 for c in citas if c["estado"] == "pendiente"),
        "ganado": sum(c["precio"] for c in citas if c["estado"] == "completada"),
    }


# ---------------------------------------------------------------- reportes de ganancias

def rango_periodo(periodo, ref):
    """Primer y último día del día / semana (lunes a domingo) / mes que contiene `ref`."""
    if periodo == "semana":
        ini = ref - timedelta(days=ref.weekday())
        return ini, ini + timedelta(days=6)
    if periodo == "mes":
        ini = ref.replace(day=1)
        return ini, (ini + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return ref, ref


def mover_periodo(periodo, ref, pasos):
    if periodo == "semana":
        return ref + timedelta(days=7 * pasos)
    if periodo == "mes":
        m = ref.month - 1 + pasos
        return date(ref.year + m // 12, m % 12 + 1, 1)
    return ref + timedelta(days=pasos)


def reporte(periodo, fecha_texto, hoy):
    """Lo ganado (citas marcadas como atendidas) en un día, semana o mes, con el detalle de cada corte."""
    if periodo not in ("dia", "semana", "mes"):
        periodo = "dia"
    try:
        ref = min(date.fromisoformat(fecha_texto or ""), hoy)
    except ValueError:
        ref = hoy
    ini, fin = rango_periodo(periodo, ref)

    filas = db().execute(
        "SELECT * FROM citas WHERE substr(inicio, 1, 10) BETWEEN ? AND ? ORDER BY inicio",
        (ini.isoformat(), fin.isoformat())).fetchall()
    atendidas = [c for c in filas if c["estado"] == "completada"]
    # Citas que ya pasaron y nadie marcó: el barbero tiene que decir si vino o no
    sin_marcar = [c for c in filas if c["estado"] == "pendiente" and datetime.fromisoformat(c["inicio"]) < ahora()]

    por_servicio = {}
    for c in atendidas:
        s = por_servicio.setdefault(c["servicio"], {"cantidad": 0, "total": 0})
        s["cantidad"] += 1
        s["total"] += c["precio"]
    por_dia = {}
    for c in atendidas:
        por_dia.setdefault(c["inicio"][:10], []).append(c)

    if periodo == "dia":
        titulo = "Hoy" if ini == hoy else "Ayer" if ini == hoy - timedelta(days=1) else fecha_larga(ini)
        rango = fecha_larga(ini)
    elif periodo == "semana":
        titulo = "Esta semana" if ini <= hoy <= fin else "Semana pasada" if ini <= hoy - timedelta(days=7) <= fin else "Semana"
        rango = f"Del {ini.day} de {MESES[ini.month - 1]} al {fin.day} de {MESES[fin.month - 1]}"
    else:
        titulo = "Este mes" if ini <= hoy <= fin else MESES[ini.month - 1].capitalize()
        rango = f"{MESES[ini.month - 1].capitalize()} {ini.year}"

    total = sum(c["precio"] for c in atendidas)
    siguiente = mover_periodo(periodo, ini, 1)
    return {
        "periodo": periodo, "titulo": titulo, "rango": rango,
        "total": total, "cantidad": len(atendidas),
        "promedio": round(total / len(atendidas)) if atendidas else 0,
        "por_servicio": sorted(por_servicio.items(), key=lambda x: -x[1]["total"]),
        "por_dia": por_dia, "sin_marcar": sin_marcar,
        "anterior": mover_periodo(periodo, ini, -1).isoformat(),
        "siguiente": siguiente.isoformat() if siguiente <= hoy else None,
    }


@app.post("/panel/cita/<int:id>/<estado>")
@requiere_login
def cambiar_estado(id, estado):
    if estado not in ("pendiente", "completada", "cancelada"):
        abort(400)
    cita = db().execute("SELECT inicio FROM citas WHERE id = ?", (id,)).fetchone()
    if not cita:
        abort(404)
    if estado == "completada" and cita["inicio"][:10] > ahora().date().isoformat():
        flash("Solo puedes marcar como atendida una cita el día que le toca.")
    else:
        db().execute("UPDATE citas SET estado = ? WHERE id = ?", (estado, id))
    return redirect(request.referrer or url_for("panel"))


# ---------------------------------------------------------------- panel del barbero: días libres

@app.post("/panel/libres")
@requiere_login
def agregar_libre():
    f = request.form
    try:
        d = date.fromisoformat(f.get("fecha", ""))
    except ValueError:
        flash("Toca el día que no vas a trabajar.")
        return redirect(url_for("panel", vista="libres"))

    desde = hasta = None
    if f.get("tipo") == "horas":
        desde, hasta = f.get("desde", ""), f.get("hasta", "")
        if desde not in HORAS_LISTA or hasta not in HORAS_LISTA or minutos(desde) >= minutos(hasta):
            flash("La hora de 'Hasta' tiene que ser después de 'Desde'.")
            return redirect(url_for("panel", vista="libres"))

    db().execute("INSERT INTO bloqueos (fecha, desde, hasta, motivo) VALUES (?, ?, ?, ?)",
                 (d.isoformat(), desde, hasta, f.get("motivo", "").strip()[:80]))

    # ¿Había clientes con cita en ese rango? Se le avisa al barbero para que les escriba.
    afectadas = [
        c for c in db().execute(
            "SELECT * FROM citas WHERE estado = 'pendiente' AND substr(inicio, 1, 10) = ? ORDER BY inicio",
            (d.isoformat(),))
        if desde is None or (minutos(c["inicio"][11:]) < minutos(hasta)
                             and minutos(c["inicio"][11:]) + c["duracion"] > minutos(desde))
    ]
    rango = "todo el día" if desde is None else f"de {hora12(desde)} a {hora12(hasta)}"
    flash(f"✓ Listo: {fecha_larga(d)} libre {rango}. Ya nadie puede reservar ahí.")
    if afectadas:
        nombres = ", ".join(f"{c['nombre']} ({hora12(c['inicio'])})" for c in afectadas)
        flash(f"⚠ Ojo: ya tenías cita con {nombres}. Escríbeles desde la pestaña de citas para moverlas.")
    return redirect(url_for("panel", vista="libres"))


@app.post("/panel/libres/<int:id>/quitar")
@requiere_login
def quitar_libre(id):
    db().execute("DELETE FROM bloqueos WHERE id = ?", (id,))
    flash("✓ Listo, ese tiempo vuelve a estar disponible para reservar.")
    return redirect(url_for("panel", vista="libres"))


# ---------------------------------------------------------------- panel del barbero: ajustes

@app.post("/panel/ajustes/negocio")
@requiere_login
def guardar_negocio():
    for clave, largo in (("nombre", 40), ("direccion", 100), ("whatsapp", 20)):
        valor = request.form.get(clave, "").strip()[:largo]
        if clave == "whatsapp":
            valor = numero_whatsapp(valor)
        if valor:
            db().execute("INSERT OR REPLACE INTO ajustes VALUES (?, ?)", (clave, valor))
    flash("✓ Datos de la barbería guardados.")
    return redirect(url_for("panel", vista="ajustes"))


@app.post("/panel/ajustes/horario")
@requiere_login
def guardar_horario():
    f = request.form
    for d in range(7):
        abre, cierra = f.get(f"abre{d}"), f.get(f"cierra{d}")
        if not f.get(f"abierto{d}"):
            abre = cierra = None
        elif abre not in HORAS_LISTA or cierra not in HORAS_LISTA or minutos(abre) >= minutos(cierra):
            flash(f"El {DIAS[d]} la hora de cerrar tiene que ser después de la de abrir. No se guardó.")
            return redirect(url_for("panel", vista="ajustes"))
        db().execute("INSERT OR REPLACE INTO horario VALUES (?, ?, ?)", (d, abre, cierra))
    flash("✓ Horario guardado.")
    return redirect(url_for("panel", vista="ajustes"))


@app.post("/panel/ajustes/servicios")
@requiere_login
def guardar_servicios():
    f = request.form
    for s in lista_servicios():
        if f.get(f"borrar{s['id']}"):
            db().execute("DELETE FROM servicios WHERE id = ?", (s["id"],))
            continue
        nombre = f.get(f"nombre{s['id']}", "").strip()[:40]
        mins, precio = entero(f.get(f"minutos{s['id']}")), entero(f.get(f"precio{s['id']}"), -1)
        if nombre and 5 <= mins <= 240 and precio >= 0:
            db().execute("UPDATE servicios SET nombre = ?, minutos = ?, precio = ? WHERE id = ?",
                         (nombre, mins, precio, s["id"]))

    nombre = f.get("nuevo_nombre", "").strip()[:40]
    if nombre:
        db().execute("INSERT INTO servicios (nombre, minutos, precio) VALUES (?, ?, ?)",
                     (nombre, entero(f.get("nuevo_minutos"), 30), max(0, entero(f.get("nuevo_precio")))))
    if not lista_servicios():   # nunca dejar la barbería sin servicios
        db().execute("INSERT INTO servicios (nombre, minutos, precio) VALUES ('Corte de pelo', 30, 300)")
        flash("Tiene que quedar al menos un servicio.")
    flash("✓ Servicios guardados.")
    return redirect(url_for("panel", vista="ajustes") + "#servicios")


@app.post("/panel/ajustes/clave")
@requiere_login
def cambiar_clave():
    f = request.form
    nueva = f.get("nueva", "")
    if not clave_correcta(f.get("actual", "")):
        flash("La clave actual no es correcta.")
    elif len(nueva) < 6:
        flash("La clave nueva debe tener al menos 6 caracteres.")
    elif nueva != f.get("repetir"):
        flash("Las dos claves nuevas no son iguales.")
    else:
        db().execute("INSERT OR REPLACE INTO ajustes VALUES ('clave_hash', ?)", (generate_password_hash(nueva),))
        flash("✓ Clave cambiada. Úsala la próxima vez que entres.")
    return redirect(url_for("panel", vista="ajustes") + "#clave")


@app.errorhandler(404)
def no_encontrado(_):
    return render_template("404.html"), 404


# ---------------------------------------------------------------- modo prueba

def cargar_demo():
    """Llena la base de prueba con citas de ejemplo para que el panel no se vea vacío."""
    con = sqlite3.connect(DB)
    if con.execute("SELECT 1 FROM citas").fetchone():
        con.close()
        return
    con.close()
    nombres = ["Carlos Martínez", "José Rodríguez", "Miguel Santos", "Andrés Peña", "Luis Fernández",
               "Rafael Gómez", "Pedro Castillo", "Juan Reyes", "Manuel Díaz", "Ángel Núñez"]
    with app.test_request_context():
        servicios = lista_servicios()
        hoy, k = ahora().date(), 0
        for dias in range(0, 6):
            d = hoy + timedelta(days=dias)
            for hora in horas_libres(d, 30)[1::4][:3]:
                s = servicios[k % len(servicios)]
                estado = "completada" if dias == 0 and k == 0 else "pendiente"
                db().execute(
                    "INSERT INTO citas (token, nombre, telefono, servicio, inicio, duracion, precio, estado, creada)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (secrets.token_urlsafe(8), nombres[k % len(nombres)], f"829555{1000 + k}", s["nombre"],
                     f"{d.isoformat()} {hora}", s["minutos"], s["precio"], estado, ahora().isoformat()))
                k += 1
        # Citas de las últimas 5 semanas ya atendidas, para que los reportes tengan datos
        for dias in range(1, 36):
            d = hoy - timedelta(days=dias)
            if not horario_de(d):
                continue
            for j in range(2 + dias % 3):
                t = horario_de(d)[0] + 30 + j * 90
                s = servicios[k % len(servicios)]
                db().execute(
                    "INSERT INTO citas (token, nombre, telefono, servicio, inicio, duracion, precio, estado, creada)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (secrets.token_urlsafe(8), nombres[k % len(nombres)], f"829555{1000 + k}", s["nombre"],
                     f"{d.isoformat()} {t // 60:02d}:{t % 60:02d}", s["minutos"], s["precio"],
                     "pendiente" if dias == 1 and j == 0 else "cancelada" if k % 9 == 0 else "completada",
                     ahora().isoformat()))
                k += 1


def ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # no envía nada; solo sirve para saber la IP de esta PC en el Wi-Fi
        return s.getsockname()[0]
    except OSError:
        return "localhost"


if __name__ == "__main__":
    if DEMO:
        cargar_demo()
    ip = ip_local()
    print("\n  ==================================================")
    print("   PÁGINA DE CLIENTES:  http://localhost:5000")
    print("   PANEL DEL BARBERO:   http://localhost:5000/panel")
    print(f"   Clave:               {CLAVE_INICIAL}")
    print(f"   Desde tu celular (mismo Wi-Fi):  http://{ip}:5000")
    print("   Para apagarlo: cierra esta ventana")
    print("  ==================================================\n")
    # host 0.0.0.0 = también se puede abrir desde el celular si está en el mismo Wi-Fi
    app.run(host="0.0.0.0", port=5000)
