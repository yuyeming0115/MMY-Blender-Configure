"""真机只读冒烟：验证 CLI 迁移场景的目标探针（不写任何文件）。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "mmy_pack_config"))

from migration_core import run_target_probe

TARGET = r"C:\BlenderAPP\Blender5.1\blender.exe"


def main() -> int:
    probe = run_target_probe(Path(TARGET))
    exe_dir = Path(TARGET).parent
    print(f"[SMOKE] version      = {probe.get('version')}")
    print(f"[SMOKE] user_root    = {probe.get('user_root')}")
    checks = {
        "version 非空": bool(probe.get("version")),
        "user_root == exe同级 portable": Path(str(probe.get("user_root"))) == exe_dir / "portable",
    }
    ok = True
    for name, passed in checks.items():
        print(f"[SMOKE] {'PASS' if passed else 'FAIL'}  {name}")
        ok = ok and passed
    print(f"[SMOKE] RESULT: {'ALL PASS' if ok else 'FAILURE'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
