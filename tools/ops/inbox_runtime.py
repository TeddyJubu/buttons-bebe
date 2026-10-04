#!/usr/bin/env python3
import argparse


RETIRED = "Inbox 1 is retired; use deploy/cd/README.md for active Inbox source recovery."


def main() -> None:
    parser = argparse.ArgumentParser(description=RETIRED)
    parser.parse_known_args()
    raise SystemExit(RETIRED)


if __name__ == '__main__':
    main()
