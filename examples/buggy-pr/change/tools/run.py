import argparse
import subprocess


def main():
    p = argparse.ArgumentParser()
    p.add_argument("name")
    p.add_argument("--resize", default="50%")
    args = p.parse_args()
    subprocess.run(f"convert {args.name} -resize {args.resize} out.png", shell=True)


if __name__ == "__main__":
    main()
