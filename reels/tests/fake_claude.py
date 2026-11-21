"""Falešný `claude` pro testy generátoru: chování podle FAKE_CLAUDE_MODE (ok, sleep, fail, write:<složka>)."""
import os
import shutil
import sys
import time
from pathlib import Path

mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
prompt = sys.stdin.buffer.read().decode("utf-8")
Path("fake_args.txt").write_text("\n".join(sys.argv[1:]) + "\n---\n" + prompt, encoding="utf-8")
if mode == "sleep":
    time.sleep(60)
elif mode == "fail":
    print("chyba modelu", file=sys.stderr)
    sys.exit(3)
elif mode.startswith("write:"):
    shutil.copytree(mode[len("write:"):], ".", dirs_exist_ok=True)
print('{"result": "ok"}')
