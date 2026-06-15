# claude-code-notifications 🔔

Notificaciones para Claude Code en **macOS**, puro stdlib de Python. Te avisa
cuando una sesión de Claude Code termina o pide permiso, sin que tengas que
mirar la terminal. Dos piezas coordinadas que viven en [`CC--VH-lite/`](CC--VH-lite/):

| Script | Qué hace |
|---|---|
| **`cc_notify.py`** | Banner nativo de macOS por sesión (vía `terminal-notifier`/Hammerspoon/`osascript`). Agrupado: cada terminal reemplaza su propio banner, no se apilan 8. Control fino con el comando `ccn`. |
| **`cc_voice_lite.py`** | Voz que lee un fragmento de la última respuesta de Claude. Usa Azure OpenAI TTS (voz *onyx*) con fallback al `say` de macOS. **Respeta el silencio de cc-notify**: si callas con `ccn quiet`, la voz también se calla. |

Cero daemon, cero cola, cero web UI. Solo dos scripts que los hooks invocan y
salen al instante (el audio/banner corre en segundo plano, nunca bloquea a Claude).

## Instalar

1. Clona este repo donde quieras.
2. Abre `CC--VH-lite/settings.snippet.json`, reemplaza `/ABSOLUTE/PATH/TO` por la
   ruta real del clon, y fusiona el bloque `hooks` en tu `~/.claude/settings.json`
   (no sobrescribas un bloque `hooks` que ya tengas — agrega las llaves).
3. (Opcional, para voz Azure) crea `~/.secrets/azure-openai-key.txt` con:
   ```
   AZURE_OPENAI_TTS_KEY: <tu-key>
   Endpoint: https://<region>.api.cognitive.microsoft.com/
   Deployment: tts
   API-Version: 2024-02-15-preview
   ```
   Sin esto, la voz cae al `say` de macOS (gratis, instantáneo). El banner de
   `cc_notify.py` no necesita ninguna key.
4. Reinicia Claude Code.

## El comando `ccn` (control de cc-notify)

```
ccn list            sesiones recientes y cuáles están silenciadas
ccn mute <sid>      silenciar una sesión   (o `mute all`)
ccn unmute <sid>    reactivar una sesión   (o `unmute all`)
ccn quiet           silencio global on/off (toggle) — también calla la voz
ccn sound           sonido on/off (toggle) — banner sin ding
ccn clear           borra los banners en pantalla + limpia estado
ccn status          estado actual (quiet, sonido, sesiones muteadas)
```

## Probar sin Claude

```bash
# Banner
echo '{"hook_event_name":"Stop","cwd":"/tmp/mi-repo","session_id":"abc123"}' \
  | python3 CC--VH-lite/cc_notify.py

# Voz (lee un transcript real)
echo '{"hook_event_name":"Stop","transcript_path":"/ruta/al/transcript.jsonl"}' \
  | python3 CC--VH-lite/cc_voice_lite.py

# Voz, modo prueba directo
python3 CC--VH-lite/cc_voice_lite.py --say "Probando, uno dos tres"
```

## Por qué no bloquea a Claude

Cada script se lanza con `start_new_session=True` (proceso desacoplado): Claude
no espera a que termine el banner ni el audio, y el hook sale con `exit 0` al
instante.

---

> Historia: nació como notificación por **voz** (`say`), que resultó invasiva
> con varias terminales hablando encimadas. `cc_notify.py` la reemplazó con
> banners nativos silenciables, y la voz se reconstruyó para integrarse con su
> silenciador. La versión sobreingeniereada original (Qwen + OpenAI TTS + cola
> SQLite + web UI) queda congelada en el historial de git.
