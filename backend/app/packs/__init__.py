"""场景包（packs）：领域资产的容器，内核不直接 import 包内模块。

每个子目录代表一个可插拔场景（客服、IT Helpdesk…），由 `manifest.yaml` 声明
自身提供的 Agent、自定义工具与知识库命名空间；内核通过 `loader` 反向装载。
"""
