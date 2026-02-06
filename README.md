# Riparr

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
  -e RIPARR_TMDB_API_KEY=your_key \
  ghcr.io/YOUR_USERNAME/riparr:latest
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
| `RIPARR_RAW_DIR` | `/data/raw` | Raw MKV output |
| `RIPARR_OUTPUT_DIR` | `/data/media` | Encoded output |
| `RIPARR_TMDB_API_KEY` | - | TMDB API key |
| `RIPARR_VIDEO_CODEC` | `x265` | Encoder (x264/x265/nvenc) |
| `RIPARR_VIDEO_QUALITY` | `20` | CRF value |
| `RIPARR_HANDBRAKE_PRESET` | `Fast 1080p30` | HandBrake preset |

## License

MIT
