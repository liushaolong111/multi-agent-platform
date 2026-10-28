"""Mapper 层：ORM Model 与业务 Entity 之间的转换器。

职责：
- to_entity(model): 把 ORM 模型转成业务实体（业务层使用）
- to_model(entity): 把业务实体转成 ORM 模型（持久化时使用）

这样业务层只依赖 entity，不直接耦合 ORM。
"""
