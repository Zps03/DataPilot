"""LLM 连通性测试：校验 get_model 的模型名校验，并对所有已配置模型各发一条测试消息。

运行：cd back && uv run python src/test_llm.py
前置：已复制 .env.example 为 .env 并填写 DASHSCOPE_API_KEY。
"""

import sys

from config import settings
from llm import get_model

# Windows 控制台默认 GBK 会乱码，统一强制 UTF-8 输出（PyCharm / Windows Terminal 均适用）
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def main() -> None:
    if not settings.dashscope_api_key:
        raise SystemExit("未配置 DASHSCOPE_API_KEY：请先执行 cp .env.example .env 并填写")

    # 1) 非法模型名应抛出 ValueError（错误信息包含支持的模型列表）
    try:
        get_model("not-a-model")
    except ValueError as exc:
        print(f"[OK] 非法模型名正确抛出 ValueError：{exc}")
    else:
        raise SystemExit("[FAIL] 非法模型名未抛出 ValueError")

    # 2) 逐个模型发送测试消息
    failed = 0
    for model_name in settings.model_list:
        try:
            reply = get_model(model_name).invoke("请用一句话介绍你自己，不超过 20 个字。")
            print(f"[OK] {model_name}: {reply.content}")
        except Exception as exc:  # noqa: BLE001 — 测试脚本需汇总所有模型的失败，不做中断
            failed += 1
            print(f"[FAIL] {model_name}: {type(exc).__name__}: {exc}")

    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
