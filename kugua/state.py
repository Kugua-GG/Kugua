"""
kugua State — 共享模块引用

所有模块持有同一个 KuguaState。模块自己读自己需要的局部状态。
没有全局梯度平滑——保留局部信号的"尖刺"供学习使用。
"""
from __future__ import annotations

from typing import Any


class KuguaState:
    """共享模块引用——不包含全局梯度、不包含平滑逻辑。"""

    def __init__(self):
        self.kb: Any = None
        self.graph: Any = None
        self.csd: Any = None
        self.mobius: Any = None
        self.efficacy: Any = None
        self.double_loop: Any = None
        self.negentropy: Any = None
        self.invocations: int = 0
