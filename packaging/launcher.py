"""The desktop executable accepts the existing CLI commands in a terminal."""
import sys

if __name__ == "__main__":
    if sys.argv[1:] == ["--probe-metal"]:
        from endoscope_degrid.mlx_backend import probe_metal
        raise SystemExit(probe_metal())
    if len(sys.argv) > 1:
        from endoscope_degrid import cli
        raise SystemExit(cli.main())
    from endoscope_degrid import desktop
    desktop.main()
