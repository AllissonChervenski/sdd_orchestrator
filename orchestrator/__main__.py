if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from orchestrator.cli import main
else:
    from .cli import main

if __name__ == "__main__":
    main()

