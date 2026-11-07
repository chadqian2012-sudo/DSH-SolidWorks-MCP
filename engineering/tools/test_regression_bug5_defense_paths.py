# -*- coding: utf-8 -*-
"""【Bug⑤ 回归测试】defense_gate 的 reports 目录必须是【候选集合】解析。

覆盖：
  1) reports_dir_candidates() 顺序 = STATE_DIR ▸ _BASE_DIR ▸ 安装副本；
  2) 凭据只存在于【任一候选】即视为存在（不能只看第一个）；
  3) 全部候选都没有 → 仍然 missing（fail-closed，绝不放水）；
  4) host_judge_room 响应回传宿主实际读的 reports_dir；
  5) 宿主判 missing 而本地候选有凭据 → 转本地验签；但：
     · 宿主还报了别的实质阻断（如结论 FAIL）→ 不转；
     · 本地候选也没有凭据 → 不转（维持 fail-closed）。

纯 stdlib，可直接 python 运行；退出码 0 = 全部通过。
"""
import sys
import os
import io
import json
import shutil
import tempfile
import importlib.util as ilu
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest.mock import patch

sys.stdout.reconfigure(encoding="utf-8")

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (("  -> " + str(detail)) if detail else ""))


def load_module(alias, filename):
    spec = ilu.spec_from_file_location(alias, os.path.join(TOOLS, filename))
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dg = load_module("dg_bug5", "defense_gate.py")

TMP = tempfile.mkdtemp(prefix="bug5-")
STATE = os.path.join(TMP, "state_tools")
BASE = os.path.join(TMP, "workspace_tools")
INST = os.path.join(TMP, "installed_tools")
for d in (STATE, BASE, INST):
    os.makedirs(d, exist_ok=True)

ROOM = "回归房5"
PHYS = ROOM + ".physics.json"
DOM = ROOM + ".domain.json"


def seed(report_dir, name=PHYS, payload=None):
    os.makedirs(report_dir, exist_ok=True)
    p = os.path.join(report_dir, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload or {"room": ROOM, "kind": "physics",
                              "overall": "PASS", "run_id": "r1"}, f,
                  ensure_ascii=False)
    return p


# ── 把解析用的三个根固定到临时目录（不碰真实现场）──────────────────────
dg.STATE_DIR = STATE
dg._BASE_DIR = BASE
dg._installed_tools_dir_candidates = lambda: [INST]
dg._current_task_id = lambda: "TASK-BUG5"

print("=== Bug⑤-1: 候选集合的顺序与来源 ===")
cands = dg.reports_dir_candidates("TASK-BUG5")
check("至少 3 个候选（状态目录/工作区/安装副本）", len(cands) >= 3, cands)
check("首选 = STATE_DIR 派生（写入侧同源）",
      os.path.normcase(cands[0]).startswith(os.path.normcase(os.path.join(STATE, "reports"))),
      cands[0])
check("包含工作区 _BASE_DIR 派生",
      any(os.path.normcase(c).startswith(os.path.normcase(os.path.join(BASE, "reports")))
          for c in cands), cands)
check("包含安装副本派生",
      any(os.path.normcase(c).startswith(os.path.normcase(os.path.join(INST, "reports")))
          for c in cands), cands)
check("无重复候选",
      len({os.path.normcase(c) for c in cands}) == len(cands), cands)

print("\n=== Bug⑤-2: 凭据只在【第 2/第 3 候选】里也要能命中 ===")
p2 = seed(cands[1])
found, tried = dg.resolve_room_credential(ROOM, ".physics.json", "TASK-BUG5")
check("命中候选②里的凭据（不再只看第一个）", found is not None, found)
check("返回路径 = 候选②", found and os.path.normcase(found) == os.path.normcase(p2), found)
check("同时回报全部尝试过的路径", len(tried) == len(cands), len(tried))

os.remove(p2)
p3 = seed(cands[2], DOM)
found_d, _ = dg.resolve_room_credential(ROOM, ".domain.json", "TASK-BUG5")
check("命中候选③里的凭据", found_d is not None, found_d)
os.remove(p3)

