"""Copyright (c) 2026 VoidEclipseAI. Licensed under AGPL-3.0-only (see LICENSE).

ReFrameVideo 一键启动：起后端（同时伺服 web/ 前端），打开浏览器。

    python start.py                         # 用 app_settings.json 里存的 comfy_url；第一次没存过就在窗口里问
    python start.py --comfy http://IP:8188  # 指定 ComfyUI 地址（会存进 app_settings.json，下次不用再写）
    python start.py --comfy mock            # 假引擎（只能看界面），也会记住
    python start.py --port 8765 --projects D:/my_projects

已经有 ReFrameVideo 在这个端口上跑：直接打开浏览器。关掉这个窗口 / Ctrl+C = 停掉这里起的后端。
提示中英双语：用户可能不懂中文（Windows 用 start.bat 启动）。
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / "app_settings.json"
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机 / 局域网地址不走系统代理
DEFAULT_COMFY = "http://127.0.0.1:8188"


def alive(port):
    try:
        with NO_PROXY.open(f"http://127.0.0.1:{port}/api/info", timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def comfy_ok(url):
    try:
        with NO_PROXY.open(f"{url}/system_stats", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def read_settings():
    return json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}


def save_comfy(url):
    """"" = 假引擎（也记住，下次不再问）。"""
    s = read_settings()
    s["comfy_url"] = url
    SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def norm(url):
    url = url.strip().rstrip("/")
    if url.lower() == "mock":
        return ""
    return url if "://" in url else "http://" + url


def ask_comfy():
    """第一次启动、没存过地址时在窗口里问。返回地址；"" = 假引擎。"""
    print()
    print("ComfyUI address / ComfyUI 地址")
    print(f"  Press Enter for {DEFAULT_COMFY}, or type the address of the computer running ComfyUI.")
    print(f"  直接回车 = {DEFAULT_COMFY}；ComfyUI 在别的电脑上就输入那台电脑的地址，例如 http://192.168.1.10:8188")
    print("  Type mock to run the interface only (no detection or generation).")
    print("  输入 mock = 先只看界面（不能识别和生成）")
    while True:
        try:
            url = norm(input("> ") or DEFAULT_COMFY)
        except EOFError:
            return ""
        if not url:
            return ""
        if comfy_ok(url):
            print(f"Connected to ComfyUI / 已连上 ComfyUI：{url}")
            return url
        print(f"Cannot reach ComfyUI at {url}. Is ComfyUI running?")
        print(f"连不上 {url}：ComfyUI 开着吗？")
        print("  Press Enter to type the address again, or y to use it anyway.")
        print("  回车 = 重新输入地址；y = 仍然用这个地址")
        try:
            if input("> ").strip().lower() == "y":
                return url
        except EOFError:
            return url


def main():
    ap = argparse.ArgumentParser(description="Start ReFrameVideo (backend + web UI on one port) / 启动 ReFrameVideo")
    ap.add_argument("--comfy", help="ComfyUI address, e.g. http://192.168.1.10:8188, or mock (remembered) / ComfyUI 地址（会记住）")
    ap.add_argument("--port", type=int, default=8765, help="web port (default 8765) / 端口（默认 8765）")
    ap.add_argument("--projects", help="project folder (default ./projects) / 项目文件夹")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser / 不自动打开浏览器")
    a = ap.parse_args()
    if a.comfy:
        save_comfy(norm(a.comfy))
    url = f"http://127.0.0.1:{a.port}"
    info = alive(a.port)
    proc = None
    if info:
        print(f"ReFrameVideo is already running at {url} (engine {info.get('engine')}, API {info.get('api')})")
        print(f"ReFrameVideo 已经在 {url} 运行")
        if a.comfy:
            print("To use another ComfyUI address, close the running one first. / 要换 ComfyUI 地址，先关掉正在运行的那个。")
    else:
        if not a.comfy and "comfy_url" not in read_settings():
            if sys.stdin and sys.stdin.isatty():
                save_comfy(ask_comfy())
            else:
                print("No ComfyUI address set: starting the mock engine. / 没有设置 ComfyUI 地址：用假引擎启动。")
        comfy = read_settings().get("comfy_url") or ""
        cmd = [sys.executable, str(ROOT / "app" / "server.py"), "--port", str(a.port)]
        if comfy:
            cmd += ["--comfy", comfy]
        else:                                                # 假引擎：只能看界面，不能识别 / 生成
            print("Mock engine: you can try the interface, but not detect or generate. / 假引擎：只能看界面。")
            print("  Set ComfyUI later / 以后设 ComfyUI：python start.py --comfy http://IP:8188  (Windows: start.bat --comfy http://IP:8188)")
        if a.projects:
            cmd += ["--projects", a.projects]
        print("Starting backend / 启动后端：", " ".join(cmd))
        proc = subprocess.Popen(cmd, cwd=str(ROOT))
        for _ in range(120):                                 # 最多等 60 秒
            if proc.poll() is not None:
                sys.exit(f"Backend failed to start (exit code {proc.returncode}), see the error above. / 后端启动失败，看上面的报错")
            if alive(a.port):
                break
            time.sleep(0.5)
        else:
            proc.terminate()
            sys.exit("Backend did not start within 60 seconds. / 后端 60 秒内没起来")
        print(f"ReFrameVideo started / 已启动：{url}")
        print("Keep this window open while you work. / 使用期间不要关这个窗口。")
    if not a.no_browser:
        webbrowser.open(url)
    if proc:
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()


if __name__ == "__main__":
    main()
