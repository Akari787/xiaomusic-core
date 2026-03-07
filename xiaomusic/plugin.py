"""Exec-plugin manager for custom voice command handlers (exec# prefix).

NOTE: This PluginManager is NOT the "PluginManager complex edition" referenced in
architecture_unified_refactor_design.md §13 deletion list. That refers to a planned
removal of the old source-plugin orchestration system, which has been superseded by
xiaomusic/core/source/. This file manages exec# command plugins only and is unrelated
to the core Source Plugin architecture.
"""

import importlib
import inspect
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xiaomusic.xiaomusic import XiaoMusic


class PluginManager:
    def __init__(self, xiaomusic: "XiaoMusic", plugin_dir="plugins"):
        self.xiaomusic = xiaomusic
        self.log = xiaomusic.log
        self._funcs = {}
        self._load_plugins(plugin_dir)

    def _load_plugins(self, plugin_dir):
        # 假设 plugins 已经在搜索路径上
        package_name = plugin_dir
        package = importlib.import_module(package_name)

        # 遍历 package 中所有模块并动态导入它们
        for _, modname, _ in pkgutil.iter_modules(package.__path__, package_name + "."):
            # 跳过__init__文件
            if modname.endswith("__init__"):
                continue
            module = importlib.import_module(modname)
            # 将 log 和 xiaomusic 注入模块的命名空间
            module.log = self.log
            module.xiaomusic = self.xiaomusic

            # 动态获取模块中与文件名同名的函数
            function_name = modname.split(".")[-1]  # 从模块全名提取函数名
            if hasattr(module, function_name):
                self._funcs[function_name] = getattr(module, function_name)
            else:
                self.log.error(
                    f"No function named '{function_name}' found in module {modname}"
                )

    def get_func(self, plugin_name):
        """根据插件名获取插件函数"""
        return self._funcs.get(plugin_name)

    def get_local_namespace(self):
        """返回包含所有插件函数的字典，可以用作 exec 要执行的代码的命名空间"""
        return self._funcs.copy()

    async def execute_plugin(self, code):
        """
        执行指定的插件代码。

        旧版本使用 eval 直接执行任意表达式，存在安全风险。
        新版本仅允许 exec#<command>(<literal args>) 形式，并受配置控制。
        """
        from xiaomusic.security.exec_plugin import ExecPluginEngine

        engine = ExecPluginEngine(
            config=self.xiaomusic.config,
            log=self.log,
            plugin_manager=self,
        )
        return await engine.execute(code)
