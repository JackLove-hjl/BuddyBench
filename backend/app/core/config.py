"""应用配置:环境变量解析(pydantic-settings)。

本地开发:在 backend/ 下放 .env(uuid 从 .env.example 拷贝)。
Docker:compose 的 env_file 注入同名环境变量,优先级相同。
"""
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 数据库(SQLAlchemy asyncpg 驱动;本地开发用 localhost,容器内用 db)
    database_url: str = "postgresql+asyncpg://llm:llm@localhost:5432/llm"
    # LangGraph checkpointer(psycopg3,无驱动前缀;与 database_url 同库)
    checkpoint_db_dsn: str = "postgresql://llm:llm@localhost:5432/llm"

    # 绘图工具
    image_dir: str = "static/images"  # 相对 CWD(backend/)
    code_exec_timeout: int = 60  # 代码执行超时(秒)

    # 静态产物保留策略(图片 / spill / 上传 都是"用一次留一份"):超过天数或总量上限就清理。
    # 见 app/services/retention.py;0 表示该条规则关闭。
    asset_retention_days: int = 7
    asset_max_total_mb: int = 256

    # 公开访问地址(视觉模型回拉图片用)。留空时取请求 Host(base_url)。
    # 容器/反向代理部署时建议显式配置,如 http://example.com
    public_base_url: str = ""

    # 联网搜索(Tavily)。留空时联网工具仍会装配,但调用 web_search 返回"未配置"提示
    tavily_api_key: str = ""

    # 内置供应商(.env 配置,模型分组固定显示为「内置模型」)
    # OPENAI_API_BASE 为任意 OpenAI 兼容端点;DEFAULT_MODEL 为该分组下的模型,并作为默认选中项
    openai_api_key: str = ""
    openai_api_base: str = ""
    default_model: str = ""
    # 内置模型的**显示清单**:`模型名[:输入上下文[:输出上下文]]`,逗号分隔。
    # 只有列在这里的模型才会出现在模型下拉的「内置模型」分组里(不再从 /models 全量拉取)。
    # 上下文数值来自各模型官方规格,用于上下文使用率圆圈与自动压缩阈值。
    builtin_models: str = (
        "deepseek-v4.1-flash:1000000:384000,"
        "glm-5.3-flash:1000000:128000,"
        "qwen3.8-flash:1000000:131072"
    )

    # 模型上下文窗口(默认值,用于前端上下文使用率圆圈展示;可按模型在 provider 扩展)
    context_window_default: int = 256000

    # 上下文回放:checkpointer 无历史时从 DB 回放的最近消息数
    history_replay_limit: int = 50

    # ===== computer use(模型操作电脑:截屏 + 鼠标键盘) =====
    # 仅「全部权限」档位装配该工具;每轮首次调用弹一次审批卡(见 app/agent/middleware.py)
    computer_use_enabled: bool = True
    computer_use_max_actions: int = 40  # 单轮动作上限(含每次截图),防止屏幕操作死循环
    computer_use_action_interval: float = 0.4  # 动作最小间隔(秒),也为人工急停留时间
    computer_use_max_image_width: int = 1280  # 截图缩放宽度 = 模型看到的坐标空间
    computer_use_image_format: str = "jpeg"  # jpeg(体积小) / png
    computer_use_jpeg_quality: int = 80

    # 工具循环守卫:最近 W 次工具调用里,同名同参、且拿到的结果也相同的调用达到 K 次,
    # 就跳过本次执行并提示模型换策略(否则它可能原地打转到烧完 recursion_limit)
    tool_loop_guard_window: int = 12
    tool_loop_guard_repeats: int = 3

    # Agent 单轮最大步数(LangGraph 的 recursion_limit)。
    # 必须显式设置:LangGraph 出厂默认只有 25,而这张图每轮「模型 → 工具」要消耗多个
    # superstep(中间件钩子各自是节点),大约 6 次工具调用就会打满 —— 模型一旦陷入
    # 重复读文件之类的循环,用户看到的就是一句英文的 "Recursion limit of 25 reached"。
    # 实测每个工具动作 ≈ 5 个 superstep(ComputerUseMiddleware.before_model →
    # PatchDangling.before_model → model → TodoList.after_model → tools),
    # 因此本值必须显著大于 computer_use_max_actions(40 → 约 200 步,这里留到 300)。
    # 真正的第一道闸是工具循环守卫与 computer 的动作上限,这里只是安全网。
    agent_recursion_limit: int = 300

    # 自动上下文压缩:占用超过 context_window * trigger_fraction 时压缩,压缩后保留最近几条
    compact_trigger_fraction: float = 0.7
    compact_keep_messages: int = 4

    # 开发期放开;上生产前收紧
    cors_origins: list[str] = ["*"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
