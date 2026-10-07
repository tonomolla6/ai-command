# AI Command Terminales

Perfil nativo de VS Code con procesos persistentes en tmux. El puente Python
terminales-native usa el modo control: tmux mantiene el proceso; VS Code gestiona
pantalla, scrollback, seleccion, portapapeles y cursor sin capturar el raton.

La extension conserva claves por pestaña, nombres y grupos, aporta Shift+Enter
(salto en Codex) y Ctrl+A (seleccion del buffer), y configura clic derecho para
copiar sin menu ni copia automatica al seleccionar. Una pestaña corresponde a
un pane; dividir desde VS Code crea una sesion independiente.

Instalar antes los lanzadores de scripts/workstation. Empaquetar con @vscode/vsce.
La guia completa y las pruebas estan en docs/WORKSTATION-TERMINALES.md.

Cada terminal persistente queda asociada a la carpeta del workspace que la abre.
La recuperacion solo muestra las sesiones de esa carpeta; abrir otro proyecto no
mezcla sus pestañas ni su orden. El lanzador conserva el directorio inicial del
workspace al crear la sesion tmux, y las sesiones antiguas visibles se reasignan
una sola vez al workspace que las esta mostrando. Las sesiones tmux y sus
procesos no se cierran al cambiar de carpeta o de ventana.

Desde 0.4.1, eliminar una terminal con la papelera termina su sesion tmux y los
procesos conectados a su pane. Cerrar o recargar la ventana conserva las sesiones.
La extension distingue TerminalExitReason.User de Shutdown/Process/Extension;
no deduce una eliminacion a partir de una señal o desconexion del puente.
El cierre comprueba ID, fecha de creacion, marca de gestion y clave persistente.
No limpia retroactivamente sesiones antiguas desconectadas.
