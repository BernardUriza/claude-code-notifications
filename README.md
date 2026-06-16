# claude-code-notifications 🔔

> **CC--VH-lite** = **C**laude **C**ode **V**oice **H**andler — versión *lite*.

Notificaciones para Claude Code en **macOS y Windows**, puro stdlib de Python
(las dependencias extra son todas opcionales). Te avisa cuando una sesión de
Claude Code termina o pide permiso, sin que tengas que mirar la terminal. Dos
piezas coordinadas que viven en [`CC--VH-lite/`](CC--VH-lite/):

| Script | Qué hace |
|---|---|
| **`cc_notify.py`** | Banner nativo por sesión. **macOS**: `terminal-notifier`/Hammerspoon/`osascript`. **Windows**: toast vía BurntToast o, sin dependencias, WinRT (`Windows.UI.Notifications`). Agrupado: cada terminal reemplaza su propio banner, no se apilan 8. Control fino con el comando `ccn`. |
| **`cc_voice_lite.py`** | Voz que lee un fragmento de la última respuesta de Claude. Cadena de fallback: **Azure OpenAI TTS (onyx)** → **edge-tts** (voz neural gratis, sin key) → voz local (`say` en macOS, SAPI en Windows). **Respeta el silencio de cc-notify**: si callas con `ccn quiet`, la voz también se calla. |

Cero daemon, cero cola, cero web UI. Solo dos scripts que los hooks invocan y
salen al instante (el audio/banner corre en segundo plano, nunca bloquea a Claude).

## Instalar (un comando)

```bash
git clone <este-repo>
python CC--VH-lite/install.py        # macOS/Linux: usa python3 si es tu binario
```

El instalador detecta tu plataforma, la ruta del clon y el intérprete correcto
(`python` vs `python3`), **fusiona** los hooks `Stop` + `Notification` en tu
`~/.claude/settings.json` sin pisar lo que ya tengas (hace backup antes), y te
dice qué dependencias opcionales faltan. Es idempotente — puedes re-correrlo.

```bash
python CC--VH-lite/install.py --dry-run     # ver qué haría, sin escribir
python CC--VH-lite/install.py --uninstall   # quitar solo estos hooks
```

Luego **reinicia Claude Code**.

> **Instalación manual** (si prefieres): abre `CC--VH-lite/settings.snippet.json`,
> reemplaza `/ABSOLUTE/PATH/TO` por la ruta real del clon y fusiona el bloque
> `hooks` en tu `~/.claude/settings.json` (no sobrescribas un bloque `hooks`
> existente — agrega las llaves). En Windows usa `python` y rutas con `/`.

## Dependencias opcionales

Todo funciona sin nada extra (banner + voz local). Para subir la calidad:

| Quieres… | Instala |
|---|---|
| Voz neural gratis (recomendado) | `pip install edge-tts` |
| Voz Azure onyx (de pago) | crea `~/.secrets/azure-openai-key.txt` (formato abajo) |
| Banner Windows "bonito" | `Install-Module BurntToast` (PowerShell). Sin esto, cae a WinRT igual de funcional. |
| Banner macOS con botón | `brew install terminal-notifier` y/o Hammerspoon |

Formato de `~/.secrets/azure-openai-key.txt`:
```
AZURE_OPENAI_TTS_KEY: <tu-key>
Endpoint: https://<region>.api.cognitive.microsoft.com/
Deployment: tts
API-Version: 2024-02-15-preview
```

## El comando `ccn` (control de cc-notify)

```
ccn list            sesiones recientes y cuáles están silenciadas
ccn mute <sid>      silenciar una sesión   (o `mute all`)
ccn unmute <sid>    reactivar una sesión   (o `unmute all`)
ccn quiet           silencio global on/off (toggle) — también calla la voz
ccn sound           sonido on/off (toggle) — banner sin ding
ccn clear           borra los banners en pantalla + limpia estado
ccn status          estado actual (quiet, sonido, sesiones muteadas)
ccn stop            corta la voz que esté sonando
```

## Probar sin Claude

```bash
# Banner (usa `python` en Windows, `python3` en macOS)
echo '{"hook_event_name":"Stop","cwd":"/tmp/mi-repo","session_id":"abc123"}' \
  | python CC--VH-lite/cc_notify.py

# Voz (lee un transcript real)
echo '{"hook_event_name":"Stop","transcript_path":"/ruta/al/transcript.jsonl"}' \
  | python CC--VH-lite/cc_voice_lite.py

# Voz, modo prueba directo
python CC--VH-lite/cc_voice_lite.py --say "Probando, uno dos tres"
```

## Notas de plataforma

- Claude Code corre los hooks a través de un shell POSIX (**Git Bash en
  Windows**), por eso las rutas se escriben con `/` y `python` debe estar en tu
  PATH. El instalador ya lo resuelve.
- En Windows, hay un bug conocido donde los hooks de `settings.json` **no** se
  invocan dentro de la **Claude Desktop App** (sí en la CLI).
- En Linux, la voz funciona (edge-tts); los banners de `cc_notify.py` están
  pensados para macOS/Windows.

## Por qué no bloquea a Claude

Cada script se lanza en un proceso desacoplado (`start_new_session=True` en
POSIX, `DETACHED_PROCESS` en Windows): Claude no espera a que termine el banner
ni el audio, y el hook sale con `exit 0` al instante.

---

> Historia: nació como notificación por **voz** (`say`), que resultó invasiva
> con varias terminales hablando encimadas. `cc_notify.py` la reemplazó con
> banners nativos silenciables, y la voz se reconstruyó para integrarse con su
> silenciador. La versión sobreingeniereada original (Qwen + OpenAI TTS + cola
> SQLite + web UI) queda congelada en el historial de git.
