"""端到端验收脚本(本地服务):流式/多轮记忆/模型切换/画图/删除。"""
import json
import re
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def req(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body else None
    r = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    with urllib.request.urlopen(r) as resp:
        return resp.status, resp.read().decode()


def chat(conversation_id, model, message, timeout=180):
    """POST /api/chat,返回 (事件列表, 完整输出)。"""
    body = json.dumps({"conversation_id": conversation_id, "model": model, "message": message},
                      ensure_ascii=False).encode()
    r = urllib.request.Request(BASE + "/api/chat", data=body, method="POST",
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read().decode()
    events = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        ev = re.search(r"event: (\w+)", block)
        data = re.search(r"data: (.+)", block, re.S)
        if ev and data:
            events.append((ev.group(1), json.loads(data.group(1))))
    return events, raw


print("== 1. 模型列表 ==")
status, body = req("GET", "/api/models")
models = json.loads(body)["models"]
check("GET /api/models 200", status == 200)
check("deepseek-chat 可用", any(m["id"] == "deepseek-chat" and m["available"] for m in models))
check("qwen 未配 key 置灰", any(m["id"] == "qwen-plus" and not m["available"] for m in models))

print("== 2. 会话 CRUD ==")
status, body = req("POST", "/api/conversations", {"title": "端到端验收"})
conv = json.loads(body)
cid = conv["id"]
check("创建会话 201", status == 201 and cid)

print("== 3. 流式对话(deepseek-chat)==")
events, raw = chat(cid, "deepseek-chat", "你好,请记住一个暗号:西瓜,不要说出来")
deltas = [e for n, e in events if n == "delta"]
done = [e for n, e in events if n == "done"]
check("有 delta 流", len(deltas) > 3, f"only {len(deltas)}")
check("以 done 结束", len(done) == 1, raw[-200:])
check("done 带 message_id/usage", bool(done) and done[0].get("message_id") and done[0].get("usage") is not None)
print(f"   (delta 帧数 {len(deltas)},usage={done[0]['usage'] if done else None})")

print("== 4. 多轮记忆(同会话追问,checkpointer 接续)==")
events, raw = chat(cid, "deepseek-chat", "刚才我告诉你的暗号是什么?请直接回答")
content = "".join(e["content"] for n, e in events if n == "delta")
check("记得暗号 西瓜", "西瓜" in content, f"回答:{content[:100]}")
print(f"   (回答:{content[:80]})")

print("== 5. 模型切换上下文连续(deepseek-chat → deepseek-reasoner)==")
events, raw = chat(cid, "deepseek-reasoner", "暗号是什么?只回答暗号本身", timeout=300)
content = "".join(e["content"] for n, e in events if n == "delta")
reasoning = [e for n, e in events if n == "reasoning"]
check("reasoning 事件(思考模型)", len(reasoning) > 0, "no reasoning frames")
check("切模型后仍记得暗号", "西瓜" in content, f"回答:{content[:100]}")
print(f"   (reasoning 帧数 {len(reasoning)},回答:{content[:60]})")

print("== 6. 画图端到端 ==")
events, raw = chat(cid, "deepseek-chat", "用 matplotlib 画 y=sin(x) 从 -3π 到 3π,保存图片并告诉我图片地址", timeout=300)
tool_starts = [e for n, e in events if n == "tool" and e.get("status") == "start"]
tool_ends = [e for n, e in events if n == "tool" and e.get("status") == "end"]
done = [e for n, e in events if n == "done"]
images = done[0].get("images", []) if done else []
content = "".join(e["content"] for n, e in events if n == "delta")
check("调用了 run_python_code", any(e["name"] == "run_python_code" for e in tool_starts), str([e["name"] for e in tool_starts]))
check("tool end 返回图片", any(e.get("result") and '"/images/' in e["result"] for e in tool_ends), str(tool_ends[-1:]))
check("done.images 非空", len(images) > 0, str(images))
check("回答引用图片链接", any("/images/" in c for c in [content]), content[-120:])
print(f"   (图片:{images},回答尾部:{content[-80:]!r})")

print("== 7. 图片可下载 ==")
if images:
    with urllib.request.urlopen(BASE + images[0], timeout=30) as resp:
        png = resp.read()
    check("GET 图片 200 image/png", resp.status == 200 and resp.headers.get("Content-Type") == "image/png")
    check("合法 PNG", png[:4] == b"\x89PNG", str(png[:4]))

print("== 8. 消息落库检查 ==")
status, body = req("GET", f"/api/conversations/{cid}/messages")
msgs = json.loads(body)["items"]
roles = [m["role"] for m in msgs]
alternating = all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)) and roles[0] == "user"
check("user/assistant 严格交替落库", alternating, str(roles))
last = msgs[-1]
check("assistant meta 含 images/tool_calls/usage",
      all(k in last["meta"] for k in ("images", "tool_calls", "usage")), str(last["meta"].keys()))
check("message_count 统计", any(m["message_count"] >= 8 for m in json.loads(req("GET", "/api/conversations")[1])["items"]))

print("== 9. 删除会话(级联 + checkpointer 清理)==")
status, body = req("DELETE", f"/api/conversations/{cid}")
check("DELETE 204", status == 204, str(status))
try:
    req("GET", f"/api/conversations/{cid}/messages")
    check("删除后查消息 404", False)
except urllib.error.HTTPError as e:
    check("删除后查消息 404", e.code == 404, str(e.code))

print(f"\n===== 结果: {PASS} 通过, {FAIL} 失败 =====")
