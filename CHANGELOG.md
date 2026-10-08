# Changelog

## Unreleased

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
