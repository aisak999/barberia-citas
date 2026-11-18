# Sistema de citas para barbería — Guía

Hecho con **Python + Flask + SQLite**. Gratis de mantener: no hay mensualidades.

---

## 1. Probarlo en tu computadora

**Modo prueba (lo más fácil):** doble clic a **`PROBAR.bat`**, o al acceso directo
**"Probar barbería"** del escritorio. Abre el panel con citas de ejemplo y una barra amarilla
arriba para cambiar entre la vista del **cliente** y la del **barbero**. Cada vez que lo abres empieza
limpio, porque usa su propia base de datos (`prueba.db`).

**Modo real:**
1. Dale **doble clic a `INICIAR.bat`**.
2. Se abre el navegador en la página de los clientes: `http://localhost:5000`
3. El panel del barbero está en `http://localhost:5000/panel`. La clave de prueba es **`barbero123`**.
4. Para apagarlo, cierra la ventana negra.

**Para probarlo en tu celular:** la computadora y el celular tienen que estar en el mismo Wi-Fi.
En la ventana negra aparece una línea como `Running on http://192.168.1.25:5000`.
Abre esa dirección en el celular.

---

## 2. Cómo está hecho (léelo con el código abierto al lado)

```
barberia-citas/
├── app.py            ← TODO el programa: rutas, base de datos, horarios, notificaciones
├── config.py         ← datos de la barbería (nombre, servicios, precios, horario)
├── tarea_diaria.py   ← se corre 1 vez al día: resumen + recordatorios
├── templates/        ← las páginas (HTML con Jinja)
│   ├── base.html         plantilla común (fuentes, estilos)
│   ├── reservar.html     página del cliente (4 pasos)
│   ├── cita.html         confirmación de la cita del cliente
│   ├── login.html        entrada del barbero
│   └── panel.html        panel del barbero
├── static/
│   ├── estilo.css    ← todos los colores y el diseño
│   └── manifest.json ← permite "instalar" el panel en el teléfono como una app
└── citas.db          ← la base de datos (se crea sola la primera vez)
```

### El viaje de una reserva, paso a paso
1. El cliente abre `/`. La función **`inicio()`** muestra `reservar.html` con los próximos 14 días.
2. Cuando elige servicio y día, el JavaScript de `reservar.html` llama a **`/api/horas`**.
   Esa ruta usa **`horas_libres()`**, la función más importante del programa:
   - toma el horario del día desde `config.HORARIO`
   - busca las citas que ya hay ese día
   - va probando cada 30 minutos y solo devuelve las horas en las que **cabe el servicio
     completo sin chocar** con otra cita (`choca = any(...)`)
3. El cliente toca "Reservar" y el formulario se envía a **`reservar()`**:
   - valida los datos
   - hace `BEGIN IMMEDIATE`, que bloquea la base un instante para que **dos personas no puedan
     tomar la misma hora** si reservan al mismo tiempo
   - vuelve a comprobar que la hora sigue libre y guarda la cita con un **token** secreto
   - le avisa al barbero (`avisar()`) y lo lleva a `/cita/<token>`
4. En `/cita/<token>` el cliente ve su cita, la agrega a su calendario (el celular le avisa
   1 hora antes) o la cancela.

### Base de datos (2 tablas)
- **`citas`**: una fila por cita. El campo `estado` puede ser `pendiente`, `completada` o `cancelada`.
- **`bloqueos`**: los días que el barbero cierra.

Para ver los datos por dentro puedes usar la extensión "SQLite Viewer" de VS Code.

---

## 3. Lo que el barbero cambia solo (panel de administrador)

En el panel tiene 2 pestañas de administrador:
- **🌴 Días libres:** toca el día en el calendario y elige **"Todo el día"** o **"Solo unas horas"**
  (con "Desde" y "Hasta"). Ese tiempo desaparece de la página de los clientes. Si ya tenía citas
  en ese tiempo, el sistema le dice con quién para que les escriba.
  En el código: tabla `bloqueos` y funciones `agregar_libre()` y `horas_libres()`.
- **⚙️ Ajustes:** el horario de cada día de la semana, los servicios y precios (puede agregar o quitar),
  el nombre, la dirección, el WhatsApp y la clave. Todo se guarda en las tablas `horario`,
  `servicios` y `ajustes`. La clave se guarda **cifrada** con `generate_password_hash`, nunca en texto.

**`config.py`** ahora solo tiene los **valores iniciales**: se usan la primera vez que se crea
la base de datos. Después, lo que cuenta es lo que el barbero guarde en el panel.

Los colores están al principio de `static/estilo.css` (`--amarillo`, `--negro`...).

---

