"""后台任务：同一时间只跑一个（识别、出候选、背景图、生成都是）。界面轮询 JOB 看日志和进度，点「停止」走 engine.request_cancel。
有任务在跑时再提交的任务进队列（QUEUE），上一个跑完（完成 / 出错 / 停止）就按提交顺序接着跑下一个。
排着的任务轮到它时才读项目，所以按「开始跑时」的项目状态生成；请求里带的参数（seed、勾的参考图等）是提交时的。"""
import itertools
import threading
import time
import traceback

from engine import Cancelled, clear_cancel


class Busy(RuntimeError):
    pass


# name / project：任务名和所属项目（界面任务列表显示）；queue：排队的任务（不含函数）；last：刚跑完的那个任务的结果
JOB = dict(busy=False, log=[], error=None, done=0, progress=None, started=None, cancelled=False,
           name=None, project=None, queue=[], last=None)
LOCK = threading.Lock()
QUEUE = []                      # [{id, fn, name, project, queued}]
_ids = itertools.count(1)


def _publish_queue():
    JOB["queue"] = [{k: v for k, v in q.items() if k != "fn"} for q in QUEUE]


def start(fn, name=None, project=None):
    """没任务在跑就马上跑；有就排队，返回 queued = 排在第几个。"""
    with LOCK:
        if JOB["busy"]:
            qid = next(_ids)
            QUEUE.append(dict(id=qid, fn=fn, name=name, project=project, queued=time.time()))
            _publish_queue()
            return {"ok": True, "queued": len(QUEUE), "qid": qid}
        _begin(fn, name, project)
    return {"ok": True}


def remove(qid):
    """把还没开始的任务移出队列。"""
    with LOCK:
        n = len(QUEUE)
        QUEUE[:] = [q for q in QUEUE if q["id"] != qid]
        _publish_queue()
        return len(QUEUE) < n


def _begin(fn, name, project):
    """开始跑一个任务（调用方持有 LOCK）。"""
    clear_cancel()                                              # 上一轮点过停止的标记要清掉
    JOB.update(busy=True, log=[], error=None, progress=None, started=time.time(), open_project=None, open_result=None,
               open_seq=None, open_segment=None, export_file=None, cancelled=False, name=name, project=project)

    def log(msg):
        JOB["log"].append(msg)
        print(msg, flush=True)

    def runner():
        try:
            res = fn(log)
            if isinstance(res, dict) and res.get("open_project"):   # 扩图合成建了新项目：界面直接打开它
                JOB["open_project"] = res["open_project"]
            if isinstance(res, dict) and res.get("open_result"):    # 合成结果：界面切到这个「结果」标签
                JOB["open_result"] = res["open_result"]
            for k in ("open_seq", "open_segment", "export_file"):                  # 长片段：导入完打开分段页；剪好一段打开这一段的项目
                if isinstance(res, dict) and res.get(k):
                    JOB[k] = res[k]
            log("完成")
        except Cancelled:
            log("已停止（用户点了停止）")                            # 不是错误：正常收尾，界面只显示“已停止”
            JOB["cancelled"] = True
        except Exception as e:
            traceback.print_exc()
            JOB["error"] = f"{type(e).__name__}: {e}"
        finally:
            with LOCK:
                JOB["last"] = {k: JOB.get(k) for k in ("started", "name", "project", "error", "cancelled", "open_project", "open_result",
                                                     "open_seq", "open_segment", "export_file")}
                JOB["last"].update(log=list(JOB["log"]), ended=time.time())
                JOB["progress"] = None
                JOB["done"] += 1
                if QUEUE:                                           # 队列里还有：直接接着跑（busy 不落地，界面不会以为全跑完了）
                    q = QUEUE.pop(0)
                    _publish_queue()
                    _begin(q["fn"], q["name"], q["project"])
                else:
                    JOB["busy"] = False

    threading.Thread(target=runner, daemon=True).start()
