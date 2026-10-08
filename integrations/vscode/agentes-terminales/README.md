# AI Command Agentes

Lista de terminales visibles con un indicador blanco (trabajando), azul
(turno terminado), rojo (atención) o gris (sin agente / estado no confirmado).
La descripción identifica si el agente es Codex o Claude.
Un clic abre la terminal correspondiente al final de la conversación, también
si está dentro de un grupo dividido. El matraz prueba los tres colores;
Actualizar vuelve al estado real.
El icono de la barra usa el símbolo nativo `terminal` de VS Code, sin depender
de una ruta SVG que pueda quedar obsoleta después de actualizar la extensión.

Mientras Codex sigue trabajando, el indicador siempre es blanco. Una pregunta
asíncrona o una marca de atención no lo ponen rojo hasta que se detiene. Un diálogo
de aprobación que bloquea el trabajo sí se muestra rojo. La actividad visible en
el pie de Codex prevalece sobre un evento antiguo de fin de turno.

Consulta local cada segundo, sin llamadas al modelo. Lee los eventos del Codex
principal asociado al proceso de cada pane; no mezcla subagentes ni sesiones
con nombres iguales. No escribe en las terminales, no cambia sus nombres,
configuración, grupos ni procesos. Las actualizaciones de estado conservan el
foco y el scroll; solo el clic explícito desplaza al final la terminal elegida.

Para Claude Code se lee exclusivamente el registro local
`$CLAUDE_CONFIG_DIR/sessions/<PID>.json` o `~/.claude/sessions/<PID>.json`,
comprobado con las versiones 2.1.258 y 2.1.284 en Linux.
El estado nativo `busy` o `shell` se muestra blanco, `idle` azul y `waiting`
rojo. Así el trabajo delegado y las tareas shell que Claude mantiene activas
siguen blancas. Se valida PID, inicio del proceso, máquina, namespace y sesión
interactiva principal para evitar registros antiguos o de subagentes. No se
añaden hooks ni se leen claves, sockets o conversaciones de Claude. Si falta
ese registro o cambia su formato, se muestra gris; no se infiere el estado por
tiempo sin actividad. El adaptador admite perfiles personalizados. Consulta únicamente HOME,
CODEX_HOME y CLAUDE_CONFIG_DIR del proceso; no guarda su entorno ni lee credenciales.

El orden sigue las pestañas del panel de VS Code. Los splits aparecen juntos,
marcados con `┌`, `├` y `└`. Al arrastrar o separar terminales en VS Code,
Agentes refleja el cambio en aproximadamente un segundo. No mantiene otro orden.
La lista se filtra por la carpeta del workspace abierto. Una terminal iniciada
en otro proyecto queda fuera aunque su sesion tmux siga viva en el mismo servidor.
Tras adoptar una sesion antigua, usa la etiqueta de workspace de tmux para que
la pestaña siga visible en el proyecto que la ha recuperado aunque conserve su
`cwd` historico.

La API pública no entrega este orden. En Linux, `native-layout.js` identifica el
PTY host mediante la ascendencia del PID de cada terminal. `layout-bridge.js`
valida la implementación de VS Code e instala un lector de sus mapas de layout
mediante una conexión de diagnóstico local temporal. La condición de captura
siempre devuelve falso: no pausa el proceso. Se cierra la conexión después y no
se modifican archivos de VS Code ni se sustituyen métodos del PTY host.

El lector publica cada 500 ms únicamente PID, grupos y tamaños, en archivos
privados bajo `${XDG_STATE_HOME:-~/.local/state}/ai-command-terminal-layout/`.
La extensión cruza esos PID con sus propias terminales y rechaza asociaciones
incompletas o duplicadas. No consulta ni consume el estado de revival. La
inicialización usa un PTY propio, invisible y transitorio, sin shell ni tmux.
No se llama al modelo ni se envía texto a una terminal del usuario.

Este adaptador depende de detalles internos, comprobados en VS Code 1.136.1 y 1.136.2.
Si una actualización cambia su estructura, conserva el último orden conocido
y muestra «Sincronizando orden…»; el error queda en la salida AI Command Agentes.
La próxima sesión instala de nuevo el lector cuando el PTY host es nuevo.

La detección de actividad usa el formato local de Codex 0.153.x. Los eventos de
inicio/fin determinan el estado, no el tiempo sin salida. Se detectan solicitudes
`request_user_input` directas y algunos diálogos de aprobación mediante sus
controles visibles. Las llamadas anidadas en code mode y preguntas en lenguaje
natural no siempre dejan un evento de espera observable. Para esos casos el
agente puede ejecutar `terminal-estado atencion` en su terminal; la marca dura
hasta el siguiente turno. No se adivina una consulta por contener interrogantes.
El adaptador debe revisarse si cambia el formato de Codex.

En 0.1.10 se sigue la cadena de lanzadores Python hasta el ejecutable nativo:
el nombre del proceso `ai` o `codex` por sí solo no identifica al agente.
Se usa el `tmux` de PATH, igual que los lanzadores de las terminales, para
consultar el servidor correcto cuando conviven instalaciones distintas.

La lectura de historial inicial está limitada a 8 MiB por agente; posteriormente
solo se leen bytes nuevos. Si faltan eventos o la asociación no es inequívoca,
se muestra gris. No se guardan ni se muestran mensajes del chat en esta extensión.
Los colores y sus etiquetas sirven también cuando no se distinguen los colores.

En 0.1.7, la asociación con Codex 0.159.2 admite registros de origen `cli` y
`vscode` y no depende de que el proceso mantenga abierto el archivo. Los nombres
de los registros solo tienen segundos: se usan para acotar la búsqueda y se
valida después la fecha ISO completa de los metadatos, junto con el directorio
del proceso. La comparación de directorios admite los bind mounts legacy.
Se consulta también el día siguiente si la ventana de inicio cruza medianoche.
Una reanudación con UUID explícito se resuelve mediante el índice local en modo
solo lectura y exige la identidad exacta del registro; si falla, no se asocia a
otra sesión cercana. Los candidatos ambiguos o incompletos se descartan.

La actividad visible y los controles de aprobación se comprueban aunque no se
pueda asociar un registro, incluido el caso de una sesión retomada desde el
selector interactivo. Eso permite confirmar blanco o rojo por evidencia actual.
Para confirmar azul siguen siendo necesarios los eventos de fin de turno; el
gris conserva su función cuando faltan pruebas. El tooltip explica que no se ha
podido asociar el registro. Las 32 pruebas incluyen procesos locales y un servidor
tmux aislado; ningún test necesita contactar un modelo ni otra sesión real.

Fuentes: [API de vistas de VS Code](https://code.visualstudio.com/api/extension-guides/tree-view),
[eventos de Codex](https://learn.chatgpt.com/docs/hooks).
