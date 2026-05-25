# CC--VH-lite 🔊

Notificaciones por **voz** para Claude Code, versión lite y sin mamadas.

Un solo script de Python (`cc_voice_lite.py`), puro stdlib. Usa el comando
`say` de macOS (ya viene instalado). **Cero** API keys, **cero** daemon,
**cero** cola SQLite, **cero** dependencias que instalar.

> Es la versión flaca del sobreingeniereado
> [claude-code-voice-handler](https://github.com/BernardUriza/claude-code-notifications).
> Ese tenía Qwen AI, OpenAI TTS, cola persistente y web UI. Este nomás habla.

## Qué hace

Cuando Claude Code dispara un hook, el script dice una frase corta con la voz
**Paulina** (mexicana). Solo en 3 eventos:

| Evento         | Cuándo suena                  | Ejemplo de frase                |
|----------------|-------------------------------|---------------------------------|
| `SessionStart` | Arranca una sesión            | "Listo pa' chambear"            |
| `Stop`         | Claude terminó                | "Ya quedó"                      |
| `Notification` | Claude necesita tu permiso    | "Oye, necesito tu visto bueno"  |

Cualquier otro evento → no suena nada (sale calladito).

## Instalar (30 segundos)

1. Copia el bloque `hooks` de `settings.snippet.json` dentro de tu
   `~/.claude/settings.json` (o fusiónalo si ya tienes hooks).
2. Listo. Reinicia Claude Code y ya te habla.

> Si ya tienes una sección `"hooks"` en tu settings, no la sobrescribas:
> nomás agrega las llaves `SessionStart`, `Stop` y `Notification`.

## Probar sin Claude

```bash
# Por stdin (como lo llama Claude de verdad)
echo '{"hook_event_name":"Stop"}' | python3 cc_voice_lite.py

# Por argumento
python3 cc_voice_lite.py --hook Notification

# Decir cualquier cosa (modo prueba)
python3 cc_voice_lite.py --say "Probando, uno dos tres"
```

## Personalizar

Todo se edita arriba del propio `cc_voice_lite.py`:

- `VOICE` — cambia la voz. Lista completa: `say -v '?'`
- `RATE`  — velocidad en palabras por minuto.
- `PHRASES` — agrega o quita frases por evento (se elige una al azar).

## Por qué no bloquea a Claude

El `say` se lanza con `start_new_session=True`, así corre desacoplado del hook:
Claude no espera a que termine el audio y el script sale al instante con `exit 0`.
