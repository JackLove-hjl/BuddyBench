"""本地开发入口(Windows 专用说明)。

psycopg3 异步要求 SelectorEventLoop,uvicorn 0.52 在 Windows 默认用
ProactorEventLoop,故通过 loop 参数注入 app.winloop 工厂。
Linux/容器内直接 uvicorn app.main:app 即可。

用法:
  python run.py           (默认 reload;RELOAD=0 关闭)
  uv run llm-backend      (uv 管理的等价入口)
"""
import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        loop="app.winloop:selector_loop_factory",
        reload=os.environ.get("RELOAD", "1") == "1",
    )


if __name__ == "__main__":
    main()
