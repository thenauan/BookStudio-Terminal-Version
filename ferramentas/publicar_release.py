"""Cria um release no GitHub e envia os arquivos.
Uso: python ferramentas/publicar_release.py <repo> <tag> <nome> <notas.md> <arquivo>...
O token vem do Git Credential Manager (o mesmo login do git push) e nunca é impresso."""
import json
import mimetypes
import os
import subprocess
import sys
import urllib.error
import urllib.request

repo, tag, nome, notas, *arquivos = sys.argv[1:]
GIT = r"C:\Program Files\Git\cmd\git.exe" if os.name == "nt" else "git"
env = dict(os.environ, GCM_INTERACTIVE="never", GIT_TERMINAL_PROMPT="0")
r = subprocess.run([GIT, "-c", "credential.helper=manager", "credential", "fill"], input=b"protocol=https\nhost=github.com\n\n",
                   capture_output=True, env=env)
token = next((l.split("=", 1)[1] for l in r.stdout.decode().splitlines() if l.startswith("password=")), None)
if not token:
    sys.exit("sem credencial do GitHub")
cab = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "release-script"}


def pedir(url, dados=None, metodo="GET", tipo="application/json"):
    req = urllib.request.Request(url, data=dados, method=metodo, headers=dict(cab, **({"Content-Type": tipo} if dados else {})))
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"{metodo} {url.split('?')[0]} -> {e.code}: {e.read()[:300]!r}")


corpo = open(notas, encoding="utf-8").read()
rel = pedir(f"https://api.github.com/repos/{repo}/releases",
            json.dumps({"tag_name": tag, "name": nome, "body": corpo, "draft": False, "prerelease": False}).encode(), "POST")
print("release:", rel["html_url"])
for arq in arquivos:
    url = rel["upload_url"].split("{")[0] + "?name=" + urllib.request.quote(os.path.basename(arq))
    a = pedir(url, open(arq, "rb").read(), "POST", mimetypes.guess_type(arq)[0] or "application/octet-stream")
    print("enviado:", a["name"], a["size"], "bytes")