## 4. Publicarlo en internet gratis (PythonAnywhere)

1. Crea una cuenta gratis en **pythonanywhere.com**. Tu página va a quedar en
   `https://TUUSUARIO.pythonanywhere.com`. Elige un usuario con el nombre de la barbería.
2. En **Files**, sube los archivos (sin `.venv` ni `citas.db`). O desde **Consoles → Bash**:
   `git clone` de tu repositorio de GitHub.
3. En **Web → Add a new web app → Flask**. En el archivo WSGI que te crean, deja:
   ```python
   import sys, os
   sys.path.insert(0, "/home/TUUSUARIO/barberia-citas")
   os.environ["BARBERIA_CLAVE"] = "una-clave-que-solo-sepa-el-barbero"
   os.environ["BARBERIA_SECRETO"] = "pon-aqui-letras-al-azar-muy-largas"
   os.environ["BARBERIA_URL"] = "https://TUUSUARIO.pythonanywhere.com"
   os.environ["NTFY_TEMA"] = "barberia-nombre-x7k2p9"   # ver punto 5
   from app import app as application
   ```
4. En **Consoles → Bash** instala la librería del QR: `pip3 install --user qrcode`
   (Flask ya viene instalado en PythonAnywhere).
5. Dale a **Reload**. ¡Ya está en internet!
6. En **Tasks**, programa `python3 /home/TUUSUARIO/barberia-citas/tarea_diaria.py`
   todos los días a las **11:00 UTC**, que son las **7:00 AM en RD**.

> En la cuenta gratis de PythonAnywhere tienes que entrar **una vez cada 3 meses** y darle
> al botón "Run until 3 months from today", o la página se apaga.

> **Importante:** cambia la clave `barbero123`. Si no la cambias, cualquiera podría entrar al panel.

---

## 5. Notificaciones al teléfono del barbero (gratis, con ntfy)

1. El barbero instala la app **ntfy** (Play Store o App Store).
2. Le da a **+** (suscribirse) y escribe el mismo tema que pusiste en `NTFY_TEMA`.
   Usa un nombre difícil de adivinar, porque cualquiera que sepa el tema puede leer los avisos.
3. Desde ese momento le llega una notificación:
   - 💈 cuando alguien **reserva**
   - ❌ cuando alguien **cancela**
   - ⏰ **1 hora antes** de cada cita
   - 📋 a las 7 AM, con la **lista de citas del día**

> Prueba que lleguen. Las cuentas gratis de PythonAnywhere solo pueden conectarse a algunos
> sitios de internet. Si las notificaciones no llegan, ese es el motivo. Aun así el sistema
> funciona igual: las citas se guardan y el panel se actualiza solo cada minuto.

**Al cliente** le avisa su propio calendario: en la confirmación hay botones para agregar la
cita a Google Calendar o al calendario del iPhone. Además, el barbero tiene en cada cita el
botón **"Recordar por WhatsApp"**, que le abre el chat del cliente con el mensaje ya escrito.

---

## 6. Que el link le llegue solo al cliente por WhatsApp

En el panel, el botón amarillo **"📲 Mi link"** (o la pestaña **Compartir**) tiene todo listo:
el link en grande, botones para copiarlo o mandarlo por WhatsApp, el **código QR**, un
**cartel para imprimir** (`/panel/cartel`) y el mensaje de bienvenida ya escrito para copiar.

Esto se hace con WhatsApp Business, que es gratis:
1. El barbero usa **WhatsApp Business** (si usa el normal, se puede pasar sin perder chats).
2. **Herramientas para la empresa → Mensaje de bienvenida → Activar.**
3. Mensaje de ejemplo:
   > ¡Hola! 💈 Bienvenido a [Nombre]. Reserva tu cita aquí en 1 minuto:
   > https://TUUSUARIO.pythonanywhere.com
4. Opcional: **Respuestas rápidas**. Si escribe `/cita`, se pega el link solo.

Y para que el barbero tenga el panel como una app: que abra `/panel` en Chrome y le dé a
**menú ⋮ → Agregar a pantalla de inicio**.

---

## 7. Retos para que lo mejores tú (así aprendes de verdad)

En orden de dificultad:
1. Agregar un campo opcional de "nota" en la reserva (ej. "fade bajito"). Toca la tabla, el formulario y el panel.
2. Que el barbero pueda ver citas **canceladas** en una pestaña aparte.
3. Una pestaña "Ganancias" con lo ganado en la semana y en el mes (`SUM(precio)` agrupando por fecha).
4. Que el barbero cambie precios y horario **desde el panel**, sin tocar `config.py` (pasar los servicios a una tabla).
5. Que el barbero pueda **mover** una cita a otra hora.
