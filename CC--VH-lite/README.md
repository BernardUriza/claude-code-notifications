# CC--VH-lite 🔊

Notificaciones por **voz** para Claude Code, versión lite y sin mamadas.

Un solo script de Python (`cc_voice_lite.py`), puro stdlib. Usa el comando
`say` de macOS (ya viene instalado). **Cero** API keys, **cero** daemon,
**cero** cola SQLite, **cero** dependencias que instalar.

> Es la versión flaca del sobreingeniereado
> [claude-code-voice-handler](https://github.com/BernardUriza/claude-code-notifications).
> Ese tenía Qwen AI, OpenAI TTS, cola persistente y web UI. Este nomás habla.

## Qué hace

Cuando Claude Code dispara un hook, el script dice **el nombre del repo/folder**
y luego **un fragmento de lo que dice Claude** (mínimo 30 palabras, o hasta el 50%
del total si la respuesta es larga), con la voz **Paulina** (mexicana). Así sabes
de qué sesión viene la voz. Ej: _"Documents. Ya quedó el módulo que andábamos..."_

| Evento         | Qué dice                                                        |
|----------------|----------------------------------------------------------------|
| `Stop`         | Las primeras 10 palabras de la última respuesta de Claude       |
| `SubagentStop` | Igual, pero de la respuesta del subagente                       |
| `Notification` | Las primeras 10 palabras del texto de la notificación (permiso) |

Para `Stop` saca el texto del **transcript** (`transcript_path` que manda el hook),
agarra la última respuesta del assistant, le quita el markdown/código/emojis y lee
las primeras `WORD_LIMIT` palabras. Cualquier otro evento → no suena nada.

## Instalar (30 segundos)

1. Copia el bloque `hooks` de `settings.snippet.json` dentro de tu
   `~/.claude/settings.json` (o fusiónalo si ya tienes hooks).
2. Listo. Reinicia Claude Code y ya te habla.

> Si ya tienes una sección `"hooks"` en tu settings, no la sobrescribas:
> nomás agrega las llaves `SessionStart`, `Stop` y `Notification`.

## Probar sin Claude

```bash
# Stop: lee un transcript real y habla las primeras 10 palabras de Claude
echo '{"hook_event_name":"Stop","transcript_path":"/ruta/al/transcript.jsonl"}' \
  | python3 cc_voice_lite.py

# Notification: habla las primeras 10 palabras del mensaje
echo '{"hook_event_name":"Notification","message":"Claude needs your permission"}' \
  | python3 cc_voice_lite.py

# Decir cualquier cosa (modo prueba)
python3 cc_voice_lite.py --say "Probando, uno dos tres"
```

## Personalizar

Todo se edita arriba del propio `cc_voice_lite.py`:

- `VOICE`     — cambia la voz. Lista completa: `say -v '?'`
- `RATE`      — velocidad en palabras por minuto.
- `MIN_WORDS` — mínimo de palabras a leer (default 30).
- `MAX_RATIO` — tope como % del total en respuestas largas (default 0.5 = 50%).

## Por qué no bloquea a Claude

El `say` se lanza con `start_new_session=True`, así corre desacoplado del hook:
Claude no espera a que termine el audio y el script sale al instante con `exit 0`.
