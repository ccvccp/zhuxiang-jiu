"""vectordb 包:三层自适应向量库的存储层(设计文档 §3 / §4 阶段一)

对外暴露 open_store / LanceStore / MemoryStore / ProductVector ——
屏蔽 LanceDB 懒加载、维度铁律校验与内存回退细节。
"""
