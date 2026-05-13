import argparse
import glob
import os
import subprocess
import sys


def get_module_name(test_filename):
    base = os.path.basename(test_filename)
    if not base.startswith("test_") or not base.endswith(".py"):
        return None
    name_part = base[5:-3]
    if os.path.exists(f"{name_part}.py"):
        return name_part
    if "_" in name_part:
        parts = name_part.split("_")
        for i in range(len(parts) - 1, 0, -1):
            candidate = "_".join(parts[:i])
            if os.path.exists(f"{candidate}.py"):
                return candidate
    return None


def main(prefix_path, pytest_binary):
    os.chdir(prefix_path)
    if not os.path.exists("service.py"):
        print("Error: Please run this script from the project root directory.")
        sys.exit(1)

    test_dir = "tests/units"
    test_files = sorted(glob.glob(os.path.join(test_dir, "test_*.py")))
    if not test_files:
        print("No test files found.")
        sys.exit(0)

    modules_to_cover = set()
    for t in test_files:
        m = get_module_name(t)
        if m:
            modules_to_cover.add(m)

    sorted_modules = sorted(list(modules_to_cover))

    print(f"Found {len(test_files)} test files.")
    print(f"Covering modules: {', '.join(sorted_modules)}\n")

    # Construct pytest command
    # Running all together for proper coverage aggregation
    cmd = [pytest_binary, "-v", "--cov-report", "term-missing"]
    for m in sorted_modules:
        cmd.append(f"--cov={m}")
    cmd.append(test_dir)

    print(f"Executing: {' '.join(cmd)}\n")

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


FALLBACK_PYTEST = "/var/local/pproxy/wepn-pytest/bin/pytest"


def default_pytest():
    try:
        import pytest_cov  # noqa: F401
        return "pytest"
    except ImportError:
        return FALLBACK_PYTEST


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The runner for pytests with coverage")
    parser.add_argument("--path", type=str, default="./", help="The path to source files.")
    parser.add_argument("--pytest", type=str, default=default_pytest(), help="The path to the pytest.")

    args = parser.parse_args()

    main(prefix_path=args.path, pytest_binary=args.pytest)
