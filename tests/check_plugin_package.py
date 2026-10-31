# -*- coding: utf-8 -*-
"""Validate the plugin the way plugins.qgis.org reads it.
usage: python check_plugin_package.py <plugin_dir_or_zip>"""
import configparser
import io
import os
import re
import sys
import zipfile

REQUIRED = ["name", "description", "about", "version", "qgisMinimumVersion", "author", "email", "repository"]


def load(target):
    """return (metadata text, list of package paths, plugin folder name)"""
    if target.endswith(".zip"):
        z = zipfile.ZipFile(target)
        names = z.namelist()
        tops = {n.split("/")[0] for n in names}
        assert len(tops) == 1, "zip must contain exactly one top-level folder: %s" % tops
        top = tops.pop()
        return z.read(top + "/metadata.txt").decode("utf-8"), names, top
    names = []
    for root, dirs, files in os.walk(target):
        for f in files:
            names.append(os.path.relpath(os.path.join(root, f), os.path.dirname(target.rstrip("/"))))
    with open(os.path.join(target, "metadata.txt"), encoding="utf-8") as fh:
        return fh.read(), names, os.path.basename(target.rstrip("/"))


def main(target):
    text, names, top = load(target)
    errors = []
    # 1. parse exactly like the site: ConfigParser with interpolation ('%' must be '%%')
    cp = configparser.ConfigParser()
    cp.optionxform = str
    cp.read_file(io.StringIO(text))
    if "general" not in cp:
        errors.append("missing [general] section")
    else:
        g = cp["general"]
        for k in g:
            try:
                g[k]
            except configparser.Error as ex:
                errors.append("metadata %s: %s" % (k, str(ex).splitlines()[0][:120]))
        for k in REQUIRED:
            if not g.get(k, raw=True, fallback="").strip():
                errors.append("missing required metadata: " + k)
        ver = g.get("version", raw=True, fallback="")
        if not re.match(r"^\d+\.\d+(\.\d+)?$", ver):
            errors.append("version should look like 1.2.3: %r" % ver)
        for k in ("qgisMinimumVersion", "qgisMaximumVersion"):
            v = g.get(k, raw=True, fallback="")
            if v and not re.match(r"^\d+\.\d+(\.\d+)?$", v):
                errors.append("%s malformed: %r" % (k, v))
        if not g.get("repository", raw=True, fallback="").startswith("http"):
            errors.append("repository must be a URL")
        icon = g.get("icon", raw=True, fallback="")
        if icon and not any(n.endswith(top + "/" + icon) or n == top + "/" + icon for n in names):
            errors.append("icon not found in package: " + icon)
    # 2. package content
    for need in ("__init__.py", "metadata.txt", "LICENSE"):
        if not any(n == top + "/" + need for n in names):
            errors.append("missing %s/%s" % (top, need))
    bad = [n for n in names if "__pycache__" in n or n.endswith((".pyc", ".pyo")) or "/." in n]
    if bad:
        errors.append("remove from package: %s" % bad[:5])
    if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", top):
        errors.append("plugin folder name must be a valid python identifier: " + top)
    if errors:
        print("PACKAGE CHECK FAILED:\n  " + "\n  ".join(errors))
        sys.exit(1)
    print("PACKAGE CHECK OK: %s %s" % (top, cp["general"].get("version")))


if __name__ == "__main__":
    main(sys.argv[1])
