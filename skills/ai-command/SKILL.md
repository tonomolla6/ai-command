---
name: ai-command
description: "Administra AI Command: altas por correo, cuentas, cuotas, sesiones, handoff y actualizaciones del gestor en Linux. No despliega las aplicaciones del usuario."
---

# AI Command

Usa `ai --help` y `ai accounts --json` antes de cambiar un registro. La configuración
es privada y por HOME; nunca incluyas sus correos reales, credenciales, cachés o
conversaciones en repositorios, releases, ejemplos ni capturas públicas.

## Alta y selección

```bash
ai add codex persona@example.org --id 6
ai add claude persona@example.org --id 6
ai login codex 6
ai disable codex 6
ai enable codex 6
ai rename codex 6 7
```

El login de navegador es manual. No copies tokens entre cuentas. Para adoptar un
perfil existente usa `--home` y deja que el CLI oficial verifique su identidad.
Dar de baja conserva los datos. Los atajos xN/cN y sus variantes r son dinámicos.

`codex`/`claude` abren NUEVA conversación con la mejor cuenta disponible. Sólo
`codex resume ID`, `claude resume ID` o `ai resume proveedor cuenta` reanudan.
La última sesión se filtra por directorio; los subagentes no son sesiones principales.
Un fallo de proveedor se resuelve con `--pick` o `ai handoff`, sin editar transcripts.

`ai usage --refresh` consulta en paralelo sin turnos al modelo. UNKNOWN es válido.
`ai usage --monitoring` mantiene un panel interactivo con consultas en segundo
plano cada cinco minutos. Admite un proveedor, `r` para consultar de nuevo y `q`
para salir. Usa una sola instancia cuando baste; no añade turnos ni consume resets.
Las tarjetas distinguen el próximo reinicio automático, los créditos y los
resets manuales disponibles. Codex publica el contador oficial; si otro CLI no
lo publica, no inferir cero ni usar endpoints privados. Consultar uso no consume
resets ni permite seleccionar una cuenta con cuota agotada.
OpenCode muestra actividad local aproximada, nunca cuota restante. AGY/OpenCode
usan el único login actual. No actives compras, proveedores de pago ni nuevos logins
sin que la tarea los pida.

## Versiones y configuración

```bash
ai update --check
ai update
ai update --list
ai update --rollback
ai migrate --dry-run
ai doctor
```

`ai update` actualiza sólo el gestor desde una release estable con SHA-256.
En instalación global puede requerir sudo; conserva cuentas y datos locales.
No publiques una release ni actualices otro servidor por una petición genérica
de desarrollo. Lee docs/UPDATES.md del repositorio al implementar migraciones.

Las políticas son explícitas y separadas por usuario/proveedor:
`ai configure --codex-danger on/off --claude-danger on/off --agy-danger on/off --opencode-danger on/off`.
Codex recibe --dangerously-bypass-approvals-and-sandbox al abrir, reanudar y en
exec/review, también con ACCOUNT. Conserva perfiles, modelo reservado y prompts;
normaliza los flags de sandbox/aprobación incompatibles. Login, ayuda, administración
y consultas oficiales no reciben el bypass. Una instalación nueva lo deja desactivado.
No cambies el modo de agentes abiertos: sólo los siguientes lanzamientos lo aplican.

La política de Claude usa `ai configure --claude-danger on/off`.
Activada aplica IS_SANDBOX=1, --dangerously-skip-permissions y bypassPermissions
a nuevos lanzamientos y resume. Comprueba modo efectivo con un proceso propio;
no escribas en TTYs de otra sesión ni eludas políticas del proveedor.

AGY y OpenCode usan `ai configure --agy-danger on/off` y
`--opencode-danger on/off`. AGY recibe --dangerously-skip-permissions; OpenCode
recibe --auto y conserva los deny explícitos. Se aplica al abrir y reanudar.
`ai install --shell` prepara funciones Bash para los comandos directos sin mover
los binarios nativos; cargar `source ~/.bashrc` en una terminal que ya estaba abierta.

`ai usage codex|claude|agy|opencode` consulta sólo ese proveedor. Admite refresh,
cached y JSON. El aviso interactivo de nueva release puede actualizar y relanzar
el comando exacto; no pregunta en pipelines ni salidas JSON.

`ai providers-update --check` inspecciona sin actualizar. El mantenimiento periódico
de proveedores es opt-in: `sudo ai providers-update --cron on/off/status`. Usa
instaladores oficiales, aplaza procesos activos y guarda backups sólo de código;
no cambia cuentas, credenciales ni historial. No lo actives en otro servidor sin
una petición de instalación allí.

Cierra con el comando exacto de login pendiente, validación real y límites de lo
probado. No describas un dry-run como una continuación autenticada comprobada.
