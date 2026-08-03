"""learn-from-video 启动入口

一条命令启动：python main.py
"""

import asyncio
import sys
from pathlib import Path

# 确保 src 可导入
sys.path.insert(0, str(Path(__file__).parent))


def main():
    from src.config import get_config, DATA_DIR, WORKDIR, LIBRARY_DIR, PROMPTS_DIR, WORKSPACE_DIR, CONVERSATIONS_DIR

    # 创建必要目录
    for d in [DATA_DIR, WORKDIR, LIBRARY_DIR, PROMPTS_DIR, WORKSPACE_DIR, CONVERSATIONS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    # 初始化数据库
    from src.database import init_db
    asyncio.run(init_db())

    # 启动 Web 服务
    config = get_config()
    import uvicorn
    from src.app import create_app

    app = create_app()
    print(f"🚀 Learn From Video v0.3.0")
    print(f"   http://{config.host}:{config.port}")
    print(f"   提交页: /  知识库: /library  Agent: /chat  设置: /settings")
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
