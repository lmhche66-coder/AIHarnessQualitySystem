"""允许通过 ``python -m agenteval`` 调用 CLI。"""

from agenteval.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