print("\n=== Bug⑤-3: 全部候选都没有 → 仍判 missing（fail-closed）===")
found_none, tried_none = dg.resolve_room_credential(ROOM, ".physics.json", "TASK-BUG5")
check("resolve 返回 None", found_none is None, found_none)
check("尝试过全部候选", len(tried_none) == len(cands), tried_none)
res_ph = dg.check_physics_attestation(ROOM)
check("物理防线判不通过", res_ph["ok"] is False)
check("原因为缺少凭据", any("缺少物理校核凭据" in r for r in res_ph["reasons"]),
      res_ph["reasons"])
check("如实回报搜索过的目录（排障）", bool(res_ph.get("searched_dirs")),
      res_ph.get("searched_dirs"))

print("\n=== Bug⑤-4: 宿主判定回传 reports_dir ===")
HOST_TOOLS = os.path.join(TMP, "host_tools")
os.makedirs(os.path.join(HOST_TOOLS, ".defense"), exist_ok=True)
RT = os.path.join(HOST_TOOLS, ".defense", "runtime.json")
saved = {
    "_host_base": dg._host_base,
    "_post_json": dg._post_json,
    "_find_runtime_file": dg._find_runtime_file,
}
dg._host_base = lambda require_owner=True: "http://127.0.0.1:0"
try:
    with open(RT, "w", encoding="utf-8") as f:
        json.dump({"port": 1, "pid": 1, "kid": "k"}, f)
    dg._find_runtime_file = lambda: RT
    dg._post_json = lambda url, payload, timeout=5.0: {
        "ok": False, "room": ROOM, "blockers": [],
        "warnings": [], "details": {}, "judged_by": "host",
    }
    hj = dg.host_judge_room(ROOM, require_physics=True)
    check("host_judge_room 有响应", isinstance(hj, dict))
    check("回传 host_reports_dir", bool(hj.get("host_reports_dir")), hj.get("host_reports_dir"))
    check("host_reports_dir = 宿主 toolsDir/reports",
          os.path.normcase(str(hj.get("host_reports_dir")))
          == os.path.normcase(os.path.join(HOST_TOOLS, "reports")),
          hj.get("host_reports_dir"))
    _chp = hj.get("credential_host_path")
    check("回传宿主会去读的凭据路径",
          isinstance(_chp, dict) and _chp.get("physics", "").endswith(PHYS), _chp)
    check("回传本地候选集合", isinstance(hj.get("reports_dir_local_candidates"), list),
          len(hj.get("reports_dir_local_candidates") or []))
finally:
    dg._host_base = saved["_host_base"]
    dg._post_json = saved["_post_json"]
    dg._find_runtime_file = saved["_find_runtime_file"]

print("\n=== Bug⑤-5: 宿主 missing + 本地有凭据 → 转本地验签 ===")
host_missing = {
    "ok": False,
    "blockers": ["【防线②物理】未做物理校核：凭据文件不存在（凭据文件不存在（reports/x））"
                 " —— 请先跑对应的 physics 校核命令生成凭据"],
    "warnings": [],
    "details": {"physics": {"verified": False, "failure": "missing",
                            "reason": "凭据文件不存在（reports/x）"}},
    "host_reports_dir": os.path.join(HOST_TOOLS, "reports"),
}
conv_absent = dg._host_missing_but_local_has_credentials(ROOM, host_missing)
check("本地也没有凭据 → 不转（fail-closed）", conv_absent["convert"] is False,
      conv_absent["note"])

p1 = seed(cands[0])
conv_ok = dg._host_missing_but_local_has_credentials(ROOM, host_missing)
check("本地候选有凭据 → 转本地验签", conv_ok["convert"] is True, conv_ok["note"])
check("转换 kinds 含 physics", conv_ok["kinds"] == ["physics"], conv_ok["kinds"])
check("留痕本地命中的凭据路径",
      os.path.normcase(str(list(conv_ok["local_paths"].values())[0]))
      == os.path.normcase(p1), conv_ok["local_paths"])

host_other_blocker = dict(host_missing)
host_other_blocker["blockers"] = list(host_missing["blockers"]) + [
    "【防线②物理】凭据有效（已验签），但物理校核结论为 FAIL"]
