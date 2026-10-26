"""post-ingest 钩子：ingest/备份流程完成后执行用户自定义命令。

用法（设置 → 存储卡 → post-ingest 钩子命令）：
- 命令通过系统 shell 执行（macOS/Linux: /bin/sh，Windows: cmd）；
- 上下文经环境变量注入：``DIT_EVENT``、``DIT_SOURCE_PATH``、
  ``DIT_PROJECT_ID``、``DIT_RESULT``（JSON 摘要）等；
- 钩子失败只记录日志，不影响主流程（数据安全优先：素材已落盘，
  通知失败不应被判为备份失败）。
"""

import json
import os
import subprocess

from DITWorkstation.Utils import logger

HOOK_TIMEOUT_SECONDS = 300
_HOOK_OUTPUT_LIMIT = 500


def run_post_ingest_hook(
    command: str, context: dict | None = None, timeout: int = HOOK_TIMEOUT_SECONDS
) -> tuple[bool, str]:
    """执行 post-ingest 钩子命令（阻塞调用，建议在后台线程使用）。

    Args:
        command: 用户配置的 shell 命令；空命令直接返回成功
        context: 注入环境变量的上下文（source_path/project_id/result 等）
        timeout: 超时秒数

    Returns:
        (是否成功, 输出/错误摘要)
    """
    if not command or not command.strip():
        return True, "未配置钩子命令"

    env = os.environ.copy()
    env["DIT_EVENT"] = "post_ingest"
    for key, value in (context or {}).items():
        if isinstance(value, (dict, list)):
            env[f"DIT_{key.upper()}"] = json.dumps(value, ensure_ascii=False)
        else:
            env[f"DIT_{key.upper()}"] = str(value)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"post-ingest 钩子超时({timeout}s): {command}")
        return False, f"钩子执行超时（{timeout}s）"
    except OSError as e:
        logger.warning(f"post-ingest 钩子执行失败: {e}")
        return False, str(e)

    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        logger.warning(
            f"post-ingest 钩子返回码 {result.returncode}: {output[:_HOOK_OUTPUT_LIMIT]}"
        )
        return False, f"返回码 {result.returncode}: {output[:_HOOK_OUTPUT_LIMIT]}"

    logger.info(f"post-ingest 钩子执行成功: {command}")
    return True, output[:_HOOK_OUTPUT_LIMIT]
