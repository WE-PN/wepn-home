import sys

try:
    from importlib.metadata import packages_distributions
    from importlib.metadata import version
    from packaging.version import Version

    packages = packages_distributions()

    arguments = sys.argv
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    need_install = []
    need_update = []
    with open(input_file, "r") as imports:
        for req in imports:
            line = req.strip()
            try:
                pkg_data = line.split("==")
                if len(pkg_data) > 1:
                    # package is pinned
                    # compare package versions
                    wants = pkg_data[1]
                    pkg = pkg_data[0].lower()
                    if pkg not in packages:
                        # package was not even install
                        need_install.append(line)
                    else:
                        installed = version(pkg)
                        if Version(wants) > Version(installed):
                            need_update.append(line)
                else:
                    # package is not pinned
                    # enough to just be installed
                    if line not in packages:
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