conv_other = dg._host_missing_but_local_has_credentials(ROOM, host_other_blocker)
check("宿主另有实质阻断（FAIL）→ 不转，保留宿主判定",
      conv_other["convert"] is False, conv_other["note"])

host_ok = {"ok": True, "blockers": [], "warnings": [], "details": {},
           "host_reports_dir": os.path.join(HOST_TOOLS, "reports")}
conv_pass = dg._host_missing_but_local_has_credentials(ROOM, host_ok)
check("宿主已通过 → 不进入转换逻辑", conv_pass["convert"] is False, conv_pass["note"])

print("\n=== Bug⑤-6: check_room_defense 端到端（宿主 missing → 本地判定）===")
saved2 = {"_port_owner_check": dg._port_owner_check,
          "_read_runtime": dg._read_runtime,
          "host_judge_room": dg.host_judge_room,
          "credential_is_signed_and_valid": dg.credential_is_signed_and_valid}
dg._read_runtime = lambda: {"port": 1, "pid": 1}
dg._port_owner_check = lambda pid, port=None: {"ok": True, "reason": "test"}
dg.host_judge_room = lambda room, require_physics=True: dict(host_missing)
dg.credential_is_signed_and_valid = lambda cred: {"ok": True}
try:
    # 本用例检查物理凭据丢失后的宿主/本地判定，明确要求物理校核。
    r = dg.check_room_defense(ROOM, require_physics=True)
    check("转为本地判定", r.get("judged_by") == "local", r.get("judged_by"))
    check("降级原因码 = HOST_MISSING_LOCAL_CREDENTIAL",
          r.get("degraded_reason_code") == "HOST_MISSING_LOCAL_CREDENTIAL",
          r.get("degraded_reason_code"))
    hj_detail = (r.get("details") or {}).get("host_judge") or {}
    check("details.host_judge 留痕宿主读的目录",
          bool(hj_detail.get("host_reports_dir")), hj_detail)
    check("details.host_judge 留痕本地命中路径",
          bool(hj_detail.get("local_credential_paths")), hj_detail)
    check("宿主缺失的 kinds 被记录", hj_detail.get("converted_kinds") == ["physics"],
          hj_detail.get("converted_kinds"))
finally:
    dg._port_owner_check = saved2["_port_owner_check"]
    dg._read_runtime = saved2["_read_runtime"]
    dg.host_judge_room = saved2["host_judge_room"]
    dg.credential_is_signed_and_valid = saved2["credential_is_signed_and_valid"]

print("\n=== Bug⑤-7: 本房完全没有凭据时，check_room_defense 仍拒绝 ===")
os.remove(p1)
saved3 = {"_port_owner_check": dg._port_owner_check, "_read_runtime": dg._read_runtime,
          "host_judge_room": dg.host_judge_room}
dg._read_runtime = lambda: {"port": 1, "pid": 1}
dg._port_owner_check = lambda pid, port=None: {"ok": True, "reason": "test"}
dg.host_judge_room = lambda room, require_physics=True: dict(host_missing)
try:
    r2 = dg.check_room_defense(ROOM, require_physics=True)
    check("不转换（本地也没凭据）", r2.get("judged_by") == "host", r2.get("judged_by"))
    check("仍然拒绝（ok=False，fail-closed）", r2.get("ok") is False)
    check("仍带回宿主 reports_dir", bool(r2.get("host_reports_dir")),
          r2.get("host_reports_dir"))
    check("带 host_judge_note 说明为何没转", bool(r2.get("host_judge_note")),
          r2.get("host_judge_note"))
finally:
    dg._port_owner_check = saved3["_port_owner_check"]
    dg._read_runtime = saved3["_read_runtime"]
    dg.host_judge_room = saved3["host_judge_room"]

print("\n=== 宿主签发调用按实际宿主目录认证，令牌不随跳转或代理发送 ===")
received = []
expected = {"token": None}


class AuthHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        token = self.headers.get("x-dsh-engineering-token")
        received.append((self.path, token))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/redirect-target")
            self.end_headers()
            return
        ok = self.path != "/dsh-engineering-ui/defense/sign" or token == expected["token"]
        self.send_response(200 if ok else 403)
        self.end_headers()
        self.wfile.write(json.dumps({"ok": ok}).encode("utf-8"))

    do_GET = do_POST

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), AuthHandler)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
token_file = os.path.join(HOST_TOOLS, ".defense", "local.token")
with open(RT, "w", encoding="utf-8") as f:
    json.dump({"port": server.server_port, "pid": os.getpid(), "host": "example.invalid"}, f)
