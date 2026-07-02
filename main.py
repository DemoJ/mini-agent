"""
mini-agent 入口文件
====================
直接进入交互式对话。
"""

import sys

from agent_loop import Agent


def main() -> None:
    # 配置路径（支持命令行参数扩展用，暂保持简单）
    config_path = "config.yaml"

    try:
        agent = Agent(config_path)
    except FileNotFoundError:
        print("[错误] 配置文件 config.yaml 不存在。", file=sys.stderr)
        print("提示: 请复制 config.yaml.example 为 config.yaml 并填入真实配置。", file=sys.stderr)
        sys.exit(1)

    print("=" * 50)
    print("  mini-agent 交互模式")
    print("  输入 /exit 或 /quit 退出")
    print("  输入 /new 清空对话历史")
    print("=" * 50)

    while True:
        try:
            user_input = input("\n你 > ")
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        text = user_input.strip()
        if not text:
            continue

        if text in ("/exit", "/quit"):
            print("再见！")
            break

        if text == "/reset" or text == "/new":
            agent.reset()
            print("对话历史已清空。")
            continue

        answer = agent.chat(text)
        if answer:
            print(f"\n agent > {answer}")
        else:
            print("\n agent > （未能生成回复）")


if __name__ == "__main__":
    main()
