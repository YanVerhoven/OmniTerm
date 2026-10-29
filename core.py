"""core.py - ядро Terminal Simulator. Без Qt и без запуска реальных команд.
Команда -> генератор событий:
  ("out", [(текст, стиль), ...])  строка вывода
  ("bar", ключ, [(текст, стиль)]) живая строка прогресса (заменяет прошлую с тем же ключом)
  ("wait", секунды)               пауза (интерфейс умножает на time_scale)
  ("clear",) / ("exit",)
"""
import json
import posixpath
import random
import re
from datetime import datetime, timedelta

WINGET = {
    "Mozilla.Firefox": dict(name="Mozilla Firefox", ver="143.0.4", new="144.0", mb=68.4, mb_new=69.1),
    "7zip.7zip": dict(name="7-Zip", ver="24.08", new="24.08", mb=1.51, mb_new=1.51),
}
CHOCO = {
    "git": ("2.47.0", 12.4),
    "7zip": ("24.08", 1.6),
    "vscode": ("1.95.3", 98.0),
    "python": ("3.12.7", 25.1),
}
PS_ALIASES = {
    "ls": "Get-ChildItem", "dir": "Get-ChildItem", "gci": "Get-ChildItem",
    "cd": "Set-Location", "sl": "Set-Location", "chdir": "Set-Location",
    "pwd": "Get-Location", "gl": "Get-Location",
    "cat": "Get-Content", "type": "Get-Content", "gc": "Get-Content",
    "echo": "Write-Output", "write": "Write-Output",
    "rm": "Remove-Item", "del": "Remove-Item", "erase": "Remove-Item", "ri": "Remove-Item",
    "cp": "Copy-Item", "copy": "Copy-Item", "cpi": "Copy-Item",
    "mv": "Move-Item", "move": "Move-Item", "mi": "Move-Item",
    "ren": "Rename-Item", "rni": "Rename-Item",
    "mkdir": "New-Item", "md": "New-Item", "ni": "New-Item",
    "clear": "Clear-Host", "cls": "Clear-Host",
    "date": "Get-Date", "history": "Get-History",
    "h": "Get-History", "man": "Get-Help", "help": "Get-Help", "?": "Where-Object",
    "%": "ForEach-Object", "select": "Select-Object", "where": "Where-Object",
    "sort": "Sort-Object", "measure": "Measure-Object",
    "sleep": "Start-Sleep", "gps": "Get-Process", "ps": "Get-Process",
    "gsv": "Get-Service", "sls": "Select-String",
}
PS_ALIASES.update({"pushd": "Push-Location", "popd": "Pop-Location", "gi": "Get-Item", "gp": "Get-ItemProperty",
                   "sc": "Set-Content", "ac": "Add-Content", "clc": "Clear-Content",
                   "sp": "Set-ItemProperty", "gm": "Get-Member"})
APT = {
    "firefox": dict(ver="1:1snap1-0ubuntu5", kb=72, deps=[], bins=["firefox"]),
    "p7zip-full": dict(ver="16.02+transitional.1", kb=24, deps=["7zip"], bins=["7z"]),
    "7zip": dict(ver="23.01+dfsg-11", kb=1891, deps=[], bins=[]),
}
PIP = {
    "requests": ("2.32.3", [("charset-normalizer", "3.4.0"), ("idna", "3.10"), ("urllib3", "2.2.3"), ("certifi", "2024.8.30")]),
    "numpy": ("2.1.2", []),
    "colorama": ("0.4.6", []),
    "flask": ("3.0.3", [("blinker", "1.8.2"), ("click", "8.1.7"), ("itsdangerous", "2.2.0"), ("Jinja2", "3.1.4"), ("MarkupSafe", "3.0.1"), ("Werkzeug", "3.0.4")]),
}
CANON = {"get-childitem": "ls", "dir": "ls", "gci": "ls", "set-location": "cd", "sl": "cd", "get-location": "pwd",
         "gl": "pwd", "get-content": "cat", "type": "cat", "gc": "cat", "write-output": "echo", "write-host": "echo",
         "remove-item": "rm", "del": "rm", "ri": "rm", "clear-host": "clear", "cls": "clear", "md": "mkdir",
         "get-date": "date", "ni": "new-item", "pip3": "pip", "rmdir": "rm"}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

CHECK_SH = """#!/bin/bash
for p in firefox p7zip-full; do
  v=$(dpkg-query -W -f='${Version}' "$p" 2>/dev/null) && echo "$p: installed ($v)" || echo "$p: not installed"
done
for b in firefox 7z; do
  p=$(command -v "$b") && echo "$b -> $p" || echo "$b -> not found"
done
"""
DEPLOY_SH = """#!/bin/bash
# deploy_linux.sh - index / install / update / uninstall + verify
set -u
stage() { echo "[$(date '+%F %T')] === $1: START ==="; }
stage 0.INDEX;     apt-get update -qq
stage 1.INSTALL;   apt-get install -y firefox p7zip-full
stage 2.UPDATE;    apt-get update -qq && apt-get upgrade -y
stage 3.UNINSTALL; apt-get remove -y p7zip-full
"""
DEPLOY_PS1 = """param([ValidateSet('Install','Update','Uninstall')][string]$Action, [string]$Package)
# Deploy-Apps.ps1 - winget: install / update / uninstall + exit codes
$apps = if ($Package) { @($Package) } else { 'Mozilla.Firefox', '7zip.7zip' }
foreach ($a in $apps) { winget $Action.ToLower() --id $a -e --silent }
exit $LASTEXITCODE
"""


def L(text="", st="out"):
    return ("out", [(text, st)])


def S(*segs):
    return ("out", list(segs))


def tok(s):
    return [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t
            for t in re.findall(r"\"[^\"]*\"|'[^']*'|\S+", s)]


def split_pipe(s):
    q = None
    for i, ch in enumerate(s):
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == "|":
            return s[:i], s[i + 1:]
    return s, None


def split_chain(s):
    out, buf, q, op, i = [], "", None, ";", 0
    while i < len(s):
        ch = s[i]
        if q:
            buf += ch
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            buf += ch
        elif s.startswith("&&", i) or ch == ";":
            out.append((buf, op))
            op = "&&" if ch == "&" else ";"
            buf = ""
            i += 1 if ch == ";" else 2
            continue
        else:
            buf += ch
        i += 1
    out.append((buf, op))
    return out


def fmb(x):
    return ("%.1f" if x >= 10 else "%.2f") % x