try:
    with patch.object(dg, "_find_runtime_file", return_value=RT), \
         patch.object(dg, "_port_owner_check", return_value={"ok": True}), \
         patch.object(dg, "_publish_workspace_root"), \
         patch.dict(os.environ, {"DSH_ENGINEERING_TOKEN": "", "HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
        base = dg._host_base()
        check("宿主地址固定为本机，不信 runtime 中的外部域名", base == "http://127.0.0.1:%d" % server.server_port)
        check("未配置令牌时仍能签发，且不经过环境代理", dg.request_signed_credential({})["ok"])
        os.makedirs(os.path.join(STATE, ".defense"), exist_ok=True)
        with open(os.path.join(STATE, ".defense", "local.token"), "w") as f:
            f.write("wrong-workspace-token")
        expected["token"] = "fixture-file-token"
        for encoding in ("utf-8", "utf-8-sig"):
            with open(token_file, "w", encoding=encoding) as f:
                f.write("fixture-file-token\n")
            check("签发读取宿主实际目录的文件令牌（%s）" % encoding, dg.request_signed_credential({})["ok"])
        with patch.dict(os.environ, {"DSH_ENGINEERING_TOKEN": "fixture-env-token"}):
            expected["token"] = "fixture-env-token"
            check("环境令牌优先于宿主文件令牌", dg.request_signed_credential({})["ok"])
        for contents in ("", " \n\t"):
            with open(token_file, "w") as f:
                f.write(contents)
            count = len(received)
            check("空白令牌文件在发请求前拒绝", not dg.request_signed_credential({})["ok"] and len(received) == count)
        check("空文件不影响普通验签请求", dg._post_json(base + "/dsh-engineering-ui/defense/verify-gate", {})["ok"])
        os.remove(token_file)
        os.mkdir(token_file)
        count = len(received)
        check("不可读令牌路径在发请求前拒绝", not dg.request_signed_credential({})["ok"] and len(received) == count)
        os.rmdir(token_file)
        expected["token"] = None
        check("删除文件令牌后恢复原有调用", dg.request_signed_credential({})["ok"])
        check("跳转目标不会收到令牌", dg._post_json(base + "/redirect", {}, token="fixture-redirect-token")["ok"] and received[-1] == ("/redirect-target", None))
finally:
    server.shutdown()
    server.server_close()
    thread.join()

if os.name == "nt":
    print("\n=== 宿主归属只认本次实际连接，仍拒绝错误进程 ===")
    with patch("socket.create_connection") as connect, patch("subprocess.run") as run:
        probe = connect.return_value.__enter__.return_value
        probe.getsockname.return_value = ("127.0.0.1", 45678)
        probe.getpeername.return_value = ("127.0.0.1", 19387)
        run.side_effect = [SimpleNamespace(stdout=value) for value in
                           ("321\n", "DeepSeek Harness\n", "D:\\Harness\\DeepSeek Harness.exe\n")]
        check("真实连接所属 PID、进程名和路径一致时通过", dg._port_owner_check(321, 19387)["ok"])
        command = run.call_args_list[0].args[0][-1]
        check("查服务端与本探针确切地址和端口，不要求 Listen", all(value in command for value in
              ("-LocalAddress '127.0.0.1'", "-LocalPort 19387", "-RemoteAddress '127.0.0.1'",
               "-RemotePort 45678", "-State Established")))
        check("查完归属立即关闭探针连接", connect.return_value.__exit__.called)
        run.side_effect = [SimpleNamespace(stdout="999\n")]
        check("相同端口但服务端 PID 不符仍拒绝", not dg._port_owner_check(321, 19387)["ok"])
        run.side_effect = [SimpleNamespace(stdout="")]
        check("无法取得该连接所属进程时仍拒绝", not dg._port_owner_check(321, 19387)["ok"])

shutil.rmtree(TMP, ignore_errors=True)
print("\nPASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
