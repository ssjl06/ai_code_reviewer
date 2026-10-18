import argparse
import subprocess


def main():
    p = argparse.ArgumentParser()
    p.add_argument("name")
    args = p.parse_args()
    subprocess.run(["identify", args.name], check=True)


if __name__ == "__main__":
    main()
