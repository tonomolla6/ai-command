# Changelog

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
