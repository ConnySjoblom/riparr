# Riparr

> **Disclaimer:** This is a hobby/vibe-coding experiment. Not intended for serious or production use. Use at your own risk.

Modern DVD/Blu-ray ripper with automated disc detection, metadata lookup, and encoding.

## Features

- **Automatic disc detection** via udev or polling
- **MakeMKV integration** for disc ripping
- **HandBrake encoding** with configurable presets
- **Metadata lookup** from ARM database and TMDB
- **Live TUI dashboard** for watch mode
- **Queue system** with automatic recovery

## Quick Start (Docker)

```bash
docker run -d \
  --name riparr \
  --privileged \
  --device /dev/sr0:/dev/sr0 \
  -v /run/udev:/run/udev:ro \
  -v riparr-raw:/data/raw \
  -v riparr-media:/data/media \
  -e TZ=America/New_York \
  -e RIPARR_TMDB_API_KEY=your_key \
  ghcr.io/connysjoblom/riparr:latest
```

## TrueNAS SCALE

1. Create a new custom app or use docker-compose
2. Mount your optical drive: `/dev/sr0`
3. Set privileged mode for device access
4. Configure volumes for raw and media output

## Commands

```bash
riparr watch --gui    # Daemon mode with live dashboard
riparr rip /dev/sr0   # Manual rip
riparr info /dev/sr0  # Show disc info
riparr queue list     # View encoding queue
```

## Configuration

All settings via environment variables with `RIPARR_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `UTC` | Timezone (e.g., `America/New_York`, `Europe/London`) |
| `RIPARR_RAW_DIR` | `/data/raw` | Raw MKV output |
| `RIPARR_OUTPUT_DIR` | `/data/media` | Encoded output |
| `RIPARR_TMDB_API_KEY` | - | TMDB API key (optional) |
| `RIPARR_VIDEO_CODEC` | `x265` | Encoder (x264/x265/nvenc) |
| `RIPARR_VIDEO_QUALITY` | `19` | CRF value |
| `RIPARR_HANDBRAKE_PRESET` | `HQ 576p25 Surround` | HandBrake preset |
| `RIPARR_EJECT_AFTER_RIP` | `true` | Auto-eject disc when done |

## License

MIT
