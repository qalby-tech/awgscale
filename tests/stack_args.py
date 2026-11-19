#!/usr/bin/env python3
"""The `docker run` arguments of one service of stack/compose.yml, for tests/stack.sh.

    stack_args.py <config.json> <service> <project dir> <stand-in root> <network> [<command> …]

config.json is what `docker compose config --format json` printed: the
arguments are made from it, not retyped, so the test runs a service with the
very environment, privileges and mounts the server will give it. Two things
differ, and only these:
  * a path of the server itself (/usr/bin/awg, /run/cascade, …) is taken from
    under the stand-in root, the same path there;
  * the network is the one given (a throwaway namespace) instead of the host's
    or the stack's own.
Printed one argument per line, the image and the command last; a command
given here takes the place of the service's own (a one-off run, like
`docker compose run <service> <command>`).
"""
import json
import sys


def arguments(config_file, service_name, project, standins, network, command):
    with open(config_file) as f:
        service = json.load(f)["services"][service_name]
    args = []
    if service.get("read_only"):
        args.append("--read-only")
    for capability in service.get("cap_drop", []):
        args += ["--cap-drop", capability]
    for capability in service.get("cap_add", []):
        args += ["--cap-add", capability]
    for option in service.get("security_opt", []):
        args += ["--security-opt", option]
    for mount in service.get("tmpfs", []):
        args += ["--tmpfs", mount]
    if service.get("mem_limit"):
        args += ["--memory", str(service["mem_limit"])]
    for name, value in sorted(service.get("environment", {}).items()):
        args += ["-e", f"{name}={value}"]
    for volume in service.get("volumes", []):
        source = volume["source"]
        if not source.startswith(project + "/"):
            source = standins + source
        args += ["-v", source + ":" + volume["target"] + (":ro" if volume.get("read_only") else "")]
    if network:
        args += ["--network", network]
    args.append(service["image"])
    args += command or service.get("command") or []
    return args


if __name__ == "__main__":
    if len(sys.argv) < 6:
        sys.exit(__doc__)
    print("\n".join(arguments(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6:])))