class Session:
    def __init__(self, kind):
        self.kind = kind  # "bash" | "ps"
        self.speed = 30.0  # МБ/с
        self.clock = datetime.now().replace(microsecond=0)
        self.last_exit = 0
        self.history = []
        self.winget, self.apt = {}, {}
        self.choco = {}
        self.pip = {"pip": "24.2", "setuptools": "75.1.0"}
        self.stages, self.vars, self.fs = {}, {}, {}
        self.env = {
            "COMPUTERNAME": "DESKTOP-LAB",
            "OS": "Windows_NT",
            "PROCESSOR_ARCHITECTURE": "AMD64",
            "USERDOMAIN": "DESKTOP-LAB",
            "USERNAME": "admin",
            "USERPROFILE": "C:\\Users\\admin",
            "HOMEDRIVE": "C:",
            "HOMEPATH": "\\Users\\admin",
            "TEMP": "C:\\Users\\admin\\AppData\\Local\\Temp",
            "TMP": "C:\\Users\\admin\\AppData\\Local\\Temp",
            "PATH": "C:\\Windows\\System32;C:\\Windows;C:\\Python312;C:\\Deploy\\tools",
        }
        self.services = {
            "BITS": {"DisplayName": "Background Intelligent Transfer Service", "Status": "Running", "StartType": "Manual"},
            "EventLog": {"DisplayName": "Windows Event Log", "Status": "Running", "StartType": "Automatic"},
            "Spooler": {"DisplayName": "Print Spooler", "Status": "Running", "StartType": "Automatic"},
            "wuauserv": {"DisplayName": "Windows Update", "Status": "Stopped", "StartType": "Manual"},
        }
        self.processes = {
            4: {"Name": "System", "CPU": 12.5, "WorkingSet": 10485760},
            912: {"Name": "explorer", "CPU": 8.2, "WorkingSet": 71303168},
            1480: {"Name": "svchost", "CPU": 4.1, "WorkingSet": 52428800},
            2844: {"Name": "TerminalSim", "CPU": 1.3, "WorkingSet": 41943040},
        }
        self.network_latency_ms = 25
        self.network_loss_pct = 0.0
        self.location_stack = []
        self.aliases = dict(PS_ALIASES)
        self.git_repos = {}
        self.pipeline_input = None
        self.pipeline_output = None
        if kind == "bash":
            self.home = self.cwd = "/root"
            for d in ["/", "/root", "/etc", "/usr", "/usr/bin", "/var", "/var/log", "/tmp", "/home"]:
                self.mkdir(d)
            self.write("/etc/os-release", 'PRETTY_NAME="Ubuntu 24.04.1 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\n')
            self.write("/root/check.sh", CHECK_SH, x=True)
            self.write("/root/deploy_linux.sh", DEPLOY_SH, x=True)
        else:
            self.cwd = "C:/Deploy"
            for d in ["C:", "C:/Deploy", "C:/Deploy/logs", "C:/Deploy/tools", "C:/Users",
                      "C:/Users/admin", "C:/Users/admin/AppData", "C:/Users/admin/AppData/Local",
                      "C:/Users/admin/AppData/Local/Temp", "C:/Windows", "C:/Windows/System32",
                      "C:/Program Files", "C:/Program Files/WindowsApps"]:
                self.mkdir(d)
            self.write("C:/Deploy/Deploy-Apps.ps1", DEPLOY_PS1)

    # ---------- виртуальная ФС ----------
    def key(self, p):
        return p if self.kind == "bash" else p.lower()

    def resolve(self, p):
        if self.kind == "bash":
            if p == "~" or p.startswith("~/"):
                p = self.home + p[1:]
            if not p.startswith("/"):
                p = self.cwd + "/" + p
            return posixpath.normpath(p)
        p = p.replace("\\", "/")
        if not re.match(r"^[A-Za-z]:", p):
            p = self.cwd + "/" + p
        r = posixpath.normpath(p)
        return r[0].upper() + r[1:]

    def get(self, p):
        return self.fs.get(self.key(p))

    def mkdir(self, p):
        self.fs[self.key(p)] = dict(n=posixpath.basename(p) or p, d=True, data="", t=self.clock.isoformat(), x=False)

    def write(self, p, data, append=False, x=False):
        n = self.get(p)
        if n and append:
            n["data"] += data
            n["t"] = self.clock.isoformat()
        else:
            self.fs[self.key(p)] = dict(n=posixpath.basename(p), d=False, data=data, t=self.clock.isoformat(), x=x)

    def listdir(self, p):
        k = self.key(p)
        items = [n for kk, n in self.fs.items() if kk != k and posixpath.dirname(kk) == k]
        return sorted(items, key=lambda n: n["n"].lower())

    def remove(self, p):
        k = self.key(p)
        for kk in [kk for kk in self.fs if kk == k or kk.startswith(k.rstrip("/") + "/")]:
            del self.fs[kk]

    def dpath(self):
        d = self.cwd.replace("/", "\\")
        return d + "\\" if len(d) == 2 else d

    def prompt(self):
        if self.kind == "bash":
            c = self.cwd
            if c == self.home:
                c = "~"
            elif c.startswith(self.home + "/"):
                c = "~" + c[len(self.home):]
            return [("root@vm:%s# " % c, "bprompt")]
        return [("PS " + self.dpath() + "> ", "out")]

    # ---------- время и загрузки ----------
    def w(self, s):
        self.clock += timedelta(seconds=s)
        return ("wait", s)

    def ts(self):
        return self.clock.strftime("%H:%M:%S")

    def lts(self):
        return self.clock.strftime("%Y-%m-%d %H:%M:%S")

    def download(self, key, mb, indent="           "):
        done, steps = 0.0, 22
        while done < mb - 1e-9:
            spd = self.speed * random.uniform(0.75, 1.1)
            chunk = min(mb - done, mb / steps)
            done += chunk
            yield self.w(chunk / spd)
            fin = done >= mb - 1e-9
            n = int(round(28 * done / mb))
            tail = "  %s MB / %s MB" % (fmb(done), fmb(mb))
            if not fin:
                tail += "  %.1f MB/s  ETA %02d:%02d" % (spd, int((mb - done) / spd) // 60, int((mb - done) / spd) % 60)
            yield ("bar", key, [(indent + "█" * n, "ok"), ("▒" * (28 - n), "dim"), (tail, "dim")])

    # ---------- выполнение ----------
    def execute(self, line):
        line = line.strip()
        if not line:
            return
        self.history.append(line)
        for cmd, op in split_chain(line):
            if op == "&&" and self.last_exit != 0:
                continue
            yield from self.run_pipe(cmd)

    def run_pipe(self, part):
        part = re.sub(r"\s*2>&1", "", part.strip())
        if not part:
            return
        parts, current, quote, i = [], "", None, 0
        while i < len(part):
            ch = part[i]
            if quote:
                current += ch
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
                current += ch
            elif ch == "|":
                parts.append(current.strip())
                current = ""
            else:
                current += ch
            i += 1
        parts.append(current.strip())
        pipeline = None
        for index, command in enumerate(parts):
            if self.kind == "bash" and index and command:
                filter_args = tok(command)
                filter_name = filter_args[0].lower() if filter_args else ""
                if filter_name in ("grep", "egrep", "findstr", "out-null"):
                    if filter_name == "out-null":
                        pipeline = []
                        self.last_exit = 0
                    else:
                        options = [arg for arg in filter_args[1:] if arg.startswith("-") or arg.startswith("/")]
                        patterns = [arg for arg in filter_args[1:] if arg not in options]
                        try:
                            regex = re.compile(patterns[-1], re.I if any(opt.lower() in ("-i", "/i") for opt in options) else 0) if patterns else None
                        except re.error:
                            regex = None
                        invert = "-v" in options or "/v" in options
                        pipeline = [line for line in (pipeline or [])
                                    if regex is not None and (bool(regex.search(str(line))) != invert)]
                        self.last_exit = 0 if pipeline else 1
                    self.pipeline_output = pipeline
                    if index == len(parts) - 1:
                        for item in pipeline:
                            yield L(str(item))
                    continue
            self.pipeline_input = pipeline
            self.pipeline_output = None
            lines = []
            for event in self.run_cmd(command):
                if event[0] == "out":
                    lines.append("".join(segment[0] for segment in event[1]))
                if index == len(parts) - 1 or event[0] != "out":
                    yield event
            pipeline = self.pipeline_output if self.pipeline_output is not None else lines
        self.pipeline_input = None
        self.pipeline_output = None

    def run_cmd(self, cmd):
        m = re.match(r"^(?:echo|write-output|write-host)\s+(.*?)\s*(>>?)\s*(\S+)$", cmd, re.I)
        if m:
            txt = " ".join(self.expand_ps_token(t) for t in tok(m.group(1)))
            self.write(self.resolve(m.group(3)), txt + "\n", append=m.group(2) == ">>")
            self.last_exit = 0
            return
        m = re.match(r"^\$?(\w+)\s*=\s*(.*)$", cmd)
        if self.kind == "ps":
            m = re.match(r"^(?:\[(?:string|int|long|bool|double)\]\s*)?\$([A-Za-z_]\w*)\s*=\s*(.*)$", cmd, re.I)
            env_assignment = re.match(r"^\$env:([A-Za-z_][\w]*)\s*=\s*(.*)$", cmd, re.I)
            if env_assignment:
                self.env[env_assignment.group(1).upper()] = self.ps_value(env_assignment.group(2))
                self.last_exit = 0
                return
        if m and not cmd.startswith("./"):
            value = " ".join(tok(m.group(2)))
            if self.kind == "ps":
                value = self.ps_value(m.group(2))
            self.vars[m.group(1)] = value
            self.last_exit = 0
            return
        argv = tok(cmd)
        if not argv:
            return
        name, args = argv[0], argv[1:]
        if name == "sudo" and args:
            name, args = args[0], args[1:]
        if self.kind == "ps":
            if name.startswith("$"):
                yield L(self.expand_ps_token(name))
                self.last_exit = 0
                return
            args = [self.expand_ps_token(a) for a in args]
            name = self.expand_ps_token(name)
        else:
            args = [re.sub(r"\$(\w+)", lambda mm: self.vars.get(mm.group(1), "") if mm.group(1) != "LASTEXITCODE" else str(self.last_exit), a).replace("$?", str(self.last_exit)) for a in args]
        if name == "$LASTEXITCODE":
            yield L(str(self.last_exit), "ok" if self.last_exit == 0 else "err")
            return
        if self.kind == "ps" and name.startswith("$"):
            yield L(self.expand_ps_token(name))
            self.last_exit = 0
            return
        rc = yield from self.dispatch(name, args)
        self.last_exit = rc or 0

    def ps_value(self, value):
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return self.expand_ps_token(value)

    def expand_ps_token(self, value):
        if self.kind != "ps" or not isinstance(value, str):
            return value
        if "$_." in value or "$PSItem." in value:
            return value

        def replace(match):
            token = match.group(1) or match.group(2) or match.group(3)
            if token.startswith("env:"):
                return self.env.get(token[4:].upper(), "")
            if token.lower() == "pwd":
                return self.dpath()
            if token.lower() == "home":
                return self.env["USERPROFILE"]
            if token.lower() == "true":
                return "True"
            if token.lower() == "false":
                return "False"
            if token.lower() == "null":
                return ""
            if token.lower() == "lastExitCode".lower():
                return str(self.last_exit)
            if token == "?":
                return str(self.last_exit == 0).lower()
            return str(next((value for key, value in self.vars.items() if key.lower() == token.lower()), ""))

        return re.sub(r"\$\{([^}]+)\}|\$([A-Za-z_]\w*(?::[A-Za-z_]\w*)?)|\$(\?)", replace, value)

    @staticmethod
    def ps_params(args):
        positional, params, i = [], {}, 0
        abbreviations = {
            "r": "recurse", "rec": "recurse", "f": "force", "fo": "force",
            "p": "path", "pa": "path", "lp": "literalpath", "v": "value",
            "it": "itemtype", "d": "destination", "nn": "newname", "fi": "filter",
            "co": "count", "s": "seconds", "id": "id",
        }
        while i < len(args):
            arg = args[i]
            if arg.startswith("-") and len(arg) > 1:
                key, sep, value = arg[1:].partition(":")
                key = key.lower()
                key = abbreviations.get(key, key)
                if sep:
                    params[key] = value
                elif i + 1 < len(args) and not args[i + 1].startswith("-"):
                    params[key] = args[i + 1]
                    i += 1
                else:
                    params[key] = True
            else:
                positional.append(arg)
            i += 1
        return positional, params

    def ps_emit_objects(self, objects, properties=None):
        self.pipeline_output = objects
        if not objects:
            return
        if properties:
            columns = []
            for prop in properties:
                key = prop.split("=")[-1]
                columns.append((key, [self.ps_property(obj, prop) for obj in objects]))
        elif isinstance(objects[0], dict):
            preferred = [key for key in ("Name", "Status", "DisplayName", "Id", "Version", "Mode", "Length")
                         if key in objects[0]]
            columns = [(key, [self.ps_property(obj, key) for obj in objects]) for key in preferred[:4]]
            if not columns:
                columns = [(key, [self.ps_property(obj, key) for obj in objects]) for key in objects[0]]
        else:
            for obj in objects:
                yield self._ps_write_line(str(obj))
            return
        widths = [max(len(title), *(len(str(value)) for value in values)) for title, values in columns]
        yield self._ps_write_line("  ".join(title.ljust(widths[i]) for i, (title, _) in enumerate(columns)))
        yield self._ps_write_line("  ".join("-" * width for width in widths))
        for row in zip(*(values for _, values in columns)):
            yield self._ps_write_line("  ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)))

    @staticmethod
    def _ps_write_line(text, style="out"):
        return L(text, style)

    @staticmethod
    def ps_property(item, name):
        if not isinstance(item, dict):
            return item
        key = name.split("=")[-1].strip()
        return next((value for prop, value in item.items() if prop.lower() == key.lower()), "")

    def unknown(self, name):
        if self.kind == "bash":
            yield L("bash: %s: command not found" % name, "out")
            return 127
        yield L('%s : Имя "%s" не распознано как имя командлета, функции, файла сценария или выполняемой программы. '
                'Проверьте правильность написания имени, а также наличие и правильность пути, после чего повторите попытку.' % (name, name), "err")
        yield L("строка:1 знак:1", "err")
        yield L("+ " + name, "err")
        yield L("+ " + "~" * len(name), "err")
        yield L("    + CategoryInfo          : ObjectNotFound: (%s:String) [], CommandNotFoundException" % name, "err")
        yield L("    + FullyQualifiedErrorId : CommandNotFoundException", "err")
        return 1

    def ps_err(self, cmd, msg, target, cat, exc):
        yield L("%s : %s" % (cmd, msg), "err")
        yield L("    + CategoryInfo          : %s: (%s:String) [%s], %s" % (cat, target, cmd, exc), "err")
        yield L("    + FullyQualifiedErrorId : " + exc, "err")
        return 1

    def ps_path(self, params, positional, default="."):
        path = params.get("literalpath", params.get("path"))
        if path is None:
            path = positional[0] if positional else default
        if not isinstance(path, str):
            return ""
        if isinstance(path, str) and path.lower().startswith("env:"):
            return path
        return self.resolve(path)

    def ps_node_object(self, path, node):
        full = path.replace("/", "\\")
        return {
            "Name": node["n"],
            "FullName": full,
            "Mode": "d-----" if node["d"] else "-a----",
            "Length": "" if node["d"] else len(node["data"]),
            "LastWriteTime": datetime.fromisoformat(node["t"]).strftime("%d.%m.%Y %H:%M"),
            "PSIsContainer": node["d"],
        }

    def ps_children(self, path, recursive=False):
        node = self.get(path)
        if not node:
            return []
        if not node["d"]:
            return [(path, node)]
        result = [(posixpath.join(path, child["n"]), child) for child in self.listdir(path)]
        if recursive:
            for child_path, child in list(result):
                if child["d"]:
                    result.extend(self.ps_children(child_path, True))
        return result

    def ps_expand_paths(self, path):
        target = self.resolve(path)
        name = posixpath.basename(target)
        if "*" not in name and "?" not in name:
            return [target]
        parent = posixpath.dirname(target)
        node = self.get(parent)
        if not node or not node["d"]:
            return []
        return [posixpath.join(parent, item["n"]) for item in self.listdir(parent)
                if self._glob_match(item["n"], name)]

    def ps_command(self, command, args):
        verb = command.lower()
        positional, params = self.ps_params(args)
        if verb in ("get-childitem",):
            target = self.ps_path(params, positional)
            if target.lower().startswith("env:"):
                prefix = target[4:].upper()
                objects = [{"Name": name, "Value": value} for name, value in sorted(self.env.items())
                           if not prefix or name.startswith(prefix)]
                yield from self.ps_emit_objects(objects, ["Name", "Value"])
                return 0
            path_pattern = posixpath.basename(target)
            parent_path = posixpath.dirname(target)
            if "*" in path_pattern or "?" in path_pattern:
                parent = self.get(parent_path)
                items = [(posixpath.join(parent_path, child["n"]), child) for child in self.listdir(parent_path)
                         if parent and parent["d"] and self._glob_match(child["n"], path_pattern)]
                node = parent
            else:
                node = self.get(target)
                if not node and ("*" in target or "?" in target):
                    parent_path, pattern = posixpath.dirname(target), posixpath.basename(target)
                    parent = self.get(parent_path)
                    matches = [(path, item) for path, item in self.ps_children(parent_path)
                               if parent and parent["d"] and self._glob_match(item["n"], pattern)]
                    if verb == "test-path":
                        yield L(str(bool(matches)).lower())
                        return 0
                    objects = [self.ps_node_object(path, item) for path, item in matches]
                    yield from self.ps_emit_objects(objects, ["Mode", "LastWriteTime", "Length", "Name"])
                    return 0 if matches else 1
                items = self.ps_children(target, bool(params.get("recurse"))) if node else []
            if not node:
                return (yield from self.ps_err("Get-ChildItem", "Не удается найти путь, так как он не существует.",
                                               target.replace("/", "\\"), "ObjectNotFound", "PathNotFound,Microsoft.PowerShell.Commands.GetChildItemCommand"))
            if not params.get("force"):
                items = [(path, item) for path, item in items if not item["n"].startswith(".")]
            if params.get("file"):
                items = [(path, item) for path, item in items if not item["d"]]
            if params.get("directory"):
                items = [(path, item) for path, item in items if item["d"]]
            pattern = params.get("filter", params.get("include"))
            if pattern:
                items = [(path, item) for path, item in items if self._glob_match(item["n"], pattern)]
            if params.get("exclude"):
                items = [(path, item) for path, item in items if not self._glob_match(item["n"], params["exclude"])]
            if params.get("name"):
                objects = [item["n"] for _, item in items]
                self.pipeline_output = objects
                for item in objects:
                    yield L(item)
                return 0
            objects = [self.ps_node_object(path, item) for path, item in items]
            yield from self.ps_emit_objects(objects, ["Mode", "LastWriteTime", "Length", "Name"])
            return 0
        if verb == "get-location":
            yield from self.ps_emit_objects([{"Path": self.dpath()}], ["Path"])
            return 0
        if verb == "set-location":
            target = self.ps_path(params, positional, self.cwd)
            node = self.get(target)
            if node and node["d"]:
                self.cwd = target
                return 0
            return (yield from self.ps_err("Set-Location", 'Не удается найти путь "%s", так как он не существует.' % target.replace("/", "\\"),
                                           target.replace("/", "\\"), "ObjectNotFound", "PathNotFound,Microsoft.PowerShell.Commands.SetLocationCommand"))
        if verb == "push-location":
            target = self.ps_path(params, positional, self.cwd)
            node = self.get(target)
            if node and node["d"]:
                self.location_stack.append(self.cwd)
                self.cwd = target
                return 0
            yield L("Push-Location: каталог не найден.", "err")
            return 1
        if verb == "pop-location":
            if not self.location_stack:
                yield L("Pop-Location: стек расположений пуст.", "warn")
                return 1
            self.cwd = self.location_stack.pop()
            return 0
        if verb == "get-item" or verb == "test-path":
            target = self.ps_path(params, positional)
            if target.lower().startswith("env:"):
                name = target[4:].upper()
                exists = name in self.env if name else True
                if verb == "test-path":
                    yield L(str(exists).lower())
                elif name in self.env:
                    yield from self.ps_emit_objects([{"Name": name, "Value": self.env[name]}], ["Name", "Value"])
                return 0 if exists else 1
            node = self.get(target)
            path_type = str(params.get("pathtype", "")).lower()
            if node and path_type == "leaf":
                node = node if not node["d"] else None
            elif node and path_type == "container":
                node = node if node["d"] else None
            if verb == "test-path":
                yield L(str(node is not None).lower())
                return 0
            if not node:
                return (yield from self.ps_err("Get-Item", "Не удается найти путь, так как он не существует.",
                                               target.replace("/", "\\"), "ObjectNotFound", "PathNotFound,Microsoft.PowerShell.Commands.GetItemCommand"))
            yield from self.ps_emit_objects([self.ps_node_object(target, node)], ["Mode", "LastWriteTime", "Length", "Name"])
            return 0
        if verb == "set-item":
            target = self.ps_path(params, positional, "")
            value = params.get("value", positional[1] if len(positional) > 1 else "")
            if target.lower().startswith("env:"):
                name = target[4:].upper()
                if not name:
                    yield L("Set-Item: имя переменной среды отсутствует.", "err")
                    return 1
                self.env[name] = str(value)
                return 0
            yield L("Set-Item: поддерживается только виртуальный провайдер Env:.", "warn")
            return 1
        if verb in ("get-content", "set-content", "add-content", "clear-content"):
            return (yield from self.ps_content(verb, positional, params))
        if verb == "new-item":
            return (yield from self.ps_new_item(positional, params))
        if verb == "remove-item":
            return (yield from self.ps_remove_item(positional, params))
        if verb in ("copy-item", "move-item"):
            return (yield from self.ps_copy_move(verb, positional, params))
        if verb == "rename-item":
            return (yield from self.ps_rename_item(positional, params))
        if verb in ("get-service", "start-service", "stop-service", "restart-service"):
            return (yield from self.ps_service(verb, positional, params))
        if verb in ("get-process", "stop-process", "start-process"):
            return (yield from self.ps_process(verb, positional, params))
        if verb in ("get-variable", "set-variable", "remove-variable"):
            return (yield from self.ps_variable(verb, positional, params))
        if verb in ("get-itemproperty", "set-itemproperty"):
            yield L("%s: реестр Windows не смоделирован; системный реестр не затрагивается." % command, "warn")
            return 1
        if verb == "get-command":
            commands = sorted(set(PS_ALIASES.values()) | {
                "Get-Item", "Test-Path", "Set-Content", "Add-Content", "Clear-Content", "Copy-Item",
                "Move-Item", "Rename-Item", "Get-Process", "Stop-Process", "Start-Process",
                "Get-Service", "Start-Service", "Stop-Service", "Restart-Service", "Get-History",
                "Clear-History", "Get-Variable", "Set-Variable", "Remove-Variable", "Start-Sleep",
                "Get-Random", "Get-Help", "Get-Date", "Get-Location", "Get-ChildItem", "New-Item",
                "Remove-Item", "Write-Output", "Write-Host", "Write-Error", "Write-Warning",
                "Select-String", "Select-Object", "Where-Object", "Sort-Object", "Measure-Object",
                "ForEach-Object", "Format-Table", "Format-List", "Out-String", "Out-File", "Out-Null",
                "Tee-Object", "ConvertTo-Json", "ConvertFrom-Json", "winget", "choco", "git", "ping",
                "Push-Location", "Pop-Location", "Get-SimulatedNetwork", "Set-SimulatedNetwork",
                "Get-Member",
            })
            pattern = params.get("name", positional[0] if positional else "*")
            commands = [item for item in commands if self._glob_match(item, pattern)]
            yield from self.ps_emit_objects([{"CommandType": "Cmdlet", "Name": name} for name in commands],
                                            ["CommandType", "Name"])
            return 0 if commands else 1
        if verb == "get-help":
            topic = positional[0].lower() if positional else "*"
            help_text = {
                "filesystem": "Виртуальные файлы: Get-ChildItem, Get-Item, New-Item, Get-Content, Set-Content, Add-Content, Remove-Item, Copy-Item, Move-Item, Rename-Item, Test-Path.",
                "pipeline": "Конвейер: Get-ChildItem | Where-Object { $_.Name -like '*.ps1' } | Select-Object Name. Поддерживаются безопасные фильтры, сортировка и форматирование.",
                "services": "Виртуальные службы: Get-Service, Start-Service, Stop-Service, Restart-Service. Изменения действуют только в текущей сессии.",
                "processes": "Виртуальные процессы: Get-Process, Start-Process, Stop-Process. Приложения и процессы не запускаются в Windows.",
                "packages": "Виртуальная установка: winget install|upgrade|uninstall|list, choco install|uninstall|list. Ничего не устанавливается в Windows.",
                "about_simulation": "Все команды изменяют только виртуальное состояние Terminal Simulator. PowerShell, CMD, Bash и системные процессы не запускаются.",
            }
            if topic in help_text:
                yield L(help_text[topic])
            else:
                yield L("Симулятор поддерживает виртуальные файловые команды, переменные, службы, процессы, историю, пакеты и безопасные конвейеры.")
                yield L("Для списка команд: Get-Command. Для сведений о границах: Get-Help about_simulation.")
            return 0
        if verb in ("get-history", "clear-history"):
            if verb == "clear-history":
                self.history.clear()
                return 0
            try:
                count = max(0, int(params.get("count", len(self.history))))
            except (TypeError, ValueError):
                yield L("Get-History: -Count должен быть целым числом.", "err")
                return 1
            objects = [{"Id": i, "CommandLine": item} for i, item in enumerate(self.history, 1)]
            yield from self.ps_emit_objects(objects[-count:] if count else [], ["Id", "CommandLine"])
            return 0
        if verb in ("get-date", "start-sleep", "get-random"):
            if verb == "get-date":
                date_format = params.get("format")
                if isinstance(date_format, str):
                    for ps_format, py_format in (("yyyy", "%Y"), ("yy", "%y"), ("MM", "%m"),
                                                 ("dd", "%d"), ("HH", "%H"), ("mm", "%M"), ("ss", "%S")):
                        date_format = date_format.replace(ps_format, py_format)
                    yield L(self.clock.strftime(date_format))
                else:
                    yield L(self.clock.strftime("%A, %B %d, %Y %H:%M:%S"))
                return 0
            if verb == "start-sleep":
                try:
                    seconds = float(params.get("seconds", positional[0] if positional else 1))
                    if "milliseconds" in params:
                        seconds = float(params["milliseconds"]) / 1000
                except (TypeError, ValueError):
                    yield L("Start-Sleep: значение секунд должно быть числом.", "err")
                    return 1
                seconds = min(max(seconds, 0), 3600)
                yield self.w(seconds)
                return 0
            try:
                low = int(params.get("minimum", 0))
                high = int(params.get("maximum", 100))
                yield L(str(random.randint(low, high)))
                return 0 if low <= high else 1
            except (TypeError, ValueError):
                yield L("Get-Random: диапазон задан неверно.", "err")
                return 1
        if verb in ("write-output", "write-host", "write-warning", "write-error"):
            text = " ".join(positional or [str(params.get("object", ""))])
            style = "warn" if verb == "write-warning" else ("err" if verb == "write-error" else "out")
            if verb == "write-error":
                yield L("Write-Error: " + text, style)
                return 1
            if params.get("nonewline"):
                yield ("out", [(text, style)])
            else:
                yield L(text, style)
            return 0
        if verb in ("get-alias", "set-alias", "remove-alias"):
            if verb == "get-alias":
                pattern = params.get("name", positional[0] if positional else "*")
                aliases = [{"Name": key, "Definition": value} for key, value in sorted(self.aliases.items())
                           if self._glob_match(key, pattern)]
                yield from self.ps_emit_objects(aliases, ["Name", "Definition"])
                return 0 if aliases else 1
            alias_name = params.get("name", positional[0] if positional else "")
            alias_value = params.get("value", positional[1] if len(positional) > 1 else "")
            if verb == "set-alias" and alias_name and alias_value:
                self.aliases[str(alias_name).lower()] = str(alias_value)
                return 0
            if verb == "remove-alias" and alias_name:
                self.aliases.pop(str(alias_name).lower(), None)
                return 0
            yield L("%s: укажите имя и определение псевдонима." % command, "err")
            return 1
        if verb == "get-member":
            incoming = self.pipeline_input or []
            if incoming and isinstance(incoming[0], dict):
                props = [{"Name": key, "MemberType": "NoteProperty", "Definition": type(value).__name__}
                         for key, value in incoming[0].items()]
                yield from self.ps_emit_objects(props, ["MemberType", "Name", "Definition"])
                return 0
            yield L("Get-Member: передайте объект через конвейер.", "warn")
            return 1
        if verb == "get-simulatednetwork":
            obj = {"LatencyMs": self.network_latency_ms, "LossPercent": self.network_loss_pct,
                   "Mode": "virtual/offline"}
            yield from self.ps_emit_objects([obj], ["LatencyMs", "LossPercent", "Mode"])
            return 0
        if verb == "set-simulatednetwork":
            try:
                if "latencyms" in params:
                    self.network_latency_ms = max(1, min(5000, int(params["latencyms"])))
                if "losspercent" in params:
                    self.network_loss_pct = max(0.0, min(100.0, float(params["losspercent"])))
            except (TypeError, ValueError):
                yield L("Set-SimulatedNetwork: укажите числовые -LatencyMs и -LossPercent.", "err")
                return 1
            yield from self.ps_emit_objects([{
                "LatencyMs": self.network_latency_ms, "LossPercent": self.network_loss_pct,
                "Mode": "virtual/offline",
            }], ["LatencyMs", "LossPercent", "Mode"])
            return 0
        if verb == "select-string":
            pattern = params.get("pattern", positional[0] if positional else "")
            file_paths = positional[1:] if positional else []
            if not file_paths:
                file_path = params.get("path", params.get("literalpath"))
                if isinstance(file_path, str):
                    file_paths = [file_path]
                pattern = params.get("pattern", pattern)
            if not file_paths:
                yield L("Select-String: укажите шаблон и файл или используйте конвейер.", "err")
                return 1
            try:
                regex = re.compile(pattern, re.I if params.get("caseinsensitive") else 0)
            except re.error:
                yield L("Select-String: неверный шаблон поиска.", "err")
                return 1
            found = []
            for file_path in file_paths:
                path = self.resolve(file_path)
                node = self.get(path)
                if not node or node["d"]:
                    yield L("Select-String: файл не найден: %s" % file_path, "warn")
                    continue
                for number, line in enumerate(node["data"].splitlines(), 1):
                    if regex.search(line):
                        found.append({"Path": path.replace("/", "\\"), "LineNumber": number, "Line": line})
            yield from self.ps_emit_objects(found, ["Path", "LineNumber", "Line"])
            return 0 if found else 1
        yield L("Командлет %s пока не входит в виртуальную модель симулятора." % command, "warn")
        return 1

    @staticmethod
    def _glob_match(value, pattern):
        pattern = str(pattern)
        value = str(value)
        return re.fullmatch(re.escape(pattern).replace(r"\*", ".*").replace(r"\?", "."), value, re.I) is not None

    def ps_content(self, verb, positional, params):
        target = self.ps_path(params, positional, "")
        if not target:
            yield L("%s: требуется параметр -Path." % verb, "err")
            return 1
        targets = self.ps_expand_paths(target)
        if verb == "get-content" and not targets:
            yield L("Get-Content : Не найдены файлы, соответствующие пути \"%s\"." % target.replace("/", "\\"), "err")
            return 1
        if verb != "get-content" and not targets and any(char in posixpath.basename(target) for char in "*?"):
            yield L("%s : Не найдено файлов, соответствующих шаблону пути." % verb, "err")
            return 1
        if verb in ("set-content", "add-content", "clear-content") and len(targets) > 1:
            yield L("%s: для шаблона пути найдено несколько файлов; укажите один путь." % verb, "err")
            return 1
        if targets:
            target = targets[0]
        node = self.get(target)
        if verb == "get-content":
            if not node or node["d"]:
                return (yield from self.ps_err("Get-Content", 'Не удается найти путь "%s", так как он не существует.' % target.replace("/", "\\"),
                                               target.replace("/", "\\"), "ObjectNotFound", "PathNotFound,Microsoft.PowerShell.Commands.GetContentCommand"))
            output = []
            for path in targets:
                item = self.get(path)
                if not item or item["d"]:
                    yield L("Get-Content : Не является файлом: %s" % path.replace("/", "\\"), "err")
                    return 1
                lines = item["data"].splitlines()
                try:
                    if "totalcount" in params:
                        lines = lines[:max(0, int(params["totalcount"]))]
                    elif "tail" in params:
                        amount = max(0, int(params["tail"]))
                        lines = lines[-amount:] if amount else []
                except (TypeError, ValueError):
                    yield L("Get-Content: TotalCount и Tail должны быть числами.", "err")
                    return 1
                if params.get("raw"):
                    output.append(item["data"])
                else:
                    output.extend(lines)
            self.pipeline_output = output
            for line in output:
                yield L(line)
            return 0
        if node and node["d"]:
            yield L("%s : Доступ к пути запрещен, так как это каталог." % verb, "err")
            return 1
        if not node and verb in ("add-content", "clear-content"):
            yield L("%s : Не удается найти указанный файл." % verb, "err")
            return 1
        parent = self.get(posixpath.dirname(target))
        if not parent or not parent["d"]:
            yield L("%s : Родительский каталог не существует." % verb, "err")
            return 1
        if params.get("whatif"):
            yield L("What if: Выполнение операции %s для целевого объекта \"%s\"." % (verb, target.replace("/", "\\")), "dim")
            return 0
        if verb == "clear-content":
            self.write(target, "")
        else:
            raw_value = params.get("value")
            if raw_value is None:
                raw_value = " ".join(positional[1:])
            if isinstance(raw_value, list):
                content = "\n".join(str(value) for value in raw_value)
            else:
                content = str(raw_value)
            if verb == "add-content":
                self.write(target, content + "\n", append=True)
            else:
                self.write(target, content + ("\n" if content else ""))
        self.pipeline_output = [self.ps_node_object(target, self.get(target))]
        return 0

    def ps_new_item(self, positional, params):
        target = self.ps_path(params, positional, "")
        name = params.get("name")
        if name:
            if not isinstance(name, str):
                yield L("New-Item: параметр -Name должен содержать имя.", "err")
                return 1
            target = posixpath.join(target, name) if target else self.resolve(name)
        if not target:
            yield L("New-Item: требуется параметр -Path или -Name.", "err")
            return 1
        kind = str(params.get("itemtype", "file")).lower()
        if kind not in ("file", "directory", "dir"):
            yield L("New-Item: поддерживаются только виртуальные файлы и каталоги.", "err")
            return 1
        is_directory = kind in ("directory", "dir")
        node = self.get(target)
        if node:
            if not params.get("force"):
                yield L("New-Item : Элемент с указанным именем \"%s\" уже существует." % target.replace("/", "\\"), "err")
                return 1
            if params.get("whatif"):
                yield L("What if: Выполнение операции New-Item для \"%s\"." % target.replace("/", "\\"), "dim")
                return 0
        parent_path = posixpath.dirname(target)
        parent = self.get(parent_path)
        if not parent and params.get("force"):
            missing = []
            current = parent_path
            while current and not self.get(current):
                missing.append(current)
                next_path = posixpath.dirname(current)
                if next_path == current:
                    break
                current = next_path
            for directory in reversed(missing):
                self.mkdir(directory)
            parent = self.get(parent_path)
        if not parent or not parent["d"]:
            yield L("New-Item : Родительский каталог не существует: %s." % target.replace("/", "\\"), "err")
            return 1
        if params.get("whatif"):
            yield L("What if: Выполнение операции New-Item для \"%s\"." % target.replace("/", "\\"), "dim")
            return 0
        if is_directory:
            self.mkdir(target)
            if "value" in params:
                yield L("New-Item: параметр -Value для каталогов игнорирован.", "warn")
        else:
            value = params.get("value", "")
            self.write(target, str(value) + ("\n" if value else ""))
        obj = self.ps_node_object(target, self.get(target))
        yield from self.ps_emit_objects([obj], ["Mode", "LastWriteTime", "Length", "Name"])
        return 0

    def ps_remove_item(self, positional, params):
        paths = params.get("literalpath", params.get("path", positional))
        if isinstance(paths, str):
            paths = [paths]
        elif not isinstance(paths, (tuple, list)):
            yield L("Remove-Item: значение -Path должно быть строкой или списком путей.", "err")
            return 1
        if not paths:
            yield L("Remove-Item: требуется параметр -Path.", "err")
            return 1
        rc = 0
        for path in paths:
            if not isinstance(path, str):
                yield L("Remove-Item: значение пути должно быть строкой.", "err")
                rc = 1
                continue
            if str(path).lower().startswith("env:"):
                name = str(path)[4:].upper()
                if name in self.env:
                    if params.get("whatif"):
                        yield L("What if: Удаление виртуальной переменной среды %s." % name, "dim")
                    else:
                        del self.env[name]
                else:
                    yield L("Remove-Item : Переменная среды не найдена: %s" % name, "warn")
                    rc = 1
                continue
            expanded = self.ps_expand_paths(path)
            if not expanded:
                yield L("Remove-Item : Путь не найден: %s" % path, "err")
                rc = 1
                continue
            for target in expanded:
                node = self.get(target)
                if not node:
                    rc = 1
                    continue
                if node["d"] and self.listdir(target) and not params.get("recurse"):
                    yield L("Remove-Item : Каталог не пуст. Укажите параметр -Recurse.", "err")
                    rc = 1
                    continue
                if params.get("whatif"):
                    yield L("What if: Выполнение операции Remove-Item для \"%s\"." % target.replace("/", "\\"), "dim")
                    continue
                self.remove(target)
        return rc

    def ps_copy_move(self, verb, positional, params):
        source = params.get("literalpath", params.get("path", positional[0] if positional else None))
        destination = params.get("destination", positional[1] if len(positional) > 1 else None)
        if not source or not destination:
            yield L("%s: требуются исходный путь и -Destination." % verb, "err")
            return 1
        if not isinstance(source, str) or not isinstance(destination, str):
            yield L("%s: пути должны быть строками." % verb, "err")
            return 1
        src, dst = self.resolve(source), self.resolve(destination)
        node = self.get(src)
        if not node:
            yield L("%s : Исходный путь не найден." % verb, "err")
            return 1
        if node["d"] and not params.get("recurse"):
            yield L("%s : Для каталогов требуется параметр -Recurse." % verb, "err")
            return 1
        if self.get(dst) and self.get(dst)["d"]:
            dst = posixpath.join(dst, node["n"])
        parent = self.get(posixpath.dirname(dst))
        if not parent or not parent["d"]:
            yield L("%s : Каталог назначения не существует." % verb, "err")
            return 1
        if params.get("whatif"):
            yield L("What if: Выполнение операции %s из \"%s\" в \"%s\"." %
                    (verb, src.replace("/", "\\"), dst.replace("/", "\\")), "dim")
            return 0
        sources = [(src, node)]
        if node["d"]:
            sources = [(src, node)] + self.ps_children(src, True)
        for old_path, old_node in sources:
            relative = posixpath.relpath(old_path, src)
            new_path = dst if relative == "." else posixpath.join(dst, relative)
            if old_node["d"]:
                self.mkdir(new_path)
            else:
                self.write(new_path, old_node["data"], x=old_node.get("x", False))
        if verb == "move-item":
            self.remove(src)
        return 0

    def ps_rename_item(self, positional, params):
        source = params.get("literalpath", params.get("path", positional[0] if positional else None))
        new_name = params.get("newname", positional[1] if len(positional) > 1 else None)
        if not source or not new_name:
            yield L("Rename-Item: требуются -Path и -NewName.", "err")
            return 1
        if not isinstance(source, str) or not isinstance(new_name, str):
            yield L("Rename-Item: пути и имя должны быть строками.", "err")
            return 1
        old_path = self.resolve(source)
        node = self.get(old_path)
        if not node:
            yield L("Rename-Item : Элемент не найден.", "err")
            return 1
        new_path = posixpath.join(posixpath.dirname(old_path), new_name)
        if self.get(new_path):
            yield L("Rename-Item : Элемент с новым именем уже существует.", "err")
            return 1
        if params.get("whatif"):
            yield L("What if: Переименование \"%s\" в \"%s\"." % (old_path, new_path), "dim")
            return 0
        descendants = [(old_path, node)]
        if node["d"]:
            descendants.extend(self.ps_children(old_path, True))
        for path, child in descendants:
            relative = posixpath.relpath(path, old_path)
            destination = new_path if relative == "." else posixpath.join(new_path, relative)
            if child["d"]:
                self.mkdir(destination)
            else:
                self.write(destination, child["data"], x=child.get("x", False))
        self.remove(old_path)
        return 0

    def ps_service(self, verb, positional, params):
        name = params.get("name", positional[0] if positional else "*")
        if self.pipeline_input is not None:
            piped_names = [self.ps_property(item, "Name") for item in self.pipeline_input]
            piped_names = [str(item) for item in piped_names if item]
            if piped_names and verb != "get-service":
                name = piped_names
        services = [key for key, data in self.services.items()
                    if any(self._glob_match(key, pattern) or self._glob_match(data["DisplayName"], pattern)
                           for pattern in (name if isinstance(name, list) else [name]))]
        if verb == "get-service":
            objects = [{"Name": key, **self.services[key]} for key in services]
            yield from self.ps_emit_objects(objects, ["Status", "Name", "DisplayName"])
            return 0 if objects else 1
        if not services:
            yield L("%s : Служба с указанным именем не найдена в виртуальной системе." % verb, "err")
            return 1
        if params.get("whatif"):
            yield L("What if: Изменение состояния виртуальной службы %s." % ", ".join(services), "dim")
            return 0
        for key in services:
            if verb == "start-service":
                self.services[key]["Status"] = "Running"
            elif verb == "stop-service":
                self.services[key]["Status"] = "Stopped"
            else:
                self.services[key]["Status"] = "Running"
        yield from self.ps_emit_objects([{"Name": key, **self.services[key]} for key in services],
                                        ["Status", "Name", "DisplayName"])
        return 0

    def ps_process(self, verb, positional, params):
        if verb == "get-process":
            name = params.get("name", positional[0] if positional else "*")
            objects = [{"Id": pid, **details} for pid, details in self.processes.items()
                       if self._glob_match(details["Name"], name)]
            yield from self.ps_emit_objects(objects, ["Id", "Name", "CPU", "WorkingSet"])
            return 0 if objects else 1
        if verb == "start-process":
            name = params.get("filePath", positional[0] if positional else "")
            if not name:
                yield L("Start-Process: требуется имя виртуального приложения.", "err")
                return 1
            known = {"notepad", "notepad.exe", "calc", "calc.exe", "explorer", "explorer.exe",
                     "code", "code.exe", "python", "python.exe", "terminal"}
            basename = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
            if basename not in known:
                yield L("Start-Process : Приложение не найдено в каталоге виртуальных программ.", "err")
                return 1
            if params.get("whatif"):
                yield L("What if: Запуск виртуального процесса %s." % basename, "dim")
                return 0
            pid = max(self.processes, default=3000) + 1
            self.processes[pid] = {"Name": basename.rsplit(".", 1)[0], "CPU": 0.0, "WorkingSet": 0}
            if params.get("passthru"):
                yield from self.ps_emit_objects([{"Id": pid, **self.processes[pid]}], ["Id", "Name"])
            return 0
        pid_value = params.get("id")
        name = params.get("name", positional[0] if positional else None)
        if self.pipeline_input is not None and pid_value is None and name is None:
            piped_ids = [self.ps_property(item, "Id") for item in self.pipeline_input]
            piped_ids = [item for item in piped_ids if item != ""]
            if piped_ids:
                matches = [int(item) for item in piped_ids if str(item).isdigit() and int(item) in self.processes]
            else:
                matches = []
            if not matches:
                yield L("Stop-Process : В конвейере не переданы виртуальные процессы.", "err")
                return 1
            if any(item in (4, 2844) for item in matches):
                yield L("Stop-Process : Защищенный процесс симулятора или системы нельзя остановить.", "err")
                return 1
            if params.get("whatif"):
                yield L("What if: Остановка виртуальных процессов %s." % ", ".join(map(str, matches)), "dim")
                return 0
            for item in matches:
                del self.processes[item]
            return 0
        try:
            pid = int(pid_value) if pid_value is not None else None
        except ValueError:
            pid = None
        matches = [item for item in self.processes
                   if (pid is not None and item == pid) or
                   (pid is None and name and self._glob_match(self.processes[item]["Name"], name))]
        if not matches:
            yield L("Stop-Process : Процесс не найден в виртуальном списке процессов.", "err")
            return 1
        if 2844 in matches or 4 in matches:
            yield L("Stop-Process : Защищенный процесс симулятора или системы нельзя остановить.", "err")
            return 1
        if params.get("whatif"):
            yield L("What if: Остановка виртуальных процессов %s." % ", ".join(map(str, matches)), "dim")
            return 0
        for item in matches:
            del self.processes[item]
        return 0

    def ps_variable(self, verb, positional, params):
        name = params.get("name", positional[0] if positional else "*")
        if verb == "get-variable":
            objects = [{"Name": key, "Value": value} for key, value in self.vars.items()
                       if self._glob_match(key, name)]
            yield from self.ps_emit_objects(objects, ["Name", "Value"])
            return 0 if objects else 1
        if verb == "set-variable":
            value = params.get("value", " ".join(positional[1:]) if len(positional) > 1 else None)
            if value is None:
                yield L("Set-Variable: укажите -Name и -Value.", "err")
                return 1
            if not name or name == "*":
                yield L("Set-Variable: укажите имя переменной.", "err")
                return 1
            existing = next((key for key in self.vars if key.lower() == str(name).lower()), str(name))
            self.vars[existing] = str(value)
            return 0
        if params.get("whatif"):
            yield L("What if: Удаление виртуальной переменной %s." % name, "dim")
            return 0
        for key in list(self.vars):
            if self._glob_match(key, name):
                del self.vars[key]
        return 0

    def ps_pipeline(self, command, args):
        verb = command.lower()
        positional, params = self.ps_params(args)
        incoming = self.pipeline_input if self.pipeline_input is not None else []
        if verb == "where-object":
            expression = " ".join(args)
            match = re.search(r"\$?(?:_|PSItem)(?:\.([A-Za-z_]\w*))?\s+-(eq|ne|like|notlike|match|notmatch|gt|ge|lt|le|contains)\s+(?:'([^']*)'|\"([^\"]*)\"|([^\s}]+))", expression, re.I)
            if not match:
                yield L("Where-Object: поддерживается только безопасное сравнение свойства: $_.Name -eq 'value'.", "warn")
                self.pipeline_output = []
                return 1
            key, operator = match.group(1), match.group(2)
            expected = next(value for value in match.groups()[2:] if value is not None)

            def matches(item):
                actual = self.ps_property(item, key) if key else item
                left, right = str(actual), expected
                op = operator.lower()
                if op in ("eq", "ne", "contains"):
                    found = right.lower() in left.lower() if op == "contains" else left.lower() == right.lower()
                    return not found if op == "ne" else found
                if op in ("like", "notlike"):
                    found = self._glob_match(left, right)
                    return not found if op == "notlike" else found
                if op in ("match", "notmatch"):
                    try:
                        found = re.search(right, left, re.I) is not None
                    except re.error:
                        found = False
                    return not found if op == "notmatch" else found
                try:
                    left_number, right_number = float(left), float(right)
                except ValueError:
                    return False
                return {"gt": left_number > right_number, "ge": left_number >= right_number,
                        "lt": left_number < right_number, "le": left_number <= right_number}[op]

            results = [item for item in incoming if matches(item)]
            yield from self.ps_emit_objects(results)
            return 0
        if verb == "select-object":
            try:
                first = int(params["first"]) if "first" in params else None
                last = int(params["last"]) if "last" in params else None
                skip = int(params.get("skip", 0) or 0)
            except (TypeError, ValueError):
                yield L("Select-Object: First, Last и Skip должны быть целыми числами.", "err")
                return 1
            unique = bool(params.get("unique"))
            values = list(incoming[skip:])
            if first is not None:
                values = values[:max(0, first)]
            if last is not None:
                values = values[-last:] if last > 0 else []
            if unique:
                seen, distinct = set(), []
                for item in values:
                    key = repr(item)
                    if key not in seen:
                        seen.add(key)
                        distinct.append(item)
                values = distinct
            properties = params.get("property", positional)
            if isinstance(properties, str):
                properties = [name for name in properties.split(",") if name]
            expanded = params.get("expandproperty")
            if expanded:
                values = [self.ps_property(item, str(expanded)) for item in values]
                yield from self.ps_emit_objects(values)
                return 0
            if properties and values and isinstance(values[0], dict):
                projected = [{prop.split("=")[-1]: self.ps_property(item, prop) for prop in properties}
                             for item in values]
                yield from self.ps_emit_objects(projected, [prop.split("=")[-1] for prop in properties])
            else:
                yield from self.ps_emit_objects(values)
            return 0
        if verb == "sort-object":
            key = params.get("property", positional[0] if positional else None)
            values = sorted(incoming, key=lambda item: str(self.ps_property(item, key)) if key else str(item),
                            reverse=bool(params.get("descending")))
            if params.get("unique"):
                seen, unique_values = set(), []
                for item in values:
                    marker = repr(item)
                    if marker not in seen:
                        seen.add(marker)
                        unique_values.append(item)
                values = unique_values
            yield from self.ps_emit_objects(values)
            return 0
        if verb == "measure-object":
            strings = [str(item) for item in incoming]
            lengths = [len(item) for item in strings]
            obj = {"Count": len(strings), "Average": round(sum(lengths) / len(lengths), 2) if lengths else 0,
                   "Sum": sum(lengths), "Maximum": max(lengths, default=0), "Minimum": min(lengths, default=0)}
            yield from self.ps_emit_objects([obj], ["Count", "Average", "Sum", "Maximum", "Minimum"])
            return 0
        if verb in ("format-table", "format-list", "out-string"):
            yield from self.ps_emit_objects(incoming)
            return 0
        if verb == "out-null":
            self.pipeline_output = []
            return 0
        if verb == "select-string":
            pattern = params.get("pattern", positional[0] if positional else "")
            try:
                regex = re.compile(pattern, re.I if params.get("caseinsensitive") else 0)
                result = [item for item in incoming if regex.search(str(item))]
            except re.error:
                yield L("Select-String: неверный шаблон поиска.", "err")
                return 1
            self.pipeline_output = result
            for item in result:
                yield L(str(item))
            return 0
        if verb == "foreach-object":
            script = " ".join(args)
            prop = re.search(r"\$?(?:_|PSItem)\.([A-Za-z_]\w*)", script, re.I)
            if prop:
                supported = re.fullmatch(
                    r"\s*\{?\s*\$?(?:_|PSItem)\.%s(?:\.(?:ToUpper|ToLower)\(\))?\s*;?\s*\}?\s*" %
                    re.escape(prop.group(1)), script, re.I)
                if not supported:
                    yield L("ForEach-Object: поддерживаются только $_, свойства и ToUpper()/ToLower().", "warn")
                    self.pipeline_output = []
                    return 1
                output = [self.ps_property(item, prop.group(1)) for item in incoming]
                if re.search(r"\.ToUpper\(\)", script, re.I):
                    output = [str(item).upper() for item in output]
                elif re.search(r"\.ToLower\(\)", script, re.I):
                    output = [str(item).lower() for item in output]
            elif re.search(r"\.ToUpper\(\)", script, re.I):
                output = [str(item).upper() for item in incoming]
            elif re.search(r"\.ToLower\(\)", script, re.I):
                output = [str(item).lower() for item in incoming]
            elif re.fullmatch(r"\s*\{?\s*\$_\s*;?\s*\}?\s*", script, re.I):
                output = incoming
            else:
                yield L("ForEach-Object: неподдерживаемое выражение скрипта.", "warn")
                self.pipeline_output = []
                return 1
            yield from self.ps_emit_objects(output)
            return 0
        if verb in ("out-file", "tee-object"):
            target = params.get("filepath", params.get("literalpath", positional[0] if positional else None))
            if not target:
                yield L("%s: требуется путь файла." % command, "err")
                return 1
            path = self.resolve(target)
            parent = self.get(posixpath.dirname(path))
            if not parent or not parent["d"]:
                yield L("%s: каталог назначения не существует." % command, "err")
                return 1
            content = "".join(str(item) + "\n" for item in incoming)
            self.write(path, content, append=bool(params.get("append")))
            if verb == "tee-object":
                yield from self.ps_emit_objects(incoming)
            else:
                self.pipeline_output = []
            return 0
        if verb == "convertto-json":
            text = json.dumps(incoming, ensure_ascii=False, default=str, indent=2 if params.get("pretty") else None)
            self.pipeline_output = [text]
            yield L(text)
            return 0
        if verb == "convertfrom-json":
            try:
                values = [json.loads(str(item)) for item in incoming]
            except json.JSONDecodeError:
                yield L("ConvertFrom-Json: входные данные не являются допустимым JSON.", "err")
                return 1
            yield from self.ps_emit_objects(values)
            return 0
        yield L("Командлет конвейера %s не поддерживается симулятором." % command, "warn")
        self.pipeline_output = incoming
        return 1

    def choco_cmd(self, args):
        verb = args[0].lower() if args else "help"
        flags = {arg.lower() for arg in args if arg.startswith("-")}
        packages = [arg.lower() for arg in args[1:] if not arg.startswith("-")]
        if verb in ("--version", "-v", "version"):
            yield L("2.3.0")
            return 0
        if verb in ("list", "installed"):
            if not self.choco:
                yield L("Chocolatey has determined 0 package(s) are installed.", "dim")
                return 0
            for name, version in sorted(self.choco.items()):
                yield L("%s %s" % (name, version))
            yield L("%d packages installed." % len(self.choco), "dim")
            return 0
        if verb in ("search", "info"):
            pattern = packages[0] if packages else "*"
            found = [(name, value[0]) for name, value in CHOCO.items() if self._glob_match(name, pattern)]
            for name, version in found:
                yield L("%s %s" % (name, version))
            return 0 if found else 1
        if verb in ("install", "upgrade", "uninstall", "remove"):
            if verb in ("upgrade", "uninstall", "remove") and packages == ["all"]:
                packages = list(self.choco)
                if not packages:
                    yield L("Chocolatey has determined 0 package(s) to process.", "dim")
                    return 0
            if not packages:
                yield L("choco %s: укажите имя пакета." % verb, "err")
                return 1
            rc = 0
            for name in packages:
                if name not in CHOCO:
                    yield L("Chocolatey was unable to find a package named '%s'." % name, "err")
                    rc = 1
                    continue
                version, size = CHOCO[name]
                if verb in ("uninstall", "remove"):
                    if name not in self.choco:
                        yield L("%s is not installed." % name, "warn")
                    elif "-whatif" in flags:
                        yield L("What if: Uninstalling virtual package %s." % name, "dim")
                    else:
                        del self.choco[name]
                        yield L("%s has been virtually uninstalled." % name)
                    continue
                if verb == "install" and name in self.choco:
                    yield L("%s v%s is already installed." % (name, self.choco[name]), "dim")
                    continue
                if verb == "upgrade" and name not in self.choco:
                    yield L("%s is not installed; use choco install." % name, "warn")
                    rc = 1
                    continue
                if "-whatif" in flags:
                    yield L("What if: Installing virtual package %s v%s." % (name, version), "dim")
                    continue
                yield L("Chocolatey v2.3.0")
                yield L("Installing the following packages: %s" % name)
                yield L("Downloading %s %s (simulated)" % (name, version), "dim")
                yield from self.download("choco-" + name, size, indent="  ")
                yield self.w(random.uniform(0.5, 1.5))
                self.choco[name] = version
                if name == "git":
                    self.mkdir("C:/Program Files/Git")
                    self.write("C:/Program Files/Git/git.exe", "Virtual Git executable placeholder")
                yield L("%s v%s has been %s." % (name, version, "upgraded" if verb == "upgrade" else "installed"))
            return rc
        yield L("Chocolatey v2.3.0. Commands: install, upgrade, uninstall, list, search.", "dim")
        return 0

    def virtual_ping(self, command, args):
        positional, params = self.ps_params(args) if self.kind == "ps" else (args, {})
        target = params.get("computername", positional[0] if positional else "")
        if not target:
            yield L("%s: укажите виртуальное имя узла." % command, "err")
            return 1
        count = params.get("count", 4)
        try:
            count = max(1, min(10, int(count)))
        except (TypeError, ValueError):
            count = 4
        if target.lower() in ("unreachable.invalid", "no-such-host.invalid"):
            yield L("Ping request could not find host %s. Check the name and try again." % target, "err")
            return 1
        sent = received = 0
        for index in range(count):
            yield self.w(self.network_latency_ms / 1000)
            sent += 1
            if random.random() < self.network_loss_pct / 100:
                yield L("Request timed out.", "warn")
                continue
            received += 1
            latency = max(1, int(random.gauss(self.network_latency_ms, max(1, self.network_latency_ms * 0.12))))
            if self.kind == "ps":
                yield L("Reply from 203.0.113.10: bytes=32 time=%dms TTL=57" % latency)
            else:
                yield L("64 bytes from %s: icmp_seq=%d ttl=57 time=%d ms" % (target, index + 1, latency))
        loss = int((sent - received) * 100 / sent)
        yield L("Ping statistics for %s: Packets: Sent = %d, Received = %d, Lost = %d (%d%% loss)" %
                (target, sent, received, sent - received, loss), "dim")
        return 0 if received else 1

    def git_root(self):
        path = self.cwd
        while path:
            if self.key(posixpath.join(path, ".git")) in self.fs:
                return path
            parent = posixpath.dirname(path)
            if parent == path:
                break
            path = parent
        return None

    def git_cmd(self, args):
        verb = args[0].lower() if args else ""
        values = args[1:]
        if verb == "--version":
            yield L("git version 2.47.0")
            return 0
        if verb == "clone":
            branch, destination, repository = "main", None, None
            i = 0
            while i < len(values):
                if values[i] in ("-b", "--branch") and i + 1 < len(values):
                    branch = values[i + 1]
                    i += 1
                elif not values[i].startswith("-"):
                    if repository is None:
                        repository = values[i]
                    elif destination is None:
                        destination = values[i]
                i += 1
            if not repository:
                yield L("fatal: repository URL is required", "err")
                return 128
            match = re.fullmatch(r"(?:https://|git@)github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?", repository, re.I)
            if not match:
                yield L("fatal: unsupported simulated repository URL; only GitHub-shaped URLs are accepted.", "err")
                return 128
            owner, repo = match.groups()
            repo = repo.removesuffix(".git")
            if repo.lower() in ("private", "not-found", "missing", "timeout"):
                message = "Repository not found or access denied." if repo.lower() != "timeout" else "Network timeout (simulated)."
                yield L("fatal: unable to access '%s': %s" % (repository, message), "err")
                return 128
            name = destination or repo
            root = self.resolve(name)
            parent = self.get(posixpath.dirname(root))
            if not parent or not parent["d"]:
                yield L("fatal: destination parent does not exist", "err")
                return 128
            if self.get(root):
                yield L("fatal: destination path '%s' already exists" % name, "err")
                return 128
            yield L("Cloning into '%s'..." % name)
            yield self.w(random.uniform(0.5, 1.5))
            yield L("remote: Repository metadata response (simulated; no network request was made)", "dim")
            yield from self.download("git-clone-" + repo, 0.8, indent="")
            self.mkdir(root)
            self.mkdir(posixpath.join(root, ".git"))
            self.mkdir(posixpath.join(root, ".git", "refs"))
            self.write(posixpath.join(root, ".git", "HEAD"), "ref: refs/heads/%s\n" % branch)
            self.write(posixpath.join(root, ".git", "config"), "[remote \"origin\"]\n\turl = %s\n" % repository)
            readme = "# %s\n\nVirtual repository metadata for %s/%s.\nNo code was fetched or executed.\n" % (repo, owner, repo)
            self.write(posixpath.join(root, "README.md"), readme)
            self.git_repos[self.key(root)] = {
                "path": root, "origin": repository, "branch": branch,
                "branches": [branch], "commits": ["a1b2c3d simulated initial commit"],
                "tracked": ["README.md"], "tracked_data": {"README.md": readme}, "staged": [],
            }
            yield L("Receiving objects: 100% (1/1), done.")
            yield L("Repository created in virtual file system; no repository code was run.", "dim")
            return 0
        root = self.git_root()
        if not root:
            yield L("fatal: not a git repository (or any of the parent directories): .git", "err")
            return 128
        repo = self.git_repos.get(self.key(root))
        if repo is None:
            yield L("fatal: virtual repository metadata is unavailable.", "err")
            return 128
        if verb == "status":
            yield L("On branch " + repo["branch"])
            changed, deleted = [], []
            for path in repo["tracked"]:
                node = self.get(posixpath.join(root, path))
                if not node:
                    deleted.append(path)
                elif node["data"] != repo.get("tracked_data", {}).get(path, node["data"]):
                    changed.append(path)
            has_changes = bool(repo["staged"] or changed or deleted)
            yield L("Changes to be committed:" if repo["staged"] else
                    ("Changes not staged for commit:" if has_changes else "nothing to commit, working tree clean"))
            for item in repo["staged"]:
                yield L("  %s: %s" % ("modified" if item in repo["tracked"] else "new file", item), "ok")
            for item in changed:
                yield L("  modified:   " + item, "warn")
            for item in deleted:
                yield L("  deleted:    " + item, "err")
            known = set(repo["tracked"]) | set(repo["staged"]) | {".git"}
            untracked = [item["n"] for item in self.listdir(root) if item["n"] not in known]
            if untracked:
                yield L("Untracked files:")
                for item in untracked:
                    yield L("  " + item, "warn")
            return 0
        if verb == "branch":
            if values and values[0] == "--show-current":
                yield L(repo["branch"])
                return 0
            if values and values[0] == "-d" and len(values) > 1:
                branch = values[1]
                if branch == repo["branch"]:
                    yield L("error: cannot delete the current branch", "err")
                    return 1
                if branch in repo["branches"]:
                    repo["branches"].remove(branch)
                else:
                    yield L("error: branch '%s' not found" % branch, "err")
                    return 1
            elif values and not values[0].startswith("-"):
                branch = values[0]
                if branch not in repo["branches"]:
                    repo["branches"].append(branch)
            for branch in repo["branches"]:
                yield L(("* " if branch == repo["branch"] else "  ") + branch)
            return 0
        if verb in ("switch", "checkout") and values:
            create = values[0] in ("-c", "-b", "--create")
            branch = values[1] if create and len(values) > 1 else values[0]
            if create and branch not in repo["branches"]:
                repo["branches"].append(branch)
            if branch not in repo["branches"]:
                yield L("fatal: invalid reference: %s" % branch, "err")
                return 1
            repo["branch"] = branch
            self.write(posixpath.join(root, ".git", "HEAD"), "ref: refs/heads/%s\n" % branch)
            yield L("Switched to branch '%s'" % branch)
            return 0
        if verb == "log":
            for index, commit in enumerate(repo["commits"]):
                yield L("commit " + commit.split()[0], "head")
                yield L("Author: Simulator <simulator@localhost>")
                yield L("Date:   " + self.clock.strftime("%a %b %d %H:%M:%S %Y"))
                yield L("")
                yield L("    " + " ".join(commit.split()[1:]))
                if index + 1 < len(repo["commits"]):
                    yield L("")
            return 0
        if verb == "add":
            for path in values:
                target = self.resolve(path)
                node = self.get(target)
                if not node:
                    yield L("fatal: pathspec '%s' did not match any virtual files" % path, "err")
                    return 128
                paths = [target]
                if node["d"]:
                    paths = [item_path for item_path, item in self.ps_children(target, True) if not item["d"]]
                for item_path in paths:
                    relative = posixpath.relpath(item_path, root)
                    if relative not in repo["staged"]:
                        repo["staged"].append(relative)
            return 0
        if verb == "commit":
            message = " ".join(values[1:]) if values and values[0] in ("-m", "--message") else "simulated commit"
            if not repo["staged"]:
                yield L("nothing to commit, working tree clean")
                return 1
            commit = "%s %s" % ("%07x" % random.randint(0, 0xFFFFFFF), message.strip("\"'"))
            repo["commits"].insert(0, commit)
            repo["tracked"] = list(dict.fromkeys(repo["tracked"] + repo["staged"]))
            for path in repo["staged"]:
                node = self.get(posixpath.join(root, path))
                if node and not node["d"]:
                    repo.setdefault("tracked_data", {})[path] = node["data"]
            repo["staged"] = []
            yield L("[%s %s] %s" % (repo["branch"], commit[:7], message.strip("\"'")))
            return 0
        if verb in ("fetch", "pull", "push"):
            yield L("fatal: remote access is disabled; repository operations are simulated offline.", "err")
            return 1
        if verb == "remote":
            yield L("origin\t%s (fetch)" % repo["origin"])
            yield L("origin\t%s (push)" % repo["origin"])
            return 0
        yield L("git: '%s' is not a supported virtual git command." % verb, "err")
        return 1

    def dispatch(self, name, args):
        bash = self.kind == "bash"
        n = name.lower() if not bash else name
        if not bash:
            command = self.aliases.get(n, name)
            pipeline_cmdlets = {
                "where-object", "select-object", "sort-object", "measure-object", "format-table",
                "format-list", "out-string", "out-null", "select-string", "foreach-object",
                "out-file", "tee-object", "convertto-json", "convertfrom-json",
            }
            if command.lower() in pipeline_cmdlets and (self.pipeline_input is not None or command.lower() != "select-string"):
                return (yield from self.ps_pipeline(command, args))
            ps_cmdlets = {
                "get-childitem", "get-location", "set-location", "push-location", "pop-location",
                "get-item", "test-path", "get-content",
                "set-content", "add-content", "clear-content", "new-item", "remove-item", "copy-item",
                "move-item", "rename-item", "get-service", "start-service", "stop-service", "restart-service",
                "get-process", "stop-process", "start-process", "get-variable", "set-variable",
                "remove-variable", "get-itemproperty", "set-itemproperty", "get-command", "get-help",
                "get-history", "clear-history", "get-date", "start-sleep", "get-random", "write-output",
                "write-host", "write-warning", "write-error", "get-alias", "set-alias", "remove-alias",
                "set-item", "select-string", "get-member", "get-simulatednetwork", "set-simulatednetwork",
            }
            if command.lower() in ps_cmdlets:
                if name.lower() in ("mkdir", "md"):
                    args = ["-ItemType", "Directory"] + args
                return (yield from self.ps_command(command, args))
            if command.lower() == "choco":
                return (yield from self.choco_cmd(args))
            if command.lower() in ("ping", "test-connection"):
                return (yield from self.virtual_ping(command, args))
            if command.lower() == "git":
                return (yield from self.git_cmd(args))
            n = command.lower()
        elif n.lower() == "git":
            return (yield from self.git_cmd(args))
        elif n.lower() == "ping":
            return (yield from self.virtual_ping(name, args))
        n = CANON.get(n.lower(), n) if not bash else n
        if n.lower() in ("pip", "pip3") or (n.lower() in ("python", "python3", "py") and args[:2] == ["-m", "pip"]):
            return (yield from self.pip_cmd(args[2:] if args[:1] == ["-m"] else args))
        if n == "clear":
            yield ("clear",)
            return 0
        if n.lower() == "exit" or (bash and n == "logout"):
            yield ("exit",)
            return 0
        if n.lower() == "help":
            yield L("Поддерживается: ls cd pwd mkdir touch cat echo rm clear exit date whoami hostname uname history")
            yield L("Пакеты: apt, dpkg, which, pip (Bash) | winget, pip (PowerShell). Скрипты: bash x.sh, .\\x.ps1, цепочки && ; и | grep")
            return 0
        if n.lower() == "pwd":
            yield L(self.cwd if bash else "")
            if not bash:
                yield L("Path")
                yield L("----")
                yield L(self.dpath())
                yield L("")
            return 0
        if n.lower() == "cd":
            t = self.resolve(args[0]) if args else (self.home if bash else self.cwd)
            node = self.get(t)
            if node and node["d"]:
                self.cwd = t
                return 0
            if bash:
                yield L("bash: cd: %s: %s" % (args[0], "Not a directory" if node else "No such file or directory"))
                return 1
            return (yield from self.ps_err("Set-Location", 'Не удается найти путь "%s", так как он не существует.' % t.replace("/", "\\"),
                                           t.replace("/", "\\"), "ObjectNotFound", "PathNotFound,Microsoft.PowerShell.Commands.SetLocationCommand"))
        if n.lower() == "ls":
            return (yield from self.ls(args))
        if n.lower() in ("mkdir", "touch", "new-item"):
            return (yield from self.mk(n.lower(), args))
        if n.lower() == "cat":
            rc = 0
            for a in args:
                node = self.get(self.resolve(a))
                if node and not node["d"]:
                    for ln in node["data"].splitlines():
                        yield L(ln)
                elif bash:
                    yield L("cat: %s: %s" % (a, "Is a directory" if node else "No such file or directory"))
                    rc = 1
                else:
                    p = self.resolve(a).replace("/", "\\")
                    rc = yield from self.ps_err("Get-Content", 'Не удается найти путь "%s", так как он не существует.' % p, p,
                                                "ObjectNotFound", "PathNotFound,Microsoft.PowerShell.Commands.GetContentCommand")
            return rc
        if n.lower() == "echo":
            yield L(" ".join(args))
            return 0
        if n.lower() == "rm":
            rc = 0
            for a in [x for x in args if not x.startswith("-")]:
                p = self.resolve(a)
                if self.get(p):
                    self.remove(p)
                elif bash and not any(f in args for f in ("-f", "-rf", "-fr")):
                    yield L("rm: cannot remove '%s': No such file or directory" % a)
                    rc = 1
                elif not bash:
                    rc = yield from self.ps_err("Remove-Item", 'Не удается найти путь "%s", так как он не существует.' % p.replace("/", "\\"),
                                                p.replace("/", "\\"), "ObjectNotFound", "PathNotFound,Microsoft.PowerShell.Commands.RemoveItemCommand")
            return rc
        if n.lower() == "whoami":
            yield L("root" if bash else "desktop-lab\\admin")
            return 0
        if n.lower() == "hostname":
            yield L("vm" if bash else "DESKTOP-LAB")
            return 0
        if n == "uname":
            yield L({"-r": "6.8.0-45-generic", "-a": "Linux vm 6.8.0-45-generic #45-Ubuntu SMP PREEMPT_DYNAMIC Fri Aug 30 12:02:04 UTC 2024 x86_64 x86_64 x86_64 GNU/Linux"}.get(args[0] if args else "", "Linux"))
            return 0
        if n.lower() == "date":
            c = self.clock
            if bash:
                yield L(c.strftime("%a %b %d %H:%M:%S UTC %Y"))
            else:
                yield L("")
                yield L("%s, %d %s %d г. %s" % (DAYS[c.weekday()], c.day, MONTHS[c.month - 1], c.year, c.strftime("%H:%M:%S")))
                yield L("")
            return 0
        if n == "history":
            for i, h in enumerate(self.history, 1):
                yield L("%5d  %s" % (i, h))
            return 0
        if n == "chmod":
            for a in args[1:]:
                nd = self.get(self.resolve(a))
                if nd:
                    nd["x"] = "x" in args[0] or "7" in args[0] or "5" in args[0]
            return 0
        if bash and n in ("which", "command"):
            rc = 1
            for a in [x for x in args if x != "-v"]:
                if any(a in APT[p]["bins"] for p in self.apt if p in APT) or a in ("bash", "ls", "cat"):
                    yield L("/usr/bin/" + a)
                    rc = 0
            return rc
        if bash and n == "apt" or n in ("apt-get", "apt"):
            return (yield from self.apt_cmd(args))
        if bash and n == "dpkg":
            return (yield from self.dpkg_cmd(args))
        if not bash and n.lower() == "winget":
            return (yield from self.winget_cmd(args))
        if n.lower().endswith((".sh", ".ps1")) or (bash and n in ("bash", "sh")) or n.startswith(("./", ".\\")):
            return (yield from self.run_script(n, args))
        return (yield from self.unknown(name))

    # ---------- файлы ----------
    def ls(self, args):
        bash = self.kind == "bash"
        flags = "".join(a for a in args if a.startswith("-"))
        paths = [a for a in args if not a.startswith("-")] or ["."]
        p = self.resolve(paths[0])
        node = self.get(p)
        if not node:
            if bash:
                yield L("ls: cannot access '%s': No such file or directory" % paths[0])
                return 2
            return (yield from self.ps_err("Get-ChildItem", 'Не удается найти путь "%s", так как он не существует.' % p.replace("/", "\\"),
                                           p.replace("/", "\\"), "ObjectNotFound", "PathNotFound,Microsoft.PowerShell.Commands.GetChildItemCommand"))
        items = self.listdir(p) if node["d"] else [node]
        if bash:
            if "a" not in flags:
                items = [i for i in items if not i["n"].startswith(".")]
            if "l" in flags:
                yield L("total %d" % (4 * (len(items) + 1)))
                for i in items:
                    t = datetime.fromisoformat(i["t"]).strftime("%b %d %H:%M")
                    mode = ("d" if i["d"] else "-") + ("rwxr-xr-x" if i["d"] or i["x"] else "rw-r--r--")
                    yield S((("%s 1 root root %5d %s " % (mode, 4096 if i["d"] else len(i["data"]), t)), "out"),
                            (i["n"], "dir" if i["d"] else ("ok" if i["x"] else "out")))
            elif items:
                segs = []
                for i in items:
                    segs += [(i["n"], "dir" if i["d"] else ("ok" if i["x"] else "out")), ("  ", "out")]
                yield S(*segs)
            return 0
        yield from self.ps_table(items, p)
        return 0

    def ps_table(self, items, p):
        yield L("")
        yield L("    Каталог: " + p.replace("/", "\\"))
        yield L("")
        yield L("")
        yield L("Mode                 LastWriteTime         Length Name")
        yield L("----                 -------------         ------ ----")
        for i in items:
            t = datetime.fromisoformat(i["t"])
            yield L("%s        %s     %s%15s %s" % ("d-----" if i["d"] else "-a----", t.strftime("%d.%m.%Y"), t.strftime("%H:%M"),
                                                       "" if i["d"] else len(i["data"]), i["n"]))
        yield L("")

    def mk(self, n, args):
        bash = self.kind == "bash"
        itype = ""
        paths = []
        i = 0
        while i < len(args):
            a = args[i]
            if a.lower() == "-itemtype" and i + 1 < len(args):
                itype = args[i + 1].lower()
                i += 1
            elif a.lower() in ("-path", "-name") and i + 1 < len(args):
                paths.append(args[i + 1])
                i += 1
            elif not a.startswith("-"):
                paths.append(a)
            i += 1
        rc = 0
        for a in paths:
            p = self.resolve(a)
            isdir = n == "mkdir" or itype in ("directory", "dir")
            if self.get(p) and isdir:
                if bash:
                    yield L("mkdir: cannot create directory '%s': File exists" % a)
                else:
                    rc = yield from self.ps_err("mkdir", "Элемент с указанным именем %s уже существует." % p.replace("/", "\\"),
                                                p.replace("/", "\\"), "ResourceExists", "DirectoryExist,Microsoft.PowerShell.Commands.NewItemCommand")
                rc = 1
                continue
            par = self.get(posixpath.dirname(p))
            if not par or not par["d"]:
                if bash:
                    yield L("%s: cannot %s '%s': No such file or directory" % (n, "create directory" if isdir else "touch", a))
                    rc = 1
                continue
            if isdir:
                self.mkdir(p)
            elif self.get(p):
                self.get(p)["t"] = self.clock.isoformat()
            else:
                self.write(p, "")
            if not bash:
                yield from self.ps_table([self.get(p)], posixpath.dirname(p))
        return rc

    def complete(self, line):
        parts = line.split(" ")
        word = parts[-1]
        if len(parts) == 1:
            cands = [c for c in ("ls", "cd", "pwd", "mkdir", "touch", "cat", "echo", "rm", "clear", "exit", "apt", "dpkg", "pip", "bash", "history", "whoami", "uname", "winget", "Get-ChildItem", "Set-Location", "Get-Content", "Get-Date") if c.lower().startswith(word.lower())]
            return word, cands
        d, _, base = word.replace("\\", "/").rpartition("/")
        folder = self.resolve(d + "/" if d else ".") if d or True else self.cwd
        names = [i["n"] + ("/" if i["d"] else "") for i in self.listdir(folder) if i["n"].lower().startswith(base.lower())]
        pre = (d + "/") if d else ""
        return word, [pre + x for x in names]

    # ---------- скрипты ----------
    def run_script(self, n, args):
        target = args[0] if n in ("bash", "sh") and args else n
        if n in ("bash", "sh") and not args:
            return 0
        base = target.replace("\\", "/").split("/")[-1]
        node = self.get(self.resolve(target))
        if not node or node["d"]:
            if self.kind == "bash":
                yield L("%s: %s: No such file or directory" % ("bash" if n in ("bash", "sh") else "bash", target))
                return 127 if n not in ("bash", "sh") else 127
            return (yield from self.ps_err(target, 'Термин "%s" не распознан как имя командлета, функции, файла сценария или выполняемой программы.' % target,
                                           target, "ObjectNotFound", "CommandNotFoundException"))
        if self.kind == "bash" and n not in ("bash", "sh") and not node["x"]:
            yield L("bash: %s: Permission denied" % target)
            return 126
        if base == "check.sh":
            return (yield from self.check_sh())
        if base == "deploy_linux.sh":
            return (yield from self.deploy_sh())
        if base.lower() == "deploy-apps.ps1":
            return (yield from self.ps_deploy(args if n.lower().endswith(".ps1") or n.startswith((".\\", "./")) else args[1:]))
        if self.kind == "ps":
            return (yield from self.run_ps_script(node["data"]))
        rc = 0
        for ln in node["data"].splitlines():
            if ln.strip() and not ln.strip().startswith("#"):
                for ev in self.execute(ln):
                    yield ev
                rc = self.last_exit
        return rc

    @staticmethod
    def split_ps_statements(source):
        statements, current = [], []
        quote, depth = None, 0
        for char in source:
            if quote:
                current.append(char)
                if char == quote:
                    quote = None
                continue
            if char in "\"'":
                quote = char
                current.append(char)
            elif char == "{":
                depth += 1
                current.append(char)
            elif char == "}":
                depth = max(0, depth - 1)
                current.append(char)
            elif char in ";\n" and depth == 0:
                statement = "".join(current).strip()
                if statement:
                    statements.append(statement)
                current = []
            else:
                current.append(char)
        statement = "".join(current).strip()
        if statement:
            statements.append(statement)
        return statements

    @staticmethod
    def ps_block(text, start):
        opening = text.find("{", start)
        if opening < 0:
            return None, len(text)
        depth, quote = 1, None
        for index in range(opening + 1, len(text)):
            char = text[index]
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[opening + 1:index].strip(), index + 1
        return None, len(text)

    def ps_array_values(self, value):
        values, current, quote = [], [], None
        for char in value:
            if quote:
                current.append(char)
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
                current.append(char)
            elif char == ",":
                item = "".join(current).strip()
                if item:
                    values.append(self.ps_value(item))
                current = []
            else:
                current.append(char)
        item = "".join(current).strip()
        if item:
            values.append(self.ps_value(item))
        return values

    def ps_condition(self, condition):
        condition = condition.strip()
        if condition.startswith("!"):
            return not self.ps_condition(condition[1:])
        if condition.lower() in ("$true", "true"):
            return True
        if condition.lower() in ("$false", "false", "$null", "null", ""):
            return False
        test_path = re.fullmatch(r"Test-Path\s+(.+)", condition, re.I)
        if test_path:
            events = list(self.execute("Test-Path " + test_path.group(1)))
            return self.last_exit == 0 and any("true" in "".join(segment[0] for segment in event[1]).lower()
                                               for event in events if event[0] == "out")
        comparison = re.fullmatch(r"(.+?)\s+-(eq|ne|like|notlike|match|notmatch|gt|ge|lt|le)\s+(.+)", condition, re.I)
        if not comparison:
            value = self.expand_ps_token(condition)
            return value.lower() not in ("", "false", "0")
        left, operator, right = comparison.groups()
        left, right = self.ps_value(left), self.ps_value(right)
        if left.startswith("$"):
            left = self.expand_ps_token(left)
        if right.startswith("$"):
            right = self.expand_ps_token(right)
        left, right, operator = str(left).strip("\"'"), str(right).strip("\"'"), operator.lower()
        if operator in ("eq", "ne"):
            equal = left.lower() == right.lower()
            return equal if operator == "eq" else not equal
        if operator in ("like", "notlike"):
            equal = self._glob_match(left, right)
            return equal if operator == "like" else not equal
        if operator in ("match", "notmatch"):
            try:
                equal = re.search(right, left, re.I) is not None
            except re.error:
                equal = False
            return equal if operator == "match" else not equal
        try:
            left_number, right_number = float(left), float(right)
        except ValueError:
            return False
        return {"gt": left_number > right_number, "ge": left_number >= right_number,
                "lt": left_number < right_number, "le": left_number <= right_number}[operator]

    def run_ps_script(self, source):
        statements = [item for item in self.split_ps_statements(source)
                      if not item.lstrip().startswith("#") and not item.lstrip().lower().startswith("param(")]
        rc = 0
        for statement in statements:
            conditional = re.match(r"^if\s*\((.*?)\)\s*", statement, re.I)
            if conditional:
                true_body, end = self.ps_block(statement, conditional.end())
                if true_body is None:
                    yield L("Скрипт: незакрытый блок if.", "err")
                    return 1
                rest = statement[end:].strip()
                false_body = ""
                if rest.lower().startswith("else"):
                    false_body, end = self.ps_block(rest, len("else"))
                    if false_body is None or rest[end:].strip():
                        yield L("Скрипт: поддерживается только простой блок else.", "err")
                        return 1
                elif rest:
                    yield L("Скрипт: неподдерживаемый синтаксис после if.", "err")
                    return 1
                body = true_body if self.ps_condition(conditional.group(1)) else false_body
                for command in self.split_ps_statements(body):
                    for event in self.execute(command):
                        yield event
                    rc = self.last_exit
                continue
            loop = re.match(r"^foreach\s*\(\s*\$([A-Za-z_]\w*)\s+in\s+(.+?)\s*\)\s*(?=\{)", statement, re.I)
            if loop:
                body, end = self.ps_block(statement, loop.end())
                if body is None or statement[end:].strip():
                    yield L("Скрипт: незакрытый или неподдерживаемый блок foreach.", "err")
                    return 1
                values = loop.group(2).strip()
                range_match = re.fullmatch(r"(-?\d+)\.\.(-?\d+)", values)
                if range_match:
                    start, stop = map(int, range_match.groups())
                    values = list(range(start, stop + (1 if stop >= start else -1), 1 if stop >= start else -1))
                else:
                    array_match = re.fullmatch(r"@?\((.*)\)", values)
                    if array_match:
                        values = self.ps_array_values(array_match.group(1))
                    elif values.startswith("$"):
                        values = str(self.expand_ps_token(values)).split(",")
                    else:
                        values = tok(values)
                if len(values) > 100:
                    yield L("Скрипт: ограничение симулятора — не более 100 итераций.", "err")
                    return 1
                for value in values:
                    self.vars[loop.group(1)] = str(value).strip("\"'")
                    for command in self.split_ps_statements(body):
                        for event in self.execute(command):
                            yield event
                        rc = self.last_exit
                        if rc != 0:
                            return rc
                continue
            for event in self.execute(statement):
                yield event
            rc = self.last_exit
        return rc

    def check_sh(self):
        for p in ("firefox", "p7zip-full"):
            if p in self.apt:
                yield L("%s: installed (%s)" % (p, self.apt[p]), "ok")
            else:
                yield L("%s: not installed" % p)
        for b, p in (("firefox", "firefox"), ("7z", "p7zip-full")):
            yield L("%s -> /usr/bin/%s" % (b, b) if p in self.apt else "%s -> not found" % b, "out")
        return 0

    # ---------- apt / dpkg ----------
    def apt_cmd(self, args):
        flags = [a for a in args if a.startswith("-")]
        rest = [a for a in args if not a.startswith("-")]
        if not rest:
            yield L("apt 2.7.14ubuntu1 (amd64)")
            return 0
        verb, names = rest[0], rest[1:]
        yes = any(f in ("-y", "--yes", "-qq") for f in flags)
        if verb == "update":
            return (yield from self.apt_update())
        if verb in ("install", "reinstall"):
            return (yield from self.apt_install(names, yes))
        if verb in ("remove", "purge", "autoremove"):
            return (yield from self.apt_remove(names, yes))
        if verb in ("upgrade", "full-upgrade", "dist-upgrade"):
            yield L("Reading package lists... Done")
            yield L("Building dependency tree... Done")
            yield L("Reading state information... Done")
            yield L("Calculating upgrade... Done")
            yield L("0 upgraded, 0 newly installed, 0 to remove and 12 not upgraded.")
            return 0
        yield L("E: Invalid operation %s" % verb, "err")
        return 100

    def apt_update(self):
        base = "http://archive.ubuntu.com/ubuntu"
        for i, s in enumerate(["noble InRelease", "noble-updates InRelease", "noble-backports InRelease"], 1):
            yield self.w(random.uniform(0.2, 0.6))
            yield L("%s:%d %s %s" % ("Hit" if i == 1 else "Get", i, base, s))
        yield L("Fetched %d kB in 1s (%d kB/s)" % (random.randint(300, 600), random.randint(400, 900)))
        yield L("Reading package lists... Done")
        yield L("Building dependency tree... Done")
        yield L("Reading state information... Done")
        yield L("12 packages can be upgraded. Run 'apt list --upgradable' to see them.")
        return 0

    def apt_install(self, names, yes, quiet=False):
        yield L("Reading package lists... Done")
        yield L("Building dependency tree... Done")
        yield L("Reading state information... Done")
        bad = [x for x in names if x not in APT]
        if bad:
            for x in bad:
                yield L("E: Unable to locate package " + x, "err")
            return 100
        new = [x for x in names if x not in self.apt]
        for x in names:
            if x in self.apt:
                yield L("%s is already the newest version (%s)." % (x, self.apt[x]))
        extra = sorted({d for x in new for d in APT[x]["deps"]} - set(self.apt))
        if not new:
            yield L("0 upgraded, 0 newly installed, 0 to remove and 12 not upgraded.")
            return 0
        if extra:
            yield L("The following additional packages will be installed:")
            if not quiet:
                yield L("  " + " ".join(extra))
        allp = extra + new
        yield L("The following NEW packages will be installed:")
        if not quiet:
            yield L("  " + " ".join(allp))
        kb = sum(APT[x]["kb"] for x in allp)
        yield L("0 upgraded, %d newly installed, 0 to remove and 12 not upgraded." % len(allp))
        yield L("Need to get {:,} kB of archives.".format(kb))
        yield L("After this operation, {:,} kB of additional disk space will be used.".format(kb * 3))
        if not yes:
            yield L("Do you want to continue? [Y/n] Y")
        for i, x in enumerate(allp, 1):
            yield self.w(random.uniform(0.2, 0.5))
            yield L("Get:%d http://archive.ubuntu.com/ubuntu noble/universe amd64 %s amd64 %s [{:,} kB]".format(APT[x]["kb"]) % (i, x, APT[x]["ver"]))
        yield L("Fetched {:,} kB in 1s ({:,} kB/s)".format(kb, int(kb * random.uniform(0.9, 1.3))))
        for i, x in enumerate(allp):
            yield L("Selecting previously unselected package %s." % x)
            yield L("(Reading database ... %d files and directories currently installed.)" % random.randint(71000, 73000))
            yield L("Preparing to unpack .../%d-%s_%s_amd64.deb ..." % (i, x, APT[x]["ver"].replace(":", "%3a")))
            yield L("Unpacking %s (%s) ..." % (x, APT[x]["ver"]))
        for x in allp:
            yield self.w(random.uniform(0.2, 0.7))
            yield L("Setting up %s (%s) ..." % (x, APT[x]["ver"]))
            self.apt[x] = APT[x]["ver"]
            if x == "firefox":
                yield L("=> Installing the firefox snap")
                yield L("==> Checking connectivity with the snap store")
                yield L("==> Installing the firefox snap")
                yield from self.download("snap", 262.0, indent="")
                yield self.w(random.uniform(3, 6))
                yield L("firefox 144.0-1 from Mozilla✓ installed")
        yield L("Processing triggers for man-db (2.12.0-4build2) ...")
        return 0

    def apt_remove(self, names, yes, quiet=False):
        yield L("Reading package lists... Done")
        yield L("Building dependency tree... Done")
        yield L("Reading state information... Done")
        rc, gone = 0, []
        for x in names:
            if x in self.apt:
                gone.append(x)
            else:
                yield L("Package '%s' is not installed, so not removed" % x)
        if not gone:
            yield L("0 upgraded, 0 newly installed, 0 to remove and 12 not upgraded.")
            return rc
        yield L("The following packages will be REMOVED:")
        if not quiet:
            yield L("  " + " ".join(gone))
        yield L("0 upgraded, 0 newly installed, %d to remove and 12 not upgraded." % len(gone))
        yield L("After this operation, {:,} kB disk space will be freed.".format(sum(APT[g]["kb"] for g in gone) * 3))
        yield L("(Reading database ... %d files and directories currently installed.)" % random.randint(71000, 73000))
        for g in gone:
            yield self.w(random.uniform(0.3, 1.0))
            yield L("Removing %s (%s) ..." % (g, self.apt.pop(g)))
        return rc

    def dpkg_cmd(self, args):
        if args and args[0] in ("-s", "--status") and len(args) > 1:
            p = args[1]
            if p in self.apt:
                yield L("Package: " + p)
                yield L("Status: install ok installed")
                yield L("Architecture: amd64")
                yield L("Version: " + self.apt[p])
                return 0
            yield L("dpkg-query: package '%s' is not installed and no information is available" % p)
            return 1
        if args and args[0] in ("-l", "--list"):
            yield L("Desired=Unknown/Install/Remove/Purge/Hold")
            yield L("| Status=Not/Inst/Conf-files/Unpacked/halF-conf/Half-inst/trig-aWait/Trig-pend")
            yield L("|/ Err?=(none)/Reinst-required (Status,Err: uppercase=bad)")
            yield L("||/ Name           Version                Architecture Description")
            yield L("+++-==============-======================-============-================================")
            for p, v in sorted(self.apt.items()):
                yield L("ii  %-14s %-22s amd64        installed by simulator" % (p, v))
            return 0
        yield L("dpkg: error: need an action option", "err")
        return 2

    # ---------- pip ----------
    def pip_cmd(self, args):
        verb = args[0] if args else ""
        names = [a for a in args[1:] if not a.startswith("-")]
        if verb == "list":
            rows = sorted(self.pip.items(), key=lambda kv: kv[0].lower())
            w1 = max(len("Package"), max(len(k) for k, _ in rows))
            yield L("%-*s Version" % (w1, "Package"))
            yield L("%s %s" % ("-" * w1, "-" * 9))
            for k, v in rows:
                yield L("%-*s %s" % (w1, k, v))
            return 0
        if verb == "install":
            for nm in names:
                key = nm.split("==")[0].lower()
                if key not in PIP:
                    yield L("ERROR: Could not find a version that satisfies the requirement %s (from versions: none)" % nm, "err")
                    yield L("ERROR: No matching distribution found for %s" % nm, "err")
                    return 1
                ver, deps = PIP[key]
                if key in self.pip:
                    yield L("Requirement already satisfied: %s in c:\\python312\\lib\\site-packages (%s)" % (key, self.pip[key]))
                    continue
                yield L("Collecting " + key)
                yield self.w(0.5)
                yield L("  Downloading %s-%s-py3-none-any.whl (64 kB)" % (key.replace("-", "_"), ver), "dim")
                yield from self.download("pip" + key, 0.064, indent="     ")
                for d, dv in deps:
                    yield L("Collecting %s>=%s (from %s)" % (d, dv, key))
                    yield L("  Downloading %s-%s-py3-none-any.whl (%d kB)" % (d.replace("-", "_"), dv, random.randint(30, 140)), "dim")
                yield L("Installing collected packages: " + ", ".join([d for d, _ in deps] + [key]))
                yield self.w(random.uniform(1, 2.5))
                for d, dv in deps:
                    self.pip[d] = dv
                self.pip[key] = ver
                yield L("Successfully installed " + " ".join(["%s-%s" % (d, dv) for d, dv in deps] + ["%s-%s" % (key, ver)]))
            return 0
        if verb in ("uninstall",):
            for nm in names:
                if nm in self.pip:
                    del self.pip[nm]
                    yield L("Successfully uninstalled " + nm)
                else:
                    yield L("WARNING: Skipping %s as it is not installed." % nm, "warn")
            return 0
        yield L("pip 24.2 from /usr/lib/python3/dist-packages/pip (python 3.12)")
        return 0

    # ---------- winget ----------
    def winget_cmd(self, args):
        verb = args[0].lower() if args else ""
        if verb in ("--version", "-v"):
            yield L("v1.9.25200")
            return 0
        pid = None
        for i, a in enumerate(args[1:], 1):
            if a == "--id" and i + 1 < len(args):
                pid = args[i + 1]
                break
            if not a.startswith("-") and (i == 1 or not args[i - 1].startswith("--")):
                pid = a
                break
        if verb == "list":
            rows = [(WINGET[p]["name"], p, v, "winget") for p, v in self.winget.items()]
            if not rows:
                yield L("No installed package found matching input criteria.")
                return -1978335212
            w = [max(len(r[i]) for r in rows + [("Name", "Id", "Version", "Source")]) for i in range(4)]
            yield L("Name".ljust(w[0]) + " " + "Id".ljust(w[1]) + " " + "Version".ljust(w[2]) + " Source")
            yield L("-" * (sum(w) + 3 + 6))
            for r in rows:
                yield L(" ".join(r[i].ljust(w[i]) for i in range(4)))
            return 0
        if verb == "upgrade" and any(a.lower() in ("--all", "-a") for a in args[1:]):
            updates = [pid for pid, version in self.winget.items() if version != WINGET[pid]["new"]]
            if not updates:
                yield L("No installed package found matching input criteria.")
                return -1978335212
            rc = 0
            for pid in updates:
                result = yield from self.winget_do(pid, "upgrade", indent="", verbose=True)
                if result != 0:
                    rc = result
            return rc
        if verb in ("install", "upgrade", "uninstall") and not pid and verb == "upgrade":
            old = [(p, v) for p, v in self.winget.items() if v != WINGET[p]["new"]]
            if not old:
                yield L("No installed package found matching input criteria.")
                return -1978335212
            yield L("Name            Id              Version Available Source")
            yield L("-" * 56)
            for p, v in old:
                yield L("%-15s %-15s %-7s %-9s winget" % (WINGET[p]["name"], p, v, WINGET[p]["new"]))
            return 0
        if verb in ("install", "upgrade", "uninstall") and pid:
            return (yield from self.winget_do(pid, verb, indent="", verbose=True))
        yield L("Не указана команда или пакет. Используйте: winget install|upgrade|uninstall|list", "err")
        return 1

    def winget_do(self, pid, mode, indent="           ", verbose=False):
        p = WINGET.get(pid)
        have = self.winget.get(pid)
        miss = "No package found matching input criteria."
        if mode == "install":
            if not p:
                yield L(miss)
                return -1978335212
            if have:
                yield L("Found an existing package already installed. Trying to upgrade the installed package...")
                yield L("No available upgrade found.")
                yield L("No newer package versions are available from the configured sources.")
                return -1978335189
            ver, mb = p["ver"], p["mb"]
        elif mode == "upgrade":
            if not have:
                yield L("No installed package found matching input criteria.")
                return -1978335217
            if have == p["new"]:
                yield L("No available upgrade found.")
                yield L("No newer package versions are available from the configured sources.")
                return -1978335189
            ver, mb = p["new"], p["mb_new"]
        else:
            if not have:
                yield L("No installed package found matching input criteria.")
                return -1978335212
            yield L(indent + "Found %s [%s] Version %s" % (p["name"], pid, have), "dim")
            if verbose:
                yield L("Starting package uninstall...")
            yield self.w(random.uniform(2.5, 5))
            yield L(indent + "Successfully uninstalled", "dim")
            del self.winget[pid]
            return 0
        yield L(indent + "Found %s [%s] Version %s" % (p["name"], pid, ver), "dim")
        if verbose:
            yield L("This application is licensed to you by its owner.")
            yield L("Microsoft is not responsible for, nor does it grant any licenses to, third-party packages.")
            yield L("Downloading https://download.example-cdn.net/%s/%s" % (pid.lower(), ver))
        yield from self.download("dl" + pid, mb, indent=indent if not verbose else "  ")
        yield L(indent + "Successfully verified installer hash", "dim")
        if verbose:
            yield L("Starting package install...")
        yield self.w(5 + mb * random.uniform(0.35, 0.5))
        self.winget[pid] = ver
        if verbose:
            yield L("Successfully installed")
        return 0

    # ---------- Deploy-Apps.ps1 ----------
    def lg(self, lvl, segs):
        tag, st = {"INFO": (" [INFO] ", "info"), "OK": (" [ OK ] ", "ok"), "WARN": (" [WARN] ", "warn"), "FAIL": (" [FAIL] ", "err")}[lvl]
        return S(("[%s]" % self.ts(), "ts"), (tag, st), *segs)

    def rule(self, title):
        return L("─" * 25 + " %s " % title + "─" * 25, "head")

    def table(self, head, rows, widths):
        def line(a, b, c):
            return L(a + b.join("─" * (w + 2) for w in widths) + c, "grid")
        yield line("┌", "┬", "┐")
        segs = [("│", "grid")]
        for h, w in zip(head, widths):
            segs += [(" " + h.ljust(w) + " ", "head"), ("│", "grid")]
        yield S(*segs)
        yield line("├", "┼", "┤")
        for row in rows:
            segs = [("│", "grid")]
            for (t, st), w in zip(row, widths):
                segs += [(" " + t.ljust(w) + " ", st), ("│", "grid")]
            yield S(*segs)
        yield line("└", "┴", "┘")

    def ps_deploy(self, args):
        a, i = {}, 0
        while i < len(args):
            if args[i].startswith("-") and i + 1 < len(args):
                a[args[i][1:].lower()] = args[i + 1]
                i += 1
            i += 1
        action = a.get("action", "").capitalize()
        if action not in ("Install", "Update", "Uninstall"):
            yield L("Deploy-Apps.ps1 : Не указан или неверен параметр -Action (Install | Update | Uninstall).", "err")
            return 1
        if action == "Uninstall" and not a.get("package"):
            yield L("")
            yield self.lg("FAIL", [("-Package is required for Uninstall", "out")])
            return 1
        pk = [a["package"]] if a.get("package") else list(WINGET)
        yield L("")
        yield S(("[%s]" % self.ts(), "ts"), (" [INFO] ", "info"), ("Pre-flight: Administrator ", "out"), ("✔", "ok"),
                ("  winget v1.9.25200 ", "out"), ("✔", "ok"))
        yield self.w(3)
        yield L("")
        return (yield from getattr(self, "stage_" + action.lower())(pk))

    def finish(self, stage, rc, log=False):
        ok = rc == 0
        self.stages[stage] = ("SUCCESS" if ok else "FAILED", rc)
        yield L("")
        yield S(("STATUS: ", "out"), ("✔ SUCCESS" if ok else "✖ FAILED", "ok" if ok else "err"), ("   exit=", "out"), (str(rc), "ok" if ok else "err"))
        if log:
            path = "C:/Deploy/logs/deploy-%s.log" % self.clock.strftime("%Y%m%d-%H%M%S")
            self.write(path, "[%s] Deploy-Apps.ps1 %s exit=%d\n" % (self.lts(), stage, rc))
            yield S(("Log file : ", "dim"), (path.replace("/", "\\"), "out"))
        return rc

    def count(self, label, ok, fail, extra, okword):
        return S((label + " summary: ", "out"), ("%d %s" % (ok, okword), "ok" if ok else "out"),
                 (", %d failed" % fail, "err" if fail else "out"), (extra, "out"))

    def stage_install(self, pk):
        ok = fail = skip = 0
        yield self.rule("1. INSTALL")
        for n, pid in enumerate(pk):
            if n:
                yield L("")
            if pid not in WINGET:
                yield self.lg("FAIL", [("Unknown package: " + pid, "out")])
                fail += 1
                continue
            if pid in self.winget:
                yield self.lg("WARN", [("%s already installed (%s) - skipped" % (pid, self.winget[pid]), "out")])
                skip += 1
                continue
            yield self.lg("INFO", [("Installing %s ..." % pid, "out")])
            t0 = self.clock
            rc = yield from self.winget_do(pid, "install")
            dt = (self.clock - t0).total_seconds()
            if rc == 0:
                yield self.lg("OK", [("✔ ", "ok"), ("%s %s installed" % (pid, self.winget[pid]), "ok"), ("  (%.1f s)" % dt, "dim")])
                ok += 1
            else:
                yield self.lg("FAIL", [("%s failed (code %d)" % (pid, rc), "out")])
                fail += 1
        yield L("")
        yield self.count("INSTALL", ok, fail, ", %d skipped" % skip, "succeeded")
        return (yield from self.finish("INSTALL", 1 if fail else 0, log=True))

    def stage_update(self, pk):
        yield self.rule("2. UPDATE")
        yield self.lg("INFO", [("Checking for updates ...", "out")])
        yield self.w(random.uniform(2, 3.5))
        before = {p: self.winget[p] for p in pk if p in self.winget}
        todo, fresh = [], 0
        for p in pk:
            if p not in self.winget:
                yield self.lg("WARN", [("%s not installed - skipped" % p, "out")])
            elif self.winget[p] != WINGET[p]["new"]:
                yield self.lg("INFO", [("%-16s %-7s →  %-6s " % (p, self.winget[p], WINGET[p]["new"]), "out"), ("  update available", "ok")])
                todo.append(p)
            else:
                yield self.lg("INFO", [("%-16s %-9s " % (p, self.winget[p]), "out"), ("  up-to-date", "dim")])
                fresh += 1
            yield self.w(0.3)
        ok = fail = 0
        for p in todo:
            yield self.lg("INFO", [("Upgrading %s ..." % p, "out")])
            t0 = self.clock
            rc = yield from self.winget_do(p, "upgrade")
            dt = (self.clock - t0).total_seconds()
            if rc == 0:
                yield self.lg("OK", [("✔ ", "ok"), ("%s updated to %s" % (p, self.winget[p]), "ok"), ("  (%.1f s)" % dt, "dim")])
                ok += 1
            else:
                yield self.lg("FAIL", [("%s failed (code %d)" % (p, rc), "out")])
                fail += 1
        yield L("")
        yield self.count("UPDATE", ok, fail, ", %d up-to-date" % fresh, "updated")
        if before:
            yield L("")
            yield self.rule("VERSIONS")
            rows = [[(p, "out"), (v, "out"), (self.winget[p], "ok")] for p, v in before.items()]
            yield from self.table(["Package", "Before", "Now"], rows, [16, 7, 5])
        return (yield from self.finish("UPDATE", 1 if fail else 0))

    def stage_uninstall(self, pk):
        ok = fail = 0
        yield self.rule("3. UNINSTALL")
        for pid in pk:
            yield self.lg("WARN", [("Uninstall requested for: " + pid, "out")])
            yield self.lg("INFO", [("Uninstalling %s ..." % pid, "out")])
            t0 = self.clock
            rc = yield from self.winget_do(pid, "uninstall")
            dt = (self.clock - t0).total_seconds()
            if rc == 0:
                yield self.lg("OK", [("✔ ", "ok"), ("%s removed" % pid, "ok"), ("  (%.1f s)" % dt, "dim")])
                ok += 1
            else:
                yield self.lg("FAIL", [("%s not installed or unknown (code %d)" % (pid, rc), "out")])
                fail += 1
        yield L("")
        yield S(("UNINSTALL summary: ", "out"), ("%d removed" % ok, "ok" if ok else "out"), (", %d failed" % fail, "err" if fail else "out"))
        rc = 1 if fail else 0
        self.stages["UNINSTALL"] = ("SUCCESS" if not rc else "FAILED", rc)
        yield L("")
        yield self.rule("EXIT CODES")
        rows = []
        for s in ("INSTALL", "UPDATE", "UNINSTALL"):
            st, c = self.stages.get(s, ("NOT RUN", None))
            sty = "ok" if st == "SUCCESS" else ("err" if st == "FAILED" else "dim")
            rows.append([(s, "out"), (st, sty), ("-" if c is None else str(c), sty)])
        yield from self.table(["Stage", "Status", "Exit"], rows, [9, 7, 4])
        yield L("")
        yield S(("RESULT: ", "out"), ("✔ SUCCESS" if not rc else "✖ FAILED", "ok" if not rc else "err"), ("  (exit code ", "out"), (str(rc), "ok" if not rc else "err"), (")", "out"))
        return rc

    # ---------- deploy_linux.sh ----------
    def deploy_sh(self):
        res = []

        def mark(name, start=True, rc=0):
            if start:
                return L("[%s] === %s: START ===" % (self.lts(), name))
            return L("[%s] === %s: OK (exit %d) ===" % (self.lts(), name, rc), "ok")

        def verify(p, expect_installed=True):
            if p in self.apt:
                return L("[%s] VERIFY %s: installed (%s)" % (self.lts(), p, self.apt[p]), "ok")
            return L("[%s] VERIFY %s: not installed (expected)" % (self.lts(), p), "ok")

        yield mark("0.INDEX")
        yield from self.apt_update()
        yield mark("0.INDEX", False)
        yield mark("1.INSTALL")
        yield from self.apt_install(["firefox", "p7zip-full"], True, quiet=True)
        yield mark("1.INSTALL", False)
        yield verify("firefox")
        yield verify("p7zip-full")
        yield mark("2.UPDATE")
        yield from self.apt_update()
        yield self.w(0.5)
        yield mark("2.UPDATE", False)
        yield mark("3.UNINSTALL")
        yield from self.apt_remove(["p7zip-full"], True, quiet=True)
        yield mark("3.UNINSTALL", False)
        yield verify("p7zip-full")
        yield L("")
        yield L("[%s] === SUMMARY ===" % self.lts())
        for s in ("0.INDEX", "1.INSTALL", "1.VERIFY", "2.UPDATE", "3.UNINSTALL", "3.VERIFY"):
            yield L("  %-12s exit=0 OK" % s, "ok")
        yield L("[%s] RESULT: SUCCESS" % self.lts(), "ok")
        yield L("exit code: 0", "ok")
        return 0

    # ---------- сохранение ----------
    def to_dict(self):
        d = {k: getattr(self, k) for k in (
            "kind", "speed", "last_exit", "history", "winget", "apt", "choco", "pip", "stages",
            "vars", "fs", "cwd", "env", "services", "processes", "network_latency_ms",
            "network_loss_pct", "location_stack", "aliases", "git_repos",
        )}
        d["clock"] = self.clock.isoformat()
        return d

    @classmethod
    def from_dict(cls, d):
        s = cls(d["kind"])
        for k, v in d.items():
            if k == "clock":
                s.clock = datetime.fromisoformat(v)
            elif k == "stages":
                s.stages = {a: tuple(b) for a, b in v.items()}
            elif k == "processes":
                s.processes = {int(pid): process for pid, process in v.items()}
            else:
                setattr(s, k, v)
        return s
