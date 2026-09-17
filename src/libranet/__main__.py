"""Allow ``python -m libranet`` to start the supervisor."""

from libranet.supervisor import main

if __name__ == "__main__":
    raise SystemExit(main())
