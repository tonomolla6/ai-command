# Changelog

## Unreleased

- Resume de Codex conserva el índice que contiene el goal cuando una conversación importada comparte el mismo transcript nativo: objetivo, presupuesto, contadores y estado permanecen intactos al cambiar de cuenta. Descubre también el índice restaurado y detecta conflictos sin fusionar bases ni recrear objetivos.
- Pruebas de regresión cubren goals en ambos índices, cambios de perfil, restauración inicial, historiales distintos, conflictos y bases ilegibles.
- La selección automática de Codex/Claude comparte la caché oficial de 120 segundos con uso, refresca sólo perfiles pendientes y evita consultas duplicadas entre lanzamientos simultáneos. Revalida al cruzar resets y permite `ai auto --refresh`; no reutiliza porcentajes históricos tras un fallo.
- AI Command Agentes 0.1.14 añade puntos independientes de actividad y proveedor, colores por agente, temas claro/oscuro y alto contraste, texto accesible y detección nativa de AGY/OpenCode sin inferir actividad.
- Pruebas con cuatro procesos verifican una sola consulta por cuenta; la selección reciente conserva el ID exacto de resume y las reglas de identidad/disponibilidad.

## 1.4.0 — 2026-10-08

- Política explícita `ai configure --codex-danger on/off`: usa el bypass oficial de Codex para sesiones nuevas, resume, exec y review, con selección automática, cuenta numerada o ACCOUNT; desactivada por defecto.
- Normaliza opciones de permisos incompatibles conservando prompts, identidad y modelo reservado. Ayuda, administración y consultas oficiales permanecen independientes; `codex resume --help` no inicia selección de cuenta.
- AGY aplica su bypass sin un flag de sandbox contradictorio; OpenCode mantiene `--auto` en sesiones nuevas y resume, respetando los deny explícitos.
- Pruebas con lanzadores instalados, procesos nativos de fixture y PTY comprueban permisos, resume exacto, aislamiento, backups y consultas sin bypass.

## 1.3.0 — 2026-10-08

- `ai usage --monitoring`: panel de terminal con consultas oficiales silenciosas cada cinco minutos en segundo plano, sin superposición. Mantiene datos visibles, filtro de proveedor, colores, cuenta atrás, scroll, resize y salida limpia con `q`/Ctrl+C.
- Pruebas reales de PTY verifican consulta periódica, actualización manual, errores, restauración de terminal y cancelación de procesos propios, incluidos hijos con una sesión separada.
- La extensión de terminales admite listas de clientes vacías en servidores tmux antiguos que usan la conexión privada; incluye una prueba con servidor real.
- Las tarjetas de uso muestran los resets manuales disponibles publicados por Codex, separados del saldo y de las fechas de reinicio automático. Cero, dato no publicado y consulta fallida se distinguen sin consumir resets ni modificar la selección de cuenta.
- VS Code conserva las pestañas y grupos Split/Join; tmux conserva los agentes. La recuperación usa claves existentes, adopta pestañas revividas y evita terminales vacías adicionales y cambios de foco.
- Resume nativo de Claude resuelve conversaciones de otros perfiles por su ruta original, sin compartir credenciales. Codex y Claude conservan scroll normal.
- Conexión privada para servidores tmux 3.2–3.5 activos, sin control clients ni reiniciar agentes. Pruebas reales de PTY y VS Code con dos reinicios, Split, Join y scroll.
- La extensión de terminales incluye sus lanzadores y usa una ruta absoluta para conservar la clave al restaurar; evita que otro perfil o un comando antiguo en PATH abra sesiones vacías adicionales.
- Consulta Claude `/usage` sin interacción cuando el ejecutable instalado declara soporte local, incluso después de una actualización; conserva PTY para formatos desconocidos y exige cero turnos y coste.
- Los perfiles OAuth vacíos aparecen como `SIN LOGIN`, con el comando de login correspondiente.
- VS Code identifica el proceso nativo detrás de los lanzadores `ai`/Python y usa el tmux de PATH. Las extensiones anteriores se retiran del registro con backup, conservando sus archivos.
- El mantenimiento nativo de Codex instala también `codex-code-mode-host` de la misma release y arquitectura, con checksums oficiales y validación previa de ambos ejecutables.
- Los backups y la recuperación incluyen el auxiliar; un fallo al sustituir el CLI restaura la pareja anterior. El mantenimiento se aplaza si cualquiera de los dos procesos está activo.

