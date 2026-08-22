"""应用版本信息。

使用构建时间戳，在模块导入时固定一次，避免每次启动生成不同版本号。
构建时可通过环境变量 DIT_BUILD_VERSION 覆盖，便于 CI/CD 注入版本号。
"""

import os
from datetime import date

VERSION_PREFIX = "alpha"

# 默认构建版本：模块导入时固定一次，之后不再变化。
# 可通过环境变量 DIT_BUILD_VERSION 在 CI/CD 中覆盖为正式版本号。
_DEFAULT_BUILD = date.today().strftime("%Y%m%d")

APP_VERSION = (
    os.environ.get("DIT_BUILD_VERSION") or f"{VERSION_PREFIX}.{_DEFAULT_BUILD}"
)
