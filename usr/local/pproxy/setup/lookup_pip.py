import subprocess  # nosec: static input
import sys


def get_installed_pip_packages():
    """
    Turns out the importlib.metadata lacks some package info.
    `pip list` on the other hand has the right information.
    """
    try:
        # Run 'pip list' command to get installed packages
        # '--format=freeze' provides a parsable output
        # '--no-color' ensures no ANSI escape codes are included
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'list', '--format=freeze', '--no-color'],
            capture_output=True,  # nosec
            text=True,
            check=True
        )

        packages = {}
        for line in result.stdout.strip().split('\n'):
            if line:
                parts = line.split('==')
                if len(parts) == 2:
                    package_name = parts[0].strip()
                    version_number = parts[1].strip()
                    packages[package_name] = version_number
                elif len(parts) == 1 and '@' in parts[0]:
                    # Handle editable installs like 'package_name @ file:///path/to/repo'
                    package_name = parts[0].split('@')[0].strip()
                    version_number = "local/editable"
                    packages[package_name] = version_number
        return packages
    except subprocess.CalledProcessError as e:
        print(f"Error running pip command: {e}")
        print(f"Stderr: {e.stderr}")
        return {}
    except FileNotFoundError:
        print("Error: 'pip' command not found. Make sure Python and pip are installed and in your PATH.")
        return {}
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return {}


if __name__ == "__main__":
    try:
        from packaging.version import Version
        arguments = sys.argv
        input_file = sys.argv[1]
        output_file = sys.argv[2]
        need_install = []
        need_update = []
        installed_packages = get_installed_pip_packages()
        with open(input_file, "r") as imports:
            for req in imports:
                line = req.strip()
                try:
                    pkg_data = line.split("==")
                    if len(pkg_data) > 1:
                        # package is pinned
                        # compare package versions
                        wants = pkg_data[1]
                        pkg = pkg_data[0]
                        if pkg not in installed_packages:
                            # package was not even install
                            need_install.append(line)
                        else:
                            installed = installed_packages[pkg]
                            # print(f"wants: {wants}, installed: {installed}")
                            if Version(wants) > Version(installed):
                                need_update.append(line)
                    else:
                        # package is not pinned
                        # enough to just be installed
                        if line not in installed_packages:
                            need_install.append(line)

                except Exception as e:
                    print(str(e))
                    need_install.append(line)

        print(f"writing to {output_file}")
        with open(output_file, "w") as output:
            for item in need_install:
                output.write(item + "\n")
            for item in need_update:
                output.write(item + "\n")
        sys.exit(0)
    except Exception as e:
        print("exception : " + str(e))
        sys.exit(1)