## 1.2.0 — 2026-10-07

- Prioridad `low` por cuenta: la rotación usa antes las cuentas normales con cuota verificada y mantiene el orden por reset dentro de cada prioridad. Se restaura con `normal` y aparece en cuentas y uso.
- `ACCOUNT=codex2 codex` y `ACCOUNT=claude2 claude` eligen explícitamente para ese lanzamiento, incluido resume y comandos nativos. También admiten xN/cN, número o correo registrado; no consultan cuotas ni rotan a otra cuenta.
- Los perfiles, el modelo reservado y los permisos de Claude se respetan al elegir por variable; las cuentas desactivadas y los proveedores distintos se rechazan.

## 1.1.2 — 2026-10-07

- Conserva los lanzadores y atajos idénticos entre versiones para evitar escrituras y fsync innecesarios en servidores con disco ocupado. Los cambios reales mantienen backup y sustitución atómica.
- Comprueba que los lanzadores iguales conservan su inode, repara su modo y mantiene backups de los lanzadores modificados.

## 1.1.1 — 2026-10-07

- Solicita JSON explícitamente al consultar metadatos de GitHub/npm para actualizaciones de binarios independientes; mantiene las descargas de archivos en modo binario.
- Pruebas de regresión de las cabeceras HTTP en la capa real de descarga.

## 1.1.0 — 2026-10-07

- Políticas explícitas de autoaprobación para AGY y OpenCode en sesiones nuevas, resume y comandos nativos mediante integración Bash opcional.
- Aviso de release estable al usar comandos interactivos; aceptar actualiza y ejecuta de nuevo el argv original sin cambiar de directorio.
- `ai usage <proveedor>` y `ai limits <proveedor>` consultan sólo las cuentas elegidas y aprovechan el ancho del panel.
- Cron opt-in para actualizadores oficiales de los cuatro CLI, con aplazamiento de procesos activos, backups de código, comprobación y recuperación.

## 1.0.3 — 2026-10-07

- Nombres e identificadores genéricos en las integraciones de VS Code.
- Control de contenido público en CI y antes de construir paquetes: archivos privados, correos reales y credenciales reconocibles.
- Ejemplos y capturas con datos ficticios; configuración y autenticación permanecen fuera de las releases.

## 1.0.2 — 2026-10-07

- Suite de pruebas portable sin Codex/Claude instalados y cierre explícito de fixtures SQLite en Python 3.13.
- CI valida Python 3.11, 3.12 y 3.13 sin duplicar ejecuciones al publicar un tag.

## 1.0.1 — 2026-10-07

- La migración de una instalación anterior conserva sus ejecutables nativos aunque no estén directamente en PATH.
- Añadida prueba de actualización desde el layout anterior usado en servidores compartidos.
- Las consultas y lanzamientos gestionados de AGY desactivan su auto-update nativo para conservar los binarios compartidos.

## 1.0.0 — 2026-10-07

- Primera versión pública: cuentas por correo, perfiles aislados y atajos dinámicos.
- Codex y Claude automáticos abren sesión nueva; resume explícito por ID/directorio.
- Uso en paralelo con caché, tres columnas, resets, créditos y dependencias agotadas.
- Antigravity, catálogo FREE y actividad local aproximada de OpenCode.
- Handoff seguro mediante estado Git y notas del usuario.
- Instalación por usuario/global, releases con SHA-256, activación atómica y rollback.
- Migraciones privadas, idempotentes y compatibles con la instalación anterior.
- Política opt-in de bypass Claude, incluida reanudación con flags nativos.
- Extensiones locales VS Code y skill para gestionar la herramienta.
