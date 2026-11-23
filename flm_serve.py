"""Compatibility entry point for existing FastFlowLM systemd installations."""
from llama_web.backends.flm_serve import main


if __name__ == "__main__":
    main()
